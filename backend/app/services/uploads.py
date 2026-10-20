"""Presigned uploads and post-upload verification with content-hash de-duplication."""

import asyncio
import hashlib
import io
import uuid
import warnings
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Image, ImageStatus
from app.schemas import (
    CompleteResult,
    UploadFile,
    UploadTicket,
)
from app.services.batches import lock_open_batch
from app.storage import Storage, safe_filename

FORMAT_TO_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


async def get_open_batch(session: AsyncSession, batch_id: uuid.UUID) -> None:
    await lock_open_batch(session, batch_id)


async def register_uploads(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    batch_id: uuid.UUID,
    user_sub: str,
    files: list[UploadFile],
) -> list[UploadTicket]:
    if len(files) > settings.max_files_per_request:
        raise ApiError(
            422, "too_many_files", f"At most {settings.max_files_per_request} files per request"
        )
    for f in files:
        if f.size > settings.max_upload_bytes:
            raise ApiError(
                422, "file_too_large", f"{f.filename} exceeds {settings.max_upload_bytes} bytes"
            )

    async with session.begin():
        await get_open_batch(session, batch_id)
        images = []
        for f in files:
            image_id = uuid.uuid4()
            images.append(
                Image(
                    id=image_id,
                    batch_id=batch_id,
                    filename=f.filename,
                    s3_key=f"batches/{batch_id}/{image_id}/{safe_filename(f.filename)}",
                    content_type=f.content_type,
                    uploaded_by=user_sub,
                    status=ImageStatus.uploading.value,
                )
            )
        session.add_all(images)

    return [
        UploadTicket(
            image_id=img.id,
            filename=img.filename,
            upload_url=await asyncio.to_thread(storage.presign_put, img.s3_key, img.content_type),
            headers={"Content-Type": img.content_type},
        )
        for img in images
    ]


@dataclass
class _Analysis:
    image_id: uuid.UUID
    ok: bool
    result: str = "ready"
    detail: str | None = None
    code: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    content_type: str | None = None


def _too_large(image_id: uuid.UUID, width: int, height: int, settings: Settings) -> _Analysis:
    return _Analysis(
        image_id,
        False,
        "invalid",
        f"Image is too large: {width}x{height} "
        f"(limit {settings.max_image_pixels:,} pixels, {settings.max_image_side:,} px per side)",
        code="image_too_large",
    )


def _analyze(storage: Storage, settings: Settings, image: Image) -> _Analysis:
    """Blocking: verify the uploaded object, hash it, and read its dimensions."""
    info = storage.head(image.s3_key)
    if info is None:
        return _Analysis(image.id, False, "missing", "Object has not been uploaded yet")
    if info.size > settings.max_upload_bytes:
        return _Analysis(image.id, False, "invalid", "File is too large")
    data = storage.read(image.s3_key)
    # Pillow reads only the header here; pixels are never decoded in the API, but the limits
    # keep absurd images (e.g. a tiny PNG declaring billions of pixels) out of every consumer.
    PILImage.MAX_IMAGE_PIXELS = settings.max_image_pixels
    try:
        with warnings.catch_warnings():
            # Sizes between 1x and 2x the limit only warn in Pillow; we enforce the limit below.
            warnings.simplefilter("ignore", PILImage.DecompressionBombWarning)
            pil = PILImage.open(io.BytesIO(data))
        with pil:
            fmt = pil.format or ""
            width, height = pil.size
            if (
                width * height > settings.max_image_pixels
                or max(width, height) > settings.max_image_side
            ):
                return _too_large(image.id, width, height, settings)
            pil.verify()
    except PILImage.DecompressionBombError:
        return _Analysis(
            image.id,
            False,
            "invalid",
            f"Image is too large (more than {settings.max_image_pixels:,} pixels)",
            code="image_too_large",
        )
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        return _Analysis(image.id, False, "invalid", f"Not a readable image: {exc}")
    if fmt not in FORMAT_TO_MIME:
        return _Analysis(image.id, False, "invalid", f"Unsupported image format {fmt}")
    return _Analysis(
        image.id,
        True,
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
        content_type=FORMAT_TO_MIME[fmt],
    )


