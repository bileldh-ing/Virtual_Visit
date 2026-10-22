"""Upload validation and privacy-safe derivatives."""

from __future__ import annotations

import io
import re
import unicodedata

from PIL import Image
from roomscape_recon.quality import analyze_bytes, load_normalized, sniff_mime

from .errors import APIError

EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def display_name(name: str | None) -> str:
    base = unicodedata.normalize("NFKC", (name or "photo").split("/")[-1].split("\\")[-1])
    base = re.sub(r"[^\w.\- ]+", "_", base).strip(" .") or "photo"
    return base[:120]


def validate_image(data: bytes) -> str:
    mime = sniff_mime(data[:16])
    if mime is None:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Only JPEG, PNG or WebP images are accepted.")
    return mime


def derivatives(data: bytes) -> tuple[bytes, bytes, tuple[int, int]]:
    """Return (web JPEG max 2048px, thumbnail JPEG 480px, size). Both are EXIF/GPS free."""
    try:
        img = load_normalized(data)
    except Exception:
        raise APIError(422, "IMAGE_DECODE_FAILED", "The image could not be decoded; it may be corrupt.")
    size = img.size
    web = img.copy()
    web.thumbnail((2048, 2048) if not _is_pano(size) else (4096, 2048), Image.LANCZOS)
    b1 = io.BytesIO()
    web.save(b1, format="JPEG", quality=86, optimize=True, progressive=True)
    thumb = img.copy()
    thumb.thumbnail((480, 480), Image.LANCZOS)
    b2 = io.BytesIO()
    thumb.save(b2, format="JPEG", quality=78)
    return b1.getvalue(), b2.getvalue(), size


def _is_pano(size: tuple[int, int]) -> bool:
    w, h = size
    return abs(w / max(h, 1) - 2.0) <= 0.06 and w >= 2000


def quality(name: str, data: bytes) -> dict:
    rec, _ = analyze_bytes(name, data)
    return rec.to_dict()
