"""Task queue boundary (RQ on Redis).

RQ was chosen for its small surface (plain functions, Redis only, no broker
plugins) which suits a handful of long-running CPU jobs. Persisted job state in
PostgreSQL is the source of truth; the queue only carries "please process job X"
messages, and the reconciler re-enqueues anything the queue lost.

``inline`` mode runs the task synchronously in-process. It exists for unit /
integration tests and single-process demos and is refused in production.
"""

from __future__ import annotations

import importlib
import logging
from datetime import timedelta
from functools import lru_cache

from .config import get_settings

log = logging.getLogger("roomscape.queue")


class QueueUnavailable(Exception):
    pass


@lru_cache
def _queue():
    from redis import Redis
    from rq import Queue

    s = get_settings()
    conn = Redis.from_url(s.redis_url, socket_timeout=5, socket_connect_timeout=2)
    return Queue(s.queue_name, connection=conn, default_timeout=6 * 3600)


def _resolve(path: str):
    mod, fn = path.rsplit(".", 1)
    return getattr(importlib.import_module(mod), fn)


def enqueue(path: str, *args, delay_s: float = 0, job_id: str | None = None) -> None:
    s = get_settings()
    if s.queue_mode == "inline":
        if not s.is_dev:
            raise RuntimeError("inline queue mode is not allowed outside development/test")
        _resolve(path)(*args)
        return
    if s.queue_mode == "none":  # tests that drive the worker explicitly
        return
    try:
        q = _queue()
        kwargs = {"job_id": job_id} if job_id else {}
        if delay_s > 0:
            q.enqueue_in(timedelta(seconds=delay_s), path, *args, **kwargs)
        else:
            q.enqueue(path, *args, **kwargs)
    except Exception as exc:
        log.warning(f"enqueue failed: {type(exc).__name__}")
        raise QueueUnavailable() from exc


def queue_stats() -> dict:
    s = get_settings()
    if s.queue_mode != "rq":
        return {"mode": s.queue_mode}
    try:
        from rq import Worker

        q = _queue()
        workers = Worker.all(connection=q.connection)
        return {"mode": "rq", "queued": q.count, "workers": len(workers),
                "failed_registry": q.failed_job_registry.count, "scheduled": q.scheduled_job_registry.count}
    except Exception as exc:
        return {"mode": "rq", "error": type(exc).__name__}
