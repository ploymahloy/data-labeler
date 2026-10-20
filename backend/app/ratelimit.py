"""Coarse per-user token-bucket rate limiting.

State is per pod (in memory), so the effective limit scales with the number of replicas. That is
deliberate: this only stops a single misbehaving client or script from hammering expensive
endpoints. IP-level and volumetric protection is the WAF's job (infra/terraform/waf.tf).
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends

from app.auth import CurrentUser, current_user
from app.config import Settings, get_settings
from app.errors import ApiError

_MAX_TRACKED = 50_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def reset(self) -> None:
        self._buckets.clear()

    def check(self, scope: str, user: str, per_minute: int) -> float:
        """Take one token. Returns 0 when allowed, otherwise the seconds until one is free."""
        now = self.clock()
        rate = per_minute / 60.0
        bucket = self._buckets.get((scope, user))
        if bucket is None:
            if len(self._buckets) >= _MAX_TRACKED:
                self._prune(now)
            bucket = self._buckets[(scope, user)] = _Bucket(float(per_minute), now)
        else:
            bucket.tokens = min(float(per_minute), bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / rate

    def _prune(self, now: float) -> None:
        # Drop buckets idle for over a minute (they would have refilled completely anyway).
        stale = [k for k, b in self._buckets.items() if now - b.updated > 60]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= _MAX_TRACKED:
            self._buckets.clear()


limiter = RateLimiter()


def _limit_for(settings: Settings, scope: str) -> int:
    return {
        "claim": settings.rate_limit_claim_per_minute,
        "presign": settings.rate_limit_presign_per_minute,
        "export": settings.rate_limit_export_per_minute,
    }[scope]


def rate_limit(scope: str) -> Callable[..., None]:
    """Route dependency: `dependencies=[Depends(rate_limit("claim"))]`."""

    def dependency(user: CurrentUser = Depends(current_user)) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        wait = limiter.check(scope, user.sub, _limit_for(settings, scope))
        if wait > 0:
            retry_after = max(1, math.ceil(wait))
            raise ApiError(
                429,
                "rate_limited",
                f"Too many requests, retry in {retry_after}s",
                retry_after=retry_after,
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
