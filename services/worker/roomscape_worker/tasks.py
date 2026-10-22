"""Worker tasks executed from the RQ queue.

``process_job`` claims a QUEUED job with a row lock, opens a new attempt with a
lease, heartbeats while COLMAP runs, persists every stage event before anything
else hears about it, validates and uploads artifacts, and only then marks the
job SUCCEEDED / PARTIAL_SUCCESS. Failures are categorised; retryable ones are
re-queued with exponential backoff + jitter up to ``max_attempts``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import shutil
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from roomscape_api import mailer, taskqueue
from roomscape_api.config import get_settings
from roomscape_api.db import get_sessionmaker, is_postgres
from roomscape_api.jobs_service import stage_event, transition
from roomscape_api.models import (Artifact, ArtifactValidationResult, CaptureReport, DeletionRequest, JobAttempt,
                                  Project, ReconstructionJob, TourNode, Upload, UploadManifest, UsageLedger, User)
from roomscape_api.state_machine import JobState
from roomscape_api.storage import get_storage, project_prefix
from roomscape_recon import __version__ as recon_version
from roomscape_recon.artifacts import sha256_file
from roomscape_recon.colmap_engine import ColmapConfig, detect
from roomscape_recon.errors import ErrorCategory, ReconError
from roomscape_recon.panorama import run_panorama
from roomscape_recon.pipeline import run_photogrammetry
from roomscape_recon.quality import build_report
from sqlalchemy import select

from . import __version__

log = logging.getLogger("roomscape.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
HEARTBEAT_S = 10
PUBLIC_KINDS = {"POINT_CLOUD_WEB", "VIEWPOINTS", "PREVIEW", "QUALITY_REPORT", "PANORAMA", "PANORAMA_THUMB", "TOUR", "MESH",
                "POINT_CLOUD_DENSE"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


class Heartbeat(threading.Thread):
    """Extends the attempt lease and watches for cancellation / lost leases."""

    def __init__(self, attempt_id: uuid.UUID, job_id: uuid.UUID, lease_s: int):
        super().__init__(daemon=True)
        self.attempt_id, self.job_id, self.lease_s = attempt_id, job_id, lease_s
        self.stop_evt = threading.Event()
        self.cancel = threading.Event()
        self.lost = threading.Event()

    def beat(self) -> None:
        with get_sessionmaker()() as db:
            att = db.get(JobAttempt, self.attempt_id)
            job = db.get(ReconstructionJob, self.job_id)
            if att is None or att.state != "RUNNING":
                self.lost.set()
                self.cancel.set()
                return
            att.heartbeat_at = now()
            att.lease_expires_at = now() + timedelta(seconds=self.lease_s)
            proj = db.get(Project, job.project_id) if job else None
            if job is None or job.cancel_requested or (proj and proj.status != "active"):
                self.cancel.set()
            db.commit()

    def run(self) -> None:
        while not self.stop_evt.wait(HEARTBEAT_S):
            try:
                self.beat()
            except Exception as exc:  # pragma: no cover - transient DB errors
                log.warning(f"heartbeat failed: {type(exc).__name__}")


def _claim(db, job_id: uuid.UUID):
    stmt = select(ReconstructionJob).where(ReconstructionJob.id == job_id)
    if is_postgres():
        stmt = stmt.with_for_update(skip_locked=True)
    return db.scalar(stmt)


def _redact(text: str, ws: Path) -> str:
    return (text or "").replace(str(ws), "<workspace>").replace(str(Path.home()), "~")[-3000:]


def process_job(job_id: str) -> str:
    s = get_settings()
    jid = uuid.UUID(job_id)
    Session = get_sessionmaker()
    with Session() as db:
        job = _claim(db, jid)
        if job is None or job.state != JobState.QUEUED.value:
            return "skipped"
        next_at = _aware(job.next_attempt_at)
        if next_at and next_at > now() + timedelta(seconds=2):
            return "not_due"
        proj = db.get(Project, job.project_id)
        if job.cancel_requested or proj is None or proj.status != "active":
            transition(db, job, JobState.CANCELLED, data={"reason": "cancelled before start"})
            db.commit()
            return "cancelled"
        if job.attempt_count >= job.max_attempts:
            job.error_category, job.error_message = "INTERNAL", "Maximum attempts reached."
            transition(db, job, JobState.FAILED)
            db.commit()
            return "failed"
        info = detect()
        job.attempt_count += 1
        att = JobAttempt(job_id=job.id, number=job.attempt_count, worker_id=WORKER_ID, worker_version=__version__,
                         engine={**info.to_dict(), "adapter": recon_version},
                         lease_expires_at=now() + timedelta(seconds=s.job_lease_s))
        db.add(att)
        db.flush()
        job.started_at = job.started_at or now()
        transition(db, job, JobState.INPUT_VALIDATION, attempt_id=att.id, data={"attempt": att.number, "worker": WORKER_ID})
        db.commit()
        attempt_id, attempt_no = att.id, att.number
        org_id, project_id, mode = proj.org_id, proj.id, job.mode
        upload_ids = [uuid.UUID(u) for u in job.input_upload_ids]
        cfg = job.config or {}

    ws = Path(s.worker_workspace).resolve() / str(jid) / f"attempt-{attempt_no}"
    hb = Heartbeat(attempt_id, jid, s.job_lease_s)
    hb.start()
    try:
        result = _run(jid, attempt_id, attempt_no, org_id, project_id, mode, upload_ids, cfg, ws, hb)
        return result
    finally:
        hb.stop_evt.set()
        shutil.rmtree(ws, ignore_errors=True)


def _on_stage_factory(jid: uuid.UUID, attempt_id: uuid.UUID):
    def on_stage(stage: str, event: str, data: dict) -> None:
        with get_sessionmaker()() as db:
            job = db.get(ReconstructionJob, jid)
            if job is None:
                return
            if event == "started" and job.state != stage and job.state != JobState.CANCEL_REQUESTED.value:
                try:
                    transition(db, job, JobState(stage), attempt_id=attempt_id, data=data)
                except ValueError:
                    stage_event(db, job, stage, event, attempt_id=attempt_id, data=data)
            else:
                stage_event(db, job, stage, event, attempt_id=attempt_id, data=data)
            db.commit()
    return on_stage


def _run(jid, attempt_id, attempt_no, org_id, project_id, mode, upload_ids, cfg, ws: Path, hb: Heartbeat) -> str:
    s = get_settings()
    storage = get_storage()
    Session = get_sessionmaker()
    try:
        inp = ws / "input"
        inp.mkdir(parents=True, exist_ok=True)
        image_map, manifest = {}, []
        with Session() as db:
            ups = db.scalars(select(Upload).where(Upload.id.in_(upload_ids))).all()
            for u in ups:
                if u.status != "stored" or u.project_id != project_id:
                    continue
                ext = Path(u.storage_key).suffix
                dest = inp / f"{u.id}{ext}"
                storage.download(u.storage_key, dest)
                if sha256_file(dest) != u.sha256:
                    raise ReconError(ErrorCategory.INVALID_INPUT, "A stored photo failed its checksum.", str(u.id))
                image_map[f"{u.id}.jpg"] = str(u.id)
                image_map[f"{u.id}{ext}"] = str(u.id)
                manifest.append({"upload_id": str(u.id), "sha256": u.sha256, "bytes": u.bytes, "width": u.width,
                                 "height": u.height, "mime": u.mime})
            db.add(UploadManifest(job_id=jid, attempt_id=attempt_id, manifest={"inputs": manifest}))
            db.commit()
        if len(manifest) < len(upload_ids):
            log.warning("some inputs were deleted before processing", extra={"job_id": str(jid)})
        on_stage = _on_stage_factory(jid, attempt_id)
        out = ws / "out"
        if mode == "PANORAMA_TOUR":
            report = run_panorama(inp, out, on_stage=on_stage, image_map=image_map)
            res = {"status": "SUCCEEDED", "output_type": "panorama_tour", "metrics": report["metrics"],
                   "warnings": report["warnings"], "limitations": report["limitations"],
                   "recapture_suggestions": [], "stages_completed": ["INPUT_VALIDATION", "TEXTURE_OR_APPEARANCE_PROCESSING",
                                                                    "ARTIFACT_VALIDATION"],
                   "engine": {"name": "panorama", "version": recon_version}}
            artifacts = report["artifacts"]
        else:
            ccfg = ColmapConfig(max_image_size=int(cfg.get("max_image_size", s.colmap_max_image_size)),
                                num_threads=int(cfg.get("threads", s.colmap_threads)),
                                matcher=cfg.get("matcher", "exhaustive"), step_timeout_s=s.job_step_timeout_s)
            r = run_photogrammetry(inp, ws / "work", out, ccfg, on_stage=on_stage, should_cancel=hb.cancel.is_set,
                                   image_map=image_map)
            res = r.to_dict()
            res.pop("artifacts", None)
            res.pop("steps", None)
            res["steps"] = [{k: v for k, v in st.items() if k != "argv"} | {"command": st["argv"][1] if len(st["argv"]) > 1 else ""}
                            for st in r.steps]
            artifacts = r.artifacts
        if hb.cancel.is_set():
            raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")
        # ---- upload + register artifacts --------------------------------------
        prefix = project_prefix(org_id, project_id) + f"jobs/{jid}/attempt-{attempt_no}/"
        with Session() as db:
            proj = db.get(Project, project_id)
            if proj is None or proj.status != "active":
                raise ReconError(ErrorCategory.CANCELLED, "The project was deleted during processing.")
            for a in artifacts:
                path = out / a["path"]
                key = prefix + a["path"]
                storage.put_file(key, path, a["mime"])
                stored = storage.size(key)
                ok = stored == a["bytes"]
                meta = {k: v for k, v in a.items() if k not in {"path", "bytes", "sha256", "mime", "kind"}}
                art = Artifact(project_id=project_id, job_id=jid, attempt_id=attempt_id, kind=a["kind"], name=a["path"],
                               storage_key=key, mime=a["mime"], bytes=a["bytes"], sha256=a["sha256"], meta=meta,
                               public_safe=a["kind"] in PUBLIC_KINDS)
                db.add(art)
                db.flush()
                db.add(ArtifactValidationResult(artifact_id=art.id, ok=ok, checks={
                    "pipeline_validation": "passed", "stored_bytes_match": ok, "sha256": a["sha256"]}))
                if not ok:
                    raise ReconError(ErrorCategory.ARTIFACT_INVALID, "An output file failed verification after upload.")
            # Internal (non-public) logs for operators.
            logs_dir = ws / "work" / "logs"
            if logs_dir.exists():
                for lf in sorted(logs_dir.glob("*.log")):
                    key = prefix + "logs/" + lf.name
                    storage.put_file(key, lf, "text/plain")
                    db.add(Artifact(project_id=project_id, job_id=jid, attempt_id=attempt_id, kind="LOG", name=lf.name,
                                    storage_key=key, mime="text/plain", bytes=lf.stat().st_size, sha256=sha256_file(lf),
                                    meta={}, public_safe=False))
            # ---- tour nodes from real output (registered photos / panoramas) ----
            _sync_nodes(db, project_id, jid, out, mode)
            job = db.get(ReconstructionJob, jid)
            job.result = res
            att = db.get(JobAttempt, attempt_id)
            att.state, att.finished_at = "SUCCEEDED", now()
            final = JobState(res["status"])
            if job.state == JobState.CANCEL_REQUESTED.value and hb.cancel.is_set():
                raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")
            transition(db, job, final, attempt_id=attempt_id, data={"output_type": res["output_type"]})
            minutes = max(1, math.ceil(float(res["metrics"].get("processing_seconds", 0)) / 60))
            key = f"job:{jid}:reconstruction_minutes"
            if not db.scalar(select(UsageLedger).where(UsageLedger.idempotency_key == key)):
                db.add(UsageLedger(org_id=org_id, kind="reconstruction_minutes", quantity=minutes, unit="minute", job_id=jid,
                                   idempotency_key=key, note=f"{mode} job, attempt {attempt_no}"))
            creator = db.get(User, job.created_by) if job.created_by else None
            if creator:
                mailer.send(db, to=creator.email, user_id=creator.id, kind="job_finished",
                            subject=f"Your reconstruction finished: {final.value.replace('_', ' ').lower()}",
                            body=f"Project '{proj.title}' finished processing. Open it in RoomScape AI to review the result.")
            db.commit()
        return final.value
    except ReconError as e:
        _fail(jid, attempt_id, e, ws)
        return e.category.value
    except Exception as e:  # unexpected: retry as INTERNAL
        log.exception("unexpected worker error", extra={"job_id": str(jid)})
        _fail(jid, attempt_id, ReconError(ErrorCategory.INTERNAL, "Unexpected processing error.", repr(e)), ws)
        return "INTERNAL"


def _sync_nodes(db, project_id, jid, out: Path, mode: str) -> None:
    existing = {n.upload_id: n for n in db.scalars(select(TourNode).where(TourNode.project_id == project_id))}
    if mode == "PANORAMA_TOUR":
        tour = json.loads((out / "tour.json").read_text())
        items = [(uuid.UUID(n["upload_id"]), "PANORAMA", n["image"], i) for i, n in enumerate(tour["nodes"]) if n.get("upload_id")]
    else:
        cams = json.loads((out / "cameras.json").read_text())
        items = [(uuid.UUID(v["upload_id"]), "VIEWPOINT", v["name"], i) for i, v in enumerate(cams["viewpoints"]) if v.get("upload_id")]
    for upload_id, kind, ref, i in items:
        n = existing.get(upload_id)
        if n:
            n.job_id, n.ref, n.kind = jid, ref, kind
        else:
            db.add(TourNode(project_id=project_id, job_id=jid, kind=kind, ref=ref, upload_id=upload_id, sort=i))


def _fail(jid: uuid.UUID, attempt_id: uuid.UUID, e: ReconError, ws: Path) -> None:
    with get_sessionmaker()() as db:
        job = db.get(ReconstructionJob, jid)
        att = db.get(JobAttempt, attempt_id)
        if att and att.state == "RUNNING":
            att.state = "CANCELLED" if e.category == ErrorCategory.CANCELLED else "FAILED"
            att.finished_at = now()
            att.error_category = e.category.value
            att.error_detail = _redact(e.detail or e.user_message, ws)
        elif att and att.state == "LOST":
            db.commit()
            return  # the reconciler already handled this attempt
        if job is None:
            db.commit()
            return
        stage_event(db, job, job.state, "failed", attempt_id=attempt_id, data=e.to_dict())
        if e.category == ErrorCategory.CANCELLED or job.cancel_requested:
            transition(db, job, JobState.CANCELLED, attempt_id=attempt_id, data={"reason": e.user_message})
        elif e.retryable and job.attempt_count < job.max_attempts:
            delay = min(600, 30 * 2 ** (job.attempt_count - 1)) * (0.8 + random.random() * 0.4)
            job.next_attempt_at = now() + timedelta(seconds=delay)
            job.enqueued_at = now()
            transition(db, job, JobState.QUEUED, attempt_id=attempt_id,
                       data={"retry_in_s": round(delay), "reason": e.category.value})
            db.commit()
            try:
                taskqueue.enqueue("roomscape_worker.tasks.process_job", str(jid), delay_s=delay)
            except taskqueue.QueueUnavailable:
                pass
            return
        else:
            job.error_category, job.error_message = e.category.value, e.user_message[:500]
            transition(db, job, JobState.FAILED, attempt_id=attempt_id, data=e.to_dict())
        db.commit()


# ---------------------------------------------------------------------------- capture QA

def analyze_capture(report_id: str) -> str:
    storage = get_storage()
    with get_sessionmaker()() as db:
        rep = db.get(CaptureReport, uuid.UUID(report_id))
        if rep is None or rep.status != "PENDING":
            return "skipped"
        ups = db.scalars(select(Upload).where(Upload.project_id == rep.project_id, Upload.status == "stored",
                                              Upload.kind == "PHOTO").order_by(Upload.display_name)).all()
        try:
            names = {}
            items = []
            for u in ups:
                label = f"{u.display_name}"
                names[label] = str(u.id)
                items.append((label, storage.get_bytes(u.web_key or u.storage_key)))
            report = build_report(items)
            for im in report["images"]:
                im["upload_id"] = names.get(im["name"])
                im.pop("sha256", None)  # derivative hash is not the original's
            report["summary"]["analysed_on"] = "web derivatives (max 2048 px, EXIF stripped)"
            rep.report, rep.status = report, report["status"]
        except Exception as exc:
            rep.status = "ANALYSIS_FAILED"
            rep.report = {"error": type(exc).__name__, "message": "Analysis failed. Try again."}
        rep.completed_at = now()
        db.commit()
        return rep.status


# ---------------------------------------------------------------------------- deletion

def cleanup_project(request_id: str) -> str:
    storage = get_storage()
    with get_sessionmaker()() as db:
        req = db.get(DeletionRequest, uuid.UUID(request_id))
        if req is None or req.status == "done":
            return "skipped"
        proj = db.get(Project, req.project_id)
        if proj is None:
            req.status, req.completed_at = "done", now()
            db.commit()
            return "done"
        running = db.scalar(select(ReconstructionJob).where(
            ReconstructionJob.project_id == proj.id,
            ReconstructionJob.state.notin_(["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED", "EXPIRED", "QUEUED"])))
        if running is not None:
            req.status = "pending"
            db.commit()
            taskqueue.enqueue("roomscape_worker.tasks.cleanup_project", request_id, delay_s=30)
            return "waiting_for_job"
        for job in db.scalars(select(ReconstructionJob).where(ReconstructionJob.project_id == proj.id,
                                                              ReconstructionJob.state == "QUEUED")):
            transition(db, job, JobState.CANCELLED, data={"reason": "project deleted"})
        req.status, req.attempts = "in_progress", req.attempts + 1
        db.commit()
        try:
            prefix = project_prefix(proj.org_id, proj.id)
            keys = storage.list(prefix)
            for k in keys:
                storage.delete(k)
            remaining = storage.list(prefix)
            if remaining:
                raise RuntimeError(f"{len(remaining)} objects remain")
            for u in db.scalars(select(Upload).where(Upload.project_id == proj.id)):
                u.status, u.deleted_at = "deleted", u.deleted_at or now()
            for a in db.scalars(select(Artifact).where(Artifact.project_id == proj.id)):
                a.deleted_at = a.deleted_at or now()
            proj.status = "deleted"
            proj.title = "Deleted project"
            proj.description = proj.address_private = ""
            req.keys_deleted += len(keys)
            req.verified_empty = True
            req.status, req.completed_at, req.last_error = "done", now(), None
            db.commit()
            return "done"
        except Exception as exc:
            db.rollback()
            req = db.get(DeletionRequest, uuid.UUID(request_id))
            req.status, req.last_error = "failed", type(exc).__name__ + ": " + str(exc)[:200]
            db.commit()
            return "failed"
