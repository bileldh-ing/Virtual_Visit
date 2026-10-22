"""Transactional email. Every message is persisted in ``notifications``; it is
also sent over SMTP when ROOMSCAPE_SMTP_URL is configured. Without SMTP the
message stays in the outbox (visible to the user in-app and via the dev outbox
endpoint in development/test). We never claim an email was delivered unless
SMTP accepted it."""

from __future__ import annotations

import logging
import smtplib
import uuid
from email.message import EmailMessage
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .config import get_settings
from .models import Notification

log = logging.getLogger("roomscape.mail")


def send(db: Session, *, to: str, user_id: uuid.UUID | None, kind: str, subject: str, body: str) -> Notification:
    s = get_settings()
    n = Notification(user_id=user_id, to_email=to, kind=kind, subject=subject, body=body, delivered_via="outbox")
    if s.smtp_url:
        try:
            u = urlparse(s.smtp_url)
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = s.mail_from, to, subject
            msg.set_content(body)
            with smtplib.SMTP(u.hostname, u.port or 587, timeout=10) as smtp:
                smtp.starttls()
                if u.username:
                    smtp.login(u.username, u.password or "")
                smtp.send_message(msg)
            n.delivered_via = "smtp"
        except Exception as exc:  # pragma: no cover - needs SMTP
            log.warning(f"smtp delivery failed: {type(exc).__name__}")
            n.delivered_via = "failed"
    db.add(n)
    return n
