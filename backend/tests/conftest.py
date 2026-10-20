"""Test harness: real Postgres (migrated with Alembic), a real S3-compatible HTTP server (moto),
and real RS256 JWT verification against a static JWKS. No mocking of application code.

Required: a reachable Postgres. Set TEST_DATABASE_URL (asyncpg URL), e.g.
    postgresql+asyncpg://labeler:labeler@localhost:5432/labeler_test
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import boto3
import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from moto.server import ThreadedMotoServer

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://labeler:labeler@localhost:5432/labeler_test"
)
S3_PORT = int(os.environ.get("TEST_S3_PORT", "5555"))
S3_ENDPOINT = f"http://127.0.0.1:{S3_PORT}"
POOL_ID = "us-east-1_testpool"
CLIENT_ID = "test-client-id"
KEY_ID = "test-key-1"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


def _b64url(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_numbers = _private_key.public_key().public_numbers()
JWKS_JSON = json.dumps(
    {
        "keys": [
            {
                "kty": "RSA",
                "kid": KEY_ID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url(_numbers.n),
                "e": _b64url(_numbers.e),
            }
        ]
    }
)

# Environment must be in place before `app` is imported.
os.environ.update(
    {
        "DATABASE_URL": TEST_DATABASE_URL,
        "AUTH_MODE": "cognito",
        "COGNITO_REGION": "us-east-1",
        "COGNITO_USER_POOL_ID": POOL_ID,
        "COGNITO_CLIENT_ID": CLIENT_ID,
        "JWKS_JSON": JWKS_JSON,
        "S3_BUCKET": "labeler-test",
        "S3_ENDPOINT_URL": S3_ENDPOINT,
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "LEASE_MINUTES": "15",
        "MAX_REJECTIONS": "3",
        "DB_POOL_SIZE": "40",
        "DB_MAX_OVERFLOW": "20",
    }
)

sys.path.insert(0, str(BACKEND_DIR))


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    async def reset() -> None:
        conn = await asyncpg.connect(_plain_dsn(TEST_DATABASE_URL))
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await conn.close()

    asyncio.run(reset())
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def s3_server() -> Iterator[None]:
    server = ThreadedMotoServer(port=S3_PORT, verbose=False)
    server.start()
    try:
        boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1").create_bucket(
            Bucket="labeler-test"
        )
        yield
    finally:
        server.stop()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(migrated_database: None) -> AsyncIterator[None]:
    conn = await asyncpg.connect(_plain_dsn(TEST_DATABASE_URL))
    try:
        await conn.execute(
            "TRUNCATE annotations, image_skips, reviews, image_submissions, images, labels,"
            " batches, users RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def client(s3_server: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def sql() -> AsyncIterator["Sql"]:
    conn = await asyncpg.connect(_plain_dsn(TEST_DATABASE_URL))
    try:
        yield Sql(conn)
    finally:
        await conn.close()


class Sql:
    """Direct DB access for arranging/inspecting state in tests."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    async def execute(self, query: str, *args):
        return await self.conn.execute(query, *args)

    async def fetch(self, query: str, *args):
        return await self.conn.fetch(query, *args)

    async def val(self, query: str, *args):
        return await self.conn.fetchval(query, *args)
