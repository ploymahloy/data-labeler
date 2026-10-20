import httpx
import pytest

from app.config import get_settings
from app.ratelimit import RateLimiter, limiter
from tests.helpers import auth, create_batch, png_bytes, upload_images


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_bucket_allows_burst_then_refills_over_time():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert [rl.check("s", "u", 3) for _ in range(3)] == [0, 0, 0]
    wait = rl.check("s", "u", 3)
    assert wait == pytest.approx(20.0)  # 3 per minute = one token per 20 s
    clock.now += 20
    assert rl.check("s", "u", 3) == 0
    assert rl.check("s", "u", 3) > 0


def test_users_and_scopes_are_independent():
    rl = RateLimiter(FakeClock())
    assert rl.check("claim", "alice", 1) == 0
    assert rl.check("claim", "alice", 1) > 0
    assert rl.check("claim", "bob", 1) == 0
    assert rl.check("export", "alice", 1) == 0


def test_idle_buckets_are_pruned_when_the_table_is_full(monkeypatch):
    import app.ratelimit as module

    monkeypatch.setattr(module, "_MAX_TRACKED", 2)
    clock = FakeClock()
    rl = RateLimiter(clock)
    rl.check("s", "a", 5)
    rl.check("s", "b", 5)
    clock.now += 120
    rl.check("s", "c", 5)
    assert len(rl._buckets) == 1


@pytest.fixture
def limits_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_claim_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_presign_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_export_per_minute", 1)
    limiter.reset()
    clock = FakeClock()
    monkeypatch.setattr(limiter, "clock", clock)
    yield clock
    limiter.reset()


async def test_claim_endpoint_returns_429_with_retry_after(
    client: httpx.AsyncClient, limits_on: FakeClock
):
    batch = await create_batch(client, auth("owner@example.com", "labeler"))
    me = auth("lab@example.com", "labeler")
    codes = [
        (await client.post(f"/batches/{batch}/images/next", headers=me)).status_code
        for _ in range(3)
    ]
    assert codes == [204, 204, 204]  # empty queue, but allowed

    r = await client.post(f"/batches/{batch}/images/next", headers=me)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1

    # Another user is unaffected, and the limit lifts as time passes.
    other = auth("other@example.com", "labeler")
    assert (await client.post(f"/batches/{batch}/images/next", headers=other)).status_code == 204
    limits_on.now += 60
    assert (await client.post(f"/batches/{batch}/images/next", headers=me)).status_code == 204


async def test_presign_and_export_are_limited(client: httpx.AsyncClient, limits_on: FakeClock):
    lab = auth("lab@example.com", "labeler")
    batch = await create_batch(client, lab)
    ok = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": 10}]},
        headers=lab,
    )
    assert ok.status_code == 200
    again = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "b.png", "content_type": "image/png", "size": 10}]},
        headers=lab,
    )
    assert again.status_code == 200
    third = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "c.png", "content_type": "image/png", "size": 10}]},
        headers=lab,
    )
    assert third.status_code == 429

    rev = auth("rev@example.com", "reviewer")
    assert (await client.get(f"/batches/{batch}/export", headers=rev)).status_code == 200
    assert (await client.get(f"/batches/{batch}/export", headers=rev)).status_code == 429


async def test_limits_are_off_in_the_default_test_config(client: httpx.AsyncClient):
    # The shared suite makes many rapid calls as one user; limiting is exercised explicitly above.
    lab = auth("lab@example.com", "labeler")
    batch = await create_batch(client, lab)
    results = await upload_images(client, lab, batch, [png_bytes(i) for i in range(5)])
    assert all(r["result"] == "ready" for r in results)