async def complete_uploads(
    session: AsyncSession,
    storage: Storage,
    settings: Settings,
    batch_id: uuid.UUID,
    user_sub: str,
    image_ids: list[uuid.UUID],
) -> list[CompleteResult]:
    async with session.begin():
        await get_open_batch(session, batch_id)
        rows = (
            await session.scalars(
                select(Image).where(
                    Image.batch_id == batch_id,
                    Image.uploaded_by == user_sub,
                    Image.id.in_(image_ids),
                )
            )
        ).all()
    by_id = {row.id: row for row in rows}

    results: dict[uuid.UUID, CompleteResult] = {}
    todo: list[Image] = []
    for image_id in image_ids:
        row = by_id.get(image_id)
        if row is None:
            results[image_id] = CompleteResult(
                image_id=image_id, result="missing", detail="Unknown image"
            )
        elif row.status != ImageStatus.uploading.value:
            results[image_id] = CompleteResult(image_id=image_id, result="ready")
        else:
            todo.append(row)

    sem = asyncio.Semaphore(8)

    async def analyze(image: Image) -> _Analysis:
        async with sem:
            return await asyncio.to_thread(_analyze, storage, settings, image)

    analyses = await asyncio.gather(*(analyze(img) for img in todo))

    for image, analysis in zip(todo, analyses, strict=True):
        if analysis.result == "missing":
            results[image.id] = CompleteResult(
                image_id=image.id, result="missing", detail=analysis.detail
            )
        elif not analysis.ok:
            await _discard(session, storage, image)
            results[image.id] = CompleteResult(
                image_id=image.id, result="invalid", detail=analysis.detail, code=analysis.code
            )
        else:
            results[image.id] = await _finalize(session, storage, image, analysis)

    return [results[i] for i in image_ids]


async def _discard(session: AsyncSession, storage: Storage, image: Image) -> None:
    async with session.begin():
        db_image = await session.get(Image, image.id)
        if db_image is not None and db_image.status == ImageStatus.uploading.value:
            await session.delete(db_image)
    await asyncio.to_thread(storage.delete, image.s3_key)


async def _finalize(
    session: AsyncSession, storage: Storage, image: Image, analysis: _Analysis
) -> CompleteResult:
    duplicate_of: uuid.UUID | None = None
    try:
        async with session.begin():
            duplicate_of = await session.scalar(
                select(Image.id).where(
                    Image.batch_id == image.batch_id,
                    Image.sha256 == analysis.sha256,
                    Image.id != image.id,
                )
            )
            db_image = await session.get(Image, image.id, with_for_update=True)
            if db_image is None or db_image.status != ImageStatus.uploading.value:
                return CompleteResult(image_id=image.id, result="ready")
            if duplicate_of is None:
                db_image.sha256 = analysis.sha256
                db_image.width = analysis.width
                db_image.height = analysis.height
                db_image.content_type = analysis.content_type or db_image.content_type
                db_image.status = ImageStatus.pending.value
                await session.flush()  # surfaces the unique (batch, sha256) race here
            else:
                await session.delete(db_image)
    except IntegrityError:
        # Another request committed the same bytes between our check and our write.
        session.expire_all()
        async with session.begin():
            duplicate_of = await session.scalar(
                select(Image.id).where(
                    Image.batch_id == image.batch_id,
                    Image.sha256 == analysis.sha256,
                    Image.id != image.id,
                )
            )
        await _discard(session, storage, image)
        return CompleteResult(image_id=image.id, result="duplicate", duplicate_of=duplicate_of)

    if duplicate_of is not None:
        await asyncio.to_thread(storage.delete, image.s3_key)
        return CompleteResult(image_id=image.id, result="duplicate", duplicate_of=duplicate_of)
    return CompleteResult(image_id=image.id, result="ready")
