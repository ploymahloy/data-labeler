"""Admin user management: the in-memory fake (local) and the real boto3 implementation (moto)."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator

import boto3
import httpx
import pytest
import pytest_asyncio

from app.config import Settings
from app.main import app
from app.services.cognito_admin import BotoCognitoAdmin, get_cognito_admin
from tests.conftest import S3_ENDPOINT
from tests.helpers import auth, sub_of

ADMIN = auth("admin@example.com", "admin")
ANA = auth("ana@example.com", "labeler")


def other_admin() -> dict[str, str]:
    """Minted fresh: tests change this user's roles, which makes older tokens stale."""
    return auth("admin2@example.com", "admin")


@pytest.fixture(autouse=True)
def fresh_fake() -> Iterator[None]:
    get_cognito_admin.cache_clear()
    yield
    get_cognito_admin.cache_clear()
    app.dependency_overrides.pop(get_cognito_admin, None)


async def invite(client: httpx.AsyncClient, email: str, roles=None, headers=ADMIN):
    body = {"email": email, **({"roles": roles} if roles is not None else {})}
    return await client.post("/admin/users", json=body, headers=headers)


async def make_admins(client: httpx.AsyncClient, *emails: str) -> dict[str, str]:
    """Create admin users and return {email: sub}. Their own tokens use sub-<email>, so the
    admin identities in the shared fake must carry those subs: adopt them via their first call."""
    subs = {}
    for email in emails:
        r = await client.get("/admin/users", headers=auth(email, "admin"))
        assert r.status_code == 200, r.text
        subs[email] = sub_of(email)
    return subs


# --- access control -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/users"),
        ("POST", "/admin/users"),
        ("PUT", "/admin/users/x/roles"),
        ("POST", "/admin/users/x/disable"),
        ("POST", "/admin/users/x/enable"),
    ],
)
async def test_only_admins_reach_user_admin(client: httpx.AsyncClient, method: str, path: str):
    body = {"email": "a@b.co", "roles": ["labeler"]} if method != "GET" else None
    if path.endswith("/roles"):
        body = {"roles": ["labeler"]}
    for headers in (ANA, auth("rev@example.com", "reviewer")):
        r = await client.request(method, path, json=body, headers=headers)
        assert r.status_code == 403
    assert (await client.request(method, path, json=body)).status_code == 401


# --- invite and list ------------------------------------------------------------


async def test_invite_lists_and_rejects_duplicates(client: httpx.AsyncClient):
    r = await invite(client, "New.Person@Example.com", ["labeler", "reviewer"])
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["email"] == "new.person@example.com"
    assert created["roles"] == ["labeler", "reviewer"]
    assert created["enabled"] is True and created["status"] == "FORCE_CHANGE_PASSWORD"

    listed = (await client.get("/admin/users", headers=ADMIN)).json()["users"]
    assert "new.person@example.com" in [u["email"] for u in listed]

    again = await invite(client, "new.person@example.com")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "user_exists"


@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "two words@x.io", ""])
async def test_invite_validates_email(client: httpx.AsyncClient, bad: str):
    assert (await invite(client, bad)).status_code == 422


async def test_invite_rejects_unknown_roles(client: httpx.AsyncClient):
    assert (await invite(client, "x@example.com", ["superuser"])).status_code == 422


async def test_listing_pages(client: httpx.AsyncClient):
    for i in range(5):
        await invite(client, f"u{i}@example.com")
    first = (await client.get("/admin/users?limit=2", headers=ADMIN)).json()
    assert len(first["users"]) == 2 and first["next_token"]
    seen = [u["email"] for u in first["users"]]
    token = first["next_token"]
    while token:
        page = (await client.get(f"/admin/users?limit=2&token={token}", headers=ADMIN)).json()
        seen += [u["email"] for u in page["users"]]
        token = page["next_token"]
    assert {f"u{i}@example.com" for i in range(5)} <= set(seen)
    assert len(seen) == len(set(seen))  # no repeats across pages


# --- roles ---------------------------------------------------------------------


async def test_change_roles_replaces_the_set(client: httpx.AsyncClient):
    sub = (await invite(client, "p@example.com", ["labeler"])).json()["sub"]
    r = await client.put(
        f"/admin/users/{sub}/roles", json={"roles": ["reviewer", "labeler"]}, headers=ADMIN
    )
    assert r.status_code == 200 and r.json()["roles"] == ["labeler", "reviewer"]
    r = await client.put(f"/admin/users/{sub}/roles", json={"roles": ["reviewer"]}, headers=ADMIN)
    assert r.json()["roles"] == ["reviewer"]


