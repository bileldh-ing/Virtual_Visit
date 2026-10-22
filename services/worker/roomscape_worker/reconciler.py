"""Reconciler: makes the database and the queue converge.

* RUNNING attempts whose lease expired (worker crashed / was killed) are marked
  LOST; their job is re-queued (bounded by max_attempts) or failed WORKER_LOST.
* QUEUED jobs the queue does not know about (enqueue failed, Redis flushed) are
  re-enqueued. ``process_job`` is idempotent, so duplicates are harmless.
* Pending/failed deletion requests and stuck capture analyses are retried.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from roomscape_api import taskqueue
from roomscape_api.db import get_sessionmaker
from roomscape_api.jobs_service import transition
from roomscape_api.models import CaptureReport, DeletionRequest, JobAttempt, ReconstructionJob
from roomscape_api.state_machine import PROCESSING, JobState
from sqlalchemy import or_, select

log = logging.getLogger("roomscape.reconciler")


def now() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_once(requeue_after_s: int = 900) -> dict:
    out = {"lost_attempts": 0, "requeued": 0, "failed": 0, "deletions_retried": 0, "analyses_retried": 0}
    Session = get_sessionmaker()
    to_enqueue: list[str] = []
    with Session() as db:
        stale = db.scalars(select(JobAttempt).where(JobAttempt.state == "RUNNING", JobAttempt.lease_expires_at < now())).all()
        for att in stale:
            att.state, att.finished_at, att.error_category = "LOST", now(), "WORKER_LOST"
            att.error_detail = "lease expired without heartbeat (worker crash, kill or network partition)"
            out["lost_attempts"] += 1
            job = db.get(ReconstructionJob, att.job_id)
            if job is None or JobState(job.state) not in {*PROCESSING, JobState.CANCEL_REQUESTED}:
                continue
            if job.cancel_requested:
                transition(db, job, JobState.CANCELLED, attempt_id=att.id, data={"reason": "worker lost during cancel"})
            elif job.attempt_count < job.max_attempts:
                transition(db, job, JobState.QUEUED, attempt_id=att.id, data={"reason": "WORKER_LOST", "recovered": True})
                job.enqueued_at, job.next_attempt_at = now(), None
                to_enqueue.append(str(job.id))
                out["requeued"] += 1
            else:
                job.error_category, job.error_message = "WORKER_LOST", "The worker stopped responding repeatedly."
                transition(db, job, JobState.FAILED, attempt_id=att.id, data={"reason": "WORKER_LOST"})
                out["failed"] += 1
        cutoff = now() - timedelta(seconds=requeue_after_s)
        queued = db.scalars(select(ReconstructionJob).where(
            ReconstructionJob.state == "QUEUED",
            or_(ReconstructionJob.enqueued_at.is_(None), ReconstructionJob.enqueued_at < cutoff,
                ReconstructionJob.next_attempt_at < now()))).all()
        for job in queued:
            if str(job.id) not in to_enqueue:
                job.enqueued_at = now()
                to_enqueue.append(str(job.id))
                out["requeued"] += 1
        dels = db.scalars(select(DeletionRequest).where(DeletionRequest.status.in_(["pending", "failed"]),
                                                        DeletionRequest.created_at < now() - timedelta(minutes=2),
                                                        DeletionRequest.attempts < 10)).all()
        del_ids = [str(d.id) for d in dels]
        reps = db.scalars(select(CaptureReport).where(CaptureReport.status == "PENDING",
                                                      CaptureReport.created_at < now() - timedelta(minutes=10))).all()
        rep_ids = [str(r.id) for r in reps]
        db.commit()
    for jid in to_enqueue:
        try:
            taskqueue.enqueue("roomscape_worker.tasks.process_job", jid)
        except taskqueue.QueueUnavailable:
            log.warning("queue unavailable during reconcile")
    for did in del_ids:
        try:
            taskqueue.enqueue("roomscape_worker.tasks.cleanup_project", did)
            out["deletions_retried"] += 1
        except taskqueue.QueueUnavailable:
            pass
    for rid in rep_ids:
        try:
            taskqueue.enqueue("roomscape_worker.tasks.analyze_capture", rid)
            out["analyses_retried"] += 1
        except taskqueue.QueueUnavailable:
            pass
    if any(out.values()):
        log.info(f"reconcile: {out}")
    return out
