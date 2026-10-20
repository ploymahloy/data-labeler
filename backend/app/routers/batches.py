import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, current_user, require_role
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import not_found
from app.models import Batch, Image, ImageStatus, Label, Role
from app.ratelimit import rate_limit
from app.schemas import (
    BatchCreate,
    BatchDeleteProgress,
    BatchOut,
    CompleteRequest,
    CompleteResponse,
    ImageSummary,
    LabelOut,
    UploadRequest,
    UploadResponse,
)
from app.services import batches as batch_service
from app.services import uploads
from app.storage import Storage, get_storage

router = APIRouter(prefix="/batches", tags=["batches"])


async def _counts(
    session: AsyncSession, batch_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    result: dict[uuid.UUID, dict[str, int]] = {b: {} for b in batch_ids}
    if not batch_ids:
        return result
    rows = await session.execute(
        select(Image.batch_id, Image.status, func.count())
        .where(Image.batch_id.in_(batch_ids))
        .group_by(Image.batch_id, Image.status)
    )
    for batch_id, status, count in rows:
        result[batch_id][status] = count
    return result


def _out(batch: Batch, counts: dict[str, int]) -> BatchOut:
    return BatchOut(
        id=batch.id,
        name=batch.name,
        status=batch.status,
        created_at=batch.created_at,
        created_by=batch.created_by,
        archived_at=batch.archived_at,
        counts=counts,
    )


@router.post("", response_model=BatchOut, status_code=201)
async def create_batch(
    body: BatchCreate,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
) -> BatchOut:
    async with session.begin():
        batch = Batch(name=body.name, created_by=user.sub)
        session.add(batch)
        await session.flush()
    return _out(batch, {})


@router.get("", response_model=list[BatchOut])
async def list_batches(
    include_archived: bool = Query(False),
    _: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BatchOut]:
    async with session.begin():
        query = select(Batch).order_by(Batch.created_at.desc())
        if not include_archived:
            query = query.where(Batch.status != "archived")
        batches = (await session.scalars(query)).all()
        counts = await _counts(session, [b.id for b in batches])
    return [_out(b, counts[b.id]) for b in batches]


@router.get("/{batch_id}", response_model=BatchOut)
async def get_batch(
    batch_id: uuid.UUID,
    _: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> BatchOut:
    async with session.begin():
        batch = await session.get(Batch, batch_id)
        if batch is None:
            raise not_found("Batch")
        counts = await _counts(session, [batch_id])
    return _out(batch, counts[batch_id])


@router.post("/{batch_id}/archive", response_model=BatchOut)
async def archive_batch(
    batch_id: uuid.UUID,
    user: CurrentUser = Depends(require_role(Role.admin)),
    session: AsyncSession = Depends(get_session),
) -> BatchOut:
    """Admin: stop all work in the batch. Active leases return to their queues."""
    async with session.begin():
        await batch_service.archive_batch(session, batch_id, user.sub)
    return await get_batch(batch_id, user, session)


@router.post("/{batch_id}/unarchive", response_model=BatchOut)
async def unarchive_batch(
    batch_id: uuid.UUID,
    user: CurrentUser = Depends(require_role(Role.admin)),
    session: AsyncSession = Depends(get_session),
) -> BatchOut:
    async with session.begin():
        await batch_service.unarchive_batch(session, batch_id)
    return await get_batch(batch_id, user, session)


@router.delete("/{batch_id}", response_model=BatchDeleteProgress)
async def delete_batch(
    batch_id: uuid.UUID,
    _: CurrentUser = Depends(require_role(Role.admin)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> BatchDeleteProgress:
    """Admin: permanently delete an archived batch. Call again while `done` is false."""
    done, objects, images = await batch_service.delete_batch(session, storage, settings, batch_id)
    return BatchDeleteProgress(done=done, objects_deleted=objects, images_deleted=images)


@router.post(
    "/{batch_id}/uploads",
    response_model=UploadResponse,
    dependencies=[Depends(rate_limit("presign"))],
)
async def request_uploads(
    batch_id: uuid.UUID,
    body: UploadRequest,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> UploadResponse:
    tickets = await uploads.register_uploads(
        session, storage, settings, batch_id, user.sub, body.files
    )
    return UploadResponse(tickets=tickets)


@router.post(
    "/{batch_id}/uploads/complete",
    response_model=CompleteResponse,
    dependencies=[Depends(rate_limit("presign"))],
)
async def complete_uploads(
    batch_id: uuid.UUID,
    body: CompleteRequest,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CompleteResponse:
    results = await uploads.complete_uploads(
        session, storage, settings, batch_id, user.sub, body.image_ids
    )
    return CompleteResponse(results=results)


@router.get("/{batch_id}/labels", response_model=list[LabelOut])
async def list_labels(
    batch_id: uuid.UUID,
    _: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LabelOut]:
    async with session.begin():
        rows = (
            await session.scalars(
                select(Label).where(Label.batch_id == batch_id).order_by(Label.normalized)
            )
        ).all()
    return [LabelOut(id=r.id, name=r.name) for r in rows]


@router.get("/{batch_id}/escalated", response_model=list[ImageSummary])
async def list_escalated(
    batch_id: uuid.UUID,
    _: CurrentUser = Depends(require_role(Role.admin)),
    session: AsyncSession = Depends(get_session),
) -> list[ImageSummary]:
    async with session.begin():
        rows = (
            await session.scalars(
                select(Image)
                .where(Image.batch_id == batch_id, Image.status == ImageStatus.escalated.value)
                .order_by(Image.created_at, Image.id)
            )
        ).all()
    return [
        ImageSummary(
            id=r.id,
            filename=r.filename,
            status=r.status,
            submitted_by=r.submitted_by,
            rejection_count=r.rejection_count,
        )
        for r in rows
    ]
