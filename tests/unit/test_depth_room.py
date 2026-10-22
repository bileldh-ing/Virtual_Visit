"""Free fallback engine (depth_room): math, navigation grid, collider and a full single-view run."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from roomscape_recon.engines import depth_room
from roomscape_recon.engines.base import EngineContext, EngineInput
from roomscape_recon.engines.depth_room import (
    DepthRoomEngine,
    NavGrid,
    build_nav_grid,
    collider_from_grid,
    fit_inverse_depth,
    grid_mesh,
)
from roomscape_recon.engines.formats import read_glb_json, validate_glb


def test_inverse_depth_fit_recovers_affine_with_outliers():
    rng = np.random.default_rng(0)
    disp = rng.uniform(0.5, 10, 300)
    inv = 0.07 * disp + 0.02
    inv[:40] *= rng.uniform(2, 5, 40)  # 13% gross outliers
    a, b, k = fit_inverse_depth(disp, inv)
    assert a == pytest.approx(0.07, rel=0.02) and b == pytest.approx(0.02, abs=0.01) and k >= 250


def test_grid_mesh_drops_discontinuities():
    z = np.ones((4, 4))
    z[:, 2:] = 5.0  # depth edge between column 1 and 2
    tris = grid_mesh(z, np.ones_like(z, bool), max_ratio=1.2).reshape(-1, 3)
    cols = (tris % 4)
    assert len(tris) == 2 * 3 * 2  # 3 rows of quads x 2 continuous columns x 2 triangles
    assert not np.any((cols.min(axis=1) <= 1) & (cols.max(axis=1) >= 2))


def _box_room():
    """Synthetic 4 x 6 room: floor points + wall points, camera at the centre at eye height."""
    rng = np.random.default_rng(1)
    floor = np.c_[rng.uniform(-2, 2, 4000), np.zeros(4000), rng.uniform(-3, 3, 4000)]
    walls = []
    for x in (-2.0, 2.0):
        walls.append(np.c_[np.full(800, x), rng.uniform(0, 2.5, 800), rng.uniform(-3, 3, 800)])
    for zc in (-3.0, 3.0):
        walls.append(np.c_[rng.uniform(-2, 2, 800), rng.uniform(0, 2.5, 800), np.full(800, zc)])
    # sofa seen from the camera: front face (z=1.0) and top (y=0.8), as surface samples
    front = np.c_[rng.uniform(0.5, 1.5, 600), rng.uniform(0.3, 0.8, 600), np.full(600, 1.0)]
    top = np.c_[rng.uniform(0.5, 1.5, 600), np.full(600, 0.8), rng.uniform(1.0, 1.6, 600)]
    sofa = np.concatenate([front, top])
    pts = np.concatenate([floor, *walls, sofa])
    cam = np.array([0.0, 1.5, 0.0])
    # crude occlusion: the camera cannot see the floor or low wall behind the sofa
    shadow = (pts[:, 0] > 0.3) & (pts[:, 2] > 0.9) & (pts[:, 1] < 1.0) & ~np.isin(np.arange(len(pts)),
                                                                              np.arange(len(pts) - 1200, len(pts)))
    return pts[~shadow], cam


def test_nav_grid_walkable_inside_room_and_blocked_by_furniture():
    pts, cam = _box_room()
    g = build_nav_grid(pts, [(cam, pts)], cam[None], cell=0.1)

    def at(x, z):
        r, c = int((z - g.origin[1]) / g.cell), int((x - g.origin[0]) / g.cell)
        inside = 0 <= r < g.walkable.shape[0] and 0 <= c < g.walkable.shape[1]
        return bool(g.walkable[r, c]) if inside else False

    assert at(0, 0) and at(-1.5, 2.5) and at(1.5, -2.5)
    assert not (at(1.0, 0.97) and at(1.0, 1.03))  # the sofa's front face blocks the way
    assert not at(0, 3.3) and not at(2.3, 0)  # outside the walls


def test_collider_is_closed_around_walkable_area():
    w = np.zeros((5, 5), bool)
    w[1:4, 1:4] = True
    g = NavGrid(origin=(0.0, 0.0), cell=1.0, walkable=w, occupied=~w)
    v, idx = collider_from_grid(g, height=2.0)
    tris = idx.reshape(-1, 3)
    floor = tris[np.all(v[tris][:, :, 1] == 0, axis=1)]
    assert len(floor) == 3 * 2  # one merged quad per row
    walls = len(tris) - len(floor)
    assert walls == 12 * 2  # 12 boundary edges on a 3x3 block


def test_depth_room_single_view_end_to_end(tmp_path, monkeypatch):
    """Full engine run without COLMAP or the ONNX model (TEST DOUBLE for the depth network only)."""
    p = tmp_path / "room.jpg"
    rgb = np.zeros((480, 360, 3), np.uint8)
    rgb[:240] = (180, 170, 150)
    rgb[240:] = (90, 80, 70)
    Image.fromarray(rgb).save(p, quality=90)

    class FakeDepth:
        def __init__(self, *a, **kw):
            pass

        def predict(self, img, short_side=518):
            h, w = 112, 84  # synthetic: far wall in the top half, floor approaching in the bottom half
            d = np.full((h, w), 0.4, np.float32)
            d[h // 2:] = np.linspace(0.5, 2.0, h - h // 2)[:, None]
            return d

    monkeypatch.setattr(depth_room, "DepthModel", FakeDepth)
    monkeypatch.setattr(depth_room, "ensure_model", lambda path, allow: path)
    eng = DepthRoomEngine(model_path=tmp_path / "missing.onnx", allow_download=False, probe_local=False)
    ctx = EngineContext(inputs=[EngineInput(path=p, name="room.jpg", upload_id="u1", width=360, height=480)],
                        work_dir=tmp_path / "w", out_dir=tmp_path / "o")
    r = eng.run(ctx)
    assert r.honesty == "depth_estimated" and r.status == "PARTIAL_SUCCESS"  # single view is partial by design
    kinds = {a["kind"] for a in r.artifacts}
    assert {"ROOM_MESH", "COLLIDER_MESH", "WORLD", "PREVIEW", "QUALITY_REPORT"} <= kinds
    room = validate_glb(r.out_dir / "room.glb")
    col = validate_glb(r.out_dir / "collider.glb")
    assert room["triangles"] > 500 and room["images"] == 1 and col["triangles"] > 4
    doc, _ = read_glb_json(r.out_dir / "room.glb")
    assert doc["extensionsUsed"] == ["KHR_materials_unlit"]
    world = json.loads((r.out_dir / "world.json").read_text())
    assert world["honesty"] == "depth_estimated" and world["measurement"]["enabled"] is False
    assert world["viewpoints"][0]["position"][1] == pytest.approx(1.5, abs=0.05)  # capture height = eye height
    assert world["depth_model"]["license"].startswith("Apache-2.0")
    assert r.usage[0]["kind"] == "reconstruction_minutes"
    assert any(w["code"] == "SINGLE_VIEW" for w in r.warnings)


def test_depth_engine_capabilities_without_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(depth_room, "_onnxruntime_available", lambda: False)
    caps = DepthRoomEngine(model_path=tmp_path / "x.onnx").capabilities()
    assert caps.available is False and "onnxruntime" in caps.reason
    caps2 = DepthRoomEngine(model_path=tmp_path / "x.onnx", enabled=False).capabilities()
    assert caps2.available is False
    est = DepthRoomEngine().estimate([EngineInput(path=Path("a.jpg"), name="a")] * 8)
    assert est.usd == 0.0 and est.credits is None
