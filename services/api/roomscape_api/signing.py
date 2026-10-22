"""Short-lived HMAC-signed file URLs served by the API.

A URL is a capability for one file variant until ``exp``. It is only minted
after an authorization check. If it was minted for a share link (``s=``), the
link's revocation/expiry is re-checked on every retrieval, so revoking a link
stops new retrievals immediately (browser caches may still hold bytes already
downloaded; responses are ``Cache-Control: private``).
"""

from __future__ import annotations

import math
import time
import uuid

from .config import get_settings
from .security import sign, verify_sig

WINDOW = 300


def _msg(kind: str, obj_id: str, variant: str, exp: int, share: str) -> str:
    return f"{kind}:{obj_id}:{variant}:{exp}:{share}"


def file_url(kind: str, obj_id: uuid.UUID | str, variant: str = "content", share_id: uuid.UUID | str | None = None,
             ttl: int | None = None) -> str:
    s = get_settings()
    ttl = ttl or s.signed_url_ttl_s
    # Round expiry to a window so URLs are stable (cacheable) for a few minutes.
    exp = int(math.ceil(time.time() / WINDOW) * WINDOW) + ttl
    share = str(share_id) if share_id else ""
    sig = sign(s.secret_key, _msg(kind, str(obj_id), variant, exp, share))
    q = f"exp={exp}&sig={sig}" + (f"&s={share}" if share else "")
    return f"/api/v1/files/{kind}/{obj_id}/{variant}?{q}"


def check(kind: str, obj_id: str, variant: str, exp: int, sig: str, share: str) -> bool:
    if exp < time.time():
        return False
    return verify_sig(get_settings().secret_key, _msg(kind, obj_id, variant, exp, share), sig)
