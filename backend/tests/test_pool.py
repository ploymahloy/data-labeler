"""Pool exhaustion must answer 503 quickly rather than hang the request."""

import time
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.config import get_settings
from app.db import get_engine, get_sessionmaker


@pytest_asyncio.fixture
async def tiny_pool(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    async def reset() -> None:
        if get_engine.cache_info().currsize:
            await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        get_settings.cache_clear()

    await reset()
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "0.3")
    try:
        yield
    finally:
        await reset()
        monkeypatch.undo()


async def test_exhausted_pool_returns_fast_503(client: httpx.AsyncClient, tiny_pool: None):
    assert (await client.get("/readyz")).status_code == 200

    async with get_engine().connect():  # hold the only connection
        started = time.monotonic()
        r = await client.get("/readyz")
        elapsed = time.monotonic() - started

    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "db_busy"
    assert r.headers["retry-after"] == "2"
    assert elapsed < 3

    # Capacity recovers once the connection is released.
    assert (await client.get("/readyz")).status_code == 200
