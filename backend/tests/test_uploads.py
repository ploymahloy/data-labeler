import struct
import zlib

import httpx
import pytest

from tests.helpers import auth, create_batch, png_bytes, upload_images

LAB = auth("lab@example.com", "labeler")


async def test_upload_roundtrip_records_hash_and_dimensions(client: httpx.AsyncClient, sql):
    batch = await create_batch(client, LAB)
    results = await upload_images(client, LAB, batch, [png_bytes(1, (80, 60)), png_bytes(2)])
    assert [r["result"] for r in results] == ["ready", "ready"]
    rows = await sql.fetch("SELECT status, width, height, sha256 FROM images ORDER BY width DESC")
    assert [r["status"] for r in rows] == ["pending", "pending"]
    assert (rows[0]["width"], rows[0]["height"]) == (80, 60)
    assert all(len(r["sha256"]) == 64 for r in rows)


async def test_identical_bytes_in_one_batch_are_flagged_duplicate_and_removed(
    client: httpx.AsyncClient, sql
):
    batch = await create_batch(client, LAB)
    first = (await upload_images(client, LAB, batch, [png_bytes(5)]))[0]
    again = (await upload_images(client, LAB, batch, [png_bytes(5)]))[0]
    assert first["result"] == "ready"
    assert again["result"] == "duplicate"
    assert again["duplicate_of"] == first["image_id"]
    assert await sql.val("SELECT count(*) FROM images") == 1


async def test_duplicates_within_a_single_request_keep_exactly_one(client: httpx.AsyncClient, sql):
    batch = await create_batch(client, LAB)
    results = await upload_images(client, LAB, batch, [png_bytes(9)] * 4 + [png_bytes(10)])
    assert sorted(r["result"] for r in results) == ["duplicate"] * 3 + ["ready"] * 2
    assert await sql.val("SELECT count(*) FROM images") == 2


async def test_same_bytes_in_different_batches_are_allowed(client: httpx.AsyncClient, sql):
    b1, b2 = await create_batch(client, LAB, "one"), await create_batch(client, LAB, "two")
    r1 = await upload_images(client, LAB, b1, [png_bytes(3)])
    r2 = await upload_images(client, LAB, b2, [png_bytes(3)])
    assert r1[0]["result"] == r2[0]["result"] == "ready"


async def test_db_rejects_duplicate_hash_even_if_app_check_is_bypassed(
    client: httpx.AsyncClient, sql
):
    import asyncpg
    import pytest

    batch = await create_batch(client, LAB)
    await upload_images(client, LAB, batch, [png_bytes(11)])
    sha = await sql.val("SELECT sha256 FROM images")
    with pytest.raises(asyncpg.UniqueViolationError):
        await sql.execute(
            "INSERT INTO images (id, batch_id, filename, s3_key, content_type, sha256, uploaded_by,"
            " status) SELECT gen_random_uuid(), batch_id, 'x', 'k-' || gen_random_uuid(),"
            " 'image/png', sha256, uploaded_by, 'pending' FROM images WHERE sha256 = $1",
            sha,
        )


async def test_complete_without_upload_reports_missing_and_can_be_retried(
    client: httpx.AsyncClient, sql
):
    batch = await create_batch(client, LAB)
    payload = png_bytes(20)
    r = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": len(payload)}]},
        headers=LAB,
    )
    ticket = r.json()["tickets"][0]
    body = {"image_ids": [ticket["image_id"]]}
    r = await client.post(f"/batches/{batch}/uploads/complete", json=body, headers=LAB)
    assert r.json()["results"][0]["result"] == "missing"
    async with httpx.AsyncClient() as s3:
        await s3.put(ticket["upload_url"], content=payload, headers=ticket["headers"])
    r = await client.post(f"/batches/{batch}/uploads/complete", json=body, headers=LAB)
    assert r.json()["results"][0]["result"] == "ready"
    # completing again is idempotent
    r = await client.post(f"/batches/{batch}/uploads/complete", json=body, headers=LAB)
    assert r.json()["results"][0]["result"] == "ready"


async def test_non_image_bytes_are_rejected_and_cleaned_up(client: httpx.AsyncClient, sql):
    batch = await create_batch(client, LAB)
    results = await upload_images(client, LAB, batch, [b"this is not an image"])
    assert results[0]["result"] == "invalid"
    assert await sql.val("SELECT count(*) FROM images") == 0


def _png_declaring(width: int, height: int) -> bytes:
    """A few bytes of PNG whose header declares huge dimensions (a decompression bomb)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "payload",
    [
        _png_declaring(60_000, 60_000),  # far beyond the pixel limit: Pillow's own bomb error
        _png_declaring(11_000, 11_000),  # 121M pixels: over our limit, under Pillow's hard stop
        png_bytes(1, (25_000, 1)),  # real image, few pixels, but a side over the limit
    ],
    ids=["bomb", "over_pixel_limit", "over_side_limit"],
)
async def test_oversized_images_are_rejected_and_cleaned_up(
    client: httpx.AsyncClient, sql, payload: bytes
):
    batch = await create_batch(client, LAB)
    results = await upload_images(client, LAB, batch, [payload])
    assert results[0]["result"] == "invalid"
    assert results[0]["code"] == "image_too_large"
    assert await sql.val("SELECT count(*) FROM images") == 0


async def test_images_within_limits_still_pass(client: httpx.AsyncClient):
    batch = await create_batch(client, LAB)
    results = await upload_images(client, LAB, batch, [png_bytes(5, (4_000, 3_000))])
    assert results[0]["result"] == "ready"
    assert results[0]["code"] is None


async def test_declared_type_is_validated(client: httpx.AsyncClient):
    batch = await create_batch(client, LAB)
    r = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.gif", "content_type": "image/gif", "size": 10}]},
        headers=LAB,
    )
    assert r.status_code == 422


async def test_oversized_declaration_is_rejected(client: httpx.AsyncClient):
    batch = await create_batch(client, LAB)
    r = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": 10**9}]},
        headers=LAB,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "file_too_large"


async def test_users_cannot_complete_each_others_uploads(client: httpx.AsyncClient):
    batch = await create_batch(client, LAB)
    payload = png_bytes(30)
    r = await client.post(
        f"/batches/{batch}/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": len(payload)}]},
        headers=LAB,
    )
    ticket = r.json()["tickets"][0]
    r = await client.post(
        f"/batches/{batch}/uploads/complete",
        json={"image_ids": [ticket["image_id"]]},
        headers=auth("other@example.com", "labeler"),
    )
    assert r.json()["results"][0]["result"] == "missing"


async def test_unknown_batch_is_404(client: httpx.AsyncClient):
    r = await client.post(
        "/batches/00000000-0000-0000-0000-000000000000/uploads",
        json={"files": [{"filename": "a.png", "content_type": "image/png", "size": 10}]},
        headers=LAB,
    )
    assert r.status_code == 404


async def test_batch_counts_reflect_ready_images(client: httpx.AsyncClient):
    batch = await create_batch(client, LAB)
    await upload_images(client, LAB, batch, [png_bytes(40), png_bytes(41)])
    r = await client.get(f"/batches/{batch}", headers=LAB)
    assert r.json()["counts"] == {"pending": 2}
