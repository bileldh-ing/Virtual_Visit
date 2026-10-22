"""Password hashing (argon2id via argon2-cffi) and opaque token helpers.

We do not implement cryptography ourselves: passwords use the maintained
argon2-cffi library; tokens are ``secrets.token_urlsafe`` values of which only
a SHA-256 hash is stored server side.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_sig(secret: str, message: str, sig: str) -> bool:
    return hmac.compare_digest(sign(secret, message), sig)


PASSWORD_MIN = 10


def password_problems(pw: str) -> list[str]:
    problems = []
    if len(pw) < PASSWORD_MIN:
        problems.append(f"Use at least {PASSWORD_MIN} characters.")
    if len(pw) > 256:
        problems.append("Password is too long.")
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        problems.append("Mix upper and lower case letters and at least one digit.")
    return problems
