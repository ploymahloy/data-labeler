"""Concurrency load test: hundreds of labelers and reviewers working one batch against a live API.

It exercises the real flow end to end (dev login, upload, claim, heartbeat, submit, review) and
then checks the invariants that matter under contention:

* no image is ever handed to two people for the same task at once,
* nobody reviews an image they annotated,
* every image reaches a terminal state (accepted or escalated) exactly once,
* no 5xx, 429, transport error or lost lease while heartbeats are being sent,
* the database never holds more connections than the pool budget allows.

It also reports latency percentiles per endpoint and how often the API shed load (503 db_busy).

    python -m loadtest.run --base-url http://localhost:8000 --labelers 300 --reviewers 100

Needs an API running with AUTH_MODE=dev (docker compose does that) and the rate limiter off.
Exit status is non-zero if any invariant fails, so it can gate a nightly workflow.
"""

import argparse
import asyncio
import io
import json
import random
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx
from PIL import Image as PILImage
from prometheus_client.parser import text_string_to_metric_families

TERMINAL = ("accepted", "escalated")
LABELS = ("car", "person", "dog", "tree")


@dataclass
class Config:
    base_url: str
    labelers: int
    reviewers: int
    images: int
    think_seconds: float
    heartbeat_seconds: float
    reject_rate: float
    timeout_seconds: float
    database_url: str | None
    max_db_connections: int | None
    max_p95_claim_seconds: float | None
    report: str | None
    seed: int


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    statuses: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    error_codes: Counter = field(default_factory=Counter)
    violations: list[str] = field(default_factory=list)
    lease_lost: int = 0
    submitted: int = 0
    reviewed: Counter = field(default_factory=Counter)
    peak_db_connections: int = 0


def png(seed: int) -> bytes:
    img = PILImage.new("RGB", (32, 24), (seed % 256, (seed * 7) % 256, (seed * 13) % 256))
    img.putpixel((0, 0), ((seed >> 8) % 256, seed % 256, 1))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


