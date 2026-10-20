import asyncio

import httpx

from app.reaper import run_once
from tests.helpers import auth, box, seed_batch

LAB = auth("lab@example.com", "labeler")


async def claim(client: httpx.AsyncClient, batch: str, email: str):
    return await client.post(f"/batches/{batch}/images/next", headers=auth(email, "labeler"))


async def test_concurrent_users_never_receive_the_same_image(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 25)
    users = [f"user{i}@example.com" for i in range(25)]
    responses = await asyncio.gather(*(claim(client, batch, u) for u in users))
    assert [r.status_code for r in responses] == [200] * 25
    ids = [r.json()["id"] for r in responses]
    assert len(set(ids)) == 25
    assert await sql.val("SELECT count(*) FROM images WHERE status = 'in_progress'") == 25


async def test_more_claimants_than_images_get_204_and_no_duplicates(client: httpx.AsyncClient):
    batch = await seed_batch(client, 5)
    responses = await asyncio.gather(
        *(claim(client, batch, f"u{i}@example.com") for i in range(20))
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * 5 + [204] * 15
    ids = [r.json()["id"] for r in responses if r.status_code == 200]
    assert len(set(ids)) == 5


async def test_many_pods_worth_of_repeated_claims_hand_out_each_image_once(
    client: httpx.AsyncClient,
):
    """Each user drains work in a loop concurrently; every image is processed exactly once."""
    batch = await seed_batch(client, 30)
    seen: list[str] = []

    async def worker(email: str) -> None:
        headers = auth(email, "labeler")
        while True:
            r = await client.post(f"/batches/{batch}/images/next", headers=headers)
            if r.status_code == 204:
                return
            image_id = r.json()["id"]
            seen.append(image_id)
            done = await client.post(
                f"/images/{image_id}/submit", json={"annotations": []}, headers=headers
            )
            assert done.status_code == 200, done.text

    await asyncio.gather(*(worker(f"w{i}@example.com") for i in range(8)))
    assert len(seen) == 30 and len(set(seen)) == 30


async def test_same_user_claiming_twice_resumes_the_same_image(client: httpx.AsyncClient):
    batch = await seed_batch(client, 3)
    first = await claim(client, batch, "ana@example.com")
    second = await claim(client, batch, "ana@example.com")
    assert first.json()["id"] == second.json()["id"]


async def test_same_user_concurrent_double_click_gets_one_image(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 5)
    rs = await asyncio.gather(*(claim(client, batch, "ana@example.com") for _ in range(6)))
    assert len({r.json()["id"] for r in rs}) == 1
    assert await sql.val("SELECT count(*) FROM images WHERE status = 'in_progress'") == 1


async def test_heartbeat_extends_lease_and_requires_ownership(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = (await claim(client, batch, "ana@example.com")).json()
    await sql.execute("UPDATE images SET lease_expires_at = now() + interval '30 seconds'")
    r = await client.post(
        f"/images/{image['id']}/heartbeat", headers=auth("ana@example.com", "labeler")
    )
    assert r.status_code == 200
    remaining = await sql.val("SELECT extract(epoch FROM lease_expires_at - now()) FROM images")
    assert remaining > 14 * 60
    r = await client.post(
        f"/images/{image['id']}/heartbeat", headers=auth("bob@example.com", "labeler")
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "lease_lost"


async def test_expired_lease_blocks_writes_and_reaper_requeues(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    ana = auth("ana@example.com", "labeler")
    image = (await claim(client, batch, "ana@example.com")).json()
    await sql.execute("UPDATE images SET lease_expires_at = now() - interval '1 minute'")

    for method, path, body in [
        ("POST", f"/images/{image['id']}/heartbeat", None),
        ("PUT", f"/images/{image['id']}/annotations", {"annotations": [box()]}),
        ("POST", f"/images/{image['id']}/submit", {"annotations": [box()]}),
    ]:
        r = await client.request(method, path, json=body, headers=ana)
        assert r.status_code == 409, (path, r.text)

    assert await run_once() == 1
    row = (await sql.fetch("SELECT status, assigned_to, lease_kind, lease_expires_at FROM images"))[
        0
    ]
    assert tuple(row.values()) == ("pending", None, None, None)

    # Someone else can now take it, and the stale holder still cannot write.
    bob = await claim(client, batch, "bob@example.com")
    assert bob.status_code == 200 and bob.json()["id"] == image["id"]
    r = await client.post(f"/images/{image['id']}/submit", json={"annotations": []}, headers=ana)
    assert r.status_code == 409


async def test_reaper_does_not_touch_live_leases(client: httpx.AsyncClient):
    batch = await seed_batch(client, 2)
    await claim(client, batch, "ana@example.com")
    assert await run_once() == 0


async def test_release_returns_image_to_pool(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = (await claim(client, batch, "ana@example.com")).json()
    r = await client.post(
        f"/images/{image['id']}/release", headers=auth("ana@example.com", "labeler")
    )
    assert r.status_code == 204
    assert await sql.val("SELECT status FROM images") == "pending"
    again = await claim(client, batch, "bob@example.com")
    assert again.json()["id"] == image["id"]


async def test_skip_returns_to_pool_but_never_to_the_skipper(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = (await claim(client, batch, "ana@example.com")).json()
    r = await client.post(f"/images/{image['id']}/skip", headers=auth("ana@example.com", "labeler"))
    assert r.status_code == 204
    assert await sql.val("SELECT status FROM images") == "pending"
    assert (await claim(client, batch, "ana@example.com")).status_code == 204
    assert (await claim(client, batch, "bob@example.com")).json()["id"] == image["id"]


async def test_only_the_holder_can_release_skip_save_or_submit(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image = (await claim(client, batch, "ana@example.com")).json()
    bob = auth("bob@example.com", "labeler")
    for method, path, body in [
        ("POST", f"/images/{image['id']}/release", None),
        ("POST", f"/images/{image['id']}/skip", None),
        ("PUT", f"/images/{image['id']}/annotations", {"annotations": [box()]}),
        ("POST", f"/images/{image['id']}/submit", {"annotations": [box()]}),
    ]:
        r = await client.request(method, path, json=body, headers=bob)
        assert r.status_code == 409, (path, r.text)
    assert await sql.val("SELECT assigned_to FROM images") == "sub-ana@example.com"
    assert await sql.val("SELECT count(*) FROM annotations") == 0


async def test_submit_moves_image_to_review_queue_and_cannot_be_repeated(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    ana = auth("ana@example.com", "labeler")
    image = (await claim(client, batch, "ana@example.com")).json()
    r = await client.post(
        f"/images/{image['id']}/submit", json={"annotations": [box()]}, headers=ana
    )
    assert r.status_code == 200
    row = (
        await sql.fetch("SELECT status, submitted_by, assigned_to, last_submission_no FROM images")
    )[0]
    assert tuple(row.values()) == ("submitted", "sub-ana@example.com", None, 1)
    assert await sql.val("SELECT count(*) FROM image_submissions") == 1
    r = await client.post(f"/images/{image['id']}/submit", json={"annotations": []}, headers=ana)
    assert r.status_code == 409
    assert (await claim(client, batch, "ana@example.com")).status_code == 204


async def test_labelers_cannot_see_images_they_have_no_part_in(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image = (await claim(client, batch, "ana@example.com")).json()
    r = await client.get(f"/images/{image['id']}", headers=auth("bob@example.com", "labeler"))
    assert r.status_code == 403
    r = await client.get(f"/images/{image['id']}", headers=auth("ana@example.com", "labeler"))
    assert r.status_code == 200
    assert r.json()["url"].startswith("http")


async def test_lease_state_is_all_or_nothing_in_the_database(client: httpx.AsyncClient, sql):
    import asyncpg
    import pytest

    await seed_batch(client, 1)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql.execute("UPDATE images SET assigned_to = 'sub-uploader@example.com'")
