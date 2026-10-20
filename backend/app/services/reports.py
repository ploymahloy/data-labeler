"""Review statistics and the streaming COCO export."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.config import get_settings
from app.db import get_export_engine
from app.errors import not_found
from app.models import Annotation, Batch, Decision, Image, ImageStatus, Label, Review, User
from app.schemas import ReviewerStats, ReviewStats

UNREVIEWED = (ImageStatus.submitted, ImageStatus.in_review)


async def review_stats(session: AsyncSession, batch_id: uuid.UUID) -> ReviewStats:
    if await session.get(Batch, batch_id) is None:
        raise not_found("Batch")
    counts = {
        status: count
        for status, count in await session.execute(
            select(Image.status, func.count())
            .where(Image.batch_id == batch_id)
            .group_by(Image.status)
        )
    }
    rows = await session.execute(
        select(Review.reviewer_id, User.email, Review.decision, func.count())
        .join(Image, Image.id == Review.image_id)
        .join(User, User.cognito_sub == Review.reviewer_id)
        .where(Image.batch_id == batch_id)
        .group_by(Review.reviewer_id, User.email, Review.decision)
    )
    per: dict[str, ReviewerStats] = {}
    for reviewer_id, email, decision, count in rows:
        stats = per.setdefault(
            reviewer_id, ReviewerStats(reviewer_id=reviewer_id, email=email, accepted=0, rejected=0)
        )
        if decision == Decision.accepted.value:
            stats.accepted += count
        else:
            stats.rejected += count
    accepted = sum(r.accepted for r in per.values())
    rejected = sum(r.rejected for r in per.values())
    total = accepted + rejected
    return ReviewStats(
        batch_id=batch_id,
        counts=counts,
        reviewers=sorted(per.values(), key=lambda r: r.email),
        rejection_rate=(rejected / total) if total else None,
    )


logger = logging.getLogger("app.export")

_IMAGES = Image.__table__
_ANNOTATIONS = Annotation.__table__


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


class CocoExport:
    """A COCO document produced page by page, so memory stays flat however large the batch is.

    One REPEATABLE READ transaction is held for the whole stream, so every page (and both passes
    below) sees the same snapshot: the document is internally consistent even if images are
    reviewed while it downloads.

    Output order is info, images, annotations, categories. Image ids are sequential in
    (created_at, id) order. The images pass and the annotations pass walk the same keyset
    query, so they assign identical ids without ever holding the id map in memory.
    """

    def __init__(
        self,
        conn: AsyncConnection,
        *,
        batch_id: uuid.UUID,
        batch_name: str,
        categories: list[tuple[uuid.UUID, str]],
        statuses: list[str],
        page_size: int,
    ) -> None:
        self._conn = conn
        self._batch_id = batch_id
        self._batch_name = batch_name
        self._categories = categories
        self._category_ids = {label_id: i for i, (label_id, _) in enumerate(categories, start=1)}
        self._statuses = statuses
        self._page_size = page_size
        self._closed = False

    async def close(self) -> None:
        """Idempotent: releases the connection (and ends the snapshot) exactly once."""
        if self._closed:
            return
        self._closed = True
        await self._conn.close()

    async def _image_pages(self) -> AsyncIterator[list[Any]]:
        accepted_reviewer = (
            select(Review.reviewer_id)
            .where(Review.image_id == _IMAGES.c.id, Review.decision == "accepted")
            .order_by(Review.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = (
            select(
                _IMAGES.c.id,
                _IMAGES.c.created_at,
                _IMAGES.c.filename,
                _IMAGES.c.width,
                _IMAGES.c.height,
                _IMAGES.c.status,
                _IMAGES.c.submitted_by,
                accepted_reviewer.label("reviewer"),
            )
            .where(_IMAGES.c.batch_id == self._batch_id, _IMAGES.c.status.in_(self._statuses))
            .order_by(_IMAGES.c.created_at, _IMAGES.c.id)
            .limit(self._page_size)
        )
        cursor: tuple[Any, Any] | None = None
        while True:
            page_query = query
            if cursor is not None:
                page_query = query.where(
                    tuple_(_IMAGES.c.created_at, _IMAGES.c.id) > tuple_(*cursor)
                )
            rows = (await self._conn.execute(page_query)).all()
            if not rows:
                return
            yield rows
            cursor = (rows[-1].created_at, rows[-1].id)
            if len(rows) < self._page_size:
                return

    async def stream(self) -> AsyncIterator[str]:
        try:
            info = {"description": self._batch_name, "version": "1.0"}
            yield f'{{"info":{_dumps(info)},"images":['

            index = 0
            separator = ""
            async for rows in self._image_pages():
                parts = []
                for row in rows:
                    index += 1
                    parts.append(
                        _dumps(
                            {
                                "id": index,
                                "file_name": row.filename,
                                "width": row.width or 0,
                                "height": row.height or 0,
                                "data_labeler": {
                                    "image_id": str(row.id),
                                    "status": row.status,
                                    "annotator": row.submitted_by,
                                    "reviewer": row.reviewer,
                                },
                            }
                        )
                    )
                yield separator + ",".join(parts)
                separator = ","

            yield '],"annotations":['
            index = 0
            ann_id = 0
            separator = ""
            async for rows in self._image_pages():
                first_index = index + 1
                index += len(rows)
                by_image: dict[uuid.UUID, list[Any]] = {row.id: [] for row in rows}
                annotations = await self._conn.execute(
                    select(
                        _ANNOTATIONS.c.image_id,
                        _ANNOTATIONS.c.label_id,
                        _ANNOTATIONS.c.x,
                        _ANNOTATIONS.c.y,
                        _ANNOTATIONS.c.w,
                        _ANNOTATIONS.c.h,
                    )
                    .where(_ANNOTATIONS.c.image_id.in_(list(by_image)))
                    .order_by(_ANNOTATIONS.c.created_at, _ANNOTATIONS.c.id)
                )
                for ann in annotations:
                    by_image[ann.image_id].append(ann)

                parts = []
                for offset, row in enumerate(rows):
                    width, height = row.width or 0, row.height or 0
                    for ann in by_image[row.id]:
                        ann_id += 1
                        bw, bh = ann.w * width, ann.h * height
                        parts.append(
                            _dumps(
                                {
                                    "id": ann_id,
                                    "image_id": first_index + offset,
                                    "category_id": self._category_ids[ann.label_id],
                                    "bbox": [
                                        round(ann.x * width, 2),
                                        round(ann.y * height, 2),
                                        round(bw, 2),
                                        round(bh, 2),
                                    ],
                                    "area": round(bw * bh, 2),
                                    "iscrowd": 0,
                                }
                            )
                        )
                if parts:
                    yield separator + ",".join(parts)
                    separator = ","

            categories = [
                {"id": self._category_ids[label_id], "name": name}
                for label_id, name in self._categories
            ]
            yield f'],"categories":{_dumps(categories)}}}'
        except Exception:
            # Headers are already sent, so the client sees a truncated body (invalid JSON),
            # which it cannot mistake for a complete export.
            logger.exception("export_failed", extra={"batch_id": str(self._batch_id)})
            raise
        finally:
            await self.close()


async def open_coco_export(batch_id: uuid.UUID, include_unreviewed: bool) -> CocoExport:
    """Validate and pin a snapshot *before* any response bytes are sent.

    A missing batch is a clean 404 and an exhausted export pool a clean 503, rather than a
    200 that dies halfway through.
    """
    settings = get_settings()
    conn = await get_export_engine().connect()
    try:
        await conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        await conn.begin()
        batch_name = await conn.scalar(select(Batch.name).where(Batch.id == batch_id))
        if batch_name is None:
            raise not_found("Batch")
        labels = (
            await conn.execute(
                select(Label.id, Label.name)
                .where(Label.batch_id == batch_id)
                .order_by(Label.normalized)
            )
        ).all()
    except BaseException:
        await conn.close()
        raise

    statuses = [ImageStatus.accepted.value]
    if include_unreviewed:
        statuses += [s.value for s in UNREVIEWED]
    return CocoExport(
        conn,
        batch_id=batch_id,
        batch_name=batch_name,
        categories=[(label.id, label.name) for label in labels],
        statuses=statuses,
        page_size=settings.export_page_size,
    )
