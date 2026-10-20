"""The streaming COCO export must match the original in-memory export, stay memory-bound, and
never let a slow download starve the rest of the API.

Data is seeded with SQL (`generate_series`) because the API path is far too slow for thousands
of images, and the interesting properties are about the export, not the upload flow.
"""

import json
import tracemalloc
import uuid
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.db import get_engine, get_export_engine, get_sessionmaker
from app.errors import ApiError
from app.services import reports
from tests.conftest import Sql
from tests.helpers import auth
from tests.reference_export import reference_coco_export

REV = auth("rev@example.com", "reviewer")
UPLOADER = "sub-uploader@example.com"
REVIEWER = "sub-rev@example.com"


async def seed_export_batch(
    sql: Sql, images: int, boxes_per_image: int, *, with_users: bool = True
) -> uuid.UUID:
    """Every image gets the same created_at (one statement), so ordering must fall back to the
    id tie-break, which is exactly where naive keyset pagination goes wrong."""
    batch_id = uuid.uuid4()
    if with_users:
        await sql.execute(
            "INSERT INTO users (cognito_sub, email, roles) VALUES ($1, 'uploader@example.com', "
            "'{labeler}'), ($2, 'rev@example.com', '{reviewer}')",
            UPLOADER,
            REVIEWER,
        )
    await sql.execute(
        "INSERT INTO batches (id, name, created_by, status) "
        "VALUES ($1, 'export-batch', $2, 'open')",
        batch_id,
        UPLOADER,
    )
    await sql.execute(
        "INSERT INTO labels (id, batch_id, name, normalized) VALUES "
        "(gen_random_uuid(), $1, 'Zebra', 'zebra'), (gen_random_uuid(), $1, 'apple', 'apple'),"
        " (gen_random_uuid(), $1, 'Mango', 'mango')",
        batch_id,
    )
    await sql.execute(
        """
        INSERT INTO images (id, batch_id, filename, s3_key, content_type, width, height,
                            uploaded_by, status, submitted_by)
        SELECT gen_random_uuid(), $1, 'f' || g || '.png', gen_random_uuid()::text, 'image/png',
               100 + g % 7, 80 + g % 5, $3::varchar,
               CASE g % 4 WHEN 0 THEN 'accepted' WHEN 1 THEN 'accepted'
                          WHEN 2 THEN 'submitted' ELSE 'pending' END,
               CASE WHEN g % 4 IN (0, 1, 2) THEN $3::varchar END
        FROM generate_series(1, $2) g
        """,
        batch_id,
        images,
        UPLOADER,
    )
    await sql.execute(
        "INSERT INTO reviews (id, image_id, submission_no, reviewer_id, decision) "
        "SELECT gen_random_uuid(), id, 1, $2, 'accepted' FROM images "
        "WHERE batch_id = $1 AND status = 'accepted'",
        batch_id,
        REVIEWER,
    )
    await sql.execute(
        """
        INSERT INTO annotations (id, image_id, label_id, x, y, w, h, fingerprint, created_by)
        SELECT gen_random_uuid(), i.id,
               (SELECT id FROM labels WHERE batch_id = $1 ORDER BY normalized
                OFFSET k % 3 LIMIT 1),
               0.05 * k, 0.04 * k, 0.1, 0.2, md5(i.id::text || k::text), $3
        FROM images i CROSS JOIN generate_series(0, $2 - 1) k
        WHERE i.batch_id = $1
        """,
        batch_id,
        boxes_per_image,
        UPLOADER,
    )
    return batch_id


async def reference(batch_id: uuid.UUID, include_unreviewed: bool) -> dict[str, Any]:
    async with get_sessionmaker()() as session, session.begin():
        return await reference_coco_export(session, batch_id, include_unreviewed)


async def streamed(batch_id: uuid.UUID, include_unreviewed: bool) -> dict[str, Any]:
    export = await reports.open_coco_export(batch_id, include_unreviewed)
    return json.loads("".join([chunk async for chunk in export.stream()]))


def annotation_multiset(coco: dict[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (a["image_id"], a["category_id"], *a["bbox"], a["area"], a["iscrowd"])
        for a in coco["annotations"]
    )


@pytest.fixture
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "export_page_size", 7)


@pytest.mark.parametrize("include_unreviewed", [False, True])
async def test_streamed_export_matches_the_in_memory_reference(
    sql: Sql, small_pages: None, include_unreviewed: bool
):
    batch_id = await seed_export_batch(sql, images=61, boxes_per_image=4)
    want = await reference(batch_id, include_unreviewed)
    got = await streamed(batch_id, include_unreviewed)

    assert list(got) == ["info", "images", "annotations", "categories"]
    assert got["info"] == want["info"]
    assert got["images"] == want["images"]  # same order, same sequential ids
    assert got["categories"] == want["categories"]
    assert [c["name"] for c in got["categories"]] == ["apple", "Mango", "Zebra"]
    # created_at ties make annotation order within an image arbitrary, so compare as a multiset.
    assert annotation_multiset(got) == annotation_multiset(want)
    assert [a["id"] for a in got["annotations"]] == list(range(1, len(want["annotations"]) + 1))
    assert len(got["images"]) == (46 if include_unreviewed else 31)
    assert all(
        i["data_labeler"]["reviewer"] == REVIEWER
        for i in got["images"]
        if i["data_labeler"]["status"] == "accepted"
    )


