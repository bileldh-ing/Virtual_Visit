from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, mailer
from ..config import get_settings
from ..db import get_db
from ..deps import COOKIE, Principal, client_key, current_principal
from ..errors import APIError
from ..models import AuthSession, EmailToken, Organization, OrganizationMembership, Subscription, User
from ..ratelimit import hit
from ..security import hash_password, new_token, password_problems, token_hash, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def now() -> datetime:
    return datetime.now(timezone.utc)


class SignUp(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=120)
    workspace_name: str | None = Field(default=None, max_length=120)


class SignIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=256)


def user_out(u: User) -> dict:
    return {"id": str(u.id), "email": u.email, "display_name": u.display_name,
            "email_verified": u.email_verified_at is not None, "is_platform_admin": u.is_platform_admin,
            "created_at": u.created_at.isoformat()}


def start_session(db: Session, response: Response, user: User, request: Request) -> str:
    s = get_settings()
    raw = new_token()
    db.add(AuthSession(user_id=user.id, token_hash=token_hash(raw), expires_at=now() + timedelta(hours=s.session_ttl_hours),
                       user_agent=(request.headers.get("user-agent") or "")[:200]))
    response.set_cookie(COOKIE, raw, httponly=True, secure=s.cookie_secure, samesite="lax", path="/",
                        max_age=s.session_ttl_hours * 3600, domain=s.cookie_domain)
    return raw


def issue_email_token(db: Session, user: User, purpose: str, hours: int) -> str:
    raw = new_token()
    db.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=token_hash(raw), expires_at=now() + timedelta(hours=hours)))
    return raw


def send_verification(db: Session, user: User) -> None:
    raw = issue_email_token(db, user, "verify", 48)
    link = f"{get_settings().public_web_url}/verify-email?token={raw}"
    mailer.send(db, to=user.email, user_id=user.id, kind="verify_email", subject="Verify your RoomScape AI email",
                body=f"Confirm your email address to publish tours:\n\n{link}\n\nThis link expires in 48 hours.")


@router.post("/signup", status_code=201)
def signup(body: SignUp, request: Request, response: Response, db: Session = Depends(get_db)):
    hit("signup", client_key(request), 10, 3600)
    problems = password_problems(body.password)
    if problems:
        raise APIError(422, "WEAK_PASSWORD", " ".join(problems), fields={"password": " ".join(problems)})
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise APIError(409, "EMAIL_IN_USE", "An account with this email already exists.")
    s = get_settings()
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name.strip(),
                is_platform_admin=bool(s.bootstrap_admin_email and s.bootstrap_admin_email.lower() == email))
    db.add(user)
    db.flush()
    org = Organization(name=body.workspace_name or f"{body.display_name or email.split('@')[0]}'s workspace")
    db.add(org)
    db.flush()
    db.add(OrganizationMembership(org_id=org.id, user_id=user.id, role="OWNER"))
    db.add(Subscription(org_id=org.id, plan_code="FREE", provider="none"))
    send_verification(db, user)
    start_session(db, response, user, request)
    audit.record(db, "user.signup", actor=user.id, org_id=org.id, target_type="user", target_id=user.id)
    db.commit()
    return {"user": user_out(user), "org_id": str(org.id)}


@router.post("/signin")
def signin(body: SignIn, request: Request, response: Response, db: Session = Depends(get_db)):
    hit("signin", client_key(request), 20, 300)
    hit("signin-email", body.email.lower(), 10, 300)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or user.status != "active" or not verify_password(user.password_hash, body.password):
        raise APIError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    start_session(db, response, user, request)
    audit.record(db, "user.signin", actor=user.id, target_type="user", target_id=user.id)
    db.commit()
    return {"user": user_out(user)}


@router.post("/signout")
def signout(response: Response, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    p.session.revoked_at = now()
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/verify-email")
def verify_email(body: TokenBody, request: Request, db: Session = Depends(get_db)):
    hit("verify", client_key(request), 30, 3600)
    tok = db.scalar(select(EmailToken).where(EmailToken.token_hash == token_hash(body.token), EmailToken.purpose == "verify"))
    if not tok or tok.used_at or (tok.expires_at.replace(tzinfo=tok.expires_at.tzinfo or timezone.utc)) < now():
        raise APIError(400, "INVALID_TOKEN", "This verification link is invalid or has expired.")
    user = db.get(User, tok.user_id)
    tok.used_at = now()
    user.email_verified_at = now()
    audit.record(db, "user.email_verified", actor=user.id, target_type="user", target_id=user.id)
    db.commit()
    return {"ok": True}


@router.post("/resend-verification")
def resend(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    hit("resend", str(p.user.id), 3, 3600)
    if p.user.email_verified_at:
        return {"ok": True, "already_verified": True}
    send_verification(db, p.user)
    db.commit()
    return {"ok": True}


@router.post("/forgot-password")
def forgot(body: ForgotBody, request: Request, db: Session = Depends(get_db)):
    hit("forgot", client_key(request), 5, 3600)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user and user.status == "active":
        raw = issue_email_token(db, user, "reset", 1)
        link = f"{get_settings().public_web_url}/reset-password?token={raw}"
        mailer.send(db, to=user.email, user_id=user.id, kind="password_reset", subject="Reset your RoomScape AI password",
                    body=f"Use this link within one hour to choose a new password:\n\n{link}\n\n"
                         "If you did not ask for this, you can ignore this email.")
        db.commit()
    # Same response whether or not the account exists (no account enumeration).
    return {"ok": True}


@router.post("/reset-password")
def reset(body: ResetBody, request: Request, db: Session = Depends(get_db)):
    hit("reset", client_key(request), 10, 3600)
    tok = db.scalar(select(EmailToken).where(EmailToken.token_hash == token_hash(body.token), EmailToken.purpose == "reset"))
    if not tok or tok.used_at or tok.expires_at.replace(tzinfo=tok.expires_at.tzinfo or timezone.utc) < now():
        raise APIError(400, "INVALID_TOKEN", "This reset link is invalid or has expired.")
    problems = password_problems(body.password)
    if problems:
        raise APIError(422, "WEAK_PASSWORD", " ".join(problems), fields={"password": " ".join(problems)})
    user = db.get(User, tok.user_id)
    user.password_hash = hash_password(body.password)
    tok.used_at = now()
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
        s.revoked_at = now()  # password change signs out every session
    audit.record(db, "user.password_reset", actor=user.id, target_type="user", target_id=user.id)
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def sessions(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == p.user.id, AuthSession.revoked_at.is_(None))
                      .order_by(AuthSession.created_at.desc()))
    return {"sessions": [{"id": str(s.id), "created_at": s.created_at.isoformat(), "last_seen_at": s.last_seen_at.isoformat(),
                          "user_agent": s.user_agent, "current": s.id == p.session.id} for s in rows]}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    import uuid as _uuid

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise APIError(404, "NOT_FOUND", "Session not found.")
    s = db.get(AuthSession, sid)
    if not s or s.user_id != p.user.id:
        raise APIError(404, "NOT_FOUND", "Session not found.")
    s.revoked_at = now()
    audit.record(db, "user.session_revoked", actor=p.user.id, target_type="session", target_id=sid)
    db.commit()
    return {"ok": True}
