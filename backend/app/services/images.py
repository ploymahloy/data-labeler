import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUser
from app.errors import forbidden, not_found
from app.models import Decision, Image, ImageSubmission, Label, Review, Role
from app.schemas import AnnotationOut, ImageDetail, ReviewFeedback
from app.storage import Storage


async def load_detail(
    session: AsyncSession,
    storage: Storage,
    image_id: uuid.UUID,
    user: CurrentUser | None = None,
) -> ImageDetail:
    """Image + annotations + latest rejection feedback. If `user` is given, enforce access."""
    image = await session.scalar(
        select(Image)
        .where(Image.id == image_id)
        .options(selectinload(Image.annotations))
        .execution_options(populate_existing=True)
    )
    if image is None:
        raise not_found("Image")

    if user is not None and not (
        image.assigned_to == user.sub
        or image.submitted_by == user.sub
        or user.has_role(Role.reviewer)
        or await session.scalar(
            select(ImageSubmission.id)
            .where(ImageSubmission.image_id == image.id, ImageSubmission.submitted_by == user.sub)
            .limit(1)
        )
    ):
        raise forbidden("not_your_image", "You do not have access to this image")

    label_ids = {a.label_id for a in image.annotations}
    names: dict[uuid.UUID, str] = {}
    if label_ids:
        rows = await session.scalars(select(Label).where(Label.id.in_(label_ids)))
        names = {label.id: label.name for label in rows}

    feedback_row = await session.scalar(
        select(Review)
        .where(Review.image_id == image.id)
        .order_by(Review.submission_no.desc())
        .limit(1)
    )
    feedback = None
    if feedback_row is not None and feedback_row.decision == Decision.rejected.value:
        feedback = ReviewFeedback(
            decision=feedback_row.decision,
            reason=feedback_row.reason,
            reviewer_id=feedback_row.reviewer_id,
            submission_no=feedback_row.submission_no,
            created_at=feedback_row.created_at,
        )

    return ImageDetail(
        id=image.id,
        batch_id=image.batch_id,
        filename=image.filename,
        url=storage.image_url(image.s3_key),
        width=image.width,
        height=image.height,
        status=image.status,
        lease_kind=image.lease_kind,
        lease_expires_at=image.lease_expires_at,
        assigned_to=image.assigned_to,
        submitted_by=image.submitted_by,
        rejection_count=image.rejection_count,
        last_submission_no=image.last_submission_no,
        annotations=[
            AnnotationOut(
                id=a.id,
                label=names.get(a.label_id) or "",
                x=a.x,
                y=a.y,
                w=a.w,
                h=a.h,
                created_by=a.created_by,
            )
            for a in image.annotations
        ],
        review_feedback=feedback,
    )
