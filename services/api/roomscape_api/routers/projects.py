"""Projects, uploads and capture-quality reports."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, media, taskqueue
from ..config import get_settings
from ..db import get_db
from ..deps import current_user, load_project, require_org_role
from ..errors import APIError, not_found
from ..models import (
    CaptureReport,
    DeletionRequest,
    ImageQualityResult,
    Project,
    ReconstructionJob,
    ShareLink,
    Subscription,
    Upload,
    User,
)
from ..plans import plan
from ..ratelimit import hit
from ..signing import file_url
from ..state_machine import ACTIVE, JobState
from ..storage import get_storage, project_prefix

router = APIRouter(prefix="/api/v1", tags=["projects"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def project_out(db: Session, p: Project, role: str | None = None) -> dict:
    latest = db.scalar(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id)
                       .order_by(ReconstructionJob.created_at.desc()).limit(1))
    n_uploads = db.scalar(select(func.count()).select_from(Upload).where(Upload.project_id == p.id, Upload.status == "stored",
                                                                        Upload.kind == "PHOTO"))
    cover = None
    if p.cover_upload_id:
        cover = file_url("upload", p.cover_upload_id, "thumb")
    elif n_uploads:
        first = db.scalar(select(Upload.id).where(Upload.project_id == p.id, Upload.status == "stored").order_by(Upload.created_at).limit(1))
        cover = file_url("upload", first, "thumb") if first else None
    return {
        "id": str(p.id), "org_id": str(p.org_id), "title": p.title, "description": p.description,
        "address_private": p.address_private if role else None, "property_type": p.property_type,
        "rooms_estimate": p.rooms_estimate, "listing_status": p.listing_status, "status": p.status,
        "capture_checklist": p.capture_checklist or {}, "version": p.version, "role": role,
        "photo_count": n_uploads, "cover_url": cover, "is_public_sample": p.is_public_sample,
        "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
        "latest_job": {"id": str(latest.id), "state": latest.state, "mode": latest.mode,
                       "updated_at": latest.updated_at.isoformat()} if latest else None,
    }


class ProjectCreate(BaseModel):
    org_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    address_private: str = Field(default="", max_length=300)
    property_type: str = Field(default="apartment", pattern="^(apartment|house|office|studio|commercial|other)$")
    rooms_estimate: int | None = Field(default=None, ge=1, le=100)


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_org_role(db, body.org_id, user, "EDITOR")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == body.org_id))
    limit = plan(sub.plan_code if sub else "FREE")["active_projects"]
    active = db.scalar(select(func.count()).select_from(Project).where(Project.org_id == body.org_id, Project.status == "active"))
    if active >= limit:
        raise APIError(402, "PROJECT_LIMIT", f"Your plan allows {limit} active projects. Delete one or upgrade.")
    p = Project(org_id=body.org_id, created_by=user.id, title=body.title.strip(), description=body.description,
                address_private=body.address_private, property_type=body.property_type, rooms_estimate=body.rooms_estimate)
    db.add(p)
    db.flush()
    audit.record(db, "project.created", actor=user.id, org_id=body.org_id, target_type="project", target_id=p.id)
    db.commit()
    return project_out(db, p, m.role)


@router.get("/projects")
def list_projects(org_id: uuid.UUID, q: str = "", status: str = "", sort: str = "updated",
                  limit: int = Query(24, ge=1, le=100), offset: int = Query(0, ge=0),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_org_role(db, org_id, user, "VIEWER")
    stmt = select(Project).where(Project.org_id == org_id, Project.status == "active")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Project.title).like(like), func.lower(Project.description).like(like)))
    if status in {"draft", "active", "archived"}:
        stmt = stmt.where(Project.listing_status == status)
    order = {"updated": Project.updated_at.desc(), "created": Project.created_at.desc(), "title": Project.title.asc()}
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(order.get(sort, Project.updated_at.desc())).limit(limit).offset(offset)).all()
    return {"total": total, "items": [project_out(db, p, m.role) for p in rows]}


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, m = load_project(db, project_id, user)
    return project_out(db, p, m.role)


class ProjectPatch(BaseModel):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    address_private: str | None = Field(default=None, max_length=300)
    property_type: str | None = Field(default=None, pattern="^(apartment|house|office|studio|commercial|other)$")
    rooms_estimate: int | None = Field(default=None, ge=1, le=100)
    listing_status: str | None = Field(default=None, pattern="^(draft|active|archived)$")
    capture_checklist: dict | None = None
    cover_upload_id: uuid.UUID | None = None


@router.patch("/projects/{project_id}")
def patch_project(project_id: uuid.UUID, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, m = load_project(db, project_id, user, "EDITOR")
    if body.version != p.version:
        raise APIError(409, "VERSION_CONFLICT", "This project was changed elsewhere. Reload and try again.")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "cover_upload_id" in changes and changes["cover_upload_id"]:
        up = db.get(Upload, changes["cover_upload_id"])
        if not up or up.project_id != p.id:
            raise APIError(422, "INVALID_COVER", "Cover photo must belong to this project.")
    if "capture_checklist" in changes and len(str(changes["capture_checklist"])) > 5000:
        raise APIError(422, "CHECKLIST_TOO_LARGE", "Checklist is too large.")
    for k, v in changes.items():
        setattr(p, k, v)
    p.version += 1
    audit.record(db, "project.updated", actor=user.id, org_id=p.org_id, target_type="project", target_id=p.id,
                 data={"fields": sorted(changes)})
    db.commit()
    return project_out(db, p, m.role)


def begin_project_deletion(db: Session, p: Project, user: User) -> DeletionRequest:
    p.status = "deleting"
    p.deleted_at = now()
    for link in db.scalars(select(ShareLink).where(ShareLink.project_id == p.id, ShareLink.revoked_at.is_(None))):
        link.revoked_at = now()
    for job in db.scalars(select(ReconstructionJob).where(ReconstructionJob.project_id == p.id)):
        if JobState(job.state) in ACTIVE:
            job.cancel_requested = True
    req = DeletionRequest(kind="project", project_id=p.id, requested_by=user.id)
    db.add(req)
    db.flush()
    audit.record(db, "project.deletion_requested", actor=user.id, org_id=p.org_id, target_type="project", target_id=p.id)
    return req


@router.delete("/projects/{project_id}", status_code=202)
def delete_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "ADMIN")
    req = begin_project_deletion(db, p, user)
    db.commit()
    try:
        taskqueue.enqueue("roomscape_worker.tasks.cleanup_project", str(req.id))
    except taskqueue.QueueUnavailable:
        pass  # the reconciler picks up pending deletion requests
    return {"deletion_request_id": str(req.id), "status": "pending"}


@router.get("/deletion-requests/{request_id}")
def deletion_status(request_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    req = db.get(DeletionRequest, request_id)
    if not req or req.requested_by != user.id:
        raise not_found("Deletion request")
    return {"id": str(req.id), "status": req.status, "keys_deleted": req.keys_deleted, "verified_empty": req.verified_empty,
            "attempts": req.attempts, "completed_at": req.completed_at.isoformat() if req.completed_at else None}


# ------------------------------------------------------------------ uploads

def upload_out(u: Upload, q: ImageQualityResult | None) -> dict:
    return {"id": str(u.id), "kind": u.kind, "name": u.display_name, "mime": u.mime, "bytes": u.bytes, "width": u.width,
            "height": u.height, "is_panorama": u.is_panorama, "sha256": u.sha256, "created_at": u.created_at.isoformat(),
            "thumb_url": file_url("upload", u.id, "thumb"), "web_url": file_url("upload", u.id, "web"),
            "quality": {"metrics": q.metrics, "issues": q.issues} if q else None}


async def _read_limited(f: UploadFile, limit: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = await f.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise APIError(413, "FILE_TOO_LARGE", f"{media.display_name(f.filename)} exceeds {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/projects/{project_id}/uploads", status_code=201)
async def upload_files(project_id: uuid.UUID, request: Request, files: list[UploadFile] = File(...),
                       kind: str = Query("PHOTO", pattern="^(PHOTO|FLOOR_PLAN)$"),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    p, _ = load_project(db, project_id, user, "EDITOR")
    hit("upload", str(user.id), 600, 3600)
    if len(files) > s.max_files_per_request:
        raise APIError(413, "TOO_MANY_FILES", f"Upload at most {s.max_files_per_request} files per request.")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == p.org_id))
    quota = plan(sub.plan_code if sub else "FREE")["photo_storage_gb"] * 1024 ** 3
    used = db.scalar(select(func.coalesce(func.sum(Upload.bytes), 0)).select_from(Upload).join(Project, Project.id == Upload.project_id)
                     .where(Project.org_id == p.org_id, Upload.status == "stored")) or 0
    storage = get_storage()
    created, duplicates, rejected = [], [], []
    for f in files:
        name = media.display_name(f.filename)
        try:
            data = await _read_limited(f, s.max_upload_bytes)
            mime = media.validate_image(data)
            sha = hashlib.sha256(data).hexdigest()
            existing = db.scalar(select(Upload).where(Upload.project_id == p.id, Upload.sha256 == sha, Upload.status == "stored"))
            if existing:
                duplicates.append({"name": name, "existing_id": str(existing.id)})
                continue
            if used + len(data) > quota:
                raise APIError(402, "STORAGE_QUOTA", "Photo storage quota reached for your plan.")
            web, thumb, (w, h) = media.derivatives(data)
            qual = media.quality(name, data)
        except APIError as e:
            rejected.append({"name": name, "code": e.code, "message": e.message})
            continue
        uid_ = uuid.uuid4()
        base = project_prefix(p.org_id, p.id) + f"uploads/{uid_}"
        ext = media.EXT[mime]
        storage.put_bytes(f"{base}/original{ext}", data, mime)
        storage.put_bytes(f"{base}/web.jpg", web, "image/jpeg")
        storage.put_bytes(f"{base}/thumb.jpg", thumb, "image/jpeg")
        up = Upload(id=uid_, project_id=p.id, uploaded_by=user.id, kind=kind, display_name=name, storage_key=f"{base}/original{ext}",
                    web_key=f"{base}/web.jpg", thumb_key=f"{base}/thumb.jpg", mime=mime, bytes=len(data), sha256=sha,
                    width=w, height=h, is_panorama=bool(qual.get("is_panorama")))
        db.add(up)
        try:
            db.flush()
        except IntegrityError:  # concurrent duplicate upload
            db.rollback()
            duplicates.append({"name": name})
            continue
        metrics = {k: qual.get(k) for k in ("blur_score", "mean_luminance", "dark_fraction", "bright_fraction", "dhash", "is_panorama")}
        q = ImageQualityResult(upload_id=up.id, metrics=metrics, issues=qual.get("issues", []))
        db.add(q)
        used += len(data)
        created.append(upload_out(up, q))
    if created:
        p.updated_at = now()
        audit.record(db, "upload.created", actor=user.id, org_id=p.org_id, target_type="project", target_id=p.id,
                     data={"count": len(created)})
    db.commit()
    return {"created": created, "duplicates": duplicates, "rejected": rejected}


@router.get("/projects/{project_id}/uploads")
def list_uploads(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    rows = db.execute(select(Upload, ImageQualityResult).outerjoin(ImageQualityResult, ImageQualityResult.upload_id == Upload.id)
                      .where(Upload.project_id == p.id, Upload.status == "stored").order_by(Upload.display_name)).all()
    return {"items": [upload_out(u, q) for u, q in rows]}


@router.delete("/projects/{project_id}/uploads/{upload_id}")
def delete_upload(project_id: uuid.UUID, upload_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    up = db.get(Upload, upload_id)
    if not up or up.project_id != p.id or up.status != "stored":
        raise not_found("Upload")
    storage = get_storage()
    for key in (up.storage_key, up.web_key, up.thumb_key):
        if key:
            storage.delete(key)
    up.status = "deleted"
    up.deleted_at = now()
    audit.record(db, "upload.deleted", actor=user.id, org_id=p.org_id, target_type="upload", target_id=up.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ capture quality

def input_fingerprint(db: Session, project_id: uuid.UUID) -> tuple[str, list[Upload]]:
    ups = db.scalars(select(Upload).where(Upload.project_id == project_id, Upload.status == "stored", Upload.kind == "PHOTO")
                     .order_by(Upload.sha256)).all()
    return hashlib.sha256("|".join(u.sha256 for u in ups).encode()).hexdigest(), list(ups)


def report_out(r: CaptureReport | None, fingerprint: str) -> dict:
    if r is None:
        return {"status": "NOT_ANALYZED", "stale": True}
    return {"id": str(r.id), "status": r.status, "stale": r.input_fingerprint != fingerprint, "report": r.report,
            "created_at": r.created_at.isoformat(), "completed_at": r.completed_at.isoformat() if r.completed_at else None}


@router.get("/projects/{project_id}/capture-quality")
def get_capture_quality(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    fp, _ = input_fingerprint(db, p.id)
    r = db.scalar(select(CaptureReport).where(CaptureReport.project_id == p.id).order_by(CaptureReport.created_at.desc()).limit(1))
    return report_out(r, fp)


@router.post("/projects/{project_id}/capture-quality/analyze", status_code=202)
def analyze_capture(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    hit("analyze", str(p.id), 30, 3600)
    fp, ups = input_fingerprint(db, p.id)
    if not ups:
        raise APIError(422, "NO_PHOTOS", "Upload photos before running the capture check.")
    existing = db.scalar(select(CaptureReport).where(CaptureReport.project_id == p.id, CaptureReport.input_fingerprint == fp)
                         .order_by(CaptureReport.created_at.desc()).limit(1))
    if existing and existing.status != "ANALYSIS_FAILED":
        return report_out(existing, fp)  # idempotent: same inputs, same report
    r = CaptureReport(project_id=p.id, status="PENDING", input_fingerprint=fp, report={})
    db.add(r)
    db.commit()
    try:
        taskqueue.enqueue("roomscape_worker.tasks.analyze_capture", str(r.id))
    except taskqueue.QueueUnavailable:
        r.status = "ANALYSIS_FAILED"
        r.report = {"error": "QUEUE_UNAVAILABLE", "message": "The analysis service is unavailable. Try again shortly."}
        db.commit()
    db.refresh(r)
    return report_out(r, fp)