async def test_http_export_matches_the_reference_and_handles_empty_batches(
    client: httpx.AsyncClient, sql: Sql, small_pages: None
):
    batch_id = await seed_export_batch(sql, images=25, boxes_per_image=2)
    r = await client.get(f"/batches/{batch_id}/export?include_unreviewed=true", headers=REV)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    want = await reference(batch_id, True)
    assert r.json()["images"] == want["images"]
    assert r.json()["categories"] == want["categories"]

    empty = uuid.uuid4()
    await sql.execute(
        "INSERT INTO batches (id, name, created_by, status) VALUES ($1, 'empty', $2, 'open')",
        empty,
        UPLOADER,
    )
    r = await client.get(f"/batches/{empty}/export", headers=REV)
    assert r.json() == {
        "info": {"description": "empty", "version": "1.0"},
        "images": [],
        "annotations": [],
        "categories": [],
    }


async def test_export_is_a_consistent_snapshot_even_if_data_changes_mid_download(
    sql: Sql, small_pages: None
):
    batch_id = await seed_export_batch(sql, images=40, boxes_per_image=3)
    want = await reference(batch_id, True)

    export = await reports.open_coco_export(batch_id, True)
    chunks = export.stream()
    first = await anext(chunks)
    # Changes committed after the export began must not appear in it.
    await sql.execute("DELETE FROM annotations WHERE image_id IN (SELECT id FROM images LIMIT 20)")
    await sql.execute("DELETE FROM images WHERE batch_id = $1 AND status = 'pending'", batch_id)
    await sql.execute("UPDATE images SET filename = 'renamed' WHERE batch_id = $1", batch_id)
    got = json.loads(first + "".join([c async for c in chunks]))

    assert got["images"] == want["images"]
    assert annotation_multiset(got) == annotation_multiset(want)


async def test_export_connections_are_released_on_completion_close_and_abandonment(
    sql: Sql, small_pages: None
):
    pool = get_export_engine().pool
    batch_id = await seed_export_batch(sql, images=20, boxes_per_image=1)

    await streamed(batch_id, False)
    assert pool.checkedout() == 0

    abandoned = await reports.open_coco_export(batch_id, False)
    assert pool.checkedout() == 1
    await abandoned.close()
    await abandoned.close()  # idempotent
    assert pool.checkedout() == 0

    half = await reports.open_coco_export(batch_id, False)
    chunks = half.stream()
    await anext(chunks)
    await chunks.aclose()  # client went away mid-stream
    assert pool.checkedout() == 0


async def test_unknown_batch_fails_before_holding_a_connection():
    pool = get_export_engine().pool
    with pytest.raises(ApiError) as excinfo:
        await reports.open_coco_export(uuid.uuid4(), False)
    assert excinfo.value.status_code == 404
    assert pool.checkedout() == 0


async def test_slow_exports_cannot_starve_the_main_pool(
    client: httpx.AsyncClient, sql: Sql, small_pages: None
):
    batch_id = await seed_export_batch(sql, images=10, boxes_per_image=1)
    held = [
        await reports.open_coco_export(batch_id, False)
        for _ in range(get_settings().export_pool_size)
    ]
    try:
        assert get_export_engine().pool.checkedout() == get_settings().export_pool_size
        r = await client.get("/batches", headers=REV)  # still served from the main pool
        assert r.status_code == 200
        assert get_engine().pool.checkedout() == 0
    finally:
        for export in held:
            await export.close()


async def stream_peak(batch_id: uuid.UUID) -> tuple[int, int]:
    """(peak traced bytes, document bytes) while consuming the stream."""
    tracemalloc.start()
    try:
        total = 0
        export = await reports.open_coco_export(batch_id, True)
        async for chunk in export.stream():
            total += len(chunk)
        return tracemalloc.get_traced_memory()[1], total
    finally:
        tracemalloc.stop()


async def test_memory_stays_flat_while_streaming_a_large_batch(sql: Sql):
    batch_id = await seed_export_batch(sql, images=12000, boxes_per_image=6)
    await streamed(batch_id, False)  # warm-up: engine, compiled statements, imports

    peak, total_bytes = await stream_peak(batch_id)
    assert total_bytes > 5_000_000  # a document big enough for the comparison to mean something

    # Calibrate: the old implementation holds the whole document (and ORM objects) in memory.
    tracemalloc.start()
    try:
        await reference(batch_id, True)
        reference_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < reference_peak / 20

    # Flat: a batch 20x smaller needs about the same memory, because only a page is ever held.
    small = await seed_export_batch(sql, images=600, boxes_per_image=6, with_users=False)
    small_peak, _ = await stream_peak(small)
    assert peak < small_peak * 2
