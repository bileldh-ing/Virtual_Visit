"""Fixed-window rate limiting backed by Redis (in-process fallback for dev/test)."""

from __future__ import annotations

import threading
import time
from functools import lru_cache

from .config import get_settings
from .errors import APIError


@lru_cache
def _redis():
    url = get_settings().redis_url
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)


_local: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def hit(bucket: str, key: str, limit: int, window_s: int) -> None:
    k = f"rl:{bucket}:{key}:{int(time.time() // window_s)}"
    count = None
    r = _redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(k)
            pipe.expire(k, window_s + 1)
            count = int(pipe.execute()[0])
        except Exception:
            count = None
    if count is None:
        with _lock:
            n, exp = _local.get(k, (0, time.time() + window_s))
            n += 1
            _local[k] = (n, exp)
            for old in [x for x, (_, e) in _local.items() if e < time.time()]:
                _local.pop(old, None)
            count = n
    if count > limit:
        raise APIError(429, "RATE_LIMITED", "Too many requests. Please wait a moment and try again.",
                       retryable=True, headers={"Retry-After": str(window_s)})


def reset_local() -> None:
    with _lock:
        _local.clear()
