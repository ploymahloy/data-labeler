"""Returns images with expired leases to their queues. Run as a Kubernetes CronJob (every minute).

python -m app.reaper

Besides doing the work, each run is the system's heartbeat: it writes a structured log line and
CloudWatch Embedded Metric Format lines (namespace `DataLabeler`). Alarms fire when `ReaperRuns`
stops arriving, when `ReaperFailures` appears, or when reviews pile up
(`OldestSubmittedAgeSeconds`).
"""

import asyncio
import logging
import sys
import time

from app.config import Settings, get_settings
from app.db import get_engine, get_sessionmaker
from app.observability import (
    configure_logging,
    emit_cloudwatch_metrics,
    publish_cloudwatch_metrics,
    queue_stats,
)
from app.services.workflow import reap_expired

logger = logging.getLogger("reaper")


async def run_once() -> int:
    async with get_sessionmaker()() as session, session.begin():
        return await reap_expired(session)


async def collect_stats() -> dict[str, float]:
    async with get_sessionmaker()() as session, session.begin():
        return await queue_stats(session)


async def report(settings: Settings, metrics: dict[str, tuple[float, str]]) -> None:
    emit_cloudwatch_metrics(settings.environment, metrics)
    if settings.metrics_sink == "cloudwatch":
        await asyncio.to_thread(
            publish_cloudwatch_metrics,
            settings.environment,
            metrics,
            region=settings.aws_region,
            endpoint_url=settings.cloudwatch_endpoint_url,
        )


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    started = time.monotonic()
    try:
        reaped = await run_once()
        stats = await collect_stats()
        duration = time.monotonic() - started
        logger.info(
            "reaper run complete",
            extra={
                "event": "reaper_run",
                "reaped": reaped,
                "duration_s": round(duration, 3),
                **stats,
            },
        )
        await report(
            settings,
            {
                "ReaperRuns": (1, "Count"),
                "ReaperFailures": (0, "Count"),
                "ReapedLeases": (reaped, "Count"),
                "ReaperDurationSeconds": (duration, "Seconds"),
                "SubmittedImages": (stats["submitted"], "Count"),
                "OldestSubmittedAgeSeconds": (stats["oldest_submitted_age_seconds"], "Seconds"),
                "ExpiredLeases": (stats["expired_leases"], "Count"),
            },
        )
        return 0
    except Exception:
        logger.exception("reaper run failed", extra={"event": "reaper_failed"})
        await report(settings, {"ReaperFailures": (1, "Count")})
        return 1
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
