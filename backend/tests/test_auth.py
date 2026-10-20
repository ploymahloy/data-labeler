import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.helpers import ISSUER, auth, make_token


async def test_missing_token_is_401(client: httpx.AsyncClient):
    r = await client.get("/batches")
    assert r.status_code == 401


async def test_garbage_token_is_401(client: httpx.AsyncClient):
    r = await client.get("/batches", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_valid_token_creates_user_and_lists(client: httpx.AsyncClient, sql):
    r = await client.get("/batches", headers=auth("ana@example.com", "labeler"))
    assert r.status_code == 200
    row = await sql.fetch("SELECT email, roles FROM users")
    assert [(x["email"], list(x["roles"])) for x in row] == [("ana@example.com", ["labeler"])]


async def test_token_signed_by_unknown_key_is_rejected(client: httpx.AsyncClient):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    token = make_token("eve@example.com", ["admin"], key=pem)
    r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_expired_token_is_rejected(client: httpx.AsyncClient):
    token = make_token("old@example.com", ["labeler"], expires_in=-60)
    r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_wrong_audience_and_issuer_are_rejected(client: httpx.AsyncClient):
    for kwargs in ({"audience": "someone-else"}, {"issuer": ISSUER + "-evil"}):
        token = make_token("x@example.com", ["labeler"], **kwargs)
        r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, kwargs


async def test_access_tokens_are_rejected_id_token_required(client: httpx.AsyncClient):
    token = make_token("x@example.com", ["labeler"], token_use="access")
    r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_hs256_tokens_are_not_accepted_outside_dev_mode(client: httpx.AsyncClient):
    token = jwt.encode(
        {"sub": "x", "exp": 9999999999}, "secret-secret-secret-secret-123456", "HS256"
    )
    r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_dev_login_is_not_mounted_in_cognito_mode(client: httpx.AsyncClient):
    r = await client.post("/dev/login", json={"email": "a@b.c", "roles": ["admin"]})
    assert r.status_code == 404


async def test_role_enforcement(client: httpx.AsyncClient):
    # reviewers cannot create batches; users with no groups can do nothing role-gated
    r = await client.post(
        "/batches", json={"name": "x"}, headers=auth("rev@example.com", "reviewer")
    )
    assert r.status_code == 403
    r = await client.post("/batches", json={"name": "x"}, headers=auth("nobody@example.com"))
    assert r.status_code == 403
    r = await client.post(
        "/batches", json={"name": "x"}, headers=auth("lab@example.com", "labeler")
    )
    assert r.status_code == 201
    r = await client.post("/batches", json={"name": "x"}, headers=auth("adm@example.com", "admin"))
    assert r.status_code == 201  # admin acts in any role


async def test_roles_follow_the_token_groups_on_each_request(client: httpx.AsyncClient, sql):
    await client.get("/batches", headers=auth("pat@example.com", "labeler"))
    await client.get("/batches", headers=auth("pat@example.com", "labeler", "reviewer", "bogus"))
    row = await sql.fetch("SELECT roles FROM users")
    assert list(row[0]["roles"]) == ["labeler", "reviewer"]


async def test_token_about_to_expire_is_still_accepted(client: httpx.AsyncClient):
    token = make_token("late@example.com", ["labeler"], expires_in=5)
    r = await client.get("/batches", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_expiry_returns_a_401_the_spa_can_recover_from(client: httpx.AsyncClient, sql):
    """The SPA reacts to 401 by refreshing its ID token and retrying once: the same user with a
    fresh token must work immediately, and expiry must be reported as a plain 401 challenge."""
    expired = make_token("ana@example.com", ["labeler"], expires_in=-1)
    r = await client.get("/batches", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "expired" in r.json()["detail"].lower()
    assert await sql.val("SELECT count(*) FROM users") == 0  # nothing is written for bad tokens

    refreshed = make_token("ana@example.com", ["labeler"], expires_in=3600)
    r = await client.get("/batches", headers={"Authorization": f"Bearer {refreshed}"})
    assert r.status_code == 200
    assert await sql.val("SELECT count(*) FROM users") == 1
