"""Share links, public tours, signed file delivery and privacy reports."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..deps import client_key, current_user, load_project, membership
from ..errors import APIError, not_found
from ..models import AbuseReport, Artifact, Project, ShareAccessEvent, ShareLink, Subscription, Upload, User
from ..plans import plan
from ..ratelimit import hit
from ..security import hash_password, new_token, sign, token_hash, verify_password, verify_sig
from ..signing import check
from ..storage import get_storage
from .jobs import artifacts_for, job_out, latest_success
from .tour import tour_payload

router = APIRouter(prefix="/api/v1", tags=["sharing"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def link_out(link: ShareLink, token: str | None = None) -> dict:
    s = get_settings()
    out = {"id": str(link.id), "visibility": link.visibility, "token_prefix": link.token_prefix,
           "password_protected": link.password_hash is not None, "allow_embed": link.allow_embed,
           "allowed_origins": link.allowed_origins, "expires_at": _aware(link.expires_at).isoformat() if link.expires_at else None,
           "revoked_at": _aware(link.revoked_at).isoformat() if link.revoked_at else None, "view_count": link.view_count,
           "created_at": link.created_at.isoformat(), "active": link_state(link) == "active"}
    if token:  # only returned once, at creation
        out["token"] = token
        out["url"] = f"{s.public_web_url}/tour/{token}"
        out["embed_html"] = (f'<iframe src="{s.public_web_url}/embed/{token}" width="100%" height="560" '
                             f'style="border:0;border-radius:16px" allow="fullscreen; xr-spatial-tracking" '
                             f'title="3D tour"></iframe>') if link.allow_embed else None
    return out


def link_state(link: ShareLink) -> str:
    if link.revoked_at:
        return "revoked"
    if link.expires_at and _aware(link.expires_at) < now():
        return "expired"
    return "active"


class ShareCreate(BaseModel):
    visibility: str = Field(default="UNLISTED", pattern="^(UNLISTED|RESTRICTED)$")
    expires_in_days: int | None = Field(default=30, ge=1, le=365)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    allow_embed: bool = False
    allowed_origins: list[str] = Field(default_factory=list, max_length=10)
    publish_consent: bool = False

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: list[str]) -> list[str]:
        out = []
        for o in v:
            u = urlparse(o.strip())
            if u.scheme not in {"https", "http"} or not u.netloc or u.path not in {"", "/"}:
                raise ValueError(f"invalid origin: {o}")
            out.append(f"{u.scheme}://{u.netloc}")
        return out


@router.post("/projects/{project_id}/share-links", status_code=201)
def create_link(project_id: uuid.UUID, body: ShareCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    hit("share-create", str(user.id), 30, 3600)
    if body.visibility == "UNLISTED":
        if not user.email_verified_at:
            raise APIError(403, "EMAIL_NOT_VERIFIED", "Verify your email before publishing a link anyone can open.")
        if not body.publish_consent and not p.public_consent_at:
            raise APIError(409, "CONSENT_REQUIRED",
                           "Confirm you have the right to publish these photos and that they show no private documents, keys or people.")
        p.public_consent_at = p.public_consent_at or now()
    sub = db.scalar(select(Subscription).where(Subscription.org_id == p.org_id))
    pl = plan(sub.plan_code if sub else "FREE")
    active = db.scalar(select(func.count()).select_from(ShareLink).where(ShareLink.project_id == p.id, ShareLink.revoked_at.is_(None)))
    if active >= pl["share_links"]:
        raise APIError(402, "SHARE_LIMIT", "Your plan's share-link limit is reached. Revoke a link or upgrade.")
    if body.allow_embed and not pl["embeds"]:
        raise APIError(402, "PLAN_FEATURE", "Embeds are available on Pro and Agency plans.")
    if body.password and not pl["password_links"]:
        raise APIError(402, "PLAN_FEATURE", "Password-protected links are available on Pro and Agency plans.")
    if not latest_success(db, p.id):
        raise APIError(409, "NOTHING_TO_SHARE", "Finish a successful reconstruction before sharing.")
    token = new_token(24)
    link = ShareLink(project_id=p.id, created_by=user.id, token_hash=token_hash(token), token_prefix=token[:6],
                     visibility=body.visibility, password_hash=hash_password(body.password) if body.password else None,
                     allow_embed=body.allow_embed, allowed_origins=body.allowed_origins,
                     expires_at=now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    db.add(link)
    db.flush()
    audit.record(db, "share.created", actor=user.id, org_id=p.org_id, target_type="share_link", target_id=link.id,
                 data={"visibility": body.visibility, "expires_in_days": body.expires_in_days, "embed": body.allow_embed})
    db.commit()
    return link_out(link, token)


@router.get("/projects/{project_id}/share-links")
def list_links(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    rows = db.scalars(select(ShareLink).where(ShareLink.project_id == p.id).order_by(ShareLink.created_at.desc())).all()
    return {"items": [link_out(link) for link in rows]}


@router.delete("/share-links/{link_id}")
def revoke_link(link_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    link = db.get(ShareLink, link_id)
    if not link:
        raise not_found("Share link")
    p, _ = load_project(db, link.project_id, user, "EDITOR")
    if not link.revoked_at:
        link.revoked_at = now()
        audit.record(db, "share.revoked", actor=user.id, org_id=p.org_id, target_type="share_link", target_id=link.id)
        db.commit()
    return link_out(link)


# ------------------------------------------------------------------ public access

UNLOCK_TTL = 3600


def _unlock_token(link: ShareLink) -> str:
    exp = int(time.time()) + UNLOCK_TTL
    return f"{exp}.{sign(get_settings().secret_key, f'unlock:{link.id}:{exp}')}"


def _unlocked(link: ShareLink, header: str | None) -> bool:
    if not header or "." not in header:
        return False
    exp_s, sig = header.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and verify_sig(get_settings().secret_key, f"unlock:{link.id}:{exp}", sig)


def resolve_link(db: Session, token: str, request: Request, unlock: str | None, record: bool = True) -> tuple[ShareLink, Project]:
    hit("public-tour", client_key(request), 240, 60)
    if len(token) > 100:
        raise not_found("Tour")
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if not link:
        raise not_found("Tour")
    p = db.get(Project, link.project_id)
    state = link_state(link)
    if state != "active" or not p or p.status != "active":
        if record:
            db.add(ShareAccessEvent(share_link_id=link.id, outcome=f"denied_{state if state != 'active' else 'deleted'}"))
            db.commit()
        raise APIError(410, "LINK_INACTIVE", "This tour link has been revoked or has expired.")
    if link.visibility == "RESTRICTED":
        from ..deps import resolve_session

        pr = resolve_session(db, request)
        if not pr or not membership(db, p.org_id, pr.user.id):
            raise APIError(401, "SIGN_IN_REQUIRED", "This tour is restricted to workspace members. Please sign in.")
    if link.password_hash and not _unlocked(link, unlock):
        raise APIError(401, "PASSWORD_REQUIRED", "This tour is password protected.")
    return link, p


@router.get("/public/tours/{token}")
def public_tour(token: str, request: Request, x_share_unlock: str | None = Header(default=None), db: Session = Depends(get_db)):
    link, p = resolve_link(db, token, request, x_share_unlock)
    job = latest_success(db, p.id)
    link.view_count += 1
    db.add(ShareAccessEvent(share_link_id=link.id, outcome="granted"))
    db.commit()
    return tour_response(db, p, job, share_id=link.id, embed={"allow": link.allow_embed, "origins": link.allowed_origins})


def tour_response(db: Session, p: Project, job, share_id=None, embed=None) -> dict:
    jo = job_out(db, job, include_events=False) if job else None
    if jo:
        jo.pop("config", None)
    return {
        "project": {"id": str(p.id), "title": p.title, "description": p.description, "property_type": p.property_type,
                    "is_public_sample": p.is_public_sample},
        "job": jo,
        "artifacts": artifacts_for(db, job, share_id=share_id, public_only=True) if job else [],
        "tour": tour_payload(db, p.id, share_id=share_id),
        "embed": embed,
        "disclaimer": "Reconstructed appearance and geometry can be incomplete or inaccurate. Distances are not "
                      "measured unless stated by the owner.",
    }


class UnlockBody(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post("/public/tours/{token}/unlock")
def unlock(token: str, body: UnlockBody, request: Request, db: Session = Depends(get_db)):
    hit("unlock", client_key(request), 10, 300)
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if not link or link_state(link) != "active" or not link.password_hash:
        raise not_found("Tour")
    if not verify_password(link.password_hash, body.password):
        db.add(ShareAccessEvent(share_link_id=link.id, outcome="denied_password"))
        db.commit()
        raise APIError(401, "WRONG_PASSWORD", "Incorrect password.")
    return {"unlock": _unlock_token(link), "expires_in": UNLOCK_TTL}


@router.get("/public/tours/{token}/embed-policy")
def embed_policy(token: str, request: Request, db: Session = Depends(get_db)):
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if not link or link_state(link) != "active":
        return {"allow": False, "origins": []}
    return {"allow": link.allow_embed, "origins": link.allowed_origins}


@router.get("/public/sample")
def public_sample(db: Session = Depends(get_db)):
    """The landing page's interactive sample: a real project flagged as public sample by an admin."""
    p = db.scalar(select(Project).where(Project.is_public_sample.is_(True), Project.status == "active").limit(1))
    job = latest_success(db, p.id) if p else None
    if not p or not job:
        raise not_found("Sample")
    return tour_response(db, p, job, share_id=f"sample-{p.id}")