async def test_change_roles_unknown_user_and_bad_role(client: httpx.AsyncClient):
    missing = await client.put(
        "/admin/users/nope/roles", json={"roles": ["labeler"]}, headers=ADMIN
    )
    assert missing.status_code == 404
    sub = (await invite(client, "q@example.com")).json()["sub"]
    bad = await client.put(f"/admin/users/{sub}/roles", json={"roles": ["root"]}, headers=ADMIN)
    assert bad.status_code == 422


async def test_role_change_makes_older_tokens_stale_but_not_new_ones(
    client: httpx.AsyncClient, sql
):
    """A promoted/demoted user's existing ID token carries the old groups: the API refuses it
    (401 token_stale) so the client refreshes, instead of honouring stale roles for an hour."""
    now = int(time.time())
    stale = auth("p@example.com", "labeler", issued_at=now - 120)
    assert (await client.get("/batches", headers=stale)).status_code == 200  # registers the user

    sub = sub_of("p@example.com")
    await make_admins(client, "admin@example.com")
    # The fake adopts users who have called the API; roles then change.
    await client.get("/admin/users", headers=ADMIN)
    r = await client.put(f"/admin/users/{sub}/roles", json={"roles": ["reviewer"]}, headers=ADMIN)
    assert r.status_code == 200, r.text

    old = await client.get("/batches", headers=stale)
    assert old.status_code == 401 and old.json()["detail"]["code"] == "token_stale"
    fresh = auth("p@example.com", "reviewer", issued_at=int(time.time()))
    assert (await client.get("/batches", headers=fresh)).status_code == 200
    assert await sql.val("SELECT roles FROM users WHERE cognito_sub = $1", sub) == ["reviewer"]


# --- guards --------------------------------------------------------------------


async def test_cannot_remove_own_admin_role_or_disable_self(client: httpx.AsyncClient):
    subs = await make_admins(client, "admin@example.com", "admin2@example.com")
    me = subs["admin@example.com"]
    demote = await client.put(
        f"/admin/users/{me}/roles", json={"roles": ["labeler"]}, headers=ADMIN
    )
    assert demote.status_code == 409 and demote.json()["detail"]["code"] == "cannot_modify_self"
    disable = await client.post(f"/admin/users/{me}/disable", headers=ADMIN)
    assert disable.status_code == 409 and disable.json()["detail"]["code"] == "cannot_modify_self"
    # Keeping admin while changing other roles is fine.
    ok = await client.put(
        f"/admin/users/{me}/roles", json={"roles": ["admin", "reviewer"]}, headers=ADMIN
    )
    assert ok.status_code == 200


