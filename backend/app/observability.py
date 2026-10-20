"""Structured JSON logs, request correlation ids, and Prometheus metrics.

- Every log line is one JSON object (CloudWatch Logs Insights friendly).
- Each request gets an id (client `X-Request-ID` honoured if sane) returned in the response header
  and attached to every log line made while serving it.
- `/metrics` is for in-cluster scrapers only: the ALB ingress answers 404 for that path and the
  endpoint itself refuses requests that carry `X-Forwarded-For` (they came through the ALB).
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings

# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------

# A mutable dict, so values set deep inside the app (e.g. the authenticated user, possibly from a
# threadpool copy of the context) are visible to the middleware that logs the finished request.
request_ctx: ContextVar[dict[str, Any] | None] = ContextVar("request_ctx", default=None)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}


def set_request_user(sub: str) -> None:
    ctx = request_ctx.get()
    if ctx is not None:
        ctx["user"] = sub


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx = request_ctx.get()
        if ctx:
            doc["request_id"] = ctx["request_id"]
            if ctx.get("user"):
                doc["user"] = ctx["user"]
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                doc[key] = value
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if settings.log_json
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    # Our middleware writes the access log (with user, route template and latency).
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").handlers[:] = []
    logging.getLogger("uvicorn.error").propagate = True


access_log = logging.getLogger("app.access")

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

registry = CollectorRegistry(auto_describe=True)

http_requests = Counter(
    "http_requests_total", "HTTP requests", ["method", "route", "status"], registry=registry
)
http_duration = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=registry,
)
claims = Counter(
    "labeler_claims_total",
    "Image claims by queue and outcome",
    ["kind", "result"],
    registry=registry,
)
lease_lost_total = Counter(
    "labeler_lease_lost_total",
    "Writes rejected because the caller lost the lease",
    registry=registry,
)
queue_submitted = Gauge("labeler_submitted_images", "Images waiting for review", registry=registry)
queue_oldest_submitted_age = Gauge(
    "labeler_oldest_submitted_age_seconds",
    "Age of the longest-waiting submission that nobody has reviewed",
    registry=registry,
)
queue_expired_leases = Gauge(
    "labeler_expired_leases",
    "Leases past their expiry that the reaper has not returned to a queue yet",
    registry=registry,
)


class DbPoolCollector:
    """Reads the live SQLAlchemy pool at scrape time."""

    def collect(self) -> Any:
        from app.db import get_engine  # local: avoids an import cycle at module load

        pool = get_engine().pool
        for name, doc, value in (
            ("labeler_db_pool_size", "Configured pool size", pool.size()),
            ("labeler_db_pool_checked_out", "Connections in use", pool.checkedout()),
            ("labeler_db_pool_overflow", "Overflow connections open", max(pool.overflow(), 0)),
        ):
            family = GaugeMetricFamily(name, doc)
            family.add_metric([], value)
            yield family


registry.register(DbPoolCollector())  # type: ignore[arg-type]


async def queue_stats(session: AsyncSession) -> dict[str, float]:
    """Review-queue health, shared by /metrics and the reaper's CloudWatch metrics."""
    submitted = (
        await session.execute(
            text(
                """
                SELECT count(*) AS n,
                       COALESCE(EXTRACT(EPOCH FROM now() - min(s.submitted_at)), 0) AS oldest
                FROM images i
                JOIN image_submissions s
                  ON s.image_id = i.id AND s.submission_no = i.last_submission_no
                WHERE i.status = 'submitted'
                """
            )
        )
    ).one()
    expired = await session.scalar(
        text(
            "SELECT count(*) FROM images WHERE assigned_to IS NOT NULL AND lease_expires_at < now()"
        )
    )
    return {
        "submitted": float(submitted.n),
        "oldest_submitted_age_seconds": float(submitted.oldest),
        "expired_leases": float(expired or 0),
    }


async def render_metrics(session: AsyncSession) -> tuple[bytes, str]:
    async with session.begin():
        stats = await queue_stats(session)
    queue_submitted.set(stats["submitted"])
    queue_oldest_submitted_age.set(stats["oldest_submitted_age_seconds"])
    queue_expired_leases.set(stats["expired_leases"])
    return generate_latest(registry), CONTENT_TYPE_LATEST


def emit_cloudwatch_metrics(
    environment: str, metrics: dict[str, tuple[float, str]], namespace: str = "DataLabeler"
) -> None:
    """Write one CloudWatch Embedded Metric Format line to stdout.

    If a log pipeline ships container logs to CloudWatch Logs, CloudWatch turns the line into
    metrics. `publish_cloudwatch_metrics` is the pipeline-independent path.
    """
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [["Environment"]],
                    "Metrics": [{"Name": n, "Unit": unit} for n, (_, unit) in metrics.items()],
                }
            ],
        },
        "Environment": environment,
        **{name: value for name, (value, _) in metrics.items()},
    }
    sys.stdout.write(json.dumps(doc) + "\n")
    sys.stdout.flush()


def publish_cloudwatch_metrics(
    environment: str,
    metrics: dict[str, tuple[float, str]],
    *,
    region: str,
    endpoint_url: str = "",
    namespace: str = "DataLabeler",
) -> bool:
    """Send the same metrics with PutMetricData. Returns False (and logs) instead of raising:
    a missed publish shows up as missing data on the heartbeat alarm, which is the right signal."""
    import boto3  # imported lazily: only the reaper publishes

    try:
        client = boto3.client("cloudwatch", region_name=region, endpoint_url=endpoint_url or None)
        client.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": name,
                    "Dimensions": [{"Name": "Environment", "Value": environment}],
                    "Value": float(value),
                    "Unit": unit,
                }
                for name, (value, unit) in metrics.items()
            ],
        )
        return True
    except Exception:
        logging.getLogger("observability").exception(
            "cloudwatch publish failed", extra={"event": "metrics_publish_failed"}
        )
        return False


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware:
    """Pure ASGI (no task hop) so context set by dependencies is visible when we log."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        ctx: dict[str, Any] = {"request_id": request_id, "user": None}
        token = request_ctx.set(ctx)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            access_log.exception("unhandled exception", extra={"path": scope["path"]})
            raise
        finally:
            elapsed = time.perf_counter() - started
            path = scope["path"]
            if path not in _QUIET_PATHS:
                route = getattr(scope.get("route"), "path", None) or "unmatched"
                method = scope["method"]
                http_requests.labels(method, route, str(status)).inc()
                http_duration.labels(method, route).observe(elapsed)
                access_log.info(
                    "request",
                    extra={
                        "method": method,
                        "route": route,
                        "path": path,
                        "status": status,
                        "latency_ms": round(elapsed * 1000, 1),
                    },
                )
            request_ctx.reset(token)
