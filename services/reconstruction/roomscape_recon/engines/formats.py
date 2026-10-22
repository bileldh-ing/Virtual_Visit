"""Format validation for engine outputs: SPZ splats, GLB meshes and images.

Every downloaded or generated file is checked by magic bytes and structure
before it is registered as an artifact. Validation returns facts (point count,
triangle count, bounds, pixel size) that end up in the artifact metadata.
"""

from __future__ import annotations

import gzip
import io
import json
import struct
from pathlib import Path

from PIL import Image

from ..errors import ErrorCategory, ReconError

SPZ_MAGIC = 0x5053474E  # "NGSP" little-endian
GLB_MAGIC = b"glTF"


def _invalid(msg: str) -> ReconError:
    return ReconError(ErrorCategory.ARTIFACT_INVALID, "An output file failed format validation.", msg)


def sniff(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(16)
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:4] == b"NGSP" or (len(head) >= 4 and struct.unpack("<I", head[:4])[0] == SPZ_MAGIC):
        return "spz-raw"
    if head[:4] == GLB_MAGIC:
        return "glb"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return "unknown"


def validate_spz(path: Path) -> dict:
    """SPZ (Niantic, MIT): optionally gzip-wrapped; 16-byte header magic/version/numPoints/shDegree/..."""
    kind = sniff(path)
    try:
        if kind == "gzip":
            with gzip.open(path, "rb") as fh:
                head = fh.read(16)
        elif kind == "spz-raw":
            head = path.read_bytes()[:16]
        else:
            raise _invalid(f"{path.name}: not an SPZ file (magic {kind})")
    except (OSError, EOFError) as e:
        raise _invalid(f"{path.name}: corrupt gzip stream ({e})") from e
    if len(head) < 16:
        raise _invalid(f"{path.name}: SPZ header truncated")
    magic, version, num_points = struct.unpack("<III", head[:12])
    sh_degree, fractional_bits, flags = head[12], head[13], head[14]
    if magic != SPZ_MAGIC:
        raise _invalid(f"{path.name}: bad SPZ magic {magic:#x}")
    if not 1 <= version <= 16:
        raise _invalid(f"{path.name}: unsupported SPZ version {version}")
    if num_points <= 0 or num_points > 50_000_000:
        raise _invalid(f"{path.name}: implausible point count {num_points}")
    if sh_degree > 4:
        raise _invalid(f"{path.name}: bad SH degree {sh_degree}")
    return {"format": "spz", "compressed": kind == "gzip", "spz_version": version, "num_points": int(num_points),
            "sh_degree": int(sh_degree), "fractional_bits": int(fractional_bits), "flags": int(flags)}


def read_glb_json(path: Path) -> tuple[dict, int]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise _invalid(f"{path.name}: not a GLB file")
    version, length = struct.unpack("<II", data[4:12])
    if version != 2:
        raise _invalid(f"{path.name}: glTF version {version} unsupported")
    if length != len(data):
        raise _invalid(f"{path.name}: GLB length field {length} != file size {len(data)}")
    clen, ctype = struct.unpack("<II", data[12:20])
    if ctype != 0x4E4F534A:  # JSON
        raise _invalid(f"{path.name}: first GLB chunk is not JSON")
    try:
        doc = json.loads(data[20:20 + clen].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise _invalid(f"{path.name}: GLB JSON chunk unreadable") from e
    return doc, len(data)


def validate_glb(path: Path) -> dict:
    doc, size = read_glb_json(path)
    accessors = doc.get("accessors", [])
    tris, verts = 0, 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            pos = accessors[prim["attributes"]["POSITION"]]
            verts += int(pos["count"])
            if "min" in pos and "max" in pos:
                lo = [min(a, b) for a, b in zip(lo, pos["min"], strict=False)]
                hi = [max(a, b) for a, b in zip(hi, pos["max"], strict=False)]
            mode = prim.get("mode", 4)
            if mode == 4:
                tris += int(accessors[prim["indices"]]["count"]) // 3 if "indices" in prim else int(pos["count"]) // 3
    if not doc.get("meshes"):
        raise _invalid(f"{path.name}: GLB contains no meshes")
    bounds = {"min": lo, "max": hi} if lo[0] != float("inf") else None
    return {"format": "glb", "meshes": len(doc.get("meshes", [])), "triangles": tris, "vertices": verts,
            "bounds": bounds, "images": len(doc.get("images", [])), "bytes": size}


def validate_image(path: Path) -> dict:
    kind = sniff(path)
    if kind not in {"png", "jpeg", "webp"}:
        raise _invalid(f"{path.name}: not an image (magic {kind})")
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            w, h = im.size
    except Exception as e:  # PIL raises many types
        raise _invalid(f"{path.name}: image does not decode ({type(e).__name__})") from e
    return {"format": kind, "width": w, "height": h}


IMAGE_EXT = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
IMAGE_MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


def make_tiny_spz(points: list[tuple[float, float, float]], colors: list[tuple[int, int, int]] | None = None) -> bytes:
    """Write a valid gzip SPZ v2 with the given centres (used for test fixtures only)."""
    n = len(points)
    frac = 12
    buf = io.BytesIO()
    buf.write(struct.pack("<IIIBBBB", SPZ_MAGIC, 2, n, 0, frac, 0, 0))
    for x, y, z in points:  # 24-bit fixed point positions
        for v in (x, y, z):
            q = int(round(v * (1 << frac))) & 0xFFFFFF
            buf.write(bytes((q & 0xFF, (q >> 8) & 0xFF, (q >> 16) & 0xFF)))
    buf.write(bytes([235] * n))  # alpha (sigmoid^-1 encoded ~ 0.92)
    cols = colors or [(200, 200, 200)] * n
    for r, g, b in cols:
        buf.write(bytes((r, g, b)))
    for _ in range(n):  # log-scale, (v+10)*16 encoding: ~ exp(-4.5)=0.011
        buf.write(bytes((88, 88, 88)))
    for _ in range(n):  # identity rotation (xyz of quaternion, w implied)
        buf.write(bytes((128, 128, 128)))
    return gzip.compress(buf.getvalue())