class LoadTest:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.stats = Stats()
        self.rng = random.Random(cfg.seed)
        self.stop = asyncio.Event()
        # (task, image_id) -> worker currently holding it. Entries are dropped *before* the
        # finishing request is sent, so a late response can never look like a double assignment.
        self.holders: dict[tuple[str, str], str] = {}
        # Control-plane calls (seeding, watching, verifying) share this one.
        self.http = self.new_client()
        self.batch_id = ""

    # --- plumbing ----------------------------------------------------------------------
    def new_client(self) -> httpx.AsyncClient:
        """One client (and so one connection) per simulated user, like a browser tab.

        A single shared httpx pool with hundreds of connections scans them all on every request,
        which makes the load generator itself the bottleneck. keepalive_expiry stays below the
        server's keep-alive timeout so a reused connection is never one the server just closed.
        """
        return httpx.AsyncClient(
            base_url=self.cfg.base_url,
            timeout=self.cfg.timeout_seconds,
            limits=httpx.Limits(max_connections=2, keepalive_expiry=3.0),
        )

    async def call(
        self,
        name: str,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        **kw: Any,
    ) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = await client.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            self.stats.statuses[name]["transport_error"] += 1
            self.stats.error_codes[f"{name}:{type(exc).__name__}"] += 1
            return None
        self.stats.latencies[name].append(time.perf_counter() - started)
        self.stats.statuses[name][response.status_code] += 1
        if response.status_code >= 400:
            try:
                code = response.json()["detail"]["code"]
            except Exception:
                code = "unknown"
            self.stats.error_codes[f"{name}:{response.status_code}:{code}"] += 1
        return response

    async def login(
        self, email: str, roles: list[str], client: httpx.AsyncClient | None = None
    ) -> dict[str, str]:
        r = await (client or self.http).post("/dev/login", json={"email": email, "roles": roles})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def take(self, task: str, image_id: str, worker: str) -> None:
        holder = self.holders.get((task, image_id))
        if holder is not None and holder != worker:
            self.stats.violations.append(
                f"{task}: image {image_id} handed to {worker} while {holder} still held it"
            )
        self.holders[(task, image_id)] = worker

    def drop(self, task: str, image_id: str) -> None:
        self.holders.pop((task, image_id), None)

    async def work(self, client: httpx.AsyncClient, image_id: str, headers: dict[str, str]) -> bool:
        """Think for a while, heartbeating like the real client. False if the lease was lost."""
        remaining = self.cfg.think_seconds * self.rng.uniform(0.5, 1.5)
        while remaining > 0:
            step = min(self.cfg.heartbeat_seconds, remaining)
            await asyncio.sleep(step)
            remaining -= step
            r = await self.call(
                "heartbeat", client, "POST", f"/images/{image_id}/heartbeat", headers
            )
            if r is None or r.status_code != 200:
                if r is not None and r.status_code == 409:
                    self.stats.lease_lost += 1
                return False
        return True

    async def idle(self) -> None:
        await asyncio.sleep(self.rng.uniform(0.5, 1.5))

    # --- seeding -----------------------------------------------------------------------
    async def seed(self) -> None:
        admin = await self.login("seeder@load.test", ["labeler", "reviewer", "admin"])
        r = await self.http.post(
            "/batches", json={"name": f"load-{uuid.uuid4().hex[:8]}"}, headers=admin
        )
        r.raise_for_status()
        self.batch_id = r.json()["id"]

        base = self.rng.randrange(10**6)
        sem = asyncio.Semaphore(32)
        async with httpx.AsyncClient(timeout=60) as s3:
            for start in range(0, self.cfg.images, 200):
                payloads = [png(base + i) for i in range(start, min(start + 200, self.cfg.images))]
                files = [
                    {
                        "filename": f"load-{start + i}.png",
                        "content_type": "image/png",
                        "size": len(p),
                    }
                    for i, p in enumerate(payloads)
                ]
                r = await self.http.post(
                    f"/batches/{self.batch_id}/uploads", json={"files": files}, headers=admin
                )
                r.raise_for_status()
                tickets = r.json()["tickets"]

                async def put(ticket: dict[str, Any], payload: bytes) -> None:
                    async with sem:
                        resp = await s3.put(
                            ticket["upload_url"], content=payload, headers=ticket["headers"]
                        )
                        resp.raise_for_status()

                await asyncio.gather(*(put(t, p) for t, p in zip(tickets, payloads, strict=True)))
                r = await self.http.post(
                    f"/batches/{self.batch_id}/uploads/complete",
                    json={"image_ids": [t["image_id"] for t in tickets]},
                    headers=admin,
                )
                r.raise_for_status()
                bad = [x for x in r.json()["results"] if x["result"] != "ready"]
                if bad:
                    raise RuntimeError(f"seeding produced non-ready images: {bad[:3]}")
        print(f"seeded batch {self.batch_id} with {self.cfg.images} images", flush=True)

    # --- workers -----------------------------------------------------------------------
    def boxes(self) -> list[dict[str, Any]]:
        out = []
        for _ in range(self.rng.randint(1, 3)):
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "label": self.rng.choice(LABELS),
                    "x": round(self.rng.uniform(0, 0.5), 3),
                    "y": round(self.rng.uniform(0, 0.5), 3),
                    "w": round(self.rng.uniform(0.05, 0.4), 3),
                    "h": round(self.rng.uniform(0.05, 0.4), 3),
                }
            )
        return out

    async def labeler(self, index: int) -> None:
        worker = f"labeler-{index}"
        async with self.new_client() as client:
            headers = await self.login(f"{worker}@load.test", ["labeler"], client)
            while not self.stop.is_set():
                r = await self.call(
                    "claim_annotate",
                    client,
                    "POST",
                    f"/batches/{self.batch_id}/images/next",
                    headers,
                )
                if r is None or r.status_code != 200:
                    await self.idle()
                    continue
                image_id = r.json()["id"]
                self.take("annotate", image_id, worker)
                kept = await self.work(client, image_id, headers)
                self.drop("annotate", image_id)
                if not kept:
                    continue
                r = await self.call(
                    "submit",
                    client,
                    "POST",
                    f"/images/{image_id}/submit",
                    headers,
                    json={"annotations": self.boxes()},
                )
                if r is not None and r.status_code == 200:
                    self.stats.submitted += 1

    async def reviewer(self, index: int) -> None:
        worker = f"reviewer-{index}"
        async with self.new_client() as client:
            headers = await self.login(f"{worker}@load.test", ["reviewer"], client)
            while not self.stop.is_set():
                r = await self.call(
                    "claim_review",
                    client,
                    "POST",
                    f"/batches/{self.batch_id}/reviews/next",
                    headers,
                )
                if r is None or r.status_code != 200:
                    await self.idle()
                    continue
                image_id = r.json()["id"]
                self.take("review", image_id, worker)
                kept = await self.work(client, image_id, headers)
                self.drop("review", image_id)
                if not kept:
                    continue
                reject = self.rng.random() < self.cfg.reject_rate
                body = (
                    {"decision": "rejected", "reason": "load test"}
                    if reject
                    else {"decision": "accepted"}
                )
                r = await self.call(
                    "review", client, "POST", f"/images/{image_id}/review", headers, json=body
                )
                if r is not None and r.status_code == 200:
                    self.stats.reviewed[body["decision"]] += 1

    # --- supervision -------------------------------------------------------------------
    async def counts(self, headers: dict[str, str]) -> dict[str, int]:
        r = await self.http.get(f"/batches/{self.batch_id}", headers=headers)
        r.raise_for_status()
        return r.json()["counts"]

    async def watch(self) -> dict[str, int]:
        admin = await self.login("watcher@load.test", ["admin", "reviewer", "labeler"])
        deadline = time.monotonic() + self.cfg.timeout_seconds
        last_print = 0.0
        while True:
            counts = await self.counts(admin)
            done = sum(counts.get(s, 0) for s in TERMINAL)
            if time.monotonic() - last_print > 5:
                print(f"  progress {done}/{self.cfg.images}  {dict(counts)}", flush=True)
                last_print = time.monotonic()
            if done >= self.cfg.images:
                return counts
            if time.monotonic() > deadline:
                self.stats.violations.append(
                    f"timed out with {done}/{self.cfg.images} images in a terminal state: {counts}"
                )
                return counts
            await asyncio.sleep(1)

    async def scrape(self) -> dict[str, dict[str, Any]] | None:
        """Per-route request counts and histogram buckets as the server measured them."""
        r = await self.http.get("/metrics")
        if r.status_code != 200:
            return None
        routes: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0.0, "sum": 0.0, "b": {}})
        for family in text_string_to_metric_families(r.text):
            if family.name != "http_request_duration_seconds":
                continue
            for sample in family.samples:
                route = f"{sample.labels['method']} {sample.labels['route']}"
                if sample.name.endswith("_count"):
                    routes[route]["count"] += sample.value
                elif sample.name.endswith("_sum"):
                    routes[route]["sum"] += sample.value
                elif sample.name.endswith("_bucket"):
                    routes[route]["b"][float(sample.labels["le"])] = sample.value
        return dict(routes)

    def server_side(
        self, before: dict[str, dict[str, Any]] | None, after: dict[str, dict[str, Any]] | None
    ) -> dict[str, dict[str, float]]:
        """Handler latency during the run (excludes client and network time)."""
        if before is None or after is None:
            return {}
        out: dict[str, dict[str, float]] = {}
        for route, now in after.items():
            then = before.get(route, {"count": 0.0, "sum": 0.0, "b": {}})
            count = now["count"] - then["count"]
            if count <= 0 or route.endswith("/dev/login"):
                continue
            buckets = sorted(now["b"].items())
            p95 = next(
                (le for le, total in buckets if total - then["b"].get(le, 0.0) >= 0.95 * count),
                float("inf"),
            )
            out[route] = {
                "n": count,
                "mean": (now["sum"] - then["sum"]) / count,
                "p95_upper_bound": p95,
            }
        return out

    async def sample_db(self) -> None:
        if not self.cfg.database_url:
            return
        conn = await asyncpg.connect(self.cfg.database_url)
        try:
            while not self.stop.is_set():
                n = await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid()"
                )
                self.stats.peak_db_connections = max(self.stats.peak_db_connections, n)
                await asyncio.sleep(0.25)
        finally:
            await conn.close()

    async def verify(self) -> None:
        """Cross-check the final state through the export (which also streams under load)."""
        admin = await self.login("verifier@load.test", ["admin", "reviewer", "labeler"])
        r = await self.http.get(
            f"/batches/{self.batch_id}/export?include_unreviewed=true", headers=admin
        )
        r.raise_for_status()
        coco = r.json()
        counts = await self.counts(admin)
        # include_unreviewed covers accepted/submitted/in_review; escalated and rework are not
        # part of an export.
        expected = sum(counts.get(s, 0) for s in ("accepted", "submitted", "in_review"))
        if len(coco["images"]) != expected:
            self.stats.violations.append(
                f"export has {len(coco['images'])} images, expected {expected}"
            )
        for image in coco["images"]:
            meta = image["data_labeler"]
            if meta["status"] == "accepted":
                if meta["reviewer"] is None:
                    self.stats.violations.append(
                        f"accepted image {meta['image_id']} has no reviewer"
                    )
                elif meta["reviewer"] == meta["annotator"]:
                    self.stats.violations.append(
                        f"image {meta['image_id']} reviewed by its own annotator "
                        f"{meta['annotator']}"
                    )
        ids = [a["id"] for a in coco["annotations"]]
        if ids != list(range(1, len(ids) + 1)):
            self.stats.violations.append("export annotation ids are not sequential")

    # --- run ---------------------------------------------------------------------------
    async def run(self) -> int:
        cfg = self.cfg
        await self.seed()
        metrics_before = await self.scrape()
        started = time.monotonic()
        workers = [asyncio.create_task(self.labeler(i)) for i in range(cfg.labelers)]
        # Reviewers arrive a little later, like a real shift change.
        await asyncio.sleep(min(2.0, cfg.think_seconds))
        workers += [asyncio.create_task(self.reviewer(i)) for i in range(cfg.reviewers)]
        sampler = asyncio.create_task(self.sample_db())

        counts = await self.watch()
        elapsed = time.monotonic() - started
        self.stop.set()
        for task in [*workers, sampler]:
            task.cancel()
        results = await asyncio.gather(*workers, sampler, return_exceptions=True)
        crashed = [
            r
            for r in results
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)
        ]
        for exc in crashed:
            self.stats.violations.append(f"worker crashed: {exc!r}")

        server = self.server_side(metrics_before, await self.scrape())
        await self.verify()
        await self.http.aclose()
        return self.finish(counts, elapsed, server)

    def finish(
        self, counts: dict[str, int], elapsed: float, server: dict[str, dict[str, float]]
    ) -> int:
        cfg, stats = self.cfg, self.stats
        print(f"\nfinished in {elapsed:.1f}s: {dict(counts)}")
        print("client-observed latency (includes this process's own scheduling)")
        print(f"{'endpoint':<16}{'n':>8}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}   statuses")
        summary: dict[str, Any] = {}
        for name, values in sorted(stats.latencies.items()):
            row = {
                "n": len(values),
                "p50": statistics.median(values),
                "p95": percentile(values, 95),
                "p99": percentile(values, 99),
                "max": max(values),
            }
            summary[name] = row | {"statuses": dict(stats.statuses[name])}
            print(
                f"{name:<16}{row['n']:>8}{row['p50'] * 1000:>8.0f}m{row['p95'] * 1000:>8.0f}m"
                f"{row['p99'] * 1000:>8.0f}m{row['max'] * 1000:>8.0f}m   "
                f"{dict(stats.statuses[name])}"
            )
        if server:
            print("\nserver-side handler latency (from /metrics, excludes client and network)")
            for route, row in sorted(server.items()):
                print(
                    f"  {route:<52}{row['n']:>8.0f}  mean {row['mean'] * 1000:>6.0f}ms"
                    f"  p95 <= {row['p95_upper_bound'] * 1000:.0f}ms"
                )
        shed = sum(c.get(503, 0) for c in stats.statuses.values())
        total = sum(sum(c.values()) for c in stats.statuses.values())
        print(f"\nsubmitted {stats.submitted}, reviews {dict(stats.reviewed)}")
        print(f"requests {total}, shed with 503: {shed}, lease_lost: {stats.lease_lost}")
        if cfg.database_url:
            print(f"peak database connections observed: {stats.peak_db_connections}")
        if stats.error_codes:
            print(f"error codes: {dict(stats.error_codes)}")

        failures = list(stats.violations)
        for name, c in stats.statuses.items():
            bad = {
                k: v
                for k, v in c.items()
                if k == "transport_error" or (isinstance(k, int) and (k >= 500 or k == 429))
            }
            if bad:
                failures.append(f"{name}: unexpected responses {bad}")
        if stats.lease_lost:
            failures.append(f"{stats.lease_lost} leases were lost despite heartbeats")
        if cfg.max_db_connections and stats.peak_db_connections > cfg.max_db_connections:
            failures.append(
                f"peak DB connections {stats.peak_db_connections} > {cfg.max_db_connections}"
            )
        claims = stats.latencies.get("claim_annotate", [])
        if (
            cfg.max_p95_claim_seconds
            and claims
            and percentile(claims, 95) > cfg.max_p95_claim_seconds
        ):
            failures.append(
                f"claim p95 {percentile(claims, 95):.2f}s > {cfg.max_p95_claim_seconds}s"
            )

        if cfg.report:
            with open(cfg.report, "w") as fh:
                json.dump(
                    {
                        "elapsed_seconds": elapsed,
                        "counts": counts,
                        "endpoints": summary,
                        "server_side": server,
                        "shed_503": shed,
                        "peak_db_connections": stats.peak_db_connections,
                        "failures": failures,
                    },
                    fh,
                    indent=2,
                )
        if failures:
            print("\nFAILED:")
            for failure in failures[:20]:
                print(f"  - {failure}")
            return 1
        print("\nPASSED: no duplicate assignment, no self-review, all images terminal")
        return 0


