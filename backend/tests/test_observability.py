import io
import json
import logging

import httpx
import pytest

from app.observability import JsonFormatter, emit_cloudwatch_metrics
from tests.helpers import auth, seed_batch
from tests.test_review import annotate_and_submit

LAB = auth("lab@example.com", "labeler")


@pytest.fixture
def json_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def lines() -> list[dict]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line]

    yield lines
    logger.removeHandler(handler)


async def test_request_id_is_generated_and_echoed(client: httpx.AsyncClient):
    r = await client.get("/batches", headers=LAB)
    assert len(r.headers["x-request-id"]) == 32

    r = await client.get("/batches", headers={**LAB, "X-Request-ID": "trace-abc-12345"})
    assert r.headers["x-request-id"] == "trace-abc-12345"

    # Unsafe values (log injection, huge strings) are replaced, not echoed.
    r = await client.get("/batches", headers={**LAB, "X-Request-ID": 'x"\n{"evil":1}'})
    assert r.headers["x-request-id"] != 'x"\n{"evil":1}'
    r = await client.get("/batches", headers={**LAB, "X-Request-ID": "a" * 200})
    assert len(r.headers["x-request-id"]) == 32


async def test_access_log_is_structured_with_route_user_and_latency(
    client: httpx.AsyncClient, json_logs
):
    r = await client.get("/batches", headers={**LAB, "X-Request-ID": "req-log-0001"})
    assert r.status_code == 200
    entry = next(e for e in json_logs() if e.get("request_id") == "req-log-0001")
    assert entry["message"] == "request"
    assert entry["method"] == "GET"
    assert entry["route"] == "/batches"
    assert entry["status"] == 200
    assert entry["user"] == "sub-lab@example.com"
    assert entry["latency_ms"] >= 0
    assert entry["level"] == "INFO" and entry["ts"].endswith("+00:00")


async def test_route_label_is_the_template_not_the_concrete_path(
    client: httpx.AsyncClient, json_logs
):
    await client.get("/batches/00000000-0000-0000-0000-000000000000", headers=LAB)
    await client.get("/definitely/not/a/route", headers=LAB)
    routes = [e["route"] for e in json_logs()]
    assert "/batches/{batch_id}" in routes
    assert "unmatched" in routes  # bounded metric cardinality


async def test_health_probes_are_not_logged(client: httpx.AsyncClient, json_logs):
    await client.get("/healthz")
    await client.get("/readyz")
    assert json_logs() == []


async def test_metrics_expose_http_domain_and_pool_series(client: httpx.AsyncClient):
    batch = await seed_batch(client, 3)
    assert (await client.post(f"/batches/{batch}/images/next", headers=LAB)).status_code == 200
    other = auth("other@example.com", "labeler")
    image = (await client.post(f"/batches/{batch}/images/next", headers=other)).json()
    # Heartbeat on someone else's lease is rejected and counted.
    r = await client.post(f"/images/{image['id']}/heartbeat", headers=LAB)
    assert r.status_code == 409

    body = (await client.get("/metrics")).text
    assert (
        'http_requests_total{method="POST",route="/batches/{batch_id}/images/next",status="200"}'
        in body
    )
    assert 'http_request_duration_seconds_count{method="POST",route="/batches"}' in body
    assert 'labeler_claims_total{kind="annotate",result="claimed"}' in body
    lost = [ln for ln in body.splitlines() if ln.startswith("labeler_lease_lost_total ")]
    assert float(lost[0].split()[1]) >= 1
    for series in (
        "labeler_db_pool_size",
        "labeler_db_pool_checked_out",
        "labeler_db_pool_overflow",
    ):
        assert f"\n{series} " in body


async def test_queue_gauges_track_submitted_images_and_expired_leases(
    client: httpx.AsyncClient, sql
):
    batch = await seed_batch(client, 3)
    await annotate_and_submit(client, batch, auth("a1@example.com", "labeler"))
    await annotate_and_submit(client, batch, auth("a2@example.com", "labeler"))
    # A claimed-but-abandoned image whose lease ran out.
    r = await client.post(
        f"/batches/{batch}/images/next", headers=auth("a3@example.com", "labeler")
    )
    await sql.execute(
        "UPDATE images SET lease_expires_at = now() - interval '1 minute' WHERE id = $1::uuid",
        r.json()["id"],
    )
    await sql.execute("UPDATE image_submissions SET submitted_at = now() - interval '2 hours'")

    body = (await client.get("/metrics")).text
    values = {
        ln.split()[0]: float(ln.split()[1])
        for ln in body.splitlines()
        if ln.startswith("labeler_") and "{" not in ln
    }
    assert values["labeler_submitted_images"] == 2
    assert values["labeler_oldest_submitted_age_seconds"] >= 2 * 3600 - 5
    assert values["labeler_expired_leases"] == 1


async def test_metrics_are_not_served_through_the_load_balancer(client: httpx.AsyncClient):
    # The ALB adds X-Forwarded-For; in-cluster scrapers hit the pod directly without it.
    r = await client.get("/metrics", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 404
    assert (await client.get("/metrics")).status_code == 200


def test_cloudwatch_metric_lines_follow_the_embedded_metric_format(capsys):
    emit_cloudwatch_metrics(
        "production", {"ReaperRuns": (1, "Count"), "OldestSubmittedAgeSeconds": (42.5, "Seconds")}
    )
    doc = json.loads(capsys.readouterr().out)
    spec = doc["_aws"]["CloudWatchMetrics"][0]
    assert spec["Namespace"] == "DataLabeler"
    assert spec["Dimensions"] == [["Environment"]]
    assert {m["Name"]: m["Unit"] for m in spec["Metrics"]} == {
        "ReaperRuns": "Count",
        "OldestSubmittedAgeSeconds": "Seconds",
    }
    assert doc["Environment"] == "production"
    assert doc["ReaperRuns"] == 1 and doc["OldestSubmittedAgeSeconds"] == 42.5
    assert isinstance(doc["_aws"]["Timestamp"], int)
