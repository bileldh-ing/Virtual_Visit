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


def validate_image(data: bytes, max_pixels: int = 100_000_000, min_side: int = 256) -> str:
    """Server-side input check: real format from magic bytes (not the extension or Content-Type), then the
    header-declared size before anything is decoded."""
    mime = sniff_mime(data[:16])
    if mime is None:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Only JPEG, PNG or WebP images are accepted.")
    try:
        with Image.open(io.BytesIO(data)) as im:  # lazy: reads the header only
            w, h = im.size
            fmt = (im.format or "").upper()
    except Exception:
        raise APIError(422, "IMAGE_DECODE_FAILED", "The image could not be decoded; it may be corrupt.")
    if fmt not in {"JPEG", "PNG", "WEBP"} or {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt] != mime:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Only JPEG, PNG or WebP images are accepted.")
    if w * h > max_pixels:
        raise APIError(413, "IMAGE_TOO_LARGE", f"The image is {w}×{h}; at most {max_pixels // 1_000_000} megapixels are accepted.")
    if min(w, h) < min_side:
        raise APIError(422, "IMAGE_TOO_SMALL", f"The image is {w}×{h}; the shorter side must be at least {min_side} px.")
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
