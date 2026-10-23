"""Reconstruction jobs: create (idempotent), status, SSE events, cancel, retry, artifacts."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, taskqueue
from ..config import get_settings
from ..db import get_db, get_sessionmaker
from ..deps import current_user, load_project, membership
from ..engines import (
    ENGINE_NAMES,
    ENGINE_TO_MODE,
    LEGACY_MODE_TO_ENGINE,
    allowed_models,
    build_registry,
    inputs_from_uploads,
    marble_status,
)
from ..errors import APIError, not_found
from ..jobs_service import transition
from ..models import (
    Artifact,
    ArtifactValidationResult,
    JobAttempt,
    JobStageEvent,
    Organization,
    Project,
    ReconstructionJob,
    Subscription,
    Upload,
    UsageLedger,
    User,
)
from ..plans import plan
from ..ratelimit import hit
from ..signing import file_url
from ..state_machine import ACTIVE, PROCESSING, TERMINAL, JobState

router = APIRouter(prefix="/api/v1", tags=["reconstruction"])

STAGE_TEXT = {
    "CREATED": "Job created", "QUEUED": "Waiting for a worker", "INPUT_VALIDATION": "Checking and preparing your photos",
    "CAMERA_POSE_ESTIMATION": "Detecting features and matching photos (COLMAP)",
    "SPARSE_RECONSTRUCTION": "Estimating camera positions and sparse 3D points (COLMAP mapper)",
    "DENSE_RECONSTRUCTION": "Estimating dense geometry", "MESH_OR_SPLAT_GENERATION": "Building the 3D world (splats / mesh)",
    "TEXTURE_OR_APPEARANCE_PROCESSING": "Preparing panorama imagery", "ARTIFACT_OPTIMIZATION": "Optimising files for the web viewer",
    "PREVIEW_GENERATION": "Rendering a preview", "ARTIFACT_VALIDATION": "Validating every output file",
    "SUCCEEDED": "Finished", "PARTIAL_SUCCESS": "Finished with partial results", "FAILED": "Failed",
    "CANCEL_REQUESTED": "Cancelling", "CANCELLED": "Cancelled",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if dt else None


def job_out(db: Session, job: ReconstructionJob, include_events: bool = True) -> dict:
    out = {
        "id": str(job.id), "project_id": str(job.project_id), "mode": job.mode, "state": job.state,
        "state_text": STAGE_TEXT.get(job.state, job.state), "is_terminal": JobState(job.state) in TERMINAL,
        "cancel_requested": job.cancel_requested, "attempt_count": job.attempt_count, "max_attempts": job.max_attempts,
        "error": {"category": job.error_category, "message": job.error_message} if job.error_category else None,
        "result": job.result, "input_count": len(job.input_upload_ids or []), "config": job.config,
        "engine": job.engine, "engine_used": job.engine_used, "fallback": job.fallback, "cost_estimate": job.cost_estimate,
        "provider": _provider_public(job.provider_state),
        "created_at": _iso(job.created_at), "started_at": _iso(job.started_at), "finished_at": _iso(job.finished_at),
        "stage_started_at": _iso(job.stage_started_at), "next_attempt_at": _iso(job.next_attempt_at),
        "server_time": now().isoformat(),
    }
    if include_events:
        evs = db.scalars(select(JobStageEvent).where(JobStageEvent.job_id == job.id).order_by(JobStageEvent.id)).all()
        out["events"] = [event_out(e) for e in evs]
        atts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id).order_by(JobAttempt.number)).all()
        out["attempts"] = [{"number": a.number, "state": a.state, "worker_id": a.worker_id, "engine": a.engine,
                            "started_at": _iso(a.started_at), "finished_at": _iso(a.finished_at),
                            "heartbeat_at": _iso(a.heartbeat_at), "error_category": a.error_category} for a in atts]
    return out


def _provider_public(state: dict | None) -> dict | None:
    """Only non-sensitive provider identifiers are exposed (never request bodies or media ids)."""
    if not state:
        return None
    m = state.get("marble_world") or {}
    if not m:
        return None
    return {"provider": "worldlabs", "operation_id": m.get("operation_id"), "world_id": m.get("world_id"),
            "model": m.get("model"), "estimate_credits": m.get("estimate_credits")}


def event_out(e: JobStageEvent) -> dict:
    return {"id": e.id, "stage": e.stage, "event": e.event, "data": e.data, "created_at": e.created_at.isoformat()}


class JobCreate(BaseModel):
    mode: str = Field(default="AUTO", pattern="^(AUTO|PHOTOGRAMMETRY|PANORAMA_TOUR|GAUSSIAN_SPLAT)$")
    engine: str | None = Field(default=None, pattern="^(" + "|".join(ENGINE_NAMES) + ")$")
    allow_fallback: bool = True
    marble_model: str | None = Field(default=None, max_length=40)
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirm_warnings: bool = False


def _month_start():
    return now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _model(requested: str | None) -> str | None:
    if requested is None:
        return None
    if requested not in allowed_models():
        raise APIError(422, "UNKNOWN_MODEL", "That Marble model is not available on this deployment.")
    return requested


def engine_options(db: Session, p: Project, ups: list, requested: str = "auto", allow_fallback: bool = True,
                   marble_model: str | None = None) -> dict:
    """Describe every engine for these uploads, the live Marble status and the chain that would run."""
    from roomscape_recon.engines import plan_chain

    reg = build_registry(probe_local=False, marble_model=marble_model)
    inputs = inputs_from_uploads(ups)
    described = reg.describe(inputs)
    status = marble_status()
    sub = db.scalar(select(Subscription).where(Subscription.org_id == p.org_id))
    pl = plan(sub.plan_code if sub else "FREE")
    used = db.scalar(select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.org_id == p.org_id, UsageLedger.kind == "marble_credits", UsageLedger.created_at >= _month_start())) or 0
    allowance = {"used": float(used), "limit": float(pl.get("marble_credits_month", 0))}
    for d in described:
        if d["name"] != "marble_world" or not d.get("runnable"):
            continue
        est = (d.get("estimate") or {}).get("credits") or 0
        why = None
        if not status["ok"]:
            why = {"PROVIDER_AUTH": "Marble unavailable: API key invalid, expired or revoked",
                   "PROVIDER_CREDITS": "Marble unavailable: out of credits",
                   "NOT_CONFIGURED": "Marble unavailable: not configured"}.get(status["error_category"] or "",
                                                                              "Marble unavailable right now")
        elif status["balance"] is not None and status["balance"] < est:
            why = "Marble unavailable: out of credits"
        elif allowance["used"] + est > allowance["limit"]:
            why = "Marble unavailable: this month's Marble allowance on your plan is used"
        if why:
            d["runnable"], d["not_runnable_reason"] = False, why
    runnable = {d["name"] for d in described if d.get("runnable")}
    plan_ = plan_chain(reg, inputs, requested, allow_fallback)
    skipped = list(plan_.skipped)
    engines = []
    for n in plan_.engines:
        if n in runnable:
            engines.append(n)
        else:
            reason = next(d["not_runnable_reason"] for d in described if d["name"] == n)
            skipped.append({"engine": n, "reason_code": status.get("error_category") or "UNAVAILABLE", "reason": reason})
    chain = {"requested": requested, "intended": plan_.intended, "engines": engines, "skipped": skipped,
             "primary": engines[0] if engines else None}
    if chain["primary"] and plan_.intended and chain["primary"] != plan_.intended:
        first = next((sk for sk in skipped if sk["engine"] == plan_.intended), None)
        chain["notice"] = (first or {}).get("reason")
    return {"engines": described, "marble": {k: v for k, v in status.items() if k != "checked_at"},
            "marble_allowance": allowance, "chain": chain,
            "marble_models": allowed_models(), "marble_model": marble_model or get_settings().marble_model}


@router.get("/projects/{project_id}/engines")
def list_engines(project_id: uuid.UUID, engine: str = "auto", marble_model: str | None = None,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    if engine not in ENGINE_NAMES:
        raise APIError(422, "UNKNOWN_ENGINE", "Unknown engine.")
    ups = db.scalars(select(Upload).where(Upload.project_id == p.id, Upload.status == "stored", Upload.kind == "PHOTO")).all()
    return engine_options(db, p, list(ups), engine, marble_model=_model(marble_model))


@router.post("/projects/{project_id}/reconstruction-jobs", status_code=202)
def create_job(project_id: uuid.UUID, body: JobCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    p, _ = load_project(db, project_id, user, "EDITOR")
    hit("job-create", str(user.id), 20, 3600)
    existing = db.scalar(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id,
                                                          ReconstructionJob.idempotency_key == body.idempotency_key))
    if existing:
        return job_out(db, existing)  # client retry: same job, never a duplicate
    ups = db.scalars(select(Upload).where(Upload.project_id == p.id, Upload.status == "stored", Upload.kind == "PHOTO")).all()
    if not ups:
        raise APIError(422, "NO_PHOTOS", "Upload photos first.")
    panos = [u for u in ups if u.is_panorama]
    mode = body.mode
    if mode == "GAUSSIAN_SPLAT" or body.engine == "gaussian_splat_local":
        raise APIError(409, "MODE_UNAVAILABLE", "Gaussian splatting needs a GPU worker, which this deployment does not have.")
    if body.engine:
        requested, allow_fallback = body.engine, body.allow_fallback
    elif mode != "AUTO":
        requested, allow_fallback = LEGACY_MODE_TO_ENGINE[mode], False  # v1 clients: exact legacy behaviour
    else:
        requested, allow_fallback = s.default_engine, body.allow_fallback
    if requested == "colmap_sparse":
        mode = "PHOTOGRAMMETRY"
    elif requested == "panorama_tour":
        mode = "PANORAMA_TOUR"
    marble_model = _model(body.marble_model)
    opts = engine_options(db, p, list(ups), requested, allow_fallback, marble_model)
    chain = opts["chain"]
    if not chain["engines"]:
        reason = "; ".join(sk["reason"] or sk["engine"] for sk in chain["skipped"]) or "no engine"
        if requested == "colmap_sparse":
            raise APIError(422, "NEEDS_MORE_IMAGES", "Photogrammetry needs at least 3 overlapping photos (many more are recommended).")
        if requested == "panorama_tour":
            raise APIError(422, "NO_PANORAMAS", "No 360° equirectangular panoramas were uploaded.")
        raise APIError(422, "NO_ENGINE", f"No engine can process these photos: {reason}")
    primary = chain["primary"]
    if mode == "AUTO":
        mode = ENGINE_TO_MODE.get(primary, "WORLD")
    family_panos = primary in ("panorama_tour",) or (primary == "marble_world" and len(panos) >= len(ups) - len(panos) and panos)
    inputs = [u for u in ups if (u.is_panorama if family_panos else not u.is_panorama)]
    if mode == "PHOTOGRAMMETRY" and len(inputs) < 3:
        raise APIError(422, "NEEDS_MORE_IMAGES", "Photogrammetry needs at least 3 overlapping photos (many more are recommended).")
    if mode == "PANORAMA_TOUR" and not inputs:
        raise APIError(422, "NO_PANORAMAS", "No 360° equirectangular panoramas were uploaded.")
    estimate = {"chain": chain, "engines": {d["name"]: d.get("estimate") for d in opts["engines"] if d["name"] in chain["engines"]},
                "marble_balance": opts["marble"].get("balance")}
    from ..models import CaptureReport
    from .projects import input_fingerprint

    fp, _ = input_fingerprint(db, p.id)
    rep = db.scalar(select(CaptureReport).where(CaptureReport.project_id == p.id, CaptureReport.input_fingerprint == fp)
                    .order_by(CaptureReport.created_at.desc()).limit(1))
    if rep and rep.status == "UNSUPPORTED_INPUT":
        raise APIError(422, "UNSUPPORTED_INPUT", "The capture check found no usable images.")
    if rep and rep.status in {"READY_WITH_WARNINGS", "NEEDS_MORE_IMAGES"} and not body.confirm_warnings:
        raise APIError(409, "CONFIRMATION_REQUIRED", "The capture check reported warnings. Review them and confirm to continue.")
    active = db.scalar(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id,
                                                        ReconstructionJob.state.in_([st.value for st in ACTIVE])))
    if active:
        raise APIError(409, "JOB_ALREADY_RUNNING", "A reconstruction is already in progress for this project.")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == p.org_id))
    pl = plan(sub.plan_code if sub else "FREE")
    if sub and sub.status in {"expired", "canceled"} and sub.plan_code != "FREE":
        raise APIError(402, "SUBSCRIPTION_INACTIVE", "Your subscription is inactive. Update billing to continue.")
    running = db.scalar(select(func.count()).select_from(ReconstructionJob).join(Project, Project.id == ReconstructionJob.project_id)
                        .where(Project.org_id == p.org_id, ReconstructionJob.state.in_([st.value for st in ACTIVE])))
    if running >= pl["concurrent_jobs"]:
        raise APIError(429, "CONCURRENCY_LIMIT", "Your plan's concurrent job limit is reached. Wait for a job to finish.", retryable=True)
    used = db.scalar(select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.org_id == p.org_id, UsageLedger.kind == "reconstruction_minutes",
        UsageLedger.created_at >= now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))) or 0
    if used >= pl["reconstruction_minutes"]:
        raise APIError(402, "USAGE_LIMIT", "You have used this month's reconstruction minutes.")
    job = ReconstructionJob(project_id=p.id, created_by=user.id, mode=mode, idempotency_key=body.idempotency_key,
                            input_upload_ids=[str(u.id) for u in inputs], max_attempts=s.job_max_attempts,
                            engine=requested, cost_estimate=estimate,
                            config={"max_image_size": s.colmap_max_image_size, "threads": s.colmap_threads,
                                    "matcher": "exhaustive" if len(inputs) <= 150 else "sequential",
                                    "allow_fallback": allow_fallback,
                                    **({"marble_model": marble_model} if marble_model else {})})
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return job_out(db, db.scalar(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id,
                                                                      ReconstructionJob.idempotency_key == body.idempotency_key)))
    transition(db, job, JobState.QUEUED, data={"inputs": len(inputs), "mode": mode, "engine": requested,
                                               "chain": chain["engines"], "notice": chain.get("notice")})
    job.enqueued_at = now()
    audit.record(db, "job.created", actor=user.id, org_id=p.org_id, target_type="job", target_id=job.id, data={"mode": mode})
    db.commit()
    try:
        taskqueue.enqueue("roomscape_worker.tasks.process_job", str(job.id))
    except taskqueue.QueueUnavailable:
        job.enqueued_at = None  # reconciler will enqueue when the queue is back
        db.commit()
    db.refresh(job)
    return job_out(db, job)


def _job_for_user(db: Session, job_id: uuid.UUID, user: User, min_role: str = "VIEWER") -> tuple[ReconstructionJob, Project]:
    job = db.get(ReconstructionJob, job_id)
    if not job:
        raise not_found("Job")
    p, _ = load_project(db, job.project_id, user, min_role, allow_deleting=True)
    return job, p


@router.get("/projects/{project_id}/reconstruction-jobs")
def list_jobs(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    rows = db.scalars(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id)
                      .order_by(ReconstructionJob.created_at.desc()).limit(50)).all()
    return {"items": [job_out(db, j, include_events=False) for j in rows]}


@router.get("/reconstruction-jobs/{job_id}")
def get_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job, _ = _job_for_user(db, job_id, user)
    return job_out(db, job)


@router.get("/reconstruction-jobs/{job_id}/events")
async def job_events(job_id: uuid.UUID, request: Request, after: int = 0, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    """Server-Sent Events stream of persisted stage events (DB is the source of truth)."""
    job, _ = _job_for_user(db, job_id, user)
    jid = job.id
    db.close()

    async def gen():
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            with get_sessionmaker()() as s:
                j = s.get(ReconstructionJob, jid)
                evs = s.scalars(select(JobStageEvent).where(JobStageEvent.job_id == jid, JobStageEvent.id > last)
                                .order_by(JobStageEvent.id)).all()
                for e in evs:
                    last = e.id
                    yield f"id: {e.id}\nevent: stage\ndata: {json.dumps(event_out(e))}\n\n"
                snapshot = {"state": j.state, "state_text": STAGE_TEXT.get(j.state, j.state),
                            "cancel_requested": j.cancel_requested, "attempt_count": j.attempt_count}
                terminal = JobState(j.state) in TERMINAL
            if evs or idle % 15 == 0:
                yield f"event: job\ndata: {json.dumps(snapshot)}\n\n"
            if terminal:
                yield "event: end\ndata: {}\n\n"
                return
            idle += 1
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/reconstruction-jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job, p = _job_for_user(db, job_id, user, "EDITOR")
    st = JobState(job.state)
    if st in TERMINAL:
        return job_out(db, job)  # idempotent
    job.cancel_requested = True
    if st in {JobState.QUEUED, JobState.CREATED, JobState.WAITING_FOR_CONFIRMATION}:
        transition(db, job, JobState.CANCELLED, data={"by": "user"})
    elif st in PROCESSING:
        transition(db, job, JobState.CANCEL_REQUESTED, data={"by": "user"})
    audit.record(db, "job.cancel_requested", actor=user.id, org_id=p.org_id, target_type="job", target_id=job.id)
    db.commit()
    return job_out(db, job)


@router.post("/reconstruction-jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job, p = _job_for_user(db, job_id, user, "EDITOR")
    if p.status != "active":
        raise not_found("Project")
    if job.state not in {JobState.FAILED.value, JobState.CANCELLED.value, JobState.PARTIAL_SUCCESS.value}:
        raise APIError(409, "NOT_RETRYABLE", "Only failed, cancelled or partial jobs can be retried.")
    if job.error_category in {"INVALID_INPUT", "INSUFFICIENT_IMAGES", "UNSUPPORTED_INPUT"}:
        raise APIError(409, "INPUT_PROBLEM", "This failure is caused by the input photos; fix the photos and start a new job.")
    job.cancel_requested = False
    job.error_category = job.error_message = None
    job.max_attempts = job.attempt_count + get_settings().job_max_attempts  # new attempts; history is kept
    transition(db, job, JobState.QUEUED, data={"by": "user_retry"})
    job.enqueued_at = now()
    job.finished_at = None
    audit.record(db, "job.retried", actor=user.id, org_id=p.org_id, target_type="job", target_id=job.id)
    db.commit()
    try:
        taskqueue.enqueue("roomscape_worker.tasks.process_job", str(job.id))
    except taskqueue.QueueUnavailable:
        job.enqueued_at = None
        db.commit()
    return job_out(db, job)


# ------------------------------------------------------------------ artifacts

def artifact_out(a: Artifact, v: ArtifactValidationResult | None, share_id=None) -> dict:
    return {"id": str(a.id), "job_id": str(a.job_id), "kind": a.kind, "name": a.name, "mime": a.mime, "bytes": a.bytes,
            "sha256": a.sha256, "meta": a.meta, "created_at": a.created_at.isoformat(),
            "validation": {"ok": v.ok, "checks": v.checks, "validated_at": v.validated_at.isoformat()} if v else None,
            "url": file_url("artifact", a.id, "content", share_id)}


def latest_success(db: Session, project_id: uuid.UUID) -> ReconstructionJob | None:
    return db.scalar(select(ReconstructionJob).where(ReconstructionJob.project_id == project_id,
                                                      ReconstructionJob.state.in_(["SUCCEEDED", "PARTIAL_SUCCESS"]))
                     .order_by(ReconstructionJob.finished_at.desc()).limit(1))


def artifacts_for(db: Session, job: ReconstructionJob, share_id=None, public_only=False) -> list[dict]:
    stmt = (select(Artifact, ArtifactValidationResult).outerjoin(ArtifactValidationResult, ArtifactValidationResult.artifact_id == Artifact.id)
            .where(Artifact.job_id == job.id, Artifact.deleted_at.is_(None)))
    if public_only:
        stmt = stmt.where(Artifact.public_safe.is_(True))
    seen, out = set(), []
    for a, v in db.execute(stmt.order_by(Artifact.created_at.desc())).all():
        if a.id in seen:
            continue
        seen.add(a.id)
        out.append(artifact_out(a, v, share_id))
    return out


@router.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: uuid.UUID, job_id: uuid.UUID | None = None, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    job = db.get(ReconstructionJob, job_id) if job_id else latest_success(db, p.id)
    if not job or job.project_id != p.id:
        return {"job": None, "artifacts": []}
    return {"job": job_out(db, job, include_events=False), "artifacts": artifacts_for(db, job)}


_ = (membership, Organization)
