"""Authentication & authorization dependencies.

Sessions are opaque random tokens in an HttpOnly, SameSite=Lax cookie (or an
``Authorization: Bearer`` header for API clients). Only SHA-256 hashes are
stored. Cookie-authenticated unsafe requests must send ``X-Requested-With:
roomscape`` (custom-header CSRF defence; cross-site forms cannot set it and
CORS is restricted to the configured web origin).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import APIError, forbidden, not_found
from .models import AuthSession, OrganizationMembership, Project, User
from .security import token_hash

COOKIE = "rs_session"
ROLE_RANK = {"VIEWER": 1, "EDITOR": 2, "ADMIN": 3, "OWNER": 4}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    user: User
    session: AuthSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_session(db: Session, request: Request) -> Principal | None:
    raw = None
    via_cookie = False
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
    elif COOKIE in request.cookies:
        raw = request.cookies[COOKIE]
        via_cookie = True
    if not raw:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(raw)))
    if not sess or sess.revoked_at or _aware(sess.expires_at) < _now():
        return None
    user = db.get(User, sess.user_id)
    if not user or user.status != "active":
        return None
    if via_cookie and request.method in UNSAFE and request.headers.get("x-requested-with") != "roomscape":
        raise APIError(403, "CSRF_CHECK_FAILED", "Missing anti-CSRF header.")
    if _aware(sess.last_seen_at) < _now() - timedelta(minutes=5):
        sess.last_seen_at = _now()
        db.commit()
    return Principal(user, sess)


def optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    return resolve_session(db, request)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve_session(db, request)
    if p is None:
        raise APIError(401, "UNAUTHENTICATED", "Please sign in.")
    return p


def current_user(p: Principal = Depends(current_principal)) -> User:
    return p.user


def platform_admin(user: User = Depends(current_user)) -> User:
    if not user.is_platform_admin:
        raise forbidden("Platform administrators only.")
    return user


def membership(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership | None:
    return db.scalar(select(OrganizationMembership).where(OrganizationMembership.org_id == org_id,
                                                          OrganizationMembership.user_id == user_id))


def require_org_role(db: Session, org_id: uuid.UUID, user: User, min_role: str) -> OrganizationMembership:
    m = membership(db, org_id, user.id)
    if m is None:
        raise not_found("Workspace")
    if ROLE_RANK[m.role] < ROLE_RANK[min_role]:
        raise forbidden()
    return m


def can(role: str, min_role: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[min_role]


def load_project(db: Session, project_id: uuid.UUID, user: User, min_role: str = "VIEWER",
                 allow_deleting: bool = False) -> tuple[Project, OrganizationMembership]:
    """Fetch a project the user may access. Non-members get 404 (no ID probing)."""
    proj = db.get(Project, project_id)
    if proj is None or proj.status == "deleted" or (proj.status == "deleting" and not allow_deleting):
        raise not_found("Project")
    m = membership(db, proj.org_id, user.id)
    if m is None:
        raise not_found("Project")
    if ROLE_RANK[m.role] < ROLE_RANK[min_role]:
        raise forbidden()
    return proj, m


def client_key(request: Request) -> str:
    # Used only for rate-limit buckets (kept in Redis for one window), never persisted.
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd and get_settings().env != "development" else "") or (
        request.client.host if request.client else "unknown")