def parse_args(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--labelers", type=int, default=200)
    p.add_argument("--reviewers", type=int, default=60)
    p.add_argument("--images", type=int, default=1000)
    p.add_argument("--think-seconds", type=float, default=2.0, help="mean time holding an image")
    p.add_argument("--heartbeat-seconds", type=float, default=1.0)
    p.add_argument("--reject-rate", type=float, default=0.1)
    p.add_argument("--timeout-seconds", type=float, default=600)
    p.add_argument("--database-url", help="plain postgresql:// DSN, to watch connection counts")
    p.add_argument("--max-db-connections", type=int)
    p.add_argument("--max-p95-claim-seconds", type=float)
    p.add_argument("--report", help="write a JSON summary here")
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args(argv)
    return Config(
        base_url=a.base_url,
        labelers=a.labelers,
        reviewers=a.reviewers,
        images=a.images,
        think_seconds=a.think_seconds,
        heartbeat_seconds=a.heartbeat_seconds,
        reject_rate=a.reject_rate,
        timeout_seconds=a.timeout_seconds,
        database_url=a.database_url,
        max_db_connections=a.max_db_connections,
        max_p95_claim_seconds=a.max_p95_claim_seconds,
        report=a.report,
        seed=a.seed,
    )


def main() -> None:
    sys.exit(asyncio.run(LoadTest(parse_args()).run()))


if __name__ == "__main__":
    main()
