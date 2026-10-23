"""Free local fallback engine (``depth_room``): monocular depth stitched at COLMAP poses.

Pipeline (CPU only, no third-party service, no credits):

1. Camera poses: COLMAP SfM on the photos (the existing v1 adapter). If fewer than
   two photos register, a single-view room is built from the best photo.
2. Depth: Depth Anything V2 *Small* (Apache-2.0; ONNX export from
   ``onnx-community/depth-anything-v2-small``) predicts relative inverse depth per photo.
3. Alignment: per photo, an affine fit ``1/z = a*d + b`` (RANSAC + least squares)
   against the depths of the COLMAP sparse points observed in that photo puts every
   depth map in one consistent frame.
4. Geometry: each photo becomes a depth-displaced, textured triangle mesh placed at
   its solved camera; triangles across depth discontinuities are dropped.
5. Navigation: a top-down grid marks floor seen in the photos and space carved by
   camera rays as walkable and surfaces at body height as obstacles; the collider is
   the walkable floor plus vertical walls at its border, so a visitor can only walk
   where the photos show free space.
6. Frame: Y up (mean camera up), floor at y=0, relative units scaled so the capture
   height is 1.5 (viewer eye height equals the camera height). No metric claim.

Honesty level ``depth_estimated``: lower fidelity than a reconstruction or Marble,
nothing beyond the photos is invented, shapes are approximate.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from ..artifacts import _align_rotation, build_manifest
from ..colmap_engine import ColmapConfig, detect
from ..errors import ErrorCategory, ReconError
from ..pipeline import run_photogrammetry
from .base import Capabilities, CostEstimate, EngineContext, EngineInput, EngineResult, Honesty, ReconstructionEngine
from .formats import validate_glb, validate_image
from .gltf import MeshPart, write_glb
from .marble import select_inputs

MODEL_REPO = "onnx-community/depth-anything-v2-small"
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/main/onnx/model.onnx"
MODEL_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
MODEL_LICENSE = "Apache-2.0 (Depth-Anything-V2-Small)"
EYE_HEIGHT = 1.5
WALL_HEIGHT = 2.6
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _onnxruntime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_model(path: Path, allow_download: bool) -> Path:
    if path.is_file():
        return path
    if not allow_download:
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE, "The depth model is not installed on this worker.", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=120) as r, tmp.open("wb") as fh:  # noqa: S310 - pinned https URL
            h = hashlib.sha256()
            while chunk := r.read(1 << 20):
                h.update(chunk)
                fh.write(chunk)
    except OSError as e:
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE, "The depth model could not be downloaded.", repr(e)) from e
    if h.hexdigest() != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE, "The downloaded depth model failed its checksum.")
    tmp.rename(path)
    return path


class DepthModel:
    def __init__(self, path: Path, threads: int = 2):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name

    def predict(self, img: Image.Image, short_side: int = 518) -> np.ndarray:
        """Relative inverse depth (larger = nearer), at a 14-multiple resolution of the image."""
        w, h = img.size
        s = short_side / min(w, h)
        nw, nh = max(14, int(round(w * s / 14)) * 14), max(14, int(round(h * s / 14)) * 14)
        x = np.asarray(img.resize((nw, nh), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
        x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
        return self.sess.run(None, {self.input: x})[0][0].astype(np.float32)


# ----------------------------------------------------------------------------------- COLMAP parsing

def intrinsics(model: str, params: list[float]) -> tuple[float, float, float, float]:
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL", "SIMPLE_RADIAL_FISHEYE", "RADIAL_FISHEYE"):
        return params[0], params[0], params[1], params[2]
    return params[0], params[1], params[2], params[3]


def qvec_to_rot(q: list[float]) -> np.ndarray:
    w, x, y, z = q
    return np.array([[1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
                     [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
                     [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y]])


@dataclass
class View:
    name: str
    path: Path
    upload_id: str | None
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    R: np.ndarray  # camera-to-world rotation (columns: camera x right, y down, z forward)
    C: np.ndarray  # camera centre (world)
    obs_xy: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    obs_xyz: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))


def read_colmap_views(text_dir: Path, by_stem: dict[str, EngineInput]) -> list[View]:
    cams = {}
    for line in (text_dir / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        cams[int(p[0])] = (p[1], int(p[2]), int(p[3]), [float(v) for v in p[4:]])
    pts = {}
    for line in (text_dir / "points3D.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        pts[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]))
    lines = [ln for ln in (text_dir / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    views = []
    for i in range(0, len(lines) - 1, 2):
        head = lines[i].split()
        if len(head) < 10:
            continue
        q = [float(v) for v in head[1:5]]
        t = np.array([float(v) for v in head[5:8]])
        model, w, h, params = cams[int(head[8])]
        name = " ".join(head[9:])
        inp = by_stem.get(Path(name).stem)
        if inp is None:
            continue
        r_w2c = qvec_to_rot(q)
        fx, fy, cx, cy = intrinsics(model, params)
        tok = lines[i + 1].split()
        xy, xyz = [], []
        for j in range(0, len(tok) - 2, 3):
            pid = int(tok[j + 2])
            if pid >= 0 and pid in pts:
                xy.append((float(tok[j]), float(tok[j + 1])))
                xyz.append(pts[pid])
        views.append(View(name=inp.name, path=inp.path, upload_id=inp.upload_id, width=w, height=h, fx=fx, fy=fy,
                          cx=cx, cy=cy, R=r_w2c.T, C=-r_w2c.T @ t, obs_xy=np.array(xy).reshape(-1, 2),
                          obs_xyz=np.array(xyz).reshape(-1, 3)))
    return sorted(views, key=lambda v: v.name)


# ----------------------------------------------------------------------------------- geometry

def fit_inverse_depth(disp: np.ndarray, inv_z: np.ndarray, iters: int = 300, tol: float = 0.12,
                      seed: int = 0) -> tuple[float, float, int]:
    """Robust affine fit inv_z ≈ a*disp + b with a > 0. Returns (a, b, inliers)."""
    n = len(disp)
    if n < 2:
        raise ValueError("not enough samples")
    rng = np.random.default_rng(seed)
    best, best_in = (float(np.median(inv_z / np.maximum(disp, 1e-6))), 0.0), -1
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        if abs(disp[i] - disp[j]) < 1e-6:
            continue
        a = (inv_z[i] - inv_z[j]) / (disp[i] - disp[j])
        b = inv_z[i] - a * disp[i]
        if a <= 0:
            continue
        err = np.abs(a * disp + b - inv_z) / np.maximum(inv_z, 1e-9)
        k = int((err < tol).sum())
        if k > best_in:
            best, best_in = (a, b), k
    a, b = best
    err = np.abs(a * disp + b - inv_z) / np.maximum(inv_z, 1e-9)
    m = err < tol
    if m.sum() >= 3:
        A = np.stack([disp[m], np.ones(int(m.sum()))], axis=1)
        sol, *_ = np.linalg.lstsq(A, inv_z[m], rcond=None)
        if sol[0] > 0:
            a, b = float(sol[0]), float(sol[1])
    return float(a), float(b), int(m.sum())


def floor_flatten_fit(inv_est: np.ndarray, k: np.ndarray, iters: int = 4) -> tuple[np.ndarray, float, float] | None:
    """Fit inv_true = c0 + c1*inv + c2*inv^2 on floor samples, where k = z_true / z_est from the floor plane.

    Monocular depth aligned with one affine map per photo tends to bend a flat floor into a bowl (the error grows
    with distance). Samples that should lie on the floor give the true inverse depth along their ray, so a smooth
    per-photo correction in inverse depth flattens it. Returns (coefficients, rms before, rms after) or None when
    there are too few consistent samples (then nothing is changed).
    """
    inv_true = inv_est / np.maximum(k, 1e-6)
    m = np.isfinite(inv_true) & (k > 0.5) & (k < 2.0)
    if m.sum() < 200:
        return None
    before = float(np.sqrt(np.mean((k[m] - 1.0) ** 2)))
    coef = np.array([0.0, 1.0, 0.0])
    for _ in range(iters):
        A = np.stack([np.ones(int(m.sum())), inv_est[m], inv_est[m] ** 2], axis=1)
        coef, *_ = np.linalg.lstsq(A, inv_true[m], rcond=None)
        pred = coef[0] + coef[1] * inv_est + coef[2] * inv_est ** 2
        r = np.abs(pred - inv_true) / np.maximum(inv_true, 1e-9)
        m = m & (r < max(0.05, float(np.percentile(r[m], 80))))
        if m.sum() < 200:
            return None
    pred = coef[0] + coef[1] * inv_est[m] + coef[2] * inv_est[m] ** 2
    after = float(np.sqrt(np.mean((inv_true[m] / np.maximum(pred, 1e-9) - 1.0) ** 2)))
    if not (after < before) or coef[1] <= 0:
        return None
    return coef, before, after


def grid_mesh(z: np.ndarray, valid: np.ndarray, max_ratio: float) -> np.ndarray:
    """Triangle indices over an (H, W) vertex grid, skipping quads across depth discontinuities."""
    h, w = z.shape
    idx = np.arange(h * w).reshape(h, w)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    zz = np.stack([z[:-1, :-1], z[:-1, 1:], z[1:, :-1], z[1:, 1:]])
    vv = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
    ratio = zz.max(axis=0) / np.maximum(zz.min(axis=0), 1e-9)
    ok = vv & (ratio < max_ratio)
    t1 = np.stack([a[ok], c[ok], b[ok]], axis=1)
    t2 = np.stack([b[ok], c[ok], d[ok]], axis=1)
    return np.concatenate([t1, t2]).astype(np.uint32).reshape(-1)


def _components(mask: np.ndarray) -> np.ndarray:
    lab = np.zeros(mask.shape, dtype=np.int32)
    cur = 0
    h, w = mask.shape
    for y0, x0 in zip(*np.nonzero(mask), strict=False):
        if lab[y0, x0]:
            continue
        cur += 1
        q = deque([(y0, x0)])
        lab[y0, x0] = cur
        while q:
            y, x = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = cur
                    q.append((ny, nx))
    return lab


def _dilate(m: np.ndarray) -> np.ndarray:
    o = m.copy()
    o[1:] |= m[:-1]
    o[:-1] |= m[1:]
    o[:, 1:] |= m[:, :-1]
    o[:, :-1] |= m[:, 1:]
    return o


def _erode(m: np.ndarray) -> np.ndarray:
    return ~_dilate(~m)


@dataclass
class NavGrid:
    origin: tuple[float, float]  # world (x, z) of cell (0, 0) corner
    cell: float
    walkable: np.ndarray  # (rows=z, cols=x) bool
    occupied: np.ndarray

    def to_dict(self) -> dict:
        return {"origin_xz": list(self.origin), "cell": self.cell, "cols": int(self.walkable.shape[1]),
                "rows": int(self.walkable.shape[0]), "walkable_cells": int(self.walkable.sum()),
                "occupied_cells": int(self.occupied.sum())}


def build_nav_grid(points: np.ndarray, rays: list[tuple[np.ndarray, np.ndarray]], cams: np.ndarray,
                   cell: float = 0.1, body: tuple[float, float] = (0.3, 1.8)) -> NavGrid:
    """Top-down walkability from a 3D voxel evidence grid.

    Voxels hit by surface points at body height are obstacles unless camera rays
    (line of sight to a seen surface) passed through the *same voxel* more often;
    a column is walkable when its floor was seen or rays carved it at body height,
    and none of its body-height voxels is an obstacle.
    """
    xz = points[:, [0, 2]]
    lo = np.minimum(xz.min(axis=0), cams[:, [0, 2]].min(axis=0)) - 0.5
    hi = np.maximum(xz.max(axis=0), cams[:, [0, 2]].max(axis=0)) + 0.5
    lo = np.maximum(lo, cams[:, [0, 2]].min(axis=0) - 15)
    hi = np.minimum(hi, cams[:, [0, 2]].max(axis=0) + 15)
    cols, rows = int(np.ceil((hi[0] - lo[0]) / cell)), int(np.ceil((hi[1] - lo[1]) / cell))
    y0, y1 = body
    nlev = int(np.ceil((y1 - y0) / cell))

    def vox(p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        cx = np.floor((p[:, 0] - lo[0]) / cell).astype(int)
        cz = np.floor((p[:, 2] - lo[1]) / cell).astype(int)
        cy = np.floor((p[:, 1] - y0) / cell).astype(int)
        ok = (cx >= 0) & (cx < cols) & (cz >= 0) & (cz < rows) & (cy >= 0) & (cy < nlev)
        return cy[ok], cz[ok], cx[ok]

    y = points[:, 1]
    floor_hits = np.zeros((rows, cols), np.int32)
    fl = points[y < 0.12]
    fx = np.floor((fl[:, 0] - lo[0]) / cell).astype(int)
    fz = np.floor((fl[:, 2] - lo[1]) / cell).astype(int)
    ok = (fx >= 0) & (fx < cols) & (fz >= 0) & (fz < rows)
    np.add.at(floor_hits, (fz[ok], fx[ok]), 1)
    obst = np.zeros((nlev, rows, cols), np.int32)
    np.add.at(obst, vox(points), 1)
    free = np.zeros((nlev, rows, cols), np.int32)
    for cam, targets in rays:  # line of sight to a seen surface is free space
        d3 = targets - cam
        dist = np.linalg.norm(d3, axis=1)
        keep = dist > 0.3
        d3, dist = d3[keep], dist[keep]
        stop = np.maximum(dist - 0.2, 0) / np.maximum(dist, 1e-9)
        n = int(np.clip(np.ceil(dist.max() / (cell * 0.7)), 8, 200)) if len(dist) else 0
        for t in np.linspace(0.0, 1.0, n):
            np.add.at(free, vox(cam + d3 * (t * stop)[:, None]), 1)
    obstacle_vox = (obst >= 3) & (obst * 2 >= free)
    # a column is blocked by a surface spanning >= 2 height levels (walls, furniture fronts); a single level is
    # usually a slightly mis-estimated floor patch
    occupied = obstacle_vox.sum(axis=0) >= 2
    carved = free.sum(axis=0) >= 3
    walk = ((floor_hits > 0) | carved) & ~occupied
    walk = (_erode(_dilate(walk)) | walk) & ~occupied  # close pinholes
    cam_cells = [(int((c[2] - lo[1]) // cell), int((c[0] - lo[0]) // cell)) for c in cams]
    for cz, cx in cam_cells:  # the capture positions are free by definition
        walk[max(cz - 1, 0):cz + 2, max(cx - 1, 0):cx + 2] = True
    lab = _components(walk)
    keep_labels = {lab[cz, cx] for cz, cx in cam_cells if 0 <= cz < rows and 0 <= cx < cols and lab[cz, cx]}
    walk = np.isin(lab, list(keep_labels))
    return NavGrid(origin=(float(lo[0]), float(lo[1])), cell=cell, walkable=walk, occupied=occupied)


def collider_from_grid(g: NavGrid, height: float = WALL_HEIGHT) -> tuple[np.ndarray, np.ndarray]:
    """Floor quads (merged per row run) + vertical walls on every walkable/non-walkable edge."""
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    rows, cols = g.walkable.shape
    ox, oz, s = g.origin[0], g.origin[1], g.cell

    def quad(a, b, c, d):
        i = len(verts)
        verts.extend([a, b, c, d])
        tris.extend([(i, i + 1, i + 2), (i, i + 2, i + 3)])

    for r in range(rows):
        c = 0
        while c < cols:
            if not g.walkable[r, c]:
                c += 1
                continue
            c0 = c
            while c < cols and g.walkable[r, c]:
                c += 1
            x0, x1, z0, z1 = ox + c0 * s, ox + c * s, oz + r * s, oz + (r + 1) * s
            quad((x0, 0.0, z0), (x0, 0.0, z1), (x1, 0.0, z1), (x1, 0.0, z0))
    w = np.pad(g.walkable, 1)
    for r in range(rows):
        for c in range(cols):
            if not w[r + 1, c + 1]:
                continue
            x0, x1, z0, z1 = ox + c * s, ox + (c + 1) * s, oz + r * s, oz + (r + 1) * s
            if not w[r, c + 1]:
                quad((x0, 0, z0), (x1, 0, z0), (x1, height, z0), (x0, height, z0))
            if not w[r + 2, c + 1]:
                quad((x0, 0, z1), (x0, height, z1), (x1, height, z1), (x1, 0, z1))
            if not w[r + 1, c]:
                quad((x0, 0, z0), (x0, height, z0), (x0, height, z1), (x0, 0, z1))
            if not w[r + 1, c + 2]:
                quad((x1, 0, z0), (x1, 0, z1), (x1, height, z1), (x1, height, z0))
    return np.asarray(verts, np.float32).reshape(-1, 3), np.asarray(tris, np.uint32).reshape(-1)


def render_topdown(g: NavGrid, cams: np.ndarray, path: Path, px: int = 4) -> None:
    rows, cols = g.walkable.shape
    img = np.full((rows, cols, 3), (14, 20, 28), np.uint8)
    img[g.occupied] = (90, 98, 110)
    img[g.walkable] = (196, 210, 222)
    im = Image.fromarray(img).resize((cols * px, rows * px), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(im)
    for c in cams:
        u, v = (c[0] - g.origin[0]) / g.cell * px, (c[2] - g.origin[1]) / g.cell * px
        d.ellipse([u - 4, v - 4, u + 4, v + 4], fill=(45, 212, 191))
    im.save(path, format="PNG", optimize=True)


# ----------------------------------------------------------------------------------- engine

@dataclass
class DepthRoomEngine(ReconstructionEngine):
    model_path: Path = Path("./.data/models/depth-anything-v2-small/model.onnx")
    allow_download: bool = True
    max_photos: int = 12
    grid_stride: int = 4
    threads: int = 2
    colmap: ColmapConfig = field(default_factory=lambda: ColmapConfig(max_image_size=1024, num_threads=2))
    enabled: bool = True
    probe_local: bool = True  # False in the API process: availability is checked by the worker at run time

    name = "depth_room"
    label = "Free depth room (local)"
    honesty = Honesty.DEPTH_ESTIMATED
    output_type = "depth_mesh_room"
    description = ("Free, always-available fallback: Depth Anything V2 Small (Apache-2.0) predicts depth for each "
                   "photo on the CPU; the depth maps are aligned to COLMAP camera poses and stitched into a textured "
                   "room you can walk with collision. Lower fidelity; nothing beyond the photos is invented.")

    def capabilities(self) -> Capabilities:
        outputs = ["ROOM_MESH", "COLLIDER_MESH", "WORLD", "PREVIEW", "QUALITY_REPORT"]
        if not self.enabled:
            return Capabilities(available=False, reason="The free depth engine is disabled on this deployment.",
                                accepts=["photos"], min_inputs=1, outputs=outputs)
        if not self.probe_local:
            return Capabilities(available=True, accepts=["photos"], min_inputs=1, max_inputs=self.max_photos,
                                outputs=outputs)
        if not _onnxruntime_available():
            return Capabilities(available=False, reason="onnxruntime is not installed on this worker.",
                                accepts=["photos"], min_inputs=1, outputs=outputs)
        if not self.model_path.is_file() and not self.allow_download:
            return Capabilities(available=False, reason="The depth model file is not installed on this worker.",
                                accepts=["photos"], min_inputs=1, outputs=outputs)
        return Capabilities(available=True, accepts=["photos"], min_inputs=1, max_inputs=self.max_photos,
                            outputs=outputs)

    def estimate(self, inputs: list[EngineInput]) -> CostEstimate:
        n = min(len([i for i in inputs if not i.is_panorama]), self.max_photos)
        minutes = round((n * 8 + n * 4 + 30) / 60, 1)
        return CostEstimate(credits=None, usd=0.0, minutes=max(1.0, minutes),
                            note="Free: runs on this deployment's CPU (no third-party service). Charged as reconstruction minutes.")

    def run(self, ctx: EngineContext) -> EngineResult:
        t_start = time.monotonic()
        photos = select_inputs([i for i in ctx.inputs if not i.is_panorama], self.max_photos)
        if not photos:
            raise ReconError(ErrorCategory.UNSUPPORTED_INPUT, "The free depth engine needs ordinary photos.")
        out = ctx.out_dir
        out.mkdir(parents=True, exist_ok=True)
        work = ctx.work_dir / "depth_room"
        work.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float] = {}
        warnings: list[dict] = []

        ctx.on_stage("INPUT_VALIDATION", "started", {"engine": self.name, "photos": len(photos)})
        model_file = ensure_model(self.model_path, self.allow_download)
        model = DepthModel(model_file, threads=self.threads)
        ctx.on_stage("INPUT_VALIDATION", "completed", {"engine": self.name, "photos": len(photos),
                                                        "depth_model": MODEL_REPO})
        if ctx.should_cancel():
            raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")

        # 1. poses -------------------------------------------------------------------------------
        t0 = time.monotonic()
        ctx.on_stage("CAMERA_POSE_ESTIMATION", "started", {"engine": "colmap"})
        views: list[View] = []
        pose_mode = "colmap"
        colmap_metrics: dict = {}
        if len(photos) >= 2 and detect().available:
            src = work / "images"
            src.mkdir(exist_ok=True)
            by_stem = {}
            for p in photos:
                d = src / p.path.name
                if not d.exists():
                    try:
                        d.hardlink_to(p.path)
                    except OSError:
                        d.write_bytes(p.path.read_bytes())
                by_stem[p.path.stem] = p
            try:
                r = run_photogrammetry(src, work / "colmap", work / "colmap_out", self.colmap,
                                       should_cancel=ctx.should_cancel)
                colmap_metrics = {k: r.metrics.get(k) for k in ("images_registered", "sparse_points",
                                                                  "mean_reprojection_error_px")}
                views = read_colmap_views(work / "colmap" / "sparse_txt", by_stem)
            except ReconError as e:
                if e.category == ErrorCategory.CANCELLED:
                    raise
                warnings.append({"code": "POSES_UNAVAILABLE", "message": e.user_message})
        if len(views) < 2:
            pose_mode = "single_view"
            best = max(photos, key=lambda p: float(np.asarray(Image.open(p.path).convert("L").resize((64, 64))).mean()))
            with Image.open(best.path) as im:
                w, h = ImageOps.exif_transpose(im).size
            f = (w / 2) / np.tan(np.radians(65 / 2))
            views = [View(name=best.name, path=best.path, upload_id=best.upload_id, width=w, height=h, fx=f, fy=f,
                          cx=w / 2, cy=h / 2, R=np.eye(3), C=np.zeros(3))]
            warnings.append({"code": "SINGLE_VIEW", "message": "Camera poses could not be solved; the room is built "
                                                              "from one photo only."})
        timings["poses_s"] = round(time.monotonic() - t0, 2)
        ctx.on_stage("CAMERA_POSE_ESTIMATION", "completed", {"views": len(views), "mode": pose_mode})

        # 2. depth + alignment ---------------------------------------------------------------------
        t0 = time.monotonic()
        ctx.on_stage("DENSE_RECONSTRUCTION", "started", {"method": "monocular depth (Depth Anything V2 Small)"})
        per_view = []
        for v in views:
            if ctx.should_cancel():
                raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")
            with Image.open(v.path) as im0:
                img = ImageOps.exif_transpose(im0).convert("RGB")
            if img.size != (v.width, v.height):
                img = img.resize((v.width, v.height), Image.Resampling.BICUBIC)
            disp = model.predict(img)
            dh, dw = disp.shape
            fit: dict[str, Any] = {"inliers": 0, "observations": int(len(v.obs_xy))}
            if pose_mode == "colmap" and len(v.obs_xy) >= 8:
                xc = (v.obs_xyz - v.C) @ v.R  # world -> camera
                z = xc[:, 2]
                front = z > 1e-6
                u = np.clip((v.obs_xy[front, 0] / v.width * dw).astype(int), 0, dw - 1)
                q = np.clip((v.obs_xy[front, 1] / v.height * dh).astype(int), 0, dh - 1)
                a, b, k = fit_inverse_depth(disp[q, u], 1.0 / z[front])
                far = float(np.percentile(z[front], 95)) * 3.0
                fit.update({"a": a, "b": b, "inliers": k})
                if k < 6:
                    warnings.append({"code": "WEAK_DEPTH_ALIGNMENT", "image": v.name, "inliers": k})
            else:
                a, b = 1.0 / (3.0 * float(np.median(disp))), 0.0  # median depth ≈ 3 units (single view)
                far = 12.0
            inv = a * disp + b
            valid = inv > 1.0 / far
            zmap = np.where(valid, 1.0 / np.maximum(inv, 1e-6), far)
            per_view.append((v, img, zmap, valid, fit))
        timings["depth_s"] = round(time.monotonic() - t0, 2)
        ctx.on_stage("DENSE_RECONSTRUCTION", "completed", {"views": len(per_view)})

        # 3. common frame (Y up, floor y=0, eye height 1.5) ---------------------------------------
        t0 = time.monotonic()
        ctx.on_stage("MESH_OR_SPLAT_GENERATION", "started", {"engine": self.name})
        up = np.mean([-v.R[:, 1] for v in views], axis=0)
        up /= max(np.linalg.norm(up), 1e-9)
        align = _align_rotation(up)
        origin = np.mean([v.C for v in views], axis=0)
        s = self.grid_stride
        clouds, grids = [], []
        for v, _img, zmap, valid, _fit in per_view:
            dh, dw = zmap.shape
            jj, ii = np.meshgrid(np.arange(0, dw, s), np.arange(0, dh, s))
            zz, vv = zmap[ii, jj], valid[ii, jj]
            u = (jj + 0.5) / dw * v.width
            q = (ii + 0.5) / dh * v.height
            rays = np.stack([(u - v.cx) / v.fx, (q - v.cy) / v.fy, np.ones_like(u)], axis=-1)
            pc = rays * zz[..., None]
            pw = pc.reshape(-1, 3) @ v.R.T + v.C
            pa = (pw - origin) @ align.T
            clouds.append(pa[vv.reshape(-1)])
            grids.append((pa, zz, vv, jj, ii, dw, dh))
        allpts = np.concatenate(clouds)
        cams_a = np.array([(v.C - origin) @ align.T for v in views])
        rel = allpts[:, 1] - cams_a[:, 1].mean()
        floor_y = float(np.percentile(rel[rel < 0], 4)) + cams_a[:, 1].mean() if (rel < 0).any() else allpts[:, 1].min()
        cam_h = float(np.median(cams_a[:, 1] - floor_y))
        scale = EYE_HEIGHT / cam_h if cam_h > 1e-6 else 1.0

        def to_final(p: np.ndarray) -> np.ndarray:
            o = p * scale
            o[..., 1] -= floor_y * scale
            return o

        # 3b. flatten the floor per photo (monocular depth bows it); see floor_flatten_fit
        floor_fix: dict[str, dict] = {}
        for gi, ((v, _img, _zmap, _valid, fit), (pa, zz, vv, jj, ii, dw, dh), cam) in enumerate(
                zip(per_view, grids, cams_a, strict=False)):
            camf = to_final(cam[None].copy())[0]
            pf = to_final(pa.copy()).reshape(zz.shape + (3,))
            dvec = pf - camf
            down = dvec[..., 1] / np.maximum(np.linalg.norm(dvec, axis=-1), 1e-9)
            lower = ii > dh * 0.45
            cand = vv & lower & (down < -0.15) & (pf[..., 1] < 0.35 * EYE_HEIGHT) & (pf[..., 1] > -0.6 * EYE_HEIGHT)
            k = np.where(cand, camf[1] / np.maximum(camf[1] - pf[..., 1], 1e-6), np.nan)
            res = floor_flatten_fit((1.0 / np.maximum(zz, 1e-6))[cand], k[cand])
            if res is None:
                floor_fix[v.name] = {"applied": False}
                continue
            coef, before, after = res
            inv = 1.0 / np.maximum(zz, 1e-6)
            inv2 = coef[0] + coef[1] * inv + coef[2] * inv ** 2
            ok = inv2 > 1e-3
            zz2 = np.where(ok, 1.0 / np.maximum(inv2, 1e-3), zz)
            vv2 = vv & ok
            rays = (pa.reshape(zz.shape + (3,)) - cam) / np.maximum(zz, 1e-9)[..., None]
            pa2 = (cam + rays * zz2[..., None]).reshape(-1, 3)
            grids[gi] = (pa2, zz2, vv2, jj, ii, dw, dh)
            fit["floor_fix"] = {"rms_before": round(before, 4), "rms_after": round(after, 4)}
            floor_fix[v.name] = {"applied": True, "rms_before": round(before, 4), "rms_after": round(after, 4)}

        parts: list[MeshPart] = []
        rays_for_nav: list[tuple[np.ndarray, np.ndarray]] = []
        final_clouds = []
        tris_total = 0
        for (v, img, _zmap, _valid, _fit), (pa, zz, vv, jj, ii, dw, dh), cam in zip(per_view, grids, cams_a, strict=False):
            pf = to_final(pa.copy())
            idx = grid_mesh(zz, vv, max_ratio=1.0 + 0.035 * s)
            tris_total += len(idx) // 3
            uv = np.stack([(jj + 0.5) / dw, (ii + 0.5) / dh], axis=-1).reshape(-1, 2).astype(np.float32)
            tex = img.copy()
            tex.thumbnail((1024, 1024))
            buf = io.BytesIO()
            tex.save(buf, format="JPEG", quality=85)
            parts.append(MeshPart(name=Path(v.name).stem, positions=pf, indices=idx, uvs=uv, texture_jpeg=buf.getvalue()))
            camf = to_final(cam[None].copy())[0]
            sel = pf[vv.reshape(-1)]
            rays_for_nav.append((camf, sel[::2]))
            final_clouds.append(sel)
        pts_final = np.concatenate(final_clouds)
        cams_final = to_final(cams_a.copy())
        grid = build_nav_grid(pts_final, rays_for_nav, cams_final)
        cverts, cidx = collider_from_grid(grid)
        room_info = write_glb(out / "room.glb", parts, unlit=True)
        col_info = write_glb(out / "collider.glb", [MeshPart("collider", cverts, cidx)], unlit=False)
        timings["mesh_s"] = round(time.monotonic() - t0, 2)
        ctx.on_stage("MESH_OR_SPLAT_GENERATION", "completed", {"triangles": room_info["triangles"],
                                                                "collider_triangles": col_info["triangles"]})

        # 4. preview, viewpoints, validation -------------------------------------------------------
        ctx.on_stage("PREVIEW_GENERATION", "started", {})
        render_topdown(grid, cams_final, out / "preview_topdown.png")
        ctx.on_stage("PREVIEW_GENERATION", "completed", {})
        ctx.on_stage("ARTIFACT_VALIDATION", "started", {})
        room_v = validate_glb(out / "room.glb")
        col_v = validate_glb(out / "collider.glb")
        validate_image(out / "preview_topdown.png")
        viewpoints: list[dict[str, Any]] = []
        for (v, _img, _z, _valid, fit), camf in zip(per_view, cams_final, strict=False):
            r_final = align @ v.R  # camera axes in the final frame (scale does not rotate)
            fov_y = float(np.degrees(2 * np.arctan(v.height / 2 / v.fy)))
            viewpoints.append({"name": v.name, "upload_id": v.upload_id, "position": [float(x) for x in camf],
                               "rotation_c2w": [[float(x) for x in row] for row in r_final],
                               "fov_y_deg": round(fov_y, 2), "aspect": round(v.width / v.height, 4),
                               "depth_fit": fit})
        spawn = viewpoints[0]
        fwd = np.asarray(spawn["rotation_c2w"])[:, 2]
        world_doc = {
            "schema": "roomscape.world/1", "engine": self.name, "honesty": self.honesty.value,
            "frame": "Y up, floor at y=0, relative units scaled so the capture height is 1.5 (not metres)",
            "eye_height": EYE_HEIGHT, "pose_mode": pose_mode,
            "spawn": {"position": spawn["position"], "yaw_deg": float(np.degrees(np.arctan2(-fwd[0], -fwd[2])))},
            "viewpoints": viewpoints, "nav_grid": grid.to_dict(),
            "depth_model": {"name": "Depth Anything V2 Small", "source": MODEL_REPO, "license": MODEL_LICENSE,
                            "sha256": MODEL_SHA256},
            "measurement": {"enabled": False, "reason": "Depth is estimated per photo; dimensions are not reliable."},
        }
        (out / "world.json").write_text(json.dumps(world_doc, indent=2))
        elapsed = round(time.monotonic() - t_start, 2)
        metrics = {"engine": self.name, "photos_used": len(photos), "views_in_room": len(views), "pose_mode": pose_mode,
                   **{f"colmap_{k}": v for k, v in colmap_metrics.items()},
                   "room_triangles": room_v["triangles"], "collider_triangles": col_v["triangles"],
                   "walkable_cells": grid.to_dict()["walkable_cells"], "cell_size": grid.cell,
                   "depth_alignment_inliers": {Path(v["name"]).stem: v["depth_fit"].get("inliers") for v in viewpoints},
                   "floor_flattening": floor_fix,
                   "timings_s": timings, "processing_seconds": elapsed, "scale": "relative (eye-height normalised)",
                   "compute_mode": "CPU"}
        limitations = ["Lower fidelity: depth is predicted per photo by an AI model and stitched; shapes are approximate.",
                       "Only what the photos show is present; you can walk only where the photos show free floor.",
                       "Scale is relative (normalised to the capture height); measurement is disabled.",
                       "Overlapping photos may show seams or doubled surfaces."]
        quality = {"status": "SUCCEEDED", "output_type": self.output_type, "honesty": self.honesty.value,
                   "metrics": metrics, "warnings": warnings, "limitations": limitations, "recapture_suggestions": []}
        (out / "quality_report.json").write_text(json.dumps(quality, indent=2))
        entries = [{"path": "room.glb", "kind": "ROOM_MESH", "triangles": room_v["triangles"], "bounds": room_v["bounds"]},
                   {"path": "collider.glb", "kind": "COLLIDER_MESH", "triangles": col_v["triangles"], "bounds": col_v["bounds"]},
                   {"path": "world.json", "kind": "WORLD"},
                   {"path": "preview_topdown.png", "kind": "PREVIEW"},
                   {"path": "quality_report.json", "kind": "QUALITY_REPORT"}]
        manifest = build_manifest(out, entries, {"name": self.name, "depth_model": MODEL_REPO, "license": MODEL_LICENSE})
        ctx.on_stage("ARTIFACT_VALIDATION", "completed", {"artifacts": len(manifest["artifacts"])})
        minutes = max(1, int(-(-elapsed // 60)))
        return EngineResult(status="SUCCEEDED" if pose_mode == "colmap" else "PARTIAL_SUCCESS",
                            output_type=self.output_type, honesty=self.honesty.value, metrics=metrics,
                            artifacts=manifest["artifacts"], out_dir=out,
                            engine={"name": self.name, "label": self.label, "depth_model": MODEL_REPO,
                                    "license": MODEL_LICENSE},
                            warnings=warnings, limitations=limitations,
                            usage=[{"kind": "reconstruction_minutes", "quantity": minutes, "unit": "minute",
                                    "key": "reconstruction_minutes", "note": "depth_room"}],
                            tour_nodes=[{"upload_id": v["upload_id"], "kind": "VIEWPOINT", "ref": v["name"]}
                                        for v in viewpoints if v["upload_id"]],
                            stages_completed=["INPUT_VALIDATION", "CAMERA_POSE_ESTIMATION", "DENSE_RECONSTRUCTION",
                                              "MESH_OR_SPLAT_GENERATION", "PREVIEW_GENERATION", "ARTIFACT_VALIDATION"])
