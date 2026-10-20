"""Batch archive / unarchive / delete."""

import asyncio

import boto3
import httpx
import pytest

from app.config import get_settings
from tests.conftest import S3_ENDPOINT
from tests.helpers import auth, box, png_bytes, seed_batch, upload_images

ADMIN = auth("admin@example.com", "admin")
ANA = auth("ana@example.com", "labeler")
REV = auth("rev@example.com", "reviewer")


async def archive(client: httpx.AsyncClient, batch: str, headers=ADMIN) -> httpx.Response:
    return await client.post(f"/batches/{batch}/archive", headers=headers)


def s3_keys(batch: str) -> list[str]:
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket="labeler-test", Prefix=f"batches/{batch}/")
    return [o["Key"] for o in listing.get("Contents", [])]


@pytest.fixture
def instant_delete(monkeypatch: pytest.MonkeyPatch):
    """No cooldown, small chunks: so tests exercise the chunk loops."""
    monkeypatch.setenv("BATCH_DELETE_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("BATCH_DELETE_S3_CHUNK", "2")
    monkeypatch.setenv("BATCH_DELETE_ROW_CHUNK", "2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- archive ------------------------------------------------------------------


async def test_archive_blocks_claims_and_uploads_and_hides_from_list(client: httpx.AsyncClient):
    batch = await seed_batch(client, 2)

    r = await archive(client, batch)
    assert r.status_code == 200
    assert r.json()["status"] == "archived" and r.json()["archived_at"]

    for path, headers in [
        (f"/batches/{batch}/images/next", ANA),
        (f"/batches/{batch}/reviews/next", REV),
    ]:
        blocked = await client.post(path, headers=headers)
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "batch_archived"

    upload = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": 10}]},
        headers=ANA,
    )
    assert upload.status_code == 409 and upload.json()["detail"]["code"] == "batch_archived"

    default_list = await client.get("/batches", headers=ANA)
    assert batch not in [b["id"] for b in default_list.json()]
    with_archived = await client.get("/batches?include_archived=true", headers=ANA)
    assert batch in [b["id"] for b in with_archived.json()]
    # Archived batches stay readable (export before delete).
    assert (await client.get(f"/batches/{batch}", headers=ANA)).status_code == 200
    assert (await client.get(f"/batches/{batch}/export", headers=ADMIN)).status_code == 200


async def test_archive_releases_active_leases_and_holders_lose_them(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 3)
    annotating = (await client.post(f"/batches/{batch}/images/next", headers=ANA)).json()["id"]
    BOB = auth("bob@example.com", "labeler")
    submitted = (await client.post(f"/batches/{batch}/images/next", headers=BOB)).json()["id"]
    await client.post(f"/images/{submitted}/submit", json={"annotations": [box()]}, headers=BOB)
    reviewing = (await client.post(f"/batches/{batch}/reviews/next", headers=REV)).json()["id"]
    assert reviewing == submitted

    assert (await archive(client, batch)).status_code == 200

    assert await sql.val("SELECT count(*) FROM images WHERE assigned_to IS NOT NULL") == 0
    statuses = {str(r["id"]): r["status"] for r in await sql.fetch("SELECT id, status FROM images")}
    assert statuses[annotating] == "pending"
    assert statuses[reviewing] == "submitted"

    hb = await client.post(f"/images/{annotating}/heartbeat", headers=ANA)
    assert hb.status_code == 409 and hb.json()["detail"]["code"] == "lease_lost"
    save = await client.put(
        f"/images/{annotating}/annotations", json={"annotations": [box()]}, headers=ANA
    )
    assert save.status_code == 409
    decision = await client.post(
        f"/images/{reviewing}/review", json={"decision": "accepted"}, headers=REV
    )
    assert decision.status_code == 409


async def test_archive_is_idempotent_and_unarchive_restores_work(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    first = await archive(client, batch)
    second = await archive(client, batch)
    assert first.status_code == second.status_code == 200
    assert first.json()["archived_at"] == second.json()["archived_at"]

    restored = await client.post(f"/batches/{batch}/unarchive", headers=ADMIN)
    assert restored.json()["status"] == "open" and restored.json()["archived_at"] is None
    assert (await client.post(f"/batches/{batch}/images/next", headers=ANA)).status_code == 200


async def test_only_admins_archive_unarchive_or_delete(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    for headers in (ANA, REV):
        assert (await archive(client, batch, headers)).status_code == 403
        assert (
            await client.post(f"/batches/{batch}/unarchive", headers=headers)
        ).status_code == 403
        assert (await client.delete(f"/batches/{batch}", headers=headers)).status_code == 403


async def test_archive_unknown_batch_is_404(client: httpx.AsyncClient):
    r = await archive(client, "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# --- delete -------------------------------------------------------------------


async def test_delete_requires_archived_batch(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    r = await client.delete(f"/batches/{batch}", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "batch_not_archived"
    assert len(s3_keys(batch)) == 1


async def test_delete_waits_out_the_upload_link_cooldown(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    await archive(client, batch)
    r = await client.delete(f"/batches/{batch}", headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "archive_cooldown"
    assert int(r.headers["retry-after"]) > 0
    assert len(s3_keys(batch)) == 1  # nothing was touched


async def test_delete_removes_objects_and_rows(client: httpx.AsyncClient, sql, instant_delete):
    batch = await seed_batch(client, 5)
    other = await seed_batch(client, 2, uploader="other@example.com")
    image = (await client.post(f"/batches/{batch}/images/next", headers=ANA)).json()["id"]
    await client.post(f"/images/{image}/submit", json={"annotations": [box()]}, headers=ANA)
    assert len(s3_keys(batch)) == 5
    await archive(client, batch)

    r = await client.delete(f"/batches/{batch}", headers=ADMIN)

    assert r.status_code == 200, r.text
    assert r.json() == {"done": True, "objects_deleted": 5, "images_deleted": 5}
    assert s3_keys(batch) == []
    assert await sql.val("SELECT count(*) FROM batches WHERE id = $1::uuid", batch) == 0
    for table in ("images", "annotations", "labels", "image_submissions"):
        col = "batch_id" if table in ("images", "labels") else None
        if col:
            assert await sql.val(f"SELECT count(*) FROM {table} WHERE {col} = $1::uuid", batch) == 0
    assert await sql.val("SELECT count(*) FROM annotations") == 0  # cascaded
    # A different batch is untouched.
    assert len(s3_keys(other)) == 2
    assert await sql.val("SELECT count(*) FROM images WHERE batch_id = $1::uuid", other) == 2
    assert (await client.get(f"/batches/{batch}", headers=ADMIN)).status_code == 404


async def test_delete_is_resumable_within_a_time_budget(
    client: httpx.AsyncClient, sql, instant_delete, monkeypatch: pytest.MonkeyPatch
):
    batch = await seed_batch(client, 6)
    await archive(client, batch)

    # No time budget at all: each call still makes progress (one chunk), and reports not done.
    monkeypatch.setenv("BATCH_DELETE_TIME_BUDGET_SECONDS", "0")
    get_settings.cache_clear()
    r = await client.delete(f"/batches/{batch}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["done"] is False
    assert r.json()["objects_deleted"] == 2  # one S3 chunk of 2, then out of time
    assert len(s3_keys(batch)) == 4

    get_settings.cache_clear()
    total_objects = 2
    total_images = 0
    for _ in range(500):
        r = await client.delete(f"/batches/{batch}", headers=ADMIN)
        assert r.status_code == 200, r.text
        total_objects += r.json()["objects_deleted"]
        total_images += r.json()["images_deleted"]
        if r.json()["done"]:
            break
    else:
        pytest.fail("delete never finished")

    assert (total_objects, total_images) == (6, 6)
    assert s3_keys(batch) == []
    assert await sql.val("SELECT count(*) FROM batches WHERE id = $1::uuid", batch) == 0


async def test_deleting_twice_is_a_clean_404(client: httpx.AsyncClient, instant_delete):
    batch = await seed_batch(client, 1)
    await archive(client, batch)
    assert (await client.delete(f"/batches/{batch}", headers=ADMIN)).json()["done"] is True
    assert (await client.delete(f"/batches/{batch}", headers=ADMIN)).status_code == 404


# --- concurrency --------------------------------------------------------------


@pytest.mark.parametrize("round_", range(4))
async def test_archive_racing_active_claimers_leaves_no_leases(
    client: httpx.AsyncClient, sql, round_: int
):
    """Claims and an archive fire together. Whatever the interleaving, afterwards nobody holds a
    lease, every claim either succeeded before the archive or was refused, and later claims fail."""
    batch = await seed_batch(client, 12)
    users = [f"racer{i}@example.com" for i in range(24)]

    async def claim(email: str) -> int:
        r = await client.post(f"/batches/{batch}/images/next", headers=auth(email, "labeler"))
        return r.status_code

    async def archive_soon() -> int:
        await asyncio.sleep(0.005 * round_)  # shift the archive across the claim window
        return (await archive(client, batch)).status_code

    results = await asyncio.gather(archive_soon(), *(claim(u) for u in users))

    assert results[0] == 200
    assert set(results[1:]) <= {200, 204, 409}
    assert await sql.val("SELECT count(*) FROM images WHERE assigned_to IS NOT NULL") == 0
    assert await sql.val("SELECT count(*) FROM images WHERE status = 'in_progress'") == 0
    assert await sql.val("SELECT count(*) FROM images WHERE status = 'pending'") == 12
    after = await client.post(f"/batches/{batch}/images/next", headers=auth(users[0], "labeler"))
    assert after.status_code == 409


async def test_uploads_after_archive_are_refused_but_earlier_images_survive(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    await archive(client, batch)
    with pytest.raises(AssertionError):  # helper asserts the presign call succeeded
        await upload_images(client, ANA, batch, [png_bytes(777)])
    assert await sql.val("SELECT count(*) FROM images WHERE batch_id = $1::uuid", batch) == 1


def test_delete_prefix_removes_every_version_and_delete_marker():
    """The production images bucket is versioned: a plain delete would leave the data behind."""
    from app.config import Settings
    from app.storage import Storage

    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1")
    bucket = "labeler-versioned"
    s3.create_bucket(Bucket=bucket)
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    for body in (b"v1", b"v2"):
        s3.put_object(Bucket=bucket, Key="batches/x/a.png", Body=body)
    s3.put_object(Bucket=bucket, Key="batches/x/b.png", Body=b"1")
    s3.delete_object(Bucket=bucket, Key="batches/x/b.png")  # leaves a delete marker
    s3.put_object(Bucket=bucket, Key="batches/y/keep.png", Body=b"1")

    storage = Storage(Settings(s3_bucket=bucket, s3_endpoint_url=S3_ENDPOINT))
    total = 0
    while (removed := storage.delete_prefix_chunk("batches/x/", limit=2)) > 0:
        total += removed

    assert total == 4  # two versions of a.png, one of b.png, one delete marker
    listing = s3.list_object_versions(Bucket=bucket)
    assert [v["Key"] for v in listing["Versions"]] == ["batches/y/keep.png"]
    assert "DeleteMarkers" not in listing
