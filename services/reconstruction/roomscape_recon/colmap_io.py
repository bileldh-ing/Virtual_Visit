"""Readers for COLMAP's documented TEXT model format.

See https://colmap.github.io/format.html. We convert binary models to text with
``colmap model_converter`` and parse them here, so we do not depend on pycolmap
(which ships no linux/aarch64 wheel).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Camera:
    camera_id: int
    model: str
    width: int
    height: int
    params: list[float]


@dataclass
class RegisteredImage:
    image_id: int
    qvec: tuple[float, float, float, float]  # w, x, y, z (world -> camera)
    tvec: tuple[float, float, float]
    camera_id: int
    name: str
    num_points2d: int

    def rotation(self) -> np.ndarray:
        return qvec_to_rotmat(self.qvec)

    def center(self) -> np.ndarray:
        r = self.rotation()
        return -r.T @ np.asarray(self.tvec)


@dataclass
class SparseModel:
    cameras: dict[int, Camera]
    images: dict[int, RegisteredImage]
    xyz: np.ndarray  # (N, 3) float32
    rgb: np.ndarray  # (N, 3) uint8
    errors: np.ndarray  # (N,) float32 reprojection error per point
    track_lengths: np.ndarray  # (N,) int32

    @property
    def mean_reprojection_error(self) -> float | None:
        return float(self.errors.mean()) if len(self.errors) else None

    @property
    def mean_track_length(self) -> float | None:
        return float(self.track_lengths.mean()) if len(self.track_lengths) else None


def qvec_to_rotmat(q: tuple[float, float, float, float]) -> np.ndarray:
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * z * x + 2 * w * y],
            [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
            [2 * z * x - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
        ]
    )


def _data_lines(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line


def read_cameras(path: Path) -> dict[int, Camera]:
    cams = {}
    for line in _data_lines(path):
        parts = line.split()
        cid = int(parts[0])
        cams[cid] = Camera(cid, parts[1], int(parts[2]), int(parts[3]), [float(v) for v in parts[4:]])
    return cams


def read_images(path: Path) -> dict[int, RegisteredImage]:
    images = {}
    with path.open("r", encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if not ln.startswith("#")]
    # images.txt has two lines per image; the second (POINTS2D) may be empty.
    for i in range(0, len(lines) - 1, 2):
        head = lines[i].split()
        if len(head) < 10:
            continue
        pts = lines[i + 1].split()
        image_id = int(head[0])
        images[image_id] = RegisteredImage(
            image_id=image_id,
            qvec=tuple(float(v) for v in head[1:5]),  # type: ignore[arg-type]
            tvec=tuple(float(v) for v in head[5:8]),  # type: ignore[arg-type]
            camera_id=int(head[8]),
            name=" ".join(head[9:]),
            num_points2d=len(pts) // 3,
        )
    return images


def read_points3d(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    xyz, rgb, err, tl = [], [], [], []
    for line in _data_lines(path):
        p = line.split()
        xyz.append((float(p[1]), float(p[2]), float(p[3])))
        rgb.append((int(p[4]), int(p[5]), int(p[6])))
        err.append(float(p[7]))
        tl.append((len(p) - 8) // 2)
    return (
        np.asarray(xyz, dtype=np.float32).reshape(-1, 3),
        np.asarray(rgb, dtype=np.uint8).reshape(-1, 3),
        np.asarray(err, dtype=np.float32),
        np.asarray(tl, dtype=np.int32),
    )


def read_text_model(directory: Path) -> SparseModel:
    xyz, rgb, err, tl = read_points3d(directory / "points3D.txt")
    return SparseModel(
        cameras=read_cameras(directory / "cameras.txt"),
        images=read_images(directory / "images.txt"),
        xyz=xyz,
        rgb=rgb,
        errors=err,
        track_lengths=tl,
    )
