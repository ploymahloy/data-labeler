"""Admin user management: Cognito is the source of truth for identity and groups; the local
`users` table carries the instant off-switch (`active`) and the token cutoff.

Guards (enforced here, not only in the UI):
  * you cannot remove your own admin role or disable yourself;
  * the last enabled admin cannot be demoted or disabled.
Admin-membership changes are serialized with an advisory lock, so two admins cannot demote each
other at the same moment and leave the system with none.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.errors import ApiError, not_found
from app.models import Role
from app.schemas import AdminUserOut, AdminUsersPage
from app.services.cognito_admin import (
    AdminUser,
    CognitoAdmin,
    UserExistsError,
    UserNotFoundError,
)

_ADMIN_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('admin-membership', 0))")


def _out(user: AdminUser) -> AdminUserOut:
    return AdminUserOut(
        sub=user.sub,
        email=user.email,
        enabled=user.enabled,
        status=user.status,
        roles=list(user.roles),
        created_at=user.created_at,
    )


async def _call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except UserNotFoundError:
        raise not_found("User") from None
    except UserExistsError:
        raise ApiError(409, "user_exists", "A user with that email already exists") from None


async def list_users(
    session: AsyncSession, admin: CognitoAdmin, limit: int, token: str | None
) -> AdminUsersPage:
    if admin.adopts_local_users:
        async with session.begin():
            rows = (
                await session.execute(text("SELECT cognito_sub, email, roles FROM users"))
            ).all()
        for sub, email, roles in rows:
            await asyncio.to_thread(admin.adopt, sub, email, list(roles))
    page = await _call(admin.list_users, limit, token)
    return AdminUsersPage(users=[_out(u) for u in page.users], next_token=page.next_token)


async def invite_user(
    session: AsyncSession, admin: CognitoAdmin, email: str, roles: list[str]
) -> AdminUserOut:
    user = await _call(admin.create_user, email, roles)
    return _out(user)


async def _record_change(
    session: AsyncSession,
    target: AdminUser,
    roles: list[str],
    *,
    active: bool | None,
) -> None:
    """Local half of a change, written *before* the Cognito call in the same transaction.

    Refuses older ID tokens (so clients refresh their groups) and optionally flips the
    off-switch. If Cognito then fails, the transaction rolls back and nothing changed.
    """
    await session.execute(
        text(
            """
            INSERT INTO users (cognito_sub, email, roles, active, tokens_valid_after)
            VALUES (:sub, :email, :roles, COALESCE(:active, true), now())
            ON CONFLICT (cognito_sub) DO UPDATE SET
                roles = EXCLUDED.roles,
                active = COALESCE(:active, users.active),
                tokens_valid_after = now()
            """
        ),
        {"sub": target.sub, "email": target.email, "roles": roles, "active": active},
    )


async def change_roles(
    session: AsyncSession,
    admin: CognitoAdmin,
    actor: CurrentUser,
    sub: str,
    roles: list[str],
) -> AdminUserOut:
    wanted = sorted(set(roles))
    async with session.begin():
        await session.execute(_ADMIN_LOCK)
        target = await _call(admin.get_user, sub)
        losing_admin = Role.admin.value in target.roles and Role.admin.value not in wanted
        if losing_admin and sub == actor.sub:
            raise ApiError(409, "cannot_modify_self", "You cannot remove your own admin role")
        if losing_admin and target.enabled and await _call(admin.count_enabled_admins) <= 1:
            raise ApiError(409, "last_admin", "At least one enabled admin must remain")
        await _record_change(session, target, wanted, active=None)
        updated = await _call(admin.set_roles, sub, wanted)
    return _out(updated)


async def set_enabled(
    session: AsyncSession,
    admin: CognitoAdmin,
    actor: CurrentUser,
    sub: str,
    enabled: bool,
) -> AdminUserOut:
    if not enabled and sub == actor.sub:
        raise ApiError(409, "cannot_modify_self", "You cannot disable your own account")
    async with session.begin():
        await session.execute(_ADMIN_LOCK)
        target = await _call(admin.get_user, sub)
        is_admin = Role.admin.value in target.roles
        if not enabled and is_admin and target.enabled:
            if await _call(admin.count_enabled_admins) <= 1:
                raise ApiError(409, "last_admin", "At least one enabled admin must remain")
        await _record_change(session, target, list(target.roles), active=enabled)
        updated = await _call(admin.set_enabled, sub, enabled)
    return _out(updated)
