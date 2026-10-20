import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, current_user, require_role
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Decision, Role
from app.schemas import (
    ImageDetail,
    LeaseOut,
    ReviewOut,
    ReviewRequest,
    SaveAnnotationsRequest,
    SubmitRequest,
)
from app.services import images as image_service
from app.services import workflow
from app.storage import Storage, get_storage

router = APIRouter(tags=["images"])


# --- annotation queue ---------------------------------------------------------------
@router.post("/batches/{batch_id}/images/next", response_model=ImageDetail)
async def next_image(
    batch_id: uuid.UUID,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    """Atomically claim the next image to annotate (or resume the one already leased)."""
    async with session.begin():
        image_id = await workflow.claim_annotate(session, batch_id, user, settings)
        if image_id is None:
            return Response(status_code=204)
        return await image_service.load_detail(session, storage, image_id)


# --- review queue -------------------------------------------------------------------
@router.post("/batches/{batch_id}/reviews/next", response_model=ImageDetail)
async def next_for_review(
    batch_id: uuid.UUID,
    user: CurrentUser = Depends(require_role(Role.reviewer)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    """Claim the next submitted image authored by someone else (or resume own review lease)."""
    async with session.begin():
        image_id = await workflow.claim_review(session, batch_id, user, settings)
        if image_id is None:
            return Response(status_code=204)
        return await image_service.load_detail(session, storage, image_id)


# --- single image ---------------------------------------------------------------------
@router.get("/images/{image_id}", response_model=ImageDetail)
async def get_image(
    image_id: uuid.UUID,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> ImageDetail:
    async with session.begin():
        return await image_service.load_detail(session, storage, image_id, user)


@router.post("/images/{image_id}/heartbeat", response_model=LeaseOut)
async def heartbeat(
    image_id: uuid.UUID,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LeaseOut:
    async with session.begin():
        image = await workflow.heartbeat(session, image_id, user, settings)
        assert image.lease_expires_at is not None
        return LeaseOut(image_id=image.id, lease_expires_at=image.lease_expires_at)


@router.post("/images/{image_id}/release", status_code=204)
async def release(
    image_id: uuid.UUID,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    async with session.begin():
        await workflow.release(session, image_id, user)
    return Response(status_code=204)


@router.post("/images/{image_id}/skip", status_code=204)
async def skip(
    image_id: uuid.UUID,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    async with session.begin():
        await workflow.skip(session, image_id, user)
    return Response(status_code=204)


@router.put("/images/{image_id}/annotations", response_model=ImageDetail)
async def save_annotations(
    image_id: uuid.UUID,
    body: SaveAnnotationsRequest,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ImageDetail:
    """Idempotent autosave: replaces the image's boxes with the posted set."""
    async with session.begin():
        await workflow.save_draft(session, image_id, user, body.annotations, settings)
        return await image_service.load_detail(session, storage, image_id)


@router.post("/images/{image_id}/submit", response_model=ImageDetail)
async def submit(
    image_id: uuid.UUID,
    body: SubmitRequest,
    user: CurrentUser = Depends(require_role(Role.labeler)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ImageDetail:
    async with session.begin():
        await workflow.submit(session, image_id, user, body.annotations, settings)
        return await image_service.load_detail(session, storage, image_id)


@router.post("/images/{image_id}/review", response_model=ReviewOut)
async def review(
    image_id: uuid.UUID,
    body: ReviewRequest,
    user: CurrentUser = Depends(require_role(Role.reviewer)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReviewOut:
    async with session.begin():
        resulting = await workflow.decide_review(
            session, image_id, user, Decision(body.decision), body.reason, settings
        )
    return ReviewOut(image_id=image_id, decision=body.decision, resulting_status=resulting)


@router.post("/images/{image_id}/requeue", status_code=204)
async def requeue(
    image_id: uuid.UUID,
    _: CurrentUser = Depends(require_role(Role.admin)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Admin: put an escalated image back into the annotation queue."""
    async with session.begin():
        await workflow.requeue_escalated(session, image_id)
    return Response(status_code=204)
