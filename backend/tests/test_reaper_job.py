"""The reaper process is also the heartbeat: its stdout is what CloudWatch alarms watch."""

import json

import httpx

from app.reaper import main
from tests.helpers import auth, seed_batch


async def test_reaper_run_returns_leases_and_emits_heartbeat_metrics(
    client: httpx.AsyncClient, sql, capsys
):
    batch = await seed_batch(client, 2)
    r = await client.post(f"/batches/{batch}/images/next", headers=auth("a@example.com", "labeler"))
    image_id = r.json()["id"]
    await sql.execute(
        "UPDATE images SET lease_expires_at = now() - interval '1 minute' WHERE id = $1::uuid",
        image_id,
    )

    assert await main() == 0

    assert await sql.val("SELECT status FROM images WHERE id = $1::uuid", image_id) == "pending"
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    summary = next(ln for ln in lines if ln.get("event") == "reaper_run")
    assert summary["reaped"] == 1
    emf = next(ln for ln in lines if "_aws" in ln and "ReaperRuns" in ln)
    assert emf["ReaperRuns"] == 1
    assert emf["ReaperFailures"] == 0
    assert emf["ReapedLeases"] == 1
    assert emf["ExpiredLeases"] == 0  # checked after reaping
    for name in ("SubmittedImages", "OldestSubmittedAgeSeconds", "ReaperDurationSeconds"):
        assert name in emf


async def test_reaper_failure_exits_nonzero_and_emits_failure_metric(
    client: httpx.AsyncClient, monkeypatch, capsys
):
    async def boom() -> int:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr("app.reaper.run_once", boom)

    assert await main() == 1

    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    emf = next(ln for ln in lines if "_aws" in ln)
    assert emf["ReaperFailures"] == 1
    assert "ReaperRuns" not in emf  # a failed run is not a heartbeat
    failed = next(ln for ln in lines if ln.get("event") == "reaper_failed")
    assert "database unreachable" in failed["exc"]


async def test_cloudwatch_sink_publishes_heartbeat_with_put_metric_data(
    client: httpx.AsyncClient, monkeypatch
):
    import boto3

    from app.config import get_settings
    from tests.conftest import S3_ENDPOINT

    monkeypatch.setenv("METRICS_SINK", "cloudwatch")
    monkeypatch.setenv("CLOUDWATCH_ENDPOINT_URL", S3_ENDPOINT)  # moto serves every service
    get_settings.cache_clear()
    try:
        assert await main() == 0
    finally:
        get_settings.cache_clear()

    cw = boto3.client("cloudwatch", region_name="us-east-1", endpoint_url=S3_ENDPOINT)
    listed = cw.list_metrics(Namespace="DataLabeler")["Metrics"]
    names = {m["MetricName"] for m in listed}
    assert {"ReaperRuns", "ReaperFailures", "OldestSubmittedAgeSeconds"} <= names
    assert all(m["Dimensions"][0]["Name"] == "Environment" for m in listed)


def test_publish_failure_is_reported_not_raised():
    from app.observability import publish_cloudwatch_metrics

    ok = publish_cloudwatch_metrics(
        "test",
        {"ReaperRuns": (1, "Count")},
        region="us-east-1",
        endpoint_url="http://127.0.0.1:9",  # nothing listens here
    )
    assert ok is False
