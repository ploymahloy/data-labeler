"""Image lifecycle: leases (single annotator / single reviewer), submit, review, reaper.

State machine (see plan):
  pending -> in_progress -> submitted -> in_review -> accepted
                   ^                        |-> rework -> in_progress ...
                   |                        '-> escalated (after max rejections)
All claims are single atomic statements using FOR UPDATE SKIP LOCKED, so two users can never
receive the same image, regardless of how many API pods are running.
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import Settings
from app.errors import ApiError, forbidden, lease_lost, not_found
from app.models import (
    Decision,
    Image,
    ImageStatus,
    ImageSubmission,
    LeaseKind,
    Review,
)
from app.schemas import AnnotationIn
from app.services.annotations import save_annotations
from app.services.batches import lock_open_batch

# --- helpers ---------------------------------------------------------------------


def _clear_lease(image: Image) -> None:
    image.assigned_to = None
    image.lease_kind = None
    image.lease_expires_at = None
    image.version += 1


def _return_to_pool(image: Image) -> None:
    """Lease released or expired: put the image back where it came from."""
    if image.lease_kind == LeaseKind.review.value:
        image.status = ImageStatus.submitted.value
    elif image.last_submission_no > 0:
        image.status = ImageStatus.rework.value
    else:
        image.status = ImageStatus.pending.value
    _clear_lease(image)


async def lock_image(session: AsyncSession, image_id: uuid.UUID) -> tuple[Image, bool]:
    """Row-lock the image. Returns (image, lease_still_valid) using the DB clock."""
    row = (
        await session.execute(
            select(Image, (Image.lease_expires_at > func.now()).label("valid"))
            .where(Image.id == image_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise not_found("Image")
    return row[0], bool(row[1])


async def lock_leased(
    session: AsyncSession,
    image_id: uuid.UUID,
    user_sub: str,
    kind: LeaseKind | None = None,
    *,
    allow_expired: bool = False,
) -> Image:
    image, valid = await lock_image(session, image_id)
    if (
        image.assigned_to != user_sub
        or (kind is not None and image.lease_kind != kind.value)
        or (not valid and not allow_expired)
    ):
        raise lease_lost()
    return image


# --- claims ----------------------------------------------------------------------

_CLAIM_ANNOTATE = text(
    """
    UPDATE images SET
        status = 'in_progress', assigned_to = :me, lease_kind = 'annotate',
        lease_expires_at = now() + make_interval(mins => :lease), version = version + 1
    WHERE id = (
        SELECT i.id FROM images i
        WHERE i.batch_id = :batch
          AND (
            i.status = 'pending'
            OR (i.status = 'rework' AND (
                  i.rework_reserved_for = :me
                  OR i.rework_reserved_until IS NULL
                  OR i.rework_reserved_until <= now()))
          )
          AND NOT EXISTS (SELECT 1 FROM image_skips k WHERE k.image_id = i.id AND k.user_id = :me)
          AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.image_id = i.id AND r.reviewer_id = :me)
        ORDER BY (i.status = 'rework' AND i.rework_reserved_for = :me) DESC, i.created_at, i.id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)

