"""Cognito user administration behind a small interface.

`BotoCognitoAdmin` talks to the real `cognito-idp` API (IRSA grants the pod the admin actions
on this pool only, see infra/terraform/ecr_secrets.tf). `FakeCognitoAdmin` is an in-memory
stand-in used when ENVIRONMENT=local so the admin page works without AWS.

Users are addressed by `sub`, the same identifier the API stores and tokens carry.
"""

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from app.config import Settings, get_settings
from app.models import Role

ROLE_NAMES = [r.value for r in Role]


class UserExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class AdminUser:
    sub: str
    email: str
    enabled: bool
    status: str
    roles: tuple[str, ...]
    created_at: datetime | None = None


@dataclass(frozen=True)
class UserPage:
    users: list[AdminUser]
    next_token: str | None = None


class CognitoAdmin(Protocol):
    """Blocking interface: call from async code with `asyncio.to_thread`."""

    #: True for the local fake: it also adopts users that signed in through dev login.
    adopts_local_users: bool

    def list_users(self, limit: int = 50, token: str | None = None) -> UserPage: ...
    def get_user(self, sub: str) -> AdminUser: ...
    def create_user(self, email: str, roles: list[str]) -> AdminUser: ...
    def set_roles(self, sub: str, roles: list[str]) -> AdminUser: ...
    def set_enabled(self, sub: str, enabled: bool) -> AdminUser: ...
    def count_enabled_admins(self) -> int: ...
    def adopt(self, sub: str, email: str, roles: list[str]) -> None: ...


# ---------------------------------------------------------------------------
# Real implementation
# ---------------------------------------------------------------------------


def _attrs(user: dict) -> dict[str, str]:
    return {a["Name"]: a["Value"] for a in user.get("Attributes", user.get("UserAttributes", []))}


class BotoCognitoAdmin:
    adopts_local_users = False

    def __init__(self, settings: Settings) -> None:
        self._pool = settings.cognito_user_pool_id
        self._client = boto3.client(
            "cognito-idp",
            region_name=settings.cognito_region,
            endpoint_url=settings.cognito_endpoint_url or None,
        )

    # --- helpers -----------------------------------------------------------
    def _username(self, sub: str) -> str:
        """Cognito's Username for a sub. `sub` is a filterable attribute, so this is exact."""
        resp = self._client.list_users(UserPoolId=self._pool, Filter=f'sub = "{sub}"', Limit=1)
        if not resp["Users"]:
            raise UserNotFoundError(sub)
        return resp["Users"][0]["Username"]

    def _roles_by_username(self) -> dict[str, set[str]]:
        members: dict[str, set[str]] = {}
        for role in ROLE_NAMES:
            token: str | None = None
            while True:
                kwargs = {"UserPoolId": self._pool, "GroupName": role, "Limit": 60}
                if token:
                    kwargs["NextToken"] = token
                resp = self._client.list_users_in_group(**kwargs)
                for u in resp["Users"]:
                    members.setdefault(u["Username"], set()).add(role)
                token = resp.get("NextToken")
                if not token:
                    break
        return members

    def _to_user(self, raw: dict, roles: set[str]) -> AdminUser:
        attrs = _attrs(raw)
        return AdminUser(
            sub=attrs.get("sub", raw["Username"]),
            email=attrs.get("email", ""),
            enabled=bool(raw.get("Enabled", True)),
            status=raw.get("UserStatus", ""),
            roles=tuple(sorted(roles)),
            created_at=raw.get("UserCreateDate"),
        )

    def _groups_of(self, username: str) -> set[str]:
        resp = self._client.admin_list_groups_for_user(
            UserPoolId=self._pool, Username=username, Limit=60
        )
        return {g["GroupName"] for g in resp["Groups"] if g["GroupName"] in ROLE_NAMES}

    # --- interface ---------------------------------------------------------
    def list_users(self, limit: int = 50, token: str | None = None) -> UserPage:
        kwargs: dict = {"UserPoolId": self._pool, "Limit": min(limit, 60)}
        if token:
            kwargs["PaginationToken"] = token
        resp = self._client.list_users(**kwargs)
        roles = self._roles_by_username()
        users = [self._to_user(u, roles.get(u["Username"], set())) for u in resp["Users"]]
        return UserPage(users=users, next_token=resp.get("PaginationToken"))

    def get_user(self, sub: str) -> AdminUser:
        username = self._username(sub)
        raw = self._client.admin_get_user(UserPoolId=self._pool, Username=username)
        return self._to_user(
            {**raw, "Attributes": raw["UserAttributes"], "Enabled": raw["Enabled"]},
            self._groups_of(username),
        )

    def create_user(self, email: str, roles: list[str]) -> AdminUser:
        try:
            resp = self._client.admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                DesiredDeliveryMediums=["EMAIL"],  # Cognito emails the temporary password
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UsernameExistsException":
                raise UserExistsError(email) from exc
            raise
        username = resp["User"]["Username"]
        for role in roles:
            self._client.admin_add_user_to_group(
                UserPoolId=self._pool, Username=username, GroupName=role
            )
        return self._to_user(resp["User"], set(roles))

    def set_roles(self, sub: str, roles: list[str]) -> AdminUser:
        username = self._username(sub)
        current = self._groups_of(username)
        wanted = set(roles)
        for role in sorted(wanted - current):
            self._client.admin_add_user_to_group(
                UserPoolId=self._pool, Username=username, GroupName=role
            )
        for role in sorted(current - wanted):
            self._client.admin_remove_user_from_group(
                UserPoolId=self._pool, Username=username, GroupName=role
            )
        return self.get_user(sub)

    def set_enabled(self, sub: str, enabled: bool) -> AdminUser:
        username = self._username(sub)
        action = self._client.admin_enable_user if enabled else self._client.admin_disable_user
        action(UserPoolId=self._pool, Username=username)
        return self.get_user(sub)

    def count_enabled_admins(self) -> int:
        count = 0
        token: str | None = None
        while True:
            kwargs = {"UserPoolId": self._pool, "GroupName": Role.admin.value, "Limit": 60}
            if token:
                kwargs["NextToken"] = token
            resp = self._client.list_users_in_group(**kwargs)
            count += sum(1 for u in resp["Users"] if u.get("Enabled", True))
            token = resp.get("NextToken")
            if not token:
                return count

    def adopt(self, sub: str, email: str, roles: list[str]) -> None:  # pragma: no cover
        raise NotImplementedError("Only the local fake adopts users")