class AbuseIn(BaseModel):
    token: str | None = Field(default=None, max_length=100)
    project_id: uuid.UUID | None = None
    reason: str = Field(pattern="^(people|documents|security|copyright|other)$")
    details: str = Field(default="", max_length=2000)


@router.post("/privacy-reports", status_code=201)
def report_content(body: AbuseIn, request: Request, db: Session = Depends(get_db)):
    hit("abuse", client_key(request), 10, 3600)
    pid, prefix = body.project_id, ""
    if body.token:
        link = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(body.token)))
        if link:
            pid, prefix = link.project_id, link.token_prefix
    r = AbuseReport(project_id=pid, share_token_prefix=prefix, reason=body.reason, details=body.details)
    db.add(r)
    audit.record(db, "privacy.report_filed", target_type="project", target_id=pid or "", data={"reason": body.reason})
    db.commit()
    return {"id": str(r.id), "status": r.status}


# ------------------------------------------------------------------ signed files

@router.get("/files/{kind}/{obj_id}/{variant}")
def get_file(kind: str, obj_id: str, variant: str, exp: int = Query(...), sig: str = Query(..., max_length=128),
             s: str = Query(default="", max_length=80), db: Session = Depends(get_db)):
    if not check(kind, obj_id, variant, exp, sig, s):
        raise APIError(403, "URL_EXPIRED", "This file link is invalid or has expired.")
    try:
        oid = uuid.UUID(obj_id)
    except ValueError:
        raise not_found("File")
    if kind == "upload" and variant in {"web", "thumb", "original"}:
        up = db.get(Upload, oid)
        if not up or up.status != "stored":
            raise not_found("File")
        project_id = up.project_id
        if s and variant == "original":
            raise not_found("File")  # originals (with EXIF) are never shared publicly
        key = {"web": up.web_key, "thumb": up.thumb_key, "original": up.storage_key}[variant]
        mime = "image/jpeg" if variant != "original" else up.mime
    elif kind == "artifact" and variant == "content":
        a = db.get(Artifact, oid)
        if not a or a.deleted_at:
            raise not_found("File")
        if s and not a.public_safe:
            raise not_found("File")
        project_id, key, mime = a.project_id, a.storage_key, a.mime
    else:
        raise not_found("File")
    p = db.get(Project, project_id)
    if not p or p.status != "active":
        raise not_found("File")
    if s:
        if s.startswith("sample-"):
            if not (p.is_public_sample and s == f"sample-{p.id}"):
                raise not_found("File")
        else:
            link = db.get(ShareLink, uuid.UUID(s)) if _is_uuid(s) else None
            if not link or link.project_id != p.id or link_state(link) != "active":
                raise APIError(410, "LINK_INACTIVE", "This tour link has been revoked or has expired.")
    storage = get_storage()
    size = storage.size(key)
    if size is None:
        raise not_found("File")
    headers = {"Cache-Control": "private, max-age=300", "Content-Length": str(size),
               "Cross-Origin-Resource-Policy": "cross-origin"}
    return StreamingResponse(storage.iter_chunks(key), media_type=mime, headers=headers)


def _is_uuid(v: str) -> bool:
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        return False
