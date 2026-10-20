"""Review statistics and COCO export."""

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import not_found
from app.models import Batch, Decision, Image, ImageStatus, Label, Review, User
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


async def coco_export(
    session: AsyncSession, batch_id: uuid.UUID, include_unreviewed: bool
) -> dict[str, Any]:
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise not_found("Batch")

    statuses = [ImageStatus.accepted.value]
    if include_unreviewed:
        statuses += [s.value for s in UNREVIEWED]

    images = (
        await session.scalars(
            select(Image)
            .where(Image.batch_id == batch_id, Image.status.in_(statuses))
            .options(selectinload(Image.annotations))
            .order_by(Image.created_at, Image.id)
        )
    ).all()
    labels = (
        await session.scalars(
            select(Label).where(Label.batch_id == batch_id).order_by(Label.normalized)
        )
    ).all()
    category_ids = {label.id: idx for idx, label in enumerate(labels, start=1)}

    reviewers: dict[uuid.UUID, str] = {}
    if images:
        rows = await session.execute(
            select(Review.image_id, Review.reviewer_id).where(
                Review.image_id.in_([i.id for i in images]), Review.decision == "accepted"
            )
        )
        reviewers = {image_id: reviewer for image_id, reviewer in rows}

    coco_images: list[dict[str, Any]] = []
    coco_annotations: list[dict[str, Any]] = []
    ann_id = 0
    per_image: dict[uuid.UUID, int] = defaultdict(int)
    for idx, image in enumerate(images, start=1):
        per_image[image.id] = idx
        width, height = image.width or 0, image.height or 0
        coco_images.append(
            {
                "id": idx,
                "file_name": image.filename,
                "width": width,
                "height": height,
                "data_labeler": {
                    "image_id": str(image.id),
                    "status": image.status,
                    "annotator": image.submitted_by,
                    "reviewer": reviewers.get(image.id),
                },
            }
        )
        for ann in image.annotations:
            ann_id += 1
            bw, bh = ann.w * width, ann.h * height
            coco_annotations.append(
                {
                    "id": ann_id,
                    "image_id": idx,
                    "category_id": category_ids[ann.label_id],
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

    return {
        "info": {"description": batch.name, "version": "1.0"},
        "images": coco_images,
        "annotations": coco_annotations,
        "categories": [{"id": category_ids[label.id], "name": label.name} for label in labels],
    }
