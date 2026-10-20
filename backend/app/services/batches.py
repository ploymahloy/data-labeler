"""Batch lifecycle: open -> archived -> deleted.

Archiving stops all work in a batch atomically: it returns every active lease to its queue and
blocks new claims and uploads. Claims take a shared row lock on the batch and archive takes a
conflicting one, so a claim either happens entirely before the archive (and the archive releases
the lease it created) or sees the batch as archived. FOR NO KEY UPDATE is used because foreign
key checks (labels, images) take FOR KEY SHARE on the batch, which it does not conflict with.
"""

import asyncio
import time
import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError, not_found
from app.models import Batch, Image
from app.storage import Storage

_RELEASE_LEASES = text(
    """
    UPDATE images SET
        status = CASE
            WHEN lease_kind = 'review' THEN 'submitted'
            WHEN last_submission_no > 0 THEN 'rework'
            ELSE 'pending' END,
        assigned_to = NULL, lease_kind = NULL, lease_expires_at = NULL, version = version + 1
    WHERE batch_id = :batch AND assigned_to IS NOT NULL
    RETURNING id
    """
)


def _inactive_error(status: str) -> ApiError:
    if status == "archived":
        return ApiError(409, "batch_archived", "Batch is archived")
    return ApiError(409, "batch_closed", "Batch is closed")


async def lock_open_batch(session: AsyncSession, batch_id: uuid.UUID) -> None:
    """Share-lock the batch and require it to be open. Must run inside a transaction."""
    status = await session.scalar(
        text("SELECT status FROM batches WHERE id = :id FOR SHARE"), {"id": batch_id}
    )
    if status is None:
        raise not_found("Batch")
    if status != "open":
        raise _inactive_error(status)


async def _lock_batch_for_change(session: AsyncSession, batch_id: uuid.UUID) -> Batch:
    batch = await session.scalar(
        select(Batch)
        .where(Batch.id == batch_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if batch is None:
        raise not_found("Batch")
    return batch


async def archive_batch(session: AsyncSession, batch_id: uuid.UUID, admin_sub: str) -> int:
    """Archive the batch and release its leases. Returns how many leases were released.

    Idempotent: archiving an archived batch changes nothing.
    """
    batch = await _lock_batch_for_change(session, batch_id)
    if batch.status == "archived":
        return 0
    batch.status = "archived"
    batch.archived_at = func.now()
    batch.archived_by = admin_sub
    released = await session.execute(_RELEASE_LEASES, {"batch": batch_id})
    await session.flush()
    await session.refresh(batch)  # archived_at was set by the database clock
    return len(released.all())


async def unarchive_batch(session: AsyncSession, batch_id: uuid.UUID) -> None:
    batch = await _lock_batch_for_change(session, batch_id)
    if batch.status == "archived":
        batch.status = "open"
        batch.archived_at = None
        batch.archived_by = None


def _cooldown(settings: Settings) -> int:
    configured = settings.batch_delete_cooldown_seconds
    return settings.presign_expiry_seconds if configured is None else configured


async def delete_batch(
    session: AsyncSession, storage: Storage, settings: Settings, batch_id: uuid.UUID
) -> tuple[bool, int, int]:
    """Delete an archived batch within a time budget. Returns (done, objects, images).

    Safe to call repeatedly: S3 objects go first (versions included), then image rows in chunks,
    then the batch. If the process dies half way the batch stays archived and the call can be
    repeated. Nothing in an archived batch is reachable by users, so partial states are invisible.
    """
    async with session.begin():
        batch = await session.get(Batch, batch_id)
        if batch is None:
            raise not_found("Batch")
        if batch.status != "archived":
            raise ApiError(409, "batch_not_archived", "Archive the batch before deleting it")
        remaining = await session.scalar(
            text(
                "SELECT GREATEST(0, CAST(:cooldown AS float)"
                " - EXTRACT(epoch FROM now() - archived_at)) FROM batches WHERE id = :id"
            ),
            {"cooldown": _cooldown(settings), "id": batch_id},
        )
    if remaining and remaining > 0:
        wait = int(remaining) + 1
        raise ApiError(
            409,
            "archive_cooldown",
            "Upload links issued before archiving may still be valid. Try again shortly.",
            headers={"Retry-After": str(wait)},
            retry_after_seconds=wait,
        )

    deadline = time.monotonic() + settings.batch_delete_time_budget_seconds
    prefix = f"batches/{batch_id}/"
    objects = 0
    images = 0

    # Every call does at least one unit of work per phase, so a tiny time budget still makes
    # progress instead of starving.
    while True:
        removed = await asyncio.to_thread(
            storage.delete_prefix_chunk, prefix, settings.batch_delete_s3_chunk
        )
        objects += removed
        if removed == 0:
            break
        if time.monotonic() >= deadline:
            return False, objects, images

    while True:
        async with session.begin():
            ids = (
                await session.scalars(
                    select(Image.id)
                    .where(Image.batch_id == batch_id)
                    .limit(settings.batch_delete_row_chunk)
                )
            ).all()
            if not ids:
                await session.execute(delete(Batch).where(Batch.id == batch_id))
                return True, objects, images
            await session.execute(delete(Image).where(Image.id.in_(ids)))
            images += len(ids)
        if time.monotonic() >= deadline:
            return False, objects, images
