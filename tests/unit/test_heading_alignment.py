"""Heading alignment of photo azimuths to a generated panorama (synthetic, deterministic)."""

from pathlib import Path

import cv2
import numpy as np
from roomscape_recon.engines.heading import _perspective, estimate_heading_offset


def _pano(tmp: Path) -> Path:
    rng = np.random.default_rng(7)
    w, h = 2048, 1024
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (90, 80, 70)
    for _ in range(260):  # coloured blobs and bars make every heading look different
        c = tuple(int(x) for x in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(h // 5, 4 * h // 5))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), int(rng.integers(12, 70)), c, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(20, 160)), y + int(rng.integers(20, 160))), c, -1)
    img = cv2.GaussianBlur(img, (7, 7), 0)
    p = tmp / "pano.png"
    cv2.imwrite(str(p), img)
    return p


def test_recovers_constant_offset(tmp_path: Path) -> None:
    pano_path = _pano(tmp_path)
    pano = cv2.imread(str(pano_path))
    true_offset = 63.0
    photos = []
    for i, az in enumerate([0.0, 20.0, 45.0, 300.0, 330.0]):
        crop = _perspective(pano, az + true_offset, 0.0, 66.0, 90.0, 480, 640)  # portrait "photo"
        p = tmp_path / f"p{i}.jpg"
        cv2.imwrite(str(p), crop)
        photos.append((p, az))
    r = estimate_heading_offset(pano_path, photos)
    assert r.confident, r.as_dict()
    assert r.offset_deg is not None
    assert abs(((r.offset_deg - true_offset + 180) % 360) - 180) <= 6


def test_needs_two_photos(tmp_path: Path) -> None:
    pano_path = _pano(tmp_path)
    r = estimate_heading_offset(pano_path, [(tmp_path / "missing.jpg", 0.0)])
    assert r.offset_deg is None and not r.confident
    assert r.as_dict()["offset_deg"] is None


def test_rejects_non_equirect(tmp_path: Path) -> None:
    p = tmp_path / "square.png"
    cv2.imwrite(str(p), np.zeros((256, 256, 3), np.uint8))
    r = estimate_heading_offset(p, [(p, 0.0), (p, 10.0)])
    assert not r.confident