# ---------------------------------------------------------------------------
# In-memory implementation for local development
# ---------------------------------------------------------------------------


@dataclass
class FakeCognitoAdmin:
    adopts_local_users = True
    _users: dict[str, AdminUser] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _get(self, sub: str) -> AdminUser:
        try:
            return self._users[sub]
        except KeyError:
            raise UserNotFoundError(sub) from None

    def list_users(self, limit: int = 50, token: str | None = None) -> UserPage:
        with self._lock:
            ordered = sorted(self._users.values(), key=lambda u: (u.email, u.sub))
        start = int(token) if token else 0
        page = ordered[start : start + limit]
        more = start + limit < len(ordered)
        return UserPage(users=page, next_token=str(start + limit) if more else None)

    def get_user(self, sub: str) -> AdminUser:
        with self._lock:
            return self._get(sub)

    def create_user(self, email: str, roles: list[str]) -> AdminUser:
        with self._lock:
            if any(u.email.lower() == email.lower() for u in self._users.values()):
                raise UserExistsError(email)
            user = AdminUser(
                sub=str(uuid.uuid4()),
                email=email,
                enabled=True,
                status="FORCE_CHANGE_PASSWORD",
                roles=tuple(sorted(roles)),
                created_at=datetime.now(UTC),
            )
            self._users[user.sub] = user
            return user

    def set_roles(self, sub: str, roles: list[str]) -> AdminUser:
        with self._lock:
            user = replace(self._get(sub), roles=tuple(sorted(set(roles))))
            self._users[sub] = user
            return user

    def set_enabled(self, sub: str, enabled: bool) -> AdminUser:
        with self._lock:
            user = replace(self._get(sub), enabled=enabled)
            self._users[sub] = user
            return user

    def count_enabled_admins(self) -> int:
        with self._lock:
            return sum(1 for u in self._users.values() if u.enabled and Role.admin.value in u.roles)

    def adopt(self, sub: str, email: str, roles: list[str]) -> None:
        """Local dev: users who signed in via dev login appear on the admin page too."""
        with self._lock:
            if sub not in self._users:
                self._users[sub] = AdminUser(
                    sub=sub,
                    email=email,
                    enabled=True,
                    status="CONFIRMED",
                    roles=tuple(sorted(roles)),
                    created_at=datetime.now(UTC),
                )


@lru_cache
def get_cognito_admin() -> CognitoAdmin:
    settings = get_settings()
    if settings.environment == "local" and not settings.cognito_endpoint_url:
        return FakeCognitoAdmin()
    return BotoCognitoAdmin(settings)
