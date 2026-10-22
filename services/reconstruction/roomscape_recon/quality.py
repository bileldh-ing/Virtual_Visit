"""Capture-quality analysis for uploaded photos.

All numbers produced here are *measured* heuristics (Laplacian variance, mean
luminance, clipped-pixel fractions, hashes, ORB match counts). They indicate
risk; they do not predict reconstruction success with certainty, and the report
says so.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # OpenCV is optional; overlap estimation is skipped (and reported) without it.
    import cv2  # type: ignore

    HAVE_CV2 = True
except Exception:  # pragma: no cover - depends on environment
    cv2 = None
    HAVE_CV2 = False

# Decompression-bomb guard: refuse anything above ~120 MP.
MAX_PIXELS = 120_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

SUPPORTED_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}

MIN_SIDE = 480
BLUR_THRESHOLD = 30.0  # variance of Laplacian on a contrast-normalised 1024px-long-side grey image
DARK_MEAN = 45.0
BRIGHT_MEAN = 215.0
CLIP_FRACTION = 0.25
NEAR_DUP_HAMMING = 4
PANO_ASPECT = 2.0
PANO_TOLERANCE = 0.03
MIN_IMAGES_PHOTOGRAMMETRY = 8  # guidance floor, not a guarantee
MIN_ORB_MATCHES = 25


def sniff_mime(head: bytes) -> str | None:
    """Detect the content type from magic bytes (never from the filename)."""
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in SUPPORTED_MAGIC.items():
        if head.startswith(magic):
            return mime
    return None


@dataclass
class ImageIssue:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    fix: str


@dataclass
class ImageQuality:
    name: str
    sha256: str
    mime: str | None
    width: int = 0
    height: int = 0
    decoded: bool = False
    blur_score: float | None = None
    mean_luminance: float | None = None
    dark_fraction: float | None = None
    bright_fraction: float | None = None
    dhash: str | None = None
    is_panorama: bool = False
    orb_keypoints: int | None = None
    best_match_count: int | None = None
    issues: list[ImageIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _dhash(gray: np.ndarray, size: int = 8) -> str:
    img = Image.fromarray(gray).resize((size + 1, size), Image.BILINEAR)
    a = np.asarray(img, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return f"{value:016x}"


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _laplacian_variance(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = (
        -4 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def load_normalized(data: bytes) -> Image.Image:
    """Decode bytes, verify integrity and apply EXIF orientation safely."""
    with Image.open(io.BytesIO(data)) as probe:
        probe.verify()  # detects truncated / malformed files
    img = Image.open(io.BytesIO(data))
    if img.width * img.height > MAX_PIXELS:
        raise ValueError("image too large")
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def analyze_bytes(name: str, data: bytes) -> tuple[ImageQuality, np.ndarray | None]:
    """Analyse one image. Returns the quality record and a small grey array."""
    q = ImageQuality(name=name, sha256=hashlib.sha256(data).hexdigest(), mime=sniff_mime(data[:16]))
    if q.mime is None:
        q.issues.append(
            ImageIssue("UNSUPPORTED_FORMAT", "error", "File is not a supported image (JPEG, PNG or WebP).",
                       "Export the photo as JPEG and upload it again.")
        )
        return q, None
    try:
        img = load_normalized(data)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        q.issues.append(
            ImageIssue("DECODE_FAILED", "error", "The image could not be decoded; it may be corrupt or truncated.",
                       "Re-export or re-capture this photo.")
        )
        return q, None
    q.decoded = True
    q.width, q.height = img.size
    aspect = q.width / max(q.height, 1)
    q.is_panorama = abs(aspect - PANO_ASPECT) <= PANO_TOLERANCE * PANO_ASPECT and q.width >= 2000

    scale = 1024 / max(q.width, q.height)
    small = img.resize((max(1, int(q.width * scale)), max(1, int(q.height * scale))), Image.BILINEAR) if scale < 1 else img
    gray = np.asarray(small.convert("L"))
    # Normalise contrast (1st..99th percentile) so dim-but-sharp photos are not flagged as blurry.
    lo, hi = np.percentile(gray, 1), np.percentile(gray, 99)
    norm = np.clip((gray.astype(np.float32) - lo) / max(float(hi - lo), 64.0) * 255.0, 0, 255)
    q.blur_score = round(_laplacian_variance(norm), 2)
    q.mean_luminance = round(float(gray.mean()), 2)
    q.dark_fraction = round(float((gray < 16).mean()), 4)
    q.bright_fraction = round(float((gray > 245).mean()), 4)
    q.dhash = _dhash(gray)

    if min(q.width, q.height) < MIN_SIDE:
        q.issues.append(ImageIssue("LOW_RESOLUTION", "warning",
                                   f"Resolution {q.width}x{q.height} is low; fewer features can be matched.",
                                   "Use the camera's full resolution."))
    if not q.is_panorama and q.blur_score < BLUR_THRESHOLD:
        q.issues.append(ImageIssue("BLURRY", "warning",
                                   "The photo looks soft or motion-blurred (low edge contrast).",
                                   "Hold the camera steady or increase light; retake this view."))
    if q.mean_luminance < DARK_MEAN or q.dark_fraction > CLIP_FRACTION:
        q.issues.append(ImageIssue("UNDEREXPOSED", "warning",
                                   "The photo is very dark; dark regions rarely produce matchable features.",
                                   "Turn on lights or open curtains and retake."))
    if q.mean_luminance > BRIGHT_MEAN or q.bright_fraction > CLIP_FRACTION:
        q.issues.append(ImageIssue("OVEREXPOSED", "warning",
                                   "Large areas are blown out (pure white).",
                                   "Avoid shooting straight into windows; lower exposure."))
    return q, gray


def _orb_overlap(grays: dict[str, np.ndarray], records: dict[str, ImageQuality]) -> None:
    """Estimate pairwise overlap with ORB + ratio test. Measured, approximate."""
    if not HAVE_CV2 or len(grays) < 2:
        return
    orb = cv2.ORB_create(nfeatures=1000)
    feats = {}
    for name, g in grays.items():
        small = cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2)) if max(g.shape) > 800 else g
        kp, des = orb.detectAndCompute(small, None)
        records[name].orb_keypoints = len(kp)
        if des is not None and len(kp) >= 10:
            feats[name] = des
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    names = list(feats)
    best = {n: 0 for n in records}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                pairs = matcher.knnMatch(feats[a], feats[b], k=2)
            except cv2.error:
                continue
            good = sum(1 for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance)
            best[a] = max(best[a], good)
            best[b] = max(best[b], good)
    for n, rec in records.items():
        if rec.decoded and not rec.is_panorama:
            rec.best_match_count = best.get(n, 0)
            if rec.best_match_count < MIN_ORB_MATCHES:
                rec.issues.append(ImageIssue(
                    "LOW_OVERLAP", "warning",
                    "Few features in this photo match any other photo; it may not register.",
                    "Add photos taken between this view and its neighbours with ~60% overlap."))
            if (rec.orb_keypoints or 0) < 150:
                rec.issues.append(ImageIssue(
                    "LOW_TEXTURE", "warning",
                    "Few distinctive features (blank walls, glare or darkness).",
                    "Include furniture, frames or edges in frame; avoid blank walls only."))


STATUS_READY = "READY"
STATUS_READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
STATUS_NEEDS_MORE_IMAGES = "NEEDS_MORE_IMAGES"
STATUS_UNSUPPORTED = "UNSUPPORTED_INPUT"
STATUS_FAILED = "ANALYSIS_FAILED"


def build_report(items: Iterable[tuple[str, bytes]]) -> dict:
    """Analyse a set of images and return a JSON-serialisable capture report."""
    records: dict[str, ImageQuality] = {}
    grays: dict[str, np.ndarray] = {}
    try:
        for name, data in items:
            rec, gray = analyze_bytes(name, data)
            records[name] = rec
            if gray is not None:
                grays[name] = gray
        # exact duplicates
        by_hash: dict[str, str] = {}
        for rec in records.values():
            if rec.sha256 in by_hash:
                rec.issues.append(ImageIssue("DUPLICATE", "warning",
                                             f"Identical to {by_hash[rec.sha256]}.",
                                             "Remove one copy; duplicates add no information."))
            else:
                by_hash[rec.sha256] = rec.name
        # near duplicates (perceptual)
        decoded = [r for r in records.values() if r.dhash]
        for i, a in enumerate(decoded):
            for b in decoded[i + 1:]:
                if a.sha256 != b.sha256 and _hamming(a.dhash, b.dhash) <= NEAR_DUP_HAMMING:
                    b.issues.append(ImageIssue("NEAR_DUPLICATE", "info",
                                               f"Almost identical to {a.name} (perceptual hash).",
                                               "Fine to keep, but moving between shots adds parallax."))
        _orb_overlap(grays, records)
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": STATUS_FAILED, "summary": {"error": type(exc).__name__}, "images": []}

    decoded_n = sum(1 for r in records.values() if r.decoded)
    pano_n = sum(1 for r in records.values() if r.is_panorama)
    errors = sum(1 for r in records.values() for i in r.issues if i.severity == "error")
    warnings = sum(1 for r in records.values() for i in r.issues if i.severity == "warning")
    perspective_n = decoded_n - pano_n

    recommended_mode = None
    if pano_n and pano_n >= perspective_n:
        recommended_mode = "PANORAMA_TOUR"
    elif perspective_n:
        recommended_mode = "PHOTOGRAMMETRY"

    if decoded_n == 0:
        status = STATUS_UNSUPPORTED
    elif recommended_mode == "PHOTOGRAMMETRY" and perspective_n < MIN_IMAGES_PHOTOGRAMMETRY:
        status = STATUS_NEEDS_MORE_IMAGES
    elif warnings or errors:
        status = STATUS_READY_WITH_WARNINGS
    else:
        status = STATUS_READY

    return {
        "status": status,
        "recommended_mode": recommended_mode,
        "summary": {
            "uploaded": len(records),
            "decoded": decoded_n,
            "panoramas": pano_n,
            "perspective": perspective_n,
            "errors": errors,
            "warnings": warnings,
            "overlap_check": "orb" if HAVE_CV2 else "unavailable",
            "guidance_min_images": MIN_IMAGES_PHOTOGRAMMETRY,
        },
        "disclaimer": (
            "These checks are heuristics measured on your photos. They flag risk but cannot "
            "guarantee that reconstruction will succeed or fail."
        ),
        "images": [r.to_dict() for r in records.values()],
    }


def build_report_from_dir(path: Path) -> dict:
    files = sorted(p for p in path.iterdir() if p.is_file())
    return build_report((p.name, p.read_bytes()) for p in files)
