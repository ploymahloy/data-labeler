import uuid

import httpx

from tests.helpers import auth, box, seed_batch

ANA = auth("ana@example.com", "labeler")
REV = auth("rev@example.com", "reviewer")


async def claim(client: httpx.AsyncClient, batch: str) -> dict:
    r = await client.post(f"/batches/{batch}/images/next", headers=ANA)
    assert r.status_code == 200, r.text
    return r.json()


async def save(client: httpx.AsyncClient, image_id: str, boxes: list[dict]):
    return await client.put(
        f"/images/{image_id}/annotations", json={"annotations": boxes}, headers=ANA
    )


async def test_save_is_idempotent_so_retries_do_not_duplicate(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    boxes = [box(0.1, 0.1, 0.2, 0.2, "car"), box(0.5, 0.5, 0.3, 0.3, "dog")]
    for _ in range(3):
        r = await save(client, image["id"], boxes)
        assert r.status_code == 200, r.text
    assert await sql.val("SELECT count(*) FROM annotations") == 2
    assert len(r.json()["annotations"]) == 2


async def test_save_replaces_the_set_edit_move_and_delete(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    a, b = box(0.1, 0.1, 0.2, 0.2, "car"), box(0.6, 0.6, 0.2, 0.2, "dog")
    await save(client, image["id"], [a, b])
    moved = {**a, "x": 0.15, "label": "truck"}
    r = await save(client, image["id"], [moved])  # b deleted, a moved & relabeled
    got = r.json()["annotations"]
    assert [(x["id"], x["label"], x["x"]) for x in got] == [(a["id"], "truck", 0.15)]
    assert await sql.val("SELECT count(*) FROM annotations") == 1


async def test_boxes_can_swap_positions_in_one_save(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    a, b = box(0.1, 0.1, 0.2, 0.2, "car"), box(0.6, 0.6, 0.2, 0.2, "car")
    assert (await save(client, image["id"], [a, b])).status_code == 200
    swapped = [{**a, "x": 0.6, "y": 0.6}, {**b, "x": 0.1, "y": 0.1}]
    r = await save(client, image["id"], swapped)
    assert r.status_code == 200, r.text


async def test_label_names_are_normalized_to_one_entity(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    boxes = [
        box(0.0, 0.0, 0.1, 0.1, "Car"),
        box(0.2, 0.2, 0.1, 0.1, " car "),
        box(0.4, 0.4, 0.1, 0.1, "CAR"),
        box(0.6, 0.6, 0.1, 0.1, "Traffic   Light"),
    ]
    assert (await save(client, image["id"], boxes)).status_code == 200
    names = [r["name"] for r in await sql.fetch("SELECT name FROM labels ORDER BY normalized")]
    assert names == ["Car", "Traffic Light"]
    r = await client.get(f"/batches/{batch}/labels", headers=ANA)
    assert [x["name"] for x in r.json()] == ["Car", "Traffic Light"]


async def test_overlapping_same_label_boxes_are_rejected_as_duplicates(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    a = box(0.1, 0.1, 0.4, 0.4, "car")
    near = {**box(label="Car"), "x": 0.11, "y": 0.1, "w": 0.4, "h": 0.4}  # IoU ~0.95
    r = await save(client, image["id"], [a, near])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "duplicate_annotations"
    assert r.json()["detail"]["pairs"] == [[a["id"], near["id"]]]
    assert await sql.val("SELECT count(*) FROM annotations") == 0
    r = await client.post(
        f"/images/{image['id']}/submit", json={"annotations": [a, near]}, headers=ANA
    )
    assert r.status_code == 422


async def test_overlap_below_threshold_or_different_label_is_allowed(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    base = box(0.1, 0.1, 0.4, 0.4, "car")
    partial = {**box(label="car"), "x": 0.3, "y": 0.1, "w": 0.4, "h": 0.4}  # IoU ~0.33
    other_label = {
        **box(label="person"),
        "x": 0.1,
        "y": 0.1,
        "w": 0.4,
        "h": 0.4,
    }  # IoU 1, new label
    r = await save(client, image["id"], [base, partial, other_label])
    assert r.status_code == 200, r.text


async def test_database_blocks_exact_duplicates_regardless_of_application_checks(
    client: httpx.AsyncClient, sql
):
    import asyncpg
    import pytest

    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    await save(client, image["id"], [box(0.1, 0.1, 0.2, 0.2, "car")])
    with pytest.raises(asyncpg.UniqueViolationError):
        await sql.execute(
            "INSERT INTO annotations (id, image_id, label_id, x, y, w, h, fingerprint, created_by)"
            " SELECT gen_random_uuid(), image_id, label_id, x, y, w, h, fingerprint, created_by"
            " FROM annotations"
        )


async def test_invalid_boxes_are_rejected(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    bad = [
        {**box(), "x": -0.1},
        {**box(), "w": 0},
        {**box(), "x": 0.8, "w": 0.4},
        {**box(), "h": 1.2},
        {**box(), "label": "   "},
    ]
    for b in bad:
        r = await save(client, image["id"], [b])
        assert r.status_code == 422, b


async def test_repeated_ids_in_one_payload_are_rejected(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    a = box(0.1, 0.1, 0.1, 0.1)
    r = await save(client, image["id"], [a, {**a, "x": 0.5}])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "duplicate_annotation_ids"


async def test_client_ids_cannot_overwrite_annotations_on_other_images(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 2)
    first = await claim(client, batch)
    mine = box(0.1, 0.1, 0.2, 0.2, "car")
    await save(client, first["id"], [mine])
    other = auth("bob@example.com", "labeler")
    second = (await client.post(f"/batches/{batch}/images/next", headers=other)).json()
    r = await client.put(
        f"/images/{second['id']}/annotations",
        json={"annotations": [{**mine, "x": 0.7, "label": "hijack"}]},
        headers=other,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "annotation_id_conflict"
    row = (await sql.fetch("SELECT image_id::text, x FROM annotations"))[0]
    assert row["image_id"] == first["id"] and row["x"] == 0.1


async def test_get_image_returns_boxes_with_label_names(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = await claim(client, batch)
    await save(client, image["id"], [box(0.1, 0.2, 0.3, 0.4, "Fire Hydrant")])
    r = await client.get(f"/images/{image['id']}", headers=ANA)
    ann = r.json()["annotations"][0]
    assert (ann["label"], ann["x"], ann["y"], ann["w"], ann["h"]) == (
        "Fire Hydrant",
        0.1,
        0.2,
        0.3,
        0.4,
    )


async def _label_and_accept(client: httpx.AsyncClient, batch: str, boxes: list[dict]) -> str:
    image = await claim(client, batch)
    r = await client.post(f"/images/{image['id']}/submit", json={"annotations": boxes}, headers=ANA)
    assert r.status_code == 200, r.text
    got = await client.post(f"/batches/{batch}/reviews/next", headers=REV)
    assert got.json()["id"] == image["id"]
    r = await client.post(
        f"/images/{image['id']}/review", json={"decision": "accepted"}, headers=REV
    )
    assert r.status_code == 200, r.text
    return image["id"]


async def test_coco_export_contains_accepted_images_only_by_default(client: httpx.AsyncClient):
    batch = await seed_batch(client, 2)
    accepted_id = await _label_and_accept(client, batch, [box(0.25, 0.5, 0.5, 0.25, "car")])
    pending_image = await claim(client, batch)  # not submitted
    assert pending_image["id"] != accepted_id

    r = await client.get(f"/batches/{batch}/export", headers=REV)
    assert r.status_code == 200
    coco = r.json()
    assert [c["name"] for c in coco["categories"]] == ["car"]
    assert len(coco["images"]) == 1
    image = coco["images"][0]
    assert (image["width"], image["height"]) == (64, 48)
    assert image["data_labeler"]["image_id"] == accepted_id
    assert image["data_labeler"]["annotator"] == "sub-ana@example.com"
    assert image["data_labeler"]["reviewer"] == "sub-rev@example.com"
    (ann,) = coco["annotations"]
    assert ann["bbox"] == [16.0, 24.0, 32.0, 12.0] and ann["area"] == 384.0
    assert ann["category_id"] == coco["categories"][0]["id"]


async def test_coco_export_can_include_unreviewed(client: httpx.AsyncClient):
    batch = await seed_batch(client, 2)
    await _label_and_accept(client, batch, [box(label="car")])
    second = await claim(client, batch)
    await client.post(
        f"/images/{second['id']}/submit", json={"annotations": [box(label="dog")]}, headers=ANA
    )
    plain = (await client.get(f"/batches/{batch}/export", headers=REV)).json()
    assert len(plain["images"]) == 1
    full = (
        await client.get(f"/batches/{batch}/export?include_unreviewed=true", headers=REV)
    ).json()
    assert len(full["images"]) == 2
    assert {c["name"] for c in full["categories"]} == {"car", "dog"}


async def test_export_requires_reviewer_or_admin(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    r = await client.get(f"/batches/{batch}/export", headers=ANA)
    assert r.status_code == 403
    r = await client.get(f"/batches/{batch}/export", headers=auth("adm@example.com", "admin"))
    assert r.status_code == 200
    r = await client.get(f"/batches/{uuid.uuid4()}/export", headers=REV)
    assert r.status_code == 404
