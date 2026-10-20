import asyncio

import asyncpg
import httpx
import pytest

from app.reaper import run_once
from tests.helpers import auth, box, seed_batch

ANA = auth("ana@example.com", "labeler")
BOB = auth("bob@example.com", "labeler")
REV1 = auth("rev1@example.com", "reviewer")
REV2 = auth("rev2@example.com", "reviewer")
ADMIN = auth("admin@example.com", "admin")


async def annotate_and_submit(
    client: httpx.AsyncClient, batch: str, headers: dict, boxes: list[dict] | None = None
) -> str:
    r = await client.post(f"/batches/{batch}/images/next", headers=headers)
    assert r.status_code == 200, r.text
    image_id = r.json()["id"]
    r = await client.post(
        f"/images/{image_id}/submit",
        json={"annotations": boxes if boxes is not None else [box()]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return image_id


async def claim_review(client: httpx.AsyncClient, batch: str, headers: dict) -> httpx.Response:
    return await client.post(f"/batches/{batch}/reviews/next", headers=headers)


async def decide(client, image_id, headers, decision, reason=None):
    body = {"decision": decision, **({"reason": reason} if reason else {})}
    return await client.post(f"/images/{image_id}/review", json=body, headers=headers)


async def test_accept_flow(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    claimed = await claim_review(client, batch, REV1)
    assert claimed.status_code == 200 and claimed.json()["id"] == image_id
    assert claimed.json()["status"] == "in_review"
    assert len(claimed.json()["annotations"]) == 1  # reviewer sees the annotator's boxes

    r = await decide(client, image_id, REV1, "accepted")
    assert r.status_code == 200 and r.json()["resulting_status"] == "accepted"
    row = (await sql.fetch("SELECT status, assigned_to FROM images"))[0]
    assert (row["status"], row["assigned_to"]) == ("accepted", None)
    review = (await sql.fetch("SELECT reviewer_id, decision, submission_no FROM reviews"))[0]
    assert tuple(review.values()) == ("sub-rev1@example.com", "accepted", 1)
    # accepted is final: nothing left to claim for anyone
    assert (await claim_review(client, batch, REV2)).status_code == 204
    assert (await client.post(f"/batches/{batch}/images/next", headers=BOB)).status_code == 204


async def test_nothing_to_review_until_something_is_submitted(client: httpx.AsyncClient):
    batch = await seed_batch(client, 2)
    assert (await claim_review(client, batch, REV1)).status_code == 204
    claimed = await client.post(f"/batches/{batch}/images/next", headers=ANA)  # in_progress only
    assert claimed.status_code == 200
    assert (await claim_review(client, batch, REV1)).status_code == 204


async def test_annotator_with_reviewer_role_never_gets_their_own_work(client: httpx.AsyncClient):
    both = auth("dual@example.com", "labeler", "reviewer")
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, both)
    assert (await claim_review(client, batch, both)).status_code == 204
    # a different reviewer can, though
    assert (await claim_review(client, batch, REV1)).json()["id"] == image_id


async def test_cannot_review_without_holding_the_review_lease(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    r = await decide(client, image_id, REV1, "accepted")  # never claimed
    assert r.status_code == 409
    await claim_review(client, batch, REV1)
    r = await decide(client, image_id, REV2, "accepted")  # someone else's lease
    assert r.status_code == 409
    r = await decide(client, image_id, ANA, "accepted")  # labelers are not reviewers
    assert r.status_code == 403


async def test_concurrent_reviewers_never_receive_the_same_image(client: httpx.AsyncClient):
    batch = await seed_batch(client, 12)
    for i in range(12):
        await annotate_and_submit(client, batch, auth(f"annotator{i}@example.com", "labeler"))
    reviewers = [auth(f"reviewer{i}@example.com", "reviewer") for i in range(12)]
    responses = await asyncio.gather(*(claim_review(client, batch, h) for h in reviewers))
    assert [r.status_code for r in responses] == [200] * 12
    assert len({r.json()["id"] for r in responses}) == 12


async def test_concurrent_reviewers_with_fewer_images_than_reviewers(client: httpx.AsyncClient):
    batch = await seed_batch(client, 3)
    for i in range(3):
        await annotate_and_submit(client, batch, auth(f"a{i}@example.com", "labeler"))
    responses = await asyncio.gather(
        *(claim_review(client, batch, auth(f"r{i}@example.com", "reviewer")) for i in range(10))
    )
    assert sorted(r.status_code for r in responses) == [200] * 3 + [204] * 7
    assert len({r.json()["id"] for r in responses if r.status_code == 200}) == 3


async def test_a_pool_of_dual_role_users_never_review_their_own_images(
    client: httpx.AsyncClient, sql
):
    """Everyone annotates and reviews; nobody may end up reviewing an image they annotated."""
    batch = await seed_batch(client, 20)
    users = [auth(f"dual{i}@example.com", "labeler", "reviewer") for i in range(5)]
    reviewed: list[tuple[str, str]] = []

    async def annotate(h):
        for _ in range(4):
            r = await client.post(f"/batches/{batch}/images/next", headers=h)
            if r.status_code != 200:
                return
            await client.post(
                f"/images/{r.json()['id']}/submit", json={"annotations": [box()]}, headers=h
            )

    await asyncio.gather(*(annotate(h) for h in users))

    async def review(h, email):
        while True:
            r = await claim_review(client, batch, h)
            if r.status_code == 204:
                return
            image_id = r.json()["id"]
            reviewed.append((image_id, email))
            d = await decide(client, image_id, h, "accepted")
            assert d.status_code == 200, d.text

    await asyncio.gather(*(review(h, f"sub-dual{i}@example.com") for i, h in enumerate(users)))
    authors = {
        str(r["image_id"]): r["submitted_by"]
        for r in await sql.fetch("SELECT image_id, submitted_by FROM image_submissions")
    }
    assert reviewed, "expected some reviews to happen"
    assert all(authors[image_id] != reviewer for image_id, reviewer in reviewed)


async def test_review_lease_resumes_heartbeats_releases_and_expires(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    first = await claim_review(client, batch, REV1)
    again = await claim_review(client, batch, REV1)
    assert first.json()["id"] == again.json()["id"] == image_id
    assert (await client.post(f"/images/{image_id}/heartbeat", headers=REV1)).status_code == 200
    assert (await claim_review(client, batch, REV2)).status_code == 204  # locked by REV1

    r = await client.post(f"/images/{image_id}/release", headers=REV1)
    assert r.status_code == 204
    assert await sql.val("SELECT status FROM images") == "submitted"

    await claim_review(client, batch, REV1)
    await sql.execute("UPDATE images SET lease_expires_at = now() - interval '1 second'")
    assert (await decide(client, image_id, REV1, "accepted")).status_code == 409
    assert await run_once() == 1
    assert await sql.val("SELECT status FROM images") == "submitted"
    assert (await claim_review(client, batch, REV2)).json()["id"] == image_id


async def test_reject_requires_a_reason(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, REV1)
    for reason in (None, "", "   "):
        r = await client.post(
            f"/images/{image_id}/review",
            json={"decision": "rejected", **({"reason": reason} if reason is not None else {})},
            headers=REV1,
        )
        assert r.status_code == 422
    assert (await decide(client, image_id, REV1, "rejected", "Missed the truck")).status_code == 200


async def test_rejected_image_is_reserved_for_the_original_annotator_then_opens_up(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, REV1)
    r = await decide(client, image_id, REV1, "rejected", "Box too loose")
    assert r.json()["resulting_status"] == "rework"
    row = (await sql.fetch("SELECT status, rejection_count, rework_reserved_for FROM images"))[0]
    assert tuple(row.values()) == ("rework", 1, "sub-ana@example.com")

    # Bob cannot take it during the reservation; Ana can, and sees the feedback.
    assert (await client.post(f"/batches/{batch}/images/next", headers=BOB)).status_code == 204
    mine = await client.post(f"/batches/{batch}/images/next", headers=ANA)
    assert mine.status_code == 200
    fb = mine.json()["review_feedback"]
    assert fb["reason"] == "Box too loose" and fb["reviewer_id"] == "sub-rev1@example.com"
    assert len(mine.json()["annotations"]) == 1  # prior work is preserved for editing

    # Ana lets it go; after the reservation lapses Bob may pick it up.
    await client.post(f"/images/{image_id}/release", headers=ANA)
    assert await sql.val("SELECT status FROM images") == "rework"
    assert (await client.post(f"/batches/{batch}/images/next", headers=BOB)).status_code == 204
    await sql.execute("UPDATE images SET rework_reserved_until = now() - interval '1 minute'")
    taken = await client.post(f"/batches/{batch}/images/next", headers=BOB)
    assert taken.status_code == 200 and taken.json()["id"] == image_id


async def test_rework_resubmission_needs_a_fresh_independent_review(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, REV1)
    await decide(client, image_id, REV1, "rejected", "Wrong label")

    r = await client.post(f"/batches/{batch}/images/next", headers=ANA)
    assert r.json()["id"] == image_id
    r = await client.post(
        f"/images/{image_id}/submit", json={"annotations": [box(label="bus")]}, headers=ANA
    )
    assert r.status_code == 200
    assert await sql.val("SELECT last_submission_no FROM images") == 2

    # The same reviewer may look again (they did not author it) and accept.
    claimed = await claim_review(client, batch, REV2)
    assert claimed.json()["id"] == image_id
    assert (await decide(client, image_id, REV2, "accepted")).json()[
        "resulting_status"
    ] == "accepted"
    decisions = await sql.fetch(
        "SELECT submission_no, decision FROM reviews ORDER BY submission_no"
    )
    assert [(d["submission_no"], d["decision"]) for d in decisions] == [
        (1, "rejected"),
        (2, "accepted"),
    ]


async def test_reworking_by_a_different_user_still_excludes_every_author_from_review(
    client: httpx.AsyncClient, sql
):
    both = auth("both@example.com", "labeler", "reviewer")
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, both)  # author of attempt 1
    await claim_review(client, batch, REV1)
    await decide(client, image_id, REV1, "rejected", "Redo")
    await sql.execute("UPDATE images SET rework_reserved_until = now() - interval '1 minute'")
    # Bob reworks attempt 2
    r = await client.post(f"/batches/{batch}/images/next", headers=BOB)
    assert r.json()["id"] == image_id
    await client.post(f"/images/{image_id}/submit", json={"annotations": [box()]}, headers=BOB)
    # Attempt-1 author must not review attempt 2, nor can Bob
    assert (await claim_review(client, batch, both)).status_code == 204
    assert (
        await claim_review(client, batch, auth("bob@example.com", "reviewer", "labeler"))
    ).status_code == 204
    assert (await claim_review(client, batch, REV2)).status_code == 200


async def test_a_reviewer_cannot_later_annotate_an_image_they_reviewed(client: httpx.AsyncClient):
    dual = auth("dual@example.com", "labeler", "reviewer")
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, dual)
    await decide(client, image_id, dual, "rejected", "Redo please")
    # reservation is for Ana; even once it lapses the reviewer is excluded from the rework
    r = await client.post(f"/batches/{batch}/images/next", headers=dual)
    assert r.status_code == 204


async def test_repeated_rejections_escalate_to_admin_who_can_requeue(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    statuses = []
    for n in range(3):
        reviewer = auth(f"reviewer{n}@example.com", "reviewer")
        claimed = await claim_review(client, batch, reviewer)
        assert claimed.json()["id"] == image_id
        statuses.append(
            (await decide(client, image_id, reviewer, "rejected", f"bad {n}")).json()[
                "resulting_status"
            ]
        )
        if n < 2:
            await client.post(f"/batches/{batch}/images/next", headers=ANA)
            await client.post(
                f"/images/{image_id}/submit", json={"annotations": [box()]}, headers=ANA
            )
    assert statuses == ["rework", "rework", "escalated"]
    assert await sql.val("SELECT rejection_count FROM images") == 3

    # Nobody can pick it up; admin sees it in the escalation queue.
    assert (await client.post(f"/batches/{batch}/images/next", headers=ANA)).status_code == 204
    assert (await claim_review(client, batch, REV2)).status_code == 204
    listing = await client.get(f"/batches/{batch}/escalated", headers=ADMIN)
    assert [i["id"] for i in listing.json()] == [image_id]
    assert (await client.get(f"/batches/{batch}/escalated", headers=REV1)).status_code == 403

    assert (await client.post(f"/images/{image_id}/requeue", headers=REV1)).status_code == 403
    assert (await client.post(f"/images/{image_id}/requeue", headers=ADMIN)).status_code == 204
    row = (await sql.fetch("SELECT status, rejection_count FROM images"))[0]
    assert (row["status"], row["rejection_count"]) == ("pending", 0)
    assert (await client.post(f"/images/{image_id}/requeue", headers=ADMIN)).status_code == 409
    assert (await client.post(f"/batches/{batch}/images/next", headers=BOB)).json()[
        "id"
    ] == image_id


async def test_a_submission_can_only_be_decided_once(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, REV1)
    assert (await decide(client, image_id, REV1, "accepted")).status_code == 200
    assert (await decide(client, image_id, REV1, "rejected", "changed my mind")).status_code == 409


async def test_database_trigger_blocks_self_review_even_if_api_is_bypassed(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql.execute(
            "INSERT INTO reviews (id, image_id, submission_no, reviewer_id, decision)"
            " VALUES (gen_random_uuid(), $1::uuid, 1, 'sub-ana@example.com', 'accepted')",
            image_id,
        )
    assert await sql.val("SELECT count(*) FROM reviews") == 0


async def test_database_trigger_blocks_reviewer_from_becoming_author(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await claim_review(client, batch, REV1)
    await decide(client, image_id, REV1, "accepted")
    with pytest.raises(asyncpg.CheckViolationError):
        await sql.execute(
            "INSERT INTO image_submissions (id, image_id, submission_no, submitted_by)"
            " VALUES (gen_random_uuid(), $1::uuid, 2, 'sub-rev1@example.com')",
            image_id,
        )


async def test_database_requires_a_reason_for_rejections(client: httpx.AsyncClient, sql):
    batch = await seed_batch(client, 1)
    image_id = await annotate_and_submit(client, batch, ANA)
    await client.get("/batches", headers=REV1)  # ensure reviewer user row exists
    with pytest.raises(asyncpg.CheckViolationError):
        await sql.execute(
            "INSERT INTO reviews (id, image_id, submission_no, reviewer_id, decision)"
            " VALUES (gen_random_uuid(), $1::uuid, 1, 'sub-rev1@example.com', 'rejected')",
            image_id,
        )


async def test_review_stats(client: httpx.AsyncClient):
    batch = await seed_batch(client, 3)
    ids = [
        await annotate_and_submit(client, batch, auth(f"a{i}@example.com", "labeler"))
        for i in range(3)
    ]
    for image_id, decision in zip(ids, ["accepted", "accepted", "rejected"], strict=True):
        claimed = await claim_review(client, batch, REV1)
        assert claimed.status_code == 200
        await decide(
            client, claimed.json()["id"], REV1, decision, "nope" if decision == "rejected" else None
        )
        assert image_id  # keep zip meaningful
    r = await client.get(f"/batches/{batch}/review-stats", headers=REV1)
    assert r.status_code == 200
    stats = r.json()
    assert stats["counts"] == {"accepted": 2, "rework": 1}
    assert stats["reviewers"] == [
        {
            "reviewer_id": "sub-rev1@example.com",
            "email": "rev1@example.com",
            "accepted": 2,
            "rejected": 1,
        }
    ]
    assert stats["rejection_rate"] == pytest.approx(1 / 3)
    assert (await client.get(f"/batches/{batch}/review-stats", headers=ANA)).status_code == 403


async def test_labeler_cannot_use_review_endpoints(client: httpx.AsyncClient):
    batch = await seed_batch(client, 1)
    assert (await claim_review(client, batch, ANA)).status_code == 403
