"""Platform admin (is_platform_admin only) + health, readiness, metrics, dev outbox."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import audit, taskqueue
from ..config import get_settings
from ..db import get_db
from ..deps import platform_admin
from ..errors import APIError, not_found
from ..jobs_service import transition
from ..models import (
    AbuseReport,
    AuditEvent,
    DeletionRequest,
    JobAttempt,
    Notification,
    Organization,
    Project,
    ReconstructionJob,
    Upload,
    User,
)
from ..observability import METRICS
from ..state_machine import ACTIVE, PROCESSING, TERMINAL, JobState
from ..storage import get_storage

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
ops = APIRouter(tags=["ops"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d):
    return d.isoformat() if d else None


@router.get("/overview")
def overview(admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    by_state = dict(db.execute(select(ReconstructionJob.state, func.count()).group_by(ReconstructionJob.state)).all())
    oldest = db.scalar(select(func.min(ReconstructionJob.enqueued_at)).where(ReconstructionJob.state == "QUEUED"))
    storage_bytes = db.scalar(select(func.coalesce(func.sum(Upload.bytes), 0)).where(Upload.status == "stored")) or 0
    stale = db.scalar(select(func.count()).select_from(JobAttempt).where(JobAttempt.state == "RUNNING",
                                                                         JobAttempt.lease_expires_at < now()))
    return {"jobs_by_state": by_state, "oldest_queued_at": _iso(oldest),
            "oldest_queued_age_s": (now() - oldest.replace(tzinfo=oldest.tzinfo or timezone.utc)).total_seconds() if oldest else None,
            "stale_leases": stale, "queue": taskqueue.queue_stats(), "photo_storage_bytes": int(storage_bytes),
            "users": db.scalar(select(func.count()).select_from(User).where(User.status == "active")),
            "projects": db.scalar(select(func.count()).select_from(Project).where(Project.status == "active")),
            "open_privacy_reports": db.scalar(select(func.count()).select_from(AbuseReport).where(AbuseReport.status == "open")),
            "pending_deletions": db.scalar(select(func.count()).select_from(DeletionRequest).where(DeletionRequest.status.in_(["pending", "in_progress", "failed"])))}


@router.get("/jobs")
def jobs(state: str = "", limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
         admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    stmt = select(ReconstructionJob, Project).join(Project, Project.id == ReconstructionJob.project_id)
    if state:
        stmt = stmt.where(ReconstructionJob.state == state)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(ReconstructionJob.created_at.desc()).limit(limit).offset(offset)).all()
    # Redacted: no photo URLs, no addresses, no artifact links.
    return {"total": total, "items": [{
        "id": str(j.id), "project_id": str(j.project_id), "org_id": str(p.org_id), "mode": j.mode, "state": j.state,
        "attempts": j.attempt_count, "error_category": j.error_category, "error_message": j.error_message,
        "inputs": len(j.input_upload_ids or []), "created_at": _iso(j.created_at), "finished_at": _iso(j.finished_at),
        "duration_s": (j.result or {}).get("metrics", {}).get("processing_seconds")} for j, p in rows]}


@router.get("/jobs/{job_id}")
def job_detail(job_id: uuid.UUID, admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    j = db.get(ReconstructionJob, job_id)
    if not j:
        raise not_found("Job")
    atts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == j.id).order_by(JobAttempt.number)).all()
    audit.record(db, "admin.job_inspected", actor=admin.id, target_type="job", target_id=j.id)
    db.commit()
    return {"id": str(j.id), "state": j.state, "mode": j.mode, "config": j.config, "error_category": j.error_category,
            "error_message": j.error_message,
            "attempts": [{"number": a.number, "state": a.state, "worker_id": a.worker_id, "engine": a.engine,
                          "error_category": a.error_category, "error_detail_redacted": (a.error_detail or "")[-1500:],
                          "started_at": _iso(a.started_at), "finished_at": _iso(a.finished_at),
                          "heartbeat_at": _iso(a.heartbeat_at), "lease_expires_at": _iso(a.lease_expires_at)} for a in atts],
            "metrics": (j.result or {}).get("metrics")}


@router.post("/jobs/{job_id}/retry")
def admin_retry(job_id: uuid.UUID, admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    j = db.get(ReconstructionJob, job_id)
    if not j:
        raise not_found("Job")
    if JobState(j.state) not in {JobState.FAILED, JobState.CANCELLED, JobState.PARTIAL_SUCCESS}:
        raise APIError(409, "NOT_RETRYABLE", "Only failed, cancelled or partial jobs can be retried.")
    j.cancel_requested = False
    j.max_attempts = j.attempt_count + get_settings().job_max_attempts
    j.error_category = j.error_message = None
    j.finished_at = None
    transition(db, j, JobState.QUEUED, data={"by": "admin_retry"})
    j.enqueued_at = now()
    audit.record(db, "admin.job_retried", actor=admin.id, target_type="job", target_id=j.id)
    db.commit()
    try:
        taskqueue.enqueue("roomscape_worker.tasks.process_job", str(j.id))
    except taskqueue.QueueUnavailable:
        j.enqueued_at = None
        db.commit()
    return {"ok": True, "state": j.state}


@router.post("/jobs/{job_id}/cancel")
def admin_cancel(job_id: uuid.UUID, admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    j = db.get(ReconstructionJob, job_id)
    if not j:
        raise not_found("Job")
    if JobState(j.state) in TERMINAL:
        return {"ok": True, "state": j.state}
    j.cancel_requested = True
    if JobState(j.state) in PROCESSING:
        transition(db, j, JobState.CANCEL_REQUESTED, data={"by": "admin"})
    else:
        transition(db, j, JobState.CANCELLED, data={"by": "admin"})
    audit.record(db, "admin.job_cancelled", actor=admin.id, target_type="job", target_id=j.id)
    db.commit()
    return {"ok": True, "state": j.state}


@router.post("/reconcile")
def reconcile(admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    from roomscape_worker.reconciler import reconcile_once

    result = reconcile_once()
    audit.record(db, "admin.reconcile_run", actor=admin.id, data=result)
    db.commit()
    return result


@router.get("/users")
def users(q: str = "", limit: int = Query(25, ge=1, le=100), offset: int = 0, admin: User = Depends(platform_admin),
          db: Session = Depends(get_db)):
    stmt = select(User).where(User.status != "deleted")
    if q:
        stmt = stmt.where(func.lower(User.email).like(f"%{q.lower()}%"))
    rows = db.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [{"id": str(u.id), "email": u.email, "status": u.status, "verified": u.email_verified_at is not None,
                       "is_platform_admin": u.is_platform_admin, "created_at": _iso(u.created_at)} for u in rows]}


class UserStatus(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


@router.patch("/users/{user_id}")
def set_user_status(user_id: uuid.UUID, body: UserStatus, admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or u.status == "deleted":
        raise not_found("User")
    if u.id == admin.id:
        raise APIError(409, "SELF_CHANGE", "You cannot change your own status.")
    u.status = body.status
    audit.record(db, "admin.user_status_changed", actor=admin.id, target_type="user", target_id=u.id, data={"status": body.status})
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=200), offset: int = 0, admin: User = Depends(platform_admin),
              db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [{"id": str(e.id), "action": e.action, "org_id": str(e.org_id) if e.org_id else None,
                       "actor": str(e.actor_user_id) if e.actor_user_id else None, "target_type": e.target_type,
                       "target_id": e.target_id, "data": e.data, "request_id": e.request_id,
                       "created_at": _iso(e.created_at)} for e in rows]}


@router.get("/privacy-reports")
def privacy_reports(admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AbuseReport).order_by(AbuseReport.created_at.desc()).limit(100)).all()
    return {"items": [{"id": str(r.id), "project_id": str(r.project_id) if r.project_id else None, "reason": r.reason,
                       "details": r.details, "status": r.status, "created_at": _iso(r.created_at)} for r in rows]}


class SampleFlag(BaseModel):
    is_public_sample: bool


@router.patch("/projects/{project_id}/sample")
def set_sample(project_id: uuid.UUID, body: SampleFlag, admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p or p.status != "active":
        raise not_found("Project")
    if body.is_public_sample:
        for other in db.scalars(select(Project).where(Project.is_public_sample.is_(True))):
            other.is_public_sample = False
    p.is_public_sample = body.is_public_sample
    audit.record(db, "admin.sample_flag", actor=admin.id, target_type="project", target_id=p.id, data=body.model_dump())
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ ops

@ops.get("/healthz")
def healthz():
    return {"status": "ok"}


@ops.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        checks["storage"] = "ok" if get_storage().health() else "error"
    except Exception:
        checks["storage"] = "error"
    qs = taskqueue.queue_stats()
    checks["queue"] = "error" if "error" in qs else "ok"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise APIError(503, "NOT_READY", "Dependencies unavailable: " + ", ".join(k for k, v in checks.items() if v != "ok"), retryable=True)
    return {"status": "ready", "checks": checks}


@ops.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)):
    lines = [METRICS.render().rstrip("\n"), "# TYPE roomscape_jobs gauge"]
    for state, n in db.execute(select(ReconstructionJob.state, func.count()).group_by(ReconstructionJob.state)).all():
        lines.append(f'roomscape_jobs{{state="{state}"}} {n}')
    oldest = db.scalar(select(func.min(ReconstructionJob.enqueued_at)).where(ReconstructionJob.state == "QUEUED"))
    age = (now() - oldest.replace(tzinfo=oldest.tzinfo or timezone.utc)).total_seconds() if oldest else 0
    lines.append(f"roomscape_oldest_queued_job_age_seconds {age:.0f}")
    active = [s.value for s in ACTIVE]
    lines.append(f"roomscape_active_jobs {db.scalar(select(func.count()).select_from(ReconstructionJob).where(ReconstructionJob.state.in_(active)))}")
    return "\n".join(lines) + "\n"


dev = APIRouter(prefix="/api/v1/dev", tags=["dev"])


@dev.get("/outbox")
def outbox(to: str, db: Session = Depends(get_db)):
    """Development/test only: read emails that were not sent over SMTP (used by e2e tests)."""
    if not get_settings().is_dev:
        raise not_found()
    rows = db.scalars(select(Notification).where(Notification.to_email == to.lower()).order_by(Notification.created_at.desc()).limit(20)).all()
    return {"items": [{"kind": n.kind, "subject": n.subject, "body": n.body, "created_at": _iso(n.created_at)} for n in rows]}


_ = Organization
