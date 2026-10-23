#!/usr/bin/env python3
"""Generate the tiny world fixture used by e2e/world.spec.ts (no provider, no key, deterministic).

Writes to apps/web/public/e2e/world/:
  room.spz      - a valid SPZ v2 file (gzip, 16-byte header, sh degree 0): coloured splats on a 4 x 2.6 x 5 m room
  collider.glb  - glTF 2.0 binary: the same room as 12 triangles (floor, ceiling, 4 walls)
  world.json    - roomscape.world/1 description in Marble's raw convention (OpenCV-style, +y down)

Raw Marble frame: the camera origin is 1.5 m above the floor, so with ground_plane_offset = 1.5 the floor is raw y = +1.5
and the ceiling raw y = -1.1. Run: python3 apps/web/e2e/fixtures/make_world_fixture.py
"""

import gzip
import hashlib
import json
import math
import random
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "public" / "e2e" / "world"
X0, X1 = -2.0, 2.0
Z0, Z1 = -2.5, 2.5
FLOOR, CEIL = 1.5, -1.1  # raw y (+y down)


def splats() -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    rnd = random.Random(42)
    pts = []
    palette = {"floor": (0.55, 0.42, 0.30), "ceil": (0.92, 0.92, 0.90), "x0": (0.80, 0.30, 0.25), "x1": (0.25, 0.55, 0.80),
               "z0": (0.85, 0.80, 0.35), "z1": (0.35, 0.70, 0.45)}
    for _ in range(4000):
        face = rnd.choice(list(palette))
        u, v = rnd.random(), rnd.random()
        if face == "floor":
            p = (X0 + u * (X1 - X0), FLOOR, Z0 + v * (Z1 - Z0))
        elif face == "ceil":
            p = (X0 + u * (X1 - X0), CEIL, Z0 + v * (Z1 - Z0))
        elif face in ("x0", "x1"):
            p = (X0 if face == "x0" else X1, CEIL + u * (FLOOR - CEIL), Z0 + v * (Z1 - Z0))
        else:
            p = (X0 + u * (X1 - X0), CEIL + v * (FLOOR - CEIL), Z0 if face == "z0" else Z1)
        r, g, b = palette[face]
        stripe = 0.75 if int((p[0] + p[2]) * 2) % 2 else 1.0  # stripes so motion is visible
        pts.append((p, (r * stripe, g * stripe, b * stripe)))
    return pts


def write_spz(path: Path) -> int:
    pts = splats()
    n = len(pts)
    frac = 12
    head = struct.pack("<IIIBBBB", 0x5053474E, 2, n, 0, frac, 0, 0)
    pos = bytearray()
    for (x, y, z), _ in pts:
        for c in (x, y, z):
            q = int(round(c * (1 << frac))) & 0xFFFFFF
            pos += bytes((q & 0xFF, (q >> 8) & 0xFF, (q >> 16) & 0xFF))
    alpha = bytes([235] * n)  # sigmoid(opacity) * 255
    col = bytearray()
    sh_c0 = 0.28209479177387814
    for _, rgb in pts:
        for c in rgb:  # SPZ stores the SH DC term as (dc * 0.15 + 0.5) * 255
            dc = (c - 0.5) / sh_c0
            col.append(max(0, min(255, int(round((dc * 0.15 + 0.5) * 255)))))
    sc = int(round((math.log(0.07) + 10.0) * 16.0))  # log-scale, 4 fractional bits, offset 10
    scales = bytes([sc] * (3 * n))
    rot = bytes([128, 128, 128] * n)  # identity quaternion (x, y, z) * 127.5 + 127.5, w derived
    data = head + bytes(pos) + alpha + bytes(col) + scales + rot
    path.write_bytes(gzip.compress(data, mtime=0))
    return n


def write_glb(path: Path) -> int:
    v = [(X0, FLOOR, Z0), (X1, FLOOR, Z0), (X1, FLOOR, Z1), (X0, FLOOR, Z1),
         (X0, CEIL, Z0), (X1, CEIL, Z0), (X1, CEIL, Z1), (X0, CEIL, Z1)]
    quads = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    idx = []
    for a, b, c, d in quads:
        idx += [a, b, c, a, c, d]
    vb = b"".join(struct.pack("<fff", *p) for p in v)
    ib = b"".join(struct.pack("<H", i) for i in idx)
    ib += b"\x00" * ((4 - len(ib) % 4) % 4)
    binbuf = vb + ib
    gltf = {
        "asset": {"version": "2.0", "generator": "roomscape e2e fixture"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binbuf)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(vb), "target": 34962},
                        {"buffer": 0, "byteOffset": len(vb), "byteLength": len(idx) * 2, "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(v), "type": "VEC3",
                       "min": [X0, CEIL, Z0], "max": [X1, FLOOR, Z1]},
                      {"bufferView": 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binbuf)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(binbuf), 0x004E4942) + binbuf)
    return len(idx) // 3


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    n = write_spz(OUT / "room.spz")
    tris = write_glb(OUT / "collider.glb")
    world = {
        "schema": "roomscape.world/1", "engine": "marble_world", "honesty": "ai_generated", "provider": "fixture",
        "model": "e2e-fixture", "caption": "Synthetic 4 x 2.6 x 5 m test room (generated by make_world_fixture.py).",
        "semantics_metadata": {"metric_scale_factor": 1.0, "ground_plane_offset": 1.5},
        "input_photos": [], "assets": {"spz_100k": {"file": "room.spz", "num_points": n}, "collider": {"file": "collider.glb", "triangles": tris}},
        "measurement": {"enabled": False, "reason": "Test fixture."},
    }
    (OUT / "world.json").write_text(json.dumps(world, indent=2) + "\n")
    for f in sorted(OUT.iterdir()):
        print(f.name, f.stat().st_size, hashlib.sha256(f.read_bytes()).hexdigest()[:16])


if __name__ == "__main__":
    main()
