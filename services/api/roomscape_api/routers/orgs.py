"""Account (me) and workspace/team management."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, mailer, taskqueue
from ..config import get_settings
from ..db import get_db
from ..deps import ROLE_RANK, Principal, current_principal, current_user, require_org_role
from ..errors import APIError, not_found
from ..models import (
    AuditEvent,
    AuthSession,
    DeletionRequest,
    Invitation,
    Notification,
    Organization,
    OrganizationMembership,
    Project,
    Subscription,
    User,
)
from ..plans import plan
from ..ratelimit import hit
from ..security import new_token, token_hash, verify_password
from .auth import user_out

router = APIRouter(prefix="/api/v1", tags=["account", "workspaces"])


def now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(OrganizationMembership, Organization).join(Organization,
                      Organization.id == OrganizationMembership.org_id).where(OrganizationMembership.user_id == user.id)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id,
                                                                             Notification.read_at.is_(None)))
    return {"user": user_out(user), "unread_notifications": unread,
            "workspaces": [{"id": str(o.id), "name": o.name, "role": m.role, "is_personal": o.is_personal} for m, o in rows]}


class MePatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)


@router.patch("/me")
def patch_me(body: MePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    db.commit()
    return {"user": user_out(user)}


@router.get("/me/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50))
    return {"notifications": [{"id": str(n.id), "kind": n.kind, "subject": n.subject, "body": n.body,
                               "delivered_via": n.delivered_via, "created_at": n.created_at.isoformat(),
                               "read": n.read_at is not None} for n in rows]}


@router.get("/me/export")
def export(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Machine-readable export of the account's own records (not binary media)."""
    mems = db.scalars(select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)).all()
    org_ids = [m.org_id for m in mems]
    projects = db.scalars(select(Project).where(Project.org_id.in_(org_ids), Project.status != "deleted")).all() if org_ids else []
    events = db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == user.id).order_by(AuditEvent.created_at.desc()).limit(500)).all()
    return {"exported_at": now().isoformat(), "user": user_out(user),
            "memberships": [{"org_id": str(m.org_id), "role": m.role} for m in mems],
            "projects": [{"id": str(p.id), "title": p.title, "created_at": p.created_at.isoformat()} for p in projects],
            "audit_events": [{"action": e.action, "at": e.created_at.isoformat(), "target": e.target_type} for e in events],
            "note": "Photos and reconstruction files are downloadable per project; they are not embedded here."}


class DeleteAccount(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirm: str


@router.post("/me/delete")
def delete_account(body: DeleteAccount, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = p.user
    hit("delete-account", str(user.id), 5, 3600)
    if body.confirm != "DELETE" or not verify_password(user.password_hash, body.password):
        raise APIError(403, "CONFIRMATION_FAILED", "Type DELETE and enter your current password to confirm.")
    # Workspaces where the user is the only owner are deleted with all projects.
    mems = db.scalars(select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)).all()
    from .projects import begin_project_deletion

    for m in mems:
        owners = db.scalar(select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.org_id == m.org_id, OrganizationMembership.role == "OWNER"))
        if m.role == "OWNER" and owners == 1:
            for proj in db.scalars(select(Project).where(Project.org_id == m.org_id, Project.status == "active")):
                begin_project_deletion(db, proj, user)
        db.delete(m)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        s.revoked_at = now()
    db.add(DeletionRequest(kind="account", user_id=user.id, requested_by=user.id, status="done", completed_at=now(),
                           verified_empty=True))
    audit.record(db, "user.deleted", actor=user.id, target_type="user", target_id=user.id)
    user.status = "deleted"
    user.deleted_at = now()
    user.email = f"deleted+{user.id.hex}@invalid.local"
    user.password_hash = None
    user.display_name = "Deleted user"
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ workspaces

def org_out(db: Session, org: Organization, role: str) -> dict:
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
    return {"id": str(org.id), "name": org.name, "role": role, "retention_days": org.retention_days,
            "default_share_visibility": org.default_share_visibility,
            "plan": sub.plan_code if sub else "FREE", "subscription_status": sub.status if sub else "active"}


