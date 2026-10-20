"""Annotation persistence: label normalization, IoU de-duplication, idempotent bulk save."""

import uuid
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Annotation, Image, Label
from app.schemas import AnnotationIn


def normalize_label(name: str) -> str:
    return " ".join(name.split()).lower()


def iou(a: AnnotationIn, b: AnnotationIn) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def fingerprint(label_id: uuid.UUID, a: AnnotationIn) -> str:
    return f"{label_id}:{a.x:.3f}:{a.y:.3f}:{a.w:.3f}:{a.h:.3f}"


def find_duplicates(
    items: list[AnnotationIn], threshold: float
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Pairs of boxes with the same (normalized) label and IoU above the threshold."""
    by_label: dict[str, list[AnnotationIn]] = defaultdict(list)
    for item in items:
        by_label[normalize_label(item.label)].append(item)
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for group in by_label.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if iou(a, b) > threshold:
                    pairs.append((a.id, b.id))
    return pairs


async def get_or_create_labels(
    session: AsyncSession, batch_id: uuid.UUID, names: list[str]
) -> dict[str, Label]:
    """Returns normalized-name -> Label. Race-safe via ON CONFLICT DO NOTHING."""
    display: dict[str, str] = {}
    for name in names:
        display.setdefault(normalize_label(name), " ".join(name.split()))
    if display:
        await session.execute(
            pg_insert(Label)
            .values(
                [
                    {"id": uuid.uuid4(), "batch_id": batch_id, "name": d, "normalized": n}
                    for n, d in display.items()
                ]
            )
            .on_conflict_do_nothing(constraint="uq_labels_batch_normalized")
        )
    rows = (
        await session.scalars(
            select(Label).where(Label.batch_id == batch_id, Label.normalized.in_(list(display)))
        )
    ).all()
    return {row.normalized: row for row in rows}


async def save_annotations(
    session: AsyncSession,
    image: Image,
    user_sub: str,
    items: list[AnnotationIn],
    settings: Settings,
) -> None:
    """Replace the image's annotation set with `items` (idempotent bulk upsert).

    Caller must hold the row lock + valid lease on `image` and be inside a transaction.
    """
    if len(items) > settings.max_annotations_per_image:
        raise ApiError(422, "too_many_annotations", "Too many annotations for one image")
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "duplicate_annotation_ids", "Annotation ids must be unique")
    dupes = find_duplicates(items, settings.duplicate_iou_threshold)
    if dupes:
        raise ApiError(
            422,
            "duplicate_annotations",
            "Overlapping boxes with the same label are duplicates; remove one of each pair",
            pairs=[[str(a), str(b)] for a, b in dupes],
        )

    labels = await get_or_create_labels(session, image.batch_id, [i.label for i in items])

    # Drop boxes the client no longer has.
    await session.execute(
        delete(Annotation).where(Annotation.image_id == image.id, Annotation.id.not_in(ids))
    )
    if not items:
        return

    stmt = pg_insert(Annotation).values(
        [
            {
                "id": item.id,
                "image_id": image.id,
                "label_id": labels[normalize_label(item.label)].id,
                "x": item.x,
                "y": item.y,
                "w": item.w,
                "h": item.h,
                "fingerprint": fingerprint(labels[normalize_label(item.label)].id, item),
                "created_by": user_sub,
            }
            for item in items
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Annotation.id],
        set_={
            "label_id": stmt.excluded.label_id,
            "x": stmt.excluded.x,
            "y": stmt.excluded.y,
            "w": stmt.excluded.w,
            "h": stmt.excluded.h,
            "fingerprint": stmt.excluded.fingerprint,
        },
        # Never let a client-chosen id overwrite another image's annotation.
        where=Annotation.image_id == stmt.excluded.image_id,
    ).returning(Annotation.id)
    saved = (await session.scalars(stmt)).all()
    if len(saved) != len(items):
        raise ApiError(409, "annotation_id_conflict", "Annotation id belongs to another image")
