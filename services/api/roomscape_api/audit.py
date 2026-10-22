from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from .models import AuditEvent
from .observability import request_id_var

REDACT = {"password", "token", "secret", "authorization", "address_private"}


def record(db: Session, action: str, *, actor: uuid.UUID | None = None, org_id: uuid.UUID | None = None,
           target_type: str = "", target_id: object = "", data: dict | None = None) -> None:
    clean = {k: ("[redacted]" if k.lower() in REDACT else v) for k, v in (data or {}).items()}
    db.add(AuditEvent(action=action, actor_user_id=actor, org_id=org_id, target_type=target_type,
                      target_id=str(target_id or ""), data=clean, request_id=request_id_var.get("")))
