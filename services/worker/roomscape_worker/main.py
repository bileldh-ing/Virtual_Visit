"""Worker entrypoint: an RQ worker (with scheduler for delayed retries) plus a
reconciler thread. Run one process per CPU-heavy slot:

    python -m roomscape_worker.main
"""

from __future__ import annotations

import logging
import threading
import time

from redis import Redis
from roomscape_api.config import get_settings
from roomscape_api.observability import setup_logging
from roomscape_recon.colmap_engine import detect
from rq import Queue, Worker

from .reconciler import reconcile_once

log = logging.getLogger("roomscape.worker")


def _reconcile_loop(stop: threading.Event, interval: int = 30) -> None:
    while not stop.wait(interval):
        try:
            reconcile_once()
        except Exception:
            log.exception("reconcile failed")


def main() -> None:
    setup_logging()
    s = get_settings()
    info = detect()
    log.info(f"worker starting; colmap={info.to_dict()}")
    if not info.available:
        log.error("COLMAP not found: photogrammetry jobs will fail with ENGINE_UNAVAILABLE")
    stop = threading.Event()
    threading.Thread(target=_reconcile_loop, args=(stop,), daemon=True).start()
    conn = Redis.from_url(s.redis_url)
    for _ in range(30):
        try:
            conn.ping()
            break
        except Exception:
            time.sleep(2)
    worker = Worker([Queue(s.queue_name, connection=conn)], connection=conn)
    try:
        worker.work(with_scheduler=True)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
