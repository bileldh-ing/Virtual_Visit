"""Minimal, dependency-free glTF 2.0 binary (GLB) writer for textured and collider meshes."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class MeshPart:
    name: str
    positions: np.ndarray  # (N, 3) float32
    indices: np.ndarray  # (M*3,) uint32
    uvs: np.ndarray | None = None  # (N, 2) float32
    texture_jpeg: bytes | None = None


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(path: Path, parts: list[MeshPart], unlit: bool = True, generator: str = "roomscape-recon") -> dict:
    bin_chunks: list[bytes] = []
    offset = 0
    buffer_views, accessors, meshes, nodes, materials, textures, images = [], [], [], [], [], [], []

    def add_view(data: bytes, target: int | None = None) -> int:
        nonlocal offset
        data = _pad4(data)
        bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            bv["target"] = target
        buffer_views.append(bv)
        bin_chunks.append(data)
        offset += len(data)
        return len(buffer_views) - 1

    tris = 0
    for part in parts:
        pos = np.ascontiguousarray(part.positions, dtype=np.float32)
        idx = np.ascontiguousarray(part.indices, dtype=np.uint32)
        if len(pos) == 0 or len(idx) == 0:
            continue
        tris += len(idx) // 3
        pv = add_view(pos.tobytes(), 34962)
        accessors.append({"bufferView": pv, "componentType": 5126, "count": int(len(pos)), "type": "VEC3",
                          "min": [float(v) for v in pos.min(axis=0)], "max": [float(v) for v in pos.max(axis=0)]})
        pa = len(accessors) - 1
        attrs = {"POSITION": pa}
        if part.uvs is not None:
            uv = np.ascontiguousarray(part.uvs, dtype=np.float32)
            uvv = add_view(uv.tobytes(), 34962)
            accessors.append({"bufferView": uvv, "componentType": 5126, "count": int(len(uv)), "type": "VEC2"})
            attrs["TEXCOORD_0"] = len(accessors) - 1
        iv = add_view(idx.tobytes(), 34963)
        accessors.append({"bufferView": iv, "componentType": 5125, "count": int(len(idx)), "type": "SCALAR"})
        prim = {"attributes": attrs, "indices": len(accessors) - 1, "mode": 4}
        if part.texture_jpeg is not None:
            imv = add_view(part.texture_jpeg)
            images.append({"bufferView": imv, "mimeType": "image/jpeg", "name": part.name})
            textures.append({"source": len(images) - 1, "sampler": 0})
            mat = {"name": part.name, "pbrMetallicRoughness": {"baseColorTexture": {"index": len(textures) - 1},
                                                               "metallicFactor": 0.0, "roughnessFactor": 1.0},
                   "doubleSided": True}
            if unlit:
                mat["extensions"] = {"KHR_materials_unlit": {}}
            materials.append(mat)
            prim["material"] = len(materials) - 1
        meshes.append({"name": part.name, "primitives": [prim]})
        nodes.append({"name": part.name, "mesh": len(meshes) - 1})

    doc: dict = {"asset": {"version": "2.0", "generator": generator}, "scene": 0,
                 "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes, "meshes": meshes,
                 "accessors": accessors, "bufferViews": buffer_views, "buffers": [{"byteLength": offset}]}
    if materials:
        doc.update({"materials": materials, "textures": textures, "images": images,
                    "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]})
        if unlit:
            doc["extensionsUsed"] = ["KHR_materials_unlit"]
    js = _pad4(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    binary = b"".join(bin_chunks)
    total = 12 + 8 + len(js) + 8 + len(binary)
    with path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A))
        fh.write(js)
        fh.write(struct.pack("<II", len(binary), 0x004E4942))
        fh.write(binary)
    return {"bytes": total, "meshes": len(meshes), "triangles": tris}
