"""Plans, usage ledger and the payment-provider boundary (Stripe).

Without Stripe credentials the app runs in *test mode*: plan changes are
recorded with ``provider='test'`` and never charge money. The Stripe webhook
verifies signatures (documented scheme: HMAC-SHA256 over ``{t}.{payload}``,
5-minute tolerance) and is idempotent on the provider event id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..deps import current_user, require_org_role
from ..errors import APIError
from ..models import BillingEvent, Project, Subscription, Upload, UsageLedger, User
from ..plans import PLAN_VERSION, PLANS, USAGE_POLICY, plan

router = APIRouter(prefix="/api/v1", tags=["billing"])


def now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/billing/plans")
def plans():
    s = get_settings()
    return {"version": PLAN_VERSION, "plans": PLANS, "usage_policy": USAGE_POLICY,
            "mode": "stripe" if s.billing_mode == "stripe" and s.stripe_secret_key else "test"}


@router.get("/usage")
def usage(org_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_org_role(db, org_id, user, "VIEWER")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    pl = plan(sub.plan_code if sub else "FREE")
    month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    minutes = db.scalar(select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.org_id == org_id, UsageLedger.kind == "reconstruction_minutes", UsageLedger.created_at >= month_start)) or 0
    storage = db.scalar(select(func.coalesce(func.sum(Upload.bytes), 0)).select_from(Upload).join(Project, Project.id == Upload.project_id)
                        .where(Project.org_id == org_id, Upload.status == "stored")) or 0
    projects = db.scalar(select(func.count()).select_from(Project).where(Project.org_id == org_id, Project.status == "active"))
    ledger = db.scalars(select(UsageLedger).where(UsageLedger.org_id == org_id).order_by(UsageLedger.created_at.desc()).limit(50)).all()
    return {
        "plan": {"code": sub.plan_code if sub else "FREE", **pl, "status": sub.status if sub else "active",
                 "provider": sub.provider if sub else "none",
                 "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None},
        "period_start": month_start.isoformat(),
        "usage": {"reconstruction_minutes": float(minutes), "photo_storage_bytes": int(storage), "active_projects": projects},
        "ledger": [{"id": str(e.id), "kind": e.kind, "quantity": e.quantity, "unit": e.unit, "note": e.note,
                    "job_id": str(e.job_id) if e.job_id else None, "created_at": e.created_at.isoformat()} for e in ledger],
        "policy": USAGE_POLICY,
    }


class Checkout(BaseModel):
    org_id: uuid.UUID
    plan_code: str = Field(pattern="^(FREE|PRO|AGENCY)$")


@router.post("/billing/checkout")
def checkout(body: Checkout, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    require_org_role(db, body.org_id, user, "ADMIN")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == body.org_id))
    if s.billing_mode == "stripe":
        if not s.stripe_secret_key:
            raise APIError(503, "BILLING_NOT_CONFIGURED", "Payments are not configured for this deployment.")
        price = {"PRO": s.stripe_price_pro, "AGENCY": s.stripe_price_agency}.get(body.plan_code)
        if not price:
            raise APIError(422, "INVALID_PLAN", "This plan cannot be purchased online.")
        resp = httpx.post("https://api.stripe.com/v1/checkout/sessions", auth=(s.stripe_secret_key, ""), timeout=15, data={
            "mode": "subscription", "line_items[0][price]": price, "line_items[0][quantity]": "1",
            "success_url": f"{s.public_web_url}/app/billing?status=success",
            "cancel_url": f"{s.public_web_url}/app/billing?status=cancelled",
            "client_reference_id": str(body.org_id), "metadata[org_id]": str(body.org_id), "metadata[plan_code]": body.plan_code})
        if resp.status_code >= 400:
            raise APIError(502, "PROVIDER_ERROR", "The payment provider rejected the request.", retryable=True)
        audit.record(db, "billing.checkout_started", actor=user.id, org_id=body.org_id, data={"plan": body.plan_code})
        db.commit()
        return {"mode": "stripe", "checkout_url": resp.json()["url"]}
    # Test mode: no money moves. Clearly labelled in the UI.
    old = sub.plan_code
    sub.plan_code, sub.provider, sub.status, sub.plan_version = body.plan_code, "test", "active", PLAN_VERSION
    audit.record(db, "billing.plan_changed_test_mode", actor=user.id, org_id=body.org_id, data={"from": old, "to": body.plan_code})
    db.commit()
    return {"mode": "test", "plan_code": sub.plan_code, "note": "Test mode: no payment was taken."}


def verify_stripe_signature(payload: bytes, header: str, secret: str, tolerance: int = 300) -> None:
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts = int(parts["t"])
        sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    except (KeyError, ValueError):
        raise APIError(400, "BAD_SIGNATURE", "Invalid signature header.")
    if abs(time.time() - ts) > tolerance:
        raise APIError(400, "BAD_SIGNATURE", "Signature timestamp outside tolerance.")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s_) for s_ in sigs):
        raise APIError(400, "BAD_SIGNATURE", "Signature mismatch.")


STATUS_MAP = {"active": "active", "trialing": "active", "past_due": "past_due", "unpaid": "past_due",
              "canceled": "canceled", "incomplete_expired": "expired"}


@router.post("/billing/webhook")
async def webhook(request: Request, stripe_signature: str | None = Header(default=None), db: Session = Depends(get_db)):
    s = get_settings()
    if not s.stripe_webhook_secret:
        raise APIError(503, "BILLING_NOT_CONFIGURED", "Webhook secret not configured.")
    payload = await request.body()
    verify_stripe_signature(payload, stripe_signature or "", s.stripe_webhook_secret)
    event = json.loads(payload)
    created = datetime.fromtimestamp(event.get("created", 0), tz=timezone.utc)
    rec = BillingEvent(provider="stripe", provider_event_id=event["id"], type=event.get("type", ""), event_created=created,
                       outcome="processing")
    db.add(rec)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}  # idempotent on event id
    obj = event.get("data", {}).get("object", {})
    org_id = (obj.get("metadata") or {}).get("org_id") or obj.get("client_reference_id")
    sub = None
    if org_id:
        try:
            sub = db.scalar(select(Subscription).where(Subscription.org_id == uuid.UUID(org_id)))
        except ValueError:
            sub = None
    if sub is None and obj.get("customer"):
        sub = db.scalar(select(Subscription).where(Subscription.provider_customer_id == obj["customer"]))
    if sub is None:
        rec.outcome = "ignored_unknown_org"
        db.commit()
        return {"ok": True}
    last = sub.last_event_at.replace(tzinfo=timezone.utc) if sub.last_event_at and sub.last_event_at.tzinfo is None else sub.last_event_at
    if last and created < last:
        rec.outcome = "ignored_out_of_order"
        db.commit()
        return {"ok": True}
    etype = event.get("type", "")
    if etype == "checkout.session.completed":
        sub.provider, sub.provider_customer_id = "stripe", obj.get("customer")
        sub.provider_subscription_id = obj.get("subscription")
        sub.plan_code = (obj.get("metadata") or {}).get("plan_code", sub.plan_code)
        sub.status = "active"
    elif etype in {"customer.subscription.updated", "customer.subscription.created"}:
        sub.status = STATUS_MAP.get(obj.get("status", ""), sub.status)
        if obj.get("current_period_end"):
            sub.current_period_end = datetime.fromtimestamp(obj["current_period_end"], tz=timezone.utc)
    elif etype == "customer.subscription.deleted":
        sub.status, sub.plan_code = "canceled", "FREE"
    elif etype == "invoice.payment_failed":
        sub.status = "past_due"
    else:
        rec.outcome = "ignored_type"
        db.commit()
        return {"ok": True}
    sub.last_event_at = created
    rec.outcome = "applied"
    audit.record(db, "billing.webhook_applied", org_id=sub.org_id, data={"type": etype})
    db.commit()
    return {"ok": True}