_CLAIM_REVIEW = text(
    """
    UPDATE images SET
        status = 'in_review', assigned_to = :me, lease_kind = 'review',
        lease_expires_at = now() + make_interval(mins => :lease), version = version + 1
    WHERE id = (
        SELECT i.id FROM images i
        WHERE i.batch_id = :batch
          AND i.status = 'submitted'
          AND i.submitted_by <> :me
          AND NOT EXISTS (
              SELECT 1 FROM image_submissions s WHERE s.image_id = i.id AND s.submitted_by = :me
          )
        ORDER BY i.created_at, i.id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)

_REAP = text(
    """
    UPDATE images SET
        status = CASE
            WHEN lease_kind = 'review' THEN 'submitted'
            WHEN last_submission_no > 0 THEN 'rework'
            ELSE 'pending' END,
        assigned_to = NULL, lease_kind = NULL, lease_expires_at = NULL, version = version + 1
    WHERE assigned_to IS NOT NULL
      AND lease_expires_at <= now()
      AND (CAST(:me AS text) IS NULL OR assigned_to = :me)
    RETURNING id
    """
)


async def reap_expired(session: AsyncSession, user_sub: str | None = None) -> int:
    """Return images with expired leases to their queue. Must run inside a transaction."""
    result = await session.execute(_REAP, {"me": user_sub})
    return len(result.all())


async def _claim(
    session: AsyncSession,
    sql,
    batch_id: uuid.UUID,
    user_sub: str,
    kind: LeaseKind,
    in_flight_status: ImageStatus,
    settings: Settings,
) -> uuid.UUID | None:
    """Claim (or resume) a lease. Must run inside a transaction."""
    # Serialize concurrent claims by the same user (e.g. double-clicked "next").
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"claim:{kind.value}:{user_sub}"},
    )
    await lock_open_batch(session, batch_id)
    await reap_expired(session, user_sub)

    existing = await session.scalar(
        select(Image.id)
        .where(
            Image.batch_id == batch_id,
            Image.assigned_to == user_sub,
            Image.lease_kind == kind.value,
            Image.status == in_flight_status.value,
            Image.lease_expires_at > func.now(),
        )
        .order_by(Image.created_at, Image.id)
        .limit(1)
        .with_for_update()
    )
    if existing is not None:
        await session.execute(
            text(
                "UPDATE images SET lease_expires_at = now() + make_interval(mins => :lease)"
                " WHERE id = :id"
            ),
            {"lease": settings.lease_minutes, "id": existing},
        )
        return existing

    return await session.scalar(
        sql, {"me": user_sub, "batch": batch_id, "lease": settings.lease_minutes}
    )


async def claim_annotate(
    session: AsyncSession, batch_id: uuid.UUID, user: CurrentUser, settings: Settings
) -> uuid.UUID | None:
    return await _claim(
        session,
        _CLAIM_ANNOTATE,
        batch_id,
        user.sub,
        LeaseKind.annotate,
        ImageStatus.in_progress,
        settings,
    )


async def claim_review(
    session: AsyncSession, batch_id: uuid.UUID, user: CurrentUser, settings: Settings
) -> uuid.UUID | None:
    return await _claim(
        session,
        _CLAIM_REVIEW,
        batch_id,
        user.sub,
        LeaseKind.review,
        ImageStatus.in_review,
        settings,
    )


# --- lease maintenance -----------------------------------------------------------


async def heartbeat(
    session: AsyncSession, image_id: uuid.UUID, user: CurrentUser, settings: Settings
) -> Image:
    image = await lock_leased(session, image_id, user.sub)
    image.lease_expires_at = func.now() + timedelta(minutes=settings.lease_minutes)  # type: ignore[assignment]
    await session.flush()
    await session.refresh(image)
    return image


async def release(session: AsyncSession, image_id: uuid.UUID, user: CurrentUser) -> None:
    image = await lock_leased(session, image_id, user.sub, allow_expired=True)
    _return_to_pool(image)


async def skip(session: AsyncSession, image_id: uuid.UUID, user: CurrentUser) -> None:
    image = await lock_leased(session, image_id, user.sub, LeaseKind.annotate, allow_expired=True)
    await session.execute(
        text("INSERT INTO image_skips (image_id, user_id) VALUES (:i, :u) ON CONFLICT DO NOTHING"),
        {"i": image.id, "u": user.sub},
    )
    _return_to_pool(image)


async def save_draft(
    session: AsyncSession,
    image_id: uuid.UUID,
    user: CurrentUser,
    items: list[AnnotationIn],
    settings: Settings,
) -> Image:
    image = await lock_leased(session, image_id, user.sub, LeaseKind.annotate)
    await save_annotations(session, image, user.sub, items, settings)
    return image


# --- submit & review -------------------------------------------------------------


async def submit(
    session: AsyncSession,
    image_id: uuid.UUID,
    user: CurrentUser,
    items: list[AnnotationIn],
    settings: Settings,
) -> Image:
    image = await lock_leased(session, image_id, user.sub, LeaseKind.annotate)
    await save_annotations(session, image, user.sub, items, settings)
    image.last_submission_no += 1
    session.add(
        ImageSubmission(
            image_id=image.id, submission_no=image.last_submission_no, submitted_by=user.sub
        )
    )
    image.status = ImageStatus.submitted.value
    image.submitted_by = user.sub
    image.rework_reserved_for = None
    image.rework_reserved_until = None
    _clear_lease(image)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise forbidden(
            "reviewer_cannot_annotate", "You already reviewed this image, so you cannot annotate it"
        ) from exc
    return image


async def decide_review(
    session: AsyncSession,
    image_id: uuid.UUID,
    user: CurrentUser,
    decision: Decision,
    reason: str | None,
    settings: Settings,
) -> str:
    image = await lock_leased(session, image_id, user.sub, LeaseKind.review)
    if image.status != ImageStatus.in_review.value:
        raise lease_lost()

    authored = image.submitted_by == user.sub or bool(
        await session.scalar(
            select(func.count())
            .select_from(ImageSubmission)
            .where(ImageSubmission.image_id == image.id, ImageSubmission.submitted_by == user.sub)
        )
    )
    if authored:
        raise forbidden("self_review", "You cannot review work you annotated")

    session.add(
        Review(
            image_id=image.id,
            submission_no=image.last_submission_no,
            reviewer_id=user.sub,
            decision=decision.value,
            reason=reason.strip() if reason else None,
        )
    )
    if decision is Decision.accepted:
        new_status = ImageStatus.accepted
    else:
        image.rejection_count += 1
        if image.rejection_count >= settings.max_rejections:
            new_status = ImageStatus.escalated
        else:
            new_status = ImageStatus.rework
            image.rework_reserved_for = image.submitted_by
            image.rework_reserved_until = func.now() + timedelta(  # type: ignore[assignment]
                hours=settings.rework_reserve_hours
            )
    image.status = new_status.value
    _clear_lease(image)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409, "already_reviewed", "This submission has already been reviewed"
        ) from exc
    return new_status.value


async def requeue_escalated(session: AsyncSession, image_id: uuid.UUID) -> None:
    image, _ = await lock_image(session, image_id)
    if image.status != ImageStatus.escalated.value:
        raise ApiError(409, "not_escalated", "Only escalated images can be requeued")
    image.status = ImageStatus.pending.value
    image.rejection_count = 0
    image.rework_reserved_for = None
    image.rework_reserved_until = None
    image.version += 1


__all__ = [
    "claim_annotate",
    "claim_review",
    "decide_review",
    "heartbeat",
    "lock_image",
    "lock_leased",
    "reap_expired",
    "release",
    "requeue_escalated",
    "save_draft",
    "skip",
    "submit",
]
