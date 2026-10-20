import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, require_role
from app.db import get_session
from app.models import Role
from app.schemas import ReviewStats
from app.services import reports

router = APIRouter(prefix="/batches", tags=["reports"])


@router.get("/{batch_id}/review-stats", response_model=ReviewStats)
async def review_stats(
    batch_id: uuid.UUID,
    _: CurrentUser = Depends(require_role(Role.reviewer)),
    session: AsyncSession = Depends(get_session),
) -> ReviewStats:
    async with session.begin():
        return await reports.review_stats(session, batch_id)


@router.get("/{batch_id}/export")
async def export_batch(
    batch_id: uuid.UUID,
    include_unreviewed: bool = False,
    _: CurrentUser = Depends(require_role(Role.reviewer)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """COCO JSON. Accepted images only unless `include_unreviewed=true`."""
    async with session.begin():
        return await reports.coco_export(session, batch_id, include_unreviewed)
