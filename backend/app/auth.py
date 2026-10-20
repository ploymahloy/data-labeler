"""Cognito JWT validation (JWKS) and request-scoped user resolution."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKSet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import ApiError
from app.models import Role
from app.observability import set_request_user

bearer_scheme = HTTPBearer(auto_error=False)

KNOWN_ROLES = {r.value for r in Role}


@dataclass(frozen=True)
class CurrentUser:
    sub: str
    email: str
    roles: frozenset[str]

    def has_role(self, *roles: Role) -> bool:
        # Admins can act in any role (but remain subject to per-image independence rules).
        return Role.admin.value in self.roles or any(r.value in self.roles for r in roles)


def _unauthorized(detail: str = "Invalid or missing credentials") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


@lru_cache
def _static_jwks(jwks_json: str) -> PyJWKSet:
    return PyJWKSet.from_json(jwks_json)


@lru_cache
def _remote_jwks_client(url: str) -> PyJWKClient:
    # PyJWKClient caches keys and refetches on unknown `kid` (Cognito key rotation).
    return PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)


def cognito_issuer(settings: Settings) -> str:
    return f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"


def _signing_key(token: str, settings: Settings) -> Any:
    if settings.jwks_json:
        kid = jwt.get_unverified_header(token).get("kid")
        for key in _static_jwks(settings.jwks_json).keys:
            if key.key_id == kid:
                return key.key
        raise jwt.InvalidTokenError("Unknown signing key")
    if not settings.cognito_user_pool_id:
        raise jwt.InvalidTokenError("Auth is not configured")
    url = f"{cognito_issuer(settings)}/.well-known/jwks.json"
    return _remote_jwks_client(url).get_signing_key_from_jwt(token).key


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    """Blocking; call via `asyncio.to_thread` (the JWKS fetch is a network call)."""
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if settings.auth_mode != "dev" or not settings.dev_auth_secret:
                raise jwt.InvalidTokenError("HS256 tokens are not accepted")
            return jwt.decode(
                token,
                settings.dev_auth_secret,
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        if alg != "RS256":
            raise jwt.InvalidTokenError("Unsupported algorithm")
        claims = jwt.decode(
            token,
            _signing_key(token, settings),
            algorithms=["RS256"],
            audience=settings.cognito_client_id or None,
            issuer=cognito_issuer(settings) if settings.cognito_user_pool_id else None,
            options={
                "require": ["exp", "sub"],
                "verify_aud": bool(settings.cognito_client_id),
                "verify_iss": bool(settings.cognito_user_pool_id),
            },
        )
        if claims.get("token_use") != "id":
            raise jwt.InvalidTokenError("An ID token is required")
        return claims
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc


def claims_to_user(claims: dict[str, Any]) -> CurrentUser:
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    roles = frozenset(g for g in groups if g in KNOWN_ROLES)
    return CurrentUser(sub=claims["sub"], email=claims.get("email", ""), roles=roles)


async def _sync_user(
    session: AsyncSession, user: CurrentUser, issued_at: int, skew_seconds: int
) -> None:
    """Enforce the off-switch and token cutoff, then keep the local copy of email and roles fresh.

    One indexed read on the hot path; a write only when something changed.
    """
    async with session.begin():
        row = (
            await session.execute(
                text(
                    """
                    SELECT email, roles, active,
                           COALESCE(
                               tokens_valid_after
                               > to_timestamp(:iat) + make_interval(secs => :skew),
                               false) AS stale
                    FROM users WHERE cognito_sub = :sub
                    """
                ),
                {"sub": user.sub, "iat": issued_at, "skew": skew_seconds},
            )
        ).first()
        if row is not None:
            if not row.active:
                raise ApiError(403, "account_disabled", "This account has been disabled")
            if row.stale:
                # Roles changed after this token was issued: make the client refresh it.
                raise ApiError(
                    401,
                    "token_stale",
                    "Your permissions changed; please refresh your session",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if row.email == user.email and sorted(row.roles) == sorted(user.roles):
                return
        await session.execute(
            text(
                """
                INSERT INTO users (cognito_sub, email, roles)
                VALUES (:sub, :email, :roles)
                ON CONFLICT (cognito_sub) DO UPDATE
                SET email = EXCLUDED.email, roles = EXCLUDED.roles
                """
            ),
            {"sub": user.sub, "email": user.email, "roles": sorted(user.roles)},
        )


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized()
    claims = await asyncio.to_thread(decode_token, credentials.credentials, settings)
    user = claims_to_user(claims)
    await _sync_user(session, user, int(claims.get("iat", 0)), settings.token_cutoff_skew_seconds)
    set_request_user(user.sub)
    return user


def require_role(*roles: Role) -> Callable[..., CurrentUser]:
    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.has_role(*roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of the roles: {', '.join(r.value for r in roles)}",
            )
        return user

    return dependency


def mint_dev_token(
    settings: Settings, email: str, roles: list[str], ttl_seconds: int = 8 * 3600
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": f"dev-{email}",
            "email": email,
            "cognito:groups": roles,
            "token_use": "id",
            "iat": now,
            "exp": now + ttl_seconds,
        },
        settings.dev_auth_secret,
        algorithm="HS256",
    )
