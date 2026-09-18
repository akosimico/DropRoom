import time
from threading import Lock


class InMemoryRateLimiter:
    """Fixed-window rate limiter keyed on a string (usually client IP).

    In-memory per-process implementation for the MVP. The seam for a distributed
    Redis-backed sliding-window limiter (Phase 5) is this class's public API:
    `check(scope, key, limit, window_seconds) -> bool`.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = Lock()

    def check(self, scope: str, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        bucket_key = (scope, key)
        with self._lock:
            start, count = self._buckets.get(bucket_key, (0.0, 0))
            if now - start >= window_seconds:
                self._buckets[bucket_key] = (now, 1)
                return True
            if count >= limit:
                return False
            self._buckets[bucket_key] = (start, count + 1)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_rate_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    return _rate_limiter


class RateLimiterDeps:
    def __init__(self, limiter: InMemoryRateLimiter | None = None) -> None:
        self.limiter = limiter or _rate_limiter

    def check(
        self, scope: str, key: str, limit: int | None = None, window_seconds: int | None = None
    ) -> None:
        from app.core import errors
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit = limit if limit is not None else settings.generic_limit
        window = window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
        if not self.limiter.check(scope, key, limit, window):
            raise errors.RateLimited("Too many requests; try again shortly")