async def test_the_last_enabled_admin_cannot_be_demoted_or_disabled(client: httpx.AsyncClient):
    subs = await make_admins(client, "admin@example.com", "admin2@example.com")
    first, second = subs["admin@example.com"], subs["admin2@example.com"]

    # Someone demotes admin2 in the Cognito console (outside this API), so admin2's still-valid
    # token says admin while Cognito says otherwise. admin@ is now the only real admin.
    get_cognito_admin().set_roles(second, ["labeler"])

    r = await client.put(
        f"/admin/users/{first}/roles", json={"roles": ["labeler"]}, headers=other_admin()
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    r = await client.post(f"/admin/users/{first}/disable", headers=other_admin())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    # Still an admin afterwards.
    users = {u["sub"]: u for u in (await client.get("/admin/users", headers=ADMIN)).json()["users"]}
    assert users[first]["roles"] == ["admin"] and users[first]["enabled"] is True


async def test_demoted_admin_loses_access_immediately_even_with_a_valid_token(
    client: httpx.AsyncClient,
):
    subs = await make_admins(client, "admin@example.com", "admin2@example.com")
    now = int(time.time())
    old_token = auth("admin2@example.com", "admin", issued_at=now - 30)
    assert (await client.get("/admin/users", headers=old_token)).status_code == 200

    r = await client.put(
        f"/admin/users/{subs['admin2@example.com']}/roles",
        json={"roles": ["labeler"]},
        headers=ADMIN,
    )
    assert r.status_code == 200

    stale = await client.get("/admin/users", headers=old_token)
    assert stale.status_code == 401 and stale.json()["detail"]["code"] == "token_stale"
    after_refresh = auth("admin2@example.com", "labeler", issued_at=int(time.time()))
    assert (await client.get("/admin/users", headers=after_refresh)).status_code == 403


async def test_two_admins_demoting_each_other_at_once_leave_one(client: httpx.AsyncClient):
    subs = await make_admins(client, "admin@example.com", "admin2@example.com")
    a, b = subs["admin@example.com"], subs["admin2@example.com"]
    demote_b, demote_a = await asyncio.gather(
        client.put(f"/admin/users/{b}/roles", json={"roles": ["labeler"]}, headers=ADMIN),
        client.put(f"/admin/users/{a}/roles", json={"roles": ["labeler"]}, headers=other_admin()),
    )
    assert sorted([demote_b.status_code, demote_a.status_code]) == [200, 409]
    # Whoever lost the race is still an admin; read the outcome with a fresh token for them.
    survivor = auth("admin@example.com", "admin") if demote_b.status_code == 200 else other_admin()
    users = (await client.get("/admin/users", headers=survivor)).json()["users"]
    assert sum("admin" in u["roles"] for u in users if u["sub"] in (a, b)) == 1


# --- disable / enable -----------------------------------------------------------


async def test_disable_cuts_off_a_valid_token_immediately_and_enable_restores(
    client: httpx.AsyncClient, sql
):
    token_now = lambda: auth("ana@example.com", "labeler", issued_at=int(time.time()))  # noqa: E731
    assert (await client.get("/batches", headers=token_now())).status_code == 200
    sub = sub_of("ana@example.com")
    await client.get("/admin/users", headers=ADMIN)  # fake adopts ana

    r = await client.post(f"/admin/users/{sub}/disable", headers=ADMIN)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert await sql.val("SELECT active FROM users WHERE cognito_sub = $1", sub) is False

    blocked = await client.get("/batches", headers=token_now())  # perfectly valid, unexpired token
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "account_disabled"

    r = await client.post(f"/admin/users/{sub}/enable", headers=ADMIN)
    assert r.json()["enabled"] is True
    assert await sql.val("SELECT active FROM users WHERE cognito_sub = $1", sub) is True
    # Enabling also sets the token cutoff, so a token from before it is stale; a new one works.
    old = await client.get(
        "/batches", headers=auth("ana@example.com", "labeler", issued_at=int(time.time()) - 60)
    )
    assert old.status_code == 401
    assert (await client.get("/batches", headers=token_now())).status_code == 200


async def test_disabling_a_user_who_never_signed_in_still_blocks_them(
    client: httpx.AsyncClient, sql
):
    sub = (await invite(client, "never@example.com")).json()["sub"]
    await client.post(f"/admin/users/{sub}/disable", headers=ADMIN)
    assert await sql.val("SELECT active FROM users WHERE cognito_sub = $1", sub) is False


# --- real boto3 implementation against moto ---------------------------------------


@pytest_asyncio.fixture
async def boto_admin(client: httpx.AsyncClient) -> AsyncIterator[BotoCognitoAdmin]:
    idp = boto3.client("cognito-idp", endpoint_url=S3_ENDPOINT, region_name="us-east-1")
    pool = idp.create_user_pool(PoolName="admin-test", UsernameAttributes=["email"])["UserPool"]
    for group in ("admin", "labeler", "reviewer"):
        idp.create_group(UserPoolId=pool["Id"], GroupName=group)
    admin = BotoCognitoAdmin(
        Settings(cognito_user_pool_id=pool["Id"], cognito_endpoint_url=S3_ENDPOINT)
    )
    app.dependency_overrides[get_cognito_admin] = lambda: admin
    yield admin


async def test_boto_implementation_full_lifecycle_through_the_api(
    client: httpx.AsyncClient, boto_admin: BotoCognitoAdmin
):
    # Two admins exist in Cognito so the last-admin guard allows demoting one.
    a = (await invite(client, "root1@example.com", ["admin"])).json()
    b = (await invite(client, "root2@example.com", ["admin", "reviewer"])).json()
    assert a["sub"] and a["roles"] == ["admin"]
    assert b["roles"] == ["admin", "reviewer"]

    listed = (await client.get("/admin/users", headers=ADMIN)).json()["users"]
    by_email = {u["email"]: u for u in listed}
    assert by_email["root2@example.com"]["roles"] == ["admin", "reviewer"]

    r = await client.put(
        f"/admin/users/{b['sub']}/roles", json={"roles": ["labeler"]}, headers=ADMIN
    )
    assert r.status_code == 200 and r.json()["roles"] == ["labeler"]

    r = await client.post(f"/admin/users/{b['sub']}/disable", headers=ADMIN)
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.post(f"/admin/users/{b['sub']}/enable", headers=ADMIN)
    assert r.json()["enabled"] is True

    dup = await invite(client, "root1@example.com")
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "user_exists"

    # root2 is no longer an admin, so root1 is the last enabled admin.
    r = await client.post(f"/admin/users/{a['sub']}/disable", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_admin"
    assert boto_admin.count_enabled_admins() == 1


async def test_boto_implementation_unknown_user_is_404(
    client: httpx.AsyncClient, boto_admin: BotoCognitoAdmin
):
    r = await client.post("/admin/users/does-not-exist/disable", headers=ADMIN)
    assert r.status_code == 404
