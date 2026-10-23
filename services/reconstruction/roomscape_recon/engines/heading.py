"""Align photo azimuths to a generated world's panorama.

With Auto Layout (``reconstruct_images``) Marble chooses its own world frame, so the azimuths we send are relative
hints and azimuth 0 does not have to face the world's forward axis. Seen on the 8-photo Eyeful set: every photo sat
~60° away from where its content is in the world. This module estimates one constant heading offset so that
``pano_longitude ≈ azimuth + offset`` for all photos at once, by comparing each photo with perspective crops of the
equirectangular panorama the provider returns. No provider call, no credits.

Conventions: azimuth and panorama longitude are degrees, clockwise (to the right) positive. Longitude 0 is the
panorama's centre column, which is the world's forward axis (the viewer's yaw 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

STEP_DEG = 3
VFOVS = (75.0, 90.0, 100.0)
PITCHES = (0.0, 12.0)
MIN_PEAK_Z = 1.4  # peak must stand this many std devs above the mean of the joint score curve
MIN_MARGIN = 0.25  # and clearly beat every heading more than 45° away


@dataclass
class HeadingAlignment:
    offset_deg: float | None
    peak_z: float
    photos: int
    margin: float = 0.0  # joint z-score gap between the best heading and the best one >45° away
    method: str = "joint pano/photo match (Lab + gradient NCC), constant heading offset"

    @property
    def confident(self) -> bool:
        return self.offset_deg is not None and self.peak_z >= MIN_PEAK_Z and self.margin >= MIN_MARGIN

    def as_dict(self) -> dict:
        return {"offset_deg": self.offset_deg if self.confident else None, "peak_z": round(self.peak_z, 2), "margin": round(self.margin, 2),
                "photos": self.photos, "confident": self.confident, "method": self.method}


def _perspective(pano: np.ndarray, lon: float, pitch: float, hfov: float, vfov: float, ow: int, oh: int) -> np.ndarray:
    h, w = pano.shape[:2]
    xs = np.tan(np.radians(hfov) / 2) * np.linspace(-1, 1, ow)
    ys = np.tan(np.radians(vfov) / 2) * np.linspace(-1, 1, oh)
    x, y = np.meshgrid(xs, ys)
    z = np.ones_like(x)
    t = np.radians(-pitch)  # positive pitch looks up (image y points down)
    y2 = y * np.cos(t) - z * np.sin(t)
    z2 = y * np.sin(t) + z * np.cos(t)
    lr = np.radians(lon)
    xr = x * np.cos(lr) + z2 * np.sin(lr)
    zr = -x * np.sin(lr) + z2 * np.cos(lr)
    lo = np.arctan2(xr, zr)
    la = np.arctan2(y2, np.hypot(xr, zr))
    u = (((lo / (2 * np.pi) + 0.5) % 1.0) * w).astype(np.float32)
    v = ((la / np.pi + 0.5) * h).astype(np.float32)
    return cv2.remap(pano, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def _feature(img: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(img.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)
    g = lab[:, :, 0]
    mag = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    parts = [lab[:, :, c] for c in range(3)] + [mag]
    f = np.concatenate([((p - p.mean()) / (p.std() + 1e-6)).ravel() for p in parts])
    return f / (np.linalg.norm(f) + 1e-9)


def estimate_heading_offset(pano_path: Path, photos: list[tuple[Path, float]]) -> HeadingAlignment:
    """Constant offset (degrees) with pano_longitude = azimuth + offset; None when it cannot be told apart."""
    if len(photos) < 2:
        return HeadingAlignment(None, 0.0, len(photos))
    raw = cv2.imread(str(pano_path), cv2.IMREAD_COLOR)
    if raw is None or raw.shape[1] < 64 or abs(raw.shape[1] - 2 * raw.shape[0]) > 4:
        return HeadingAlignment(None, 0.0, len(photos))
    pano = cv2.GaussianBlur(cv2.resize(raw, (1024, 512), interpolation=cv2.INTER_AREA), (5, 5), 0)
    lons = np.arange(0, 360, STEP_DEG, dtype=float)
    n = len(lons)
    joint = np.zeros(n)
    loaded = []
    for path, az in photos:
        im = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if im is not None:
            loaded.append((im, az))
    if len(loaded) < 2:
        return HeadingAlignment(None, 0.0, len(loaded))
    for vf in VFOVS:
        for pitch in PITCHES:
            tot = np.zeros(n)
            for im, az in loaded:
                h, w = im.shape[:2]
                ow = 36
                oh = max(8, int(round(ow * h / w)))
                hf = float(np.degrees(2 * np.arctan(np.tan(np.radians(vf) / 2) * w / h)))
                fp = _feature(cv2.resize(cv2.GaussianBlur(im, (9, 9), 0), (ow, oh), interpolation=cv2.INTER_AREA))
                sc = np.array([fp @ _feature(_perspective(pano, lo, pitch, hf, vf, ow, oh)) for lo in lons])
                shift = int(round((az % 360) / STEP_DEG)) % n
                tot += np.roll(sc, -shift)  # tot[c] += sc[(az + c) / step]
            joint += (tot - tot.mean()) / (tot.std() + 1e-9)  # each configuration votes with its own z-curve
    joint /= len(VFOVS) * len(PITCHES)
    # smooth over ±1 step (circular) so a broad, consistent peak beats a single spike
    sm = (np.roll(joint, 1) + joint + np.roll(joint, -1)) / 3
    i = int(np.argmax(sm))
    z = float((sm[i] - sm.mean()) / (sm.std() + 1e-9))
    off = float(lons[i])
    far = np.abs(((lons - off + 180) % 360) - 180) > 45
    second = float(sm[far].max()) if far.any() else float(sm.min())
    margin = float(sm[i] - second)
    return HeadingAlignment(round(((off + 180) % 360) - 180, 1), z, len(loaded), margin=margin)