@router.get("/orgs/{org_id}")
def get_org(org_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_org_role(db, org_id, user, "VIEWER")
    return org_out(db, db.get(Organization, org_id), m.role)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    retention_days: int | None = Field(default=None, ge=7, le=3650)
    default_share_visibility: str | None = Field(default=None, pattern="^(UNLISTED|RESTRICTED)$")


@router.patch("/orgs/{org_id}")
def patch_org(org_id: uuid.UUID, body: OrgPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_org_role(db, org_id, user, "ADMIN")
    org = db.get(Organization, org_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(org, k, v)
    audit.record(db, "org.updated", actor=user.id, org_id=org_id, target_type="org", target_id=org_id, data=changes)
    db.commit()
    return org_out(db, org, m.role)


@router.get("/orgs/{org_id}/members")
def members(org_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_org_role(db, org_id, user, "VIEWER")
    rows = db.scalars(select(OrganizationMembership).where(OrganizationMembership.org_id == org_id)).all()
    invites = db.scalars(select(Invitation).where(Invitation.org_id == org_id, Invitation.accepted_at.is_(None),
                                                  Invitation.revoked_at.is_(None))).all()
    return {"members": [{"id": str(m.id), "user_id": str(m.user_id), "email": m.user.email, "display_name": m.user.display_name,
                         "role": m.role, "joined_at": m.created_at.isoformat()} for m in rows],
            "invitations": [{"id": str(i.id), "email": i.email, "role": i.role, "expires_at": i.expires_at.isoformat()} for i in invites]}


class InviteBody(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(ADMIN|EDITOR|VIEWER)$")


@router.post("/orgs/{org_id}/invitations", status_code=201)
def invite(org_id: uuid.UUID, body: InviteBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_org_role(db, org_id, user, "ADMIN")
    hit("invite", str(user.id), 30, 3600)
    org = db.get(Organization, org_id)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    seats = db.scalar(select(func.count()).select_from(OrganizationMembership).where(OrganizationMembership.org_id == org_id))
    if seats >= plan(sub.plan_code if sub else "FREE")["seats"]:
        raise APIError(402, "SEAT_LIMIT", "Your plan has no free seats. Upgrade to invite teammates.")
    raw = new_token()
    inv = Invitation(org_id=org_id, email=body.email.lower(), role=body.role, token_hash=token_hash(raw),
                     invited_by=user.id, expires_at=now() + timedelta(days=7))
    db.add(inv)
    existing = db.scalar(select(User).where(User.email == body.email.lower()))
    mailer.send(db, to=body.email.lower(), user_id=existing.id if existing else None, kind="invitation",
                subject=f"You're invited to {org.name} on RoomScape AI",
                body=f"{user.display_name or user.email} invited you as {body.role}.\n\n"
                     f"{get_settings().public_web_url}/invite?token={raw}\n\nThis invitation expires in 7 days.")
    audit.record(db, "org.member_invited", actor=user.id, org_id=org_id, target_type="invitation", data={"role": body.role})
    db.commit()
    return {"id": str(inv.id), "email": inv.email, "role": inv.role}


class AcceptBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/invitations/accept")
def accept(body: AcceptBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(body.token)))
    if not inv or inv.accepted_at or inv.revoked_at or inv.expires_at.replace(tzinfo=inv.expires_at.tzinfo or timezone.utc) < now():
        raise APIError(400, "INVALID_TOKEN", "This invitation is invalid or has expired.")
    if inv.email != user.email:
        raise APIError(403, "WRONG_ACCOUNT", "This invitation was sent to a different email address.")
    if not db.scalar(select(OrganizationMembership).where(OrganizationMembership.org_id == inv.org_id,
                                                          OrganizationMembership.user_id == user.id)):
        db.add(OrganizationMembership(org_id=inv.org_id, user_id=user.id, role=inv.role))
    inv.accepted_at = now()
    audit.record(db, "org.member_joined", actor=user.id, org_id=inv.org_id, target_type="user", target_id=user.id,
                 data={"role": inv.role})
    db.commit()
    return {"org_id": str(inv.org_id)}


class RoleBody(BaseModel):
    role: str = Field(pattern="^(OWNER|ADMIN|EDITOR|VIEWER)$")


@router.patch("/orgs/{org_id}/members/{membership_id}")
def change_role(org_id: uuid.UUID, membership_id: uuid.UUID, body: RoleBody, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    me_m = require_org_role(db, org_id, user, "ADMIN")
    target = db.get(OrganizationMembership, membership_id)
    if not target or target.org_id != org_id:
        raise not_found("Member")
    # Only owners may grant/revoke OWNER; admins cannot change owners.
    if (body.role == "OWNER" or target.role == "OWNER") and me_m.role != "OWNER":
        raise APIError(403, "FORBIDDEN", "Only an owner can change owner roles.")
    if target.role == "OWNER" and body.role != "OWNER":
        owners = db.scalar(select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.org_id == org_id, OrganizationMembership.role == "OWNER"))
        if owners <= 1:
            raise APIError(409, "LAST_OWNER", "A workspace needs at least one owner.")
    old = target.role
    target.role = body.role
    audit.record(db, "org.role_changed", actor=user.id, org_id=org_id, target_type="user", target_id=target.user_id,
                 data={"from": old, "to": body.role})
    db.commit()
    return {"ok": True}


@router.delete("/orgs/{org_id}/members/{membership_id}")
def remove_member(org_id: uuid.UUID, membership_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me_m = require_org_role(db, org_id, user, "ADMIN")
    target = db.get(OrganizationMembership, membership_id)
    if not target or target.org_id != org_id:
        raise not_found("Member")
    if ROLE_RANK[target.role] >= ROLE_RANK[me_m.role] and target.user_id != user.id:
        raise APIError(403, "FORBIDDEN", "You cannot remove a member with an equal or higher role.")
    if target.role == "OWNER":
        owners = db.scalar(select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.org_id == org_id, OrganizationMembership.role == "OWNER"))
        if owners <= 1:
            raise APIError(409, "LAST_OWNER", "A workspace needs at least one owner.")
    db.delete(target)
    audit.record(db, "org.member_removed", actor=user.id, org_id=org_id, target_type="user", target_id=target.user_id)
    db.commit()
    return {"ok": True}


@router.get("/orgs/{org_id}/audit")
def org_audit(org_id: uuid.UUID, limit: int = 50, offset: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_org_role(db, org_id, user, "ADMIN")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.created_at.desc())
                      .limit(min(limit, 200)).offset(offset)).all()
    return {"events": [{"id": str(e.id), "action": e.action, "actor": str(e.actor_user_id) if e.actor_user_id else None,
                        "target_type": e.target_type, "target_id": e.target_id, "data": e.data,
                        "created_at": e.created_at.isoformat()} for e in rows]}


_ = taskqueue  # imported for side-effect free typing of optional cleanup
