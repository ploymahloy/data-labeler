from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, require_role
from app.db import get_session
from app.models import Role
from app.schemas import (
    AdminRolesUpdate,
    AdminUserCreate,
    AdminUserOut,
    AdminUsersPage,
)
from app.services import user_admin
from app.services.cognito_admin import CognitoAdmin, get_cognito_admin

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_only = require_role(Role.admin)


@router.get("/users", response_model=AdminUsersPage)
async def list_users(
    limit: int = Query(50, ge=1, le=60),
    token: str | None = Query(None),
    _: CurrentUser = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
    admin: CognitoAdmin = Depends(get_cognito_admin),
) -> AdminUsersPage:
    return await user_admin.list_users(session, admin, limit, token)


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def invite_user(
    body: AdminUserCreate,
    _: CurrentUser = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
    admin: CognitoAdmin = Depends(get_cognito_admin),
) -> AdminUserOut:
    """Invite a user: Cognito emails them a temporary password."""
    return await user_admin.invite_user(session, admin, body.email, list(body.roles))


@router.put("/users/{sub}/roles", response_model=AdminUserOut)
async def change_roles(
    sub: str,
    body: AdminRolesUpdate,
    actor: CurrentUser = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
    admin: CognitoAdmin = Depends(get_cognito_admin),
) -> AdminUserOut:
    return await user_admin.change_roles(session, admin, actor, sub, list(body.roles))


@router.post("/users/{sub}/disable", response_model=AdminUserOut)
async def disable_user(
    sub: str,
    actor: CurrentUser = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
    admin: CognitoAdmin = Depends(get_cognito_admin),
) -> AdminUserOut:
    return await user_admin.set_enabled(session, admin, actor, sub, False)


@router.post("/users/{sub}/enable", response_model=AdminUserOut)
async def enable_user(
    sub: str,
    actor: CurrentUser = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
    admin: CognitoAdmin = Depends(get_cognito_admin),
) -> AdminUserOut:
    return await user_admin.set_enabled(session, admin, actor, sub, True)
