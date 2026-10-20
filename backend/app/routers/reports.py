import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.auth import CurrentUser, require_role
from app.db import get_session
from app.models import Role
from app.ratelimit import rate_limit
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


@router.get("/{batch_id}/export", dependencies=[Depends(rate_limit("export"))])
async def export_batch(
    batch_id: uuid.UUID,
    include_unreviewed: bool = False,
    _: CurrentUser = Depends(require_role(Role.reviewer)),
) -> StreamingResponse:
    """COCO JSON, streamed. Accepted images only unless `include_unreviewed=true`."""
    export = await reports.open_coco_export(batch_id, include_unreviewed)
    # The generator closes the connection when it finishes or fails; the background task covers
    # a client that disconnects before the first byte is pulled.
    return StreamingResponse(
        export.stream(),
        media_type="application/json",
        background=BackgroundTask(export.close),
    )
