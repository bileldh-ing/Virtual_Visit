"""Recorded-shape HTTP fixtures for the World Labs Marble API (no network, no key).

Response bodies follow the documented shapes (docs.worldlabs.ai/api, retrieved 2026-10-22)
and the real responses observed in docs/MARBLE_SPIKE.md, with ids and URLs replaced.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from roomscape_recon.engines.formats import make_tiny_spz
from roomscape_recon.engines.gltf import MeshPart, write_glb

OP_ID = "op-test-0001"
WORLD_ID = "world-test-0001"
CDN = "https://cdn.test.invalid"


def tiny_glb(tmp: Path) -> bytes:
    p = tmp / "c.glb"
    v = np.array([[-2, 0, -2], [2, 0, -2], [2, 0, 2], [-2, 0, 2]], np.float32)
    write_glb(p, [MeshPart("collider", v, np.array([0, 1, 2, 0, 2, 3], np.uint32))], unlit=False)
    return p.read_bytes()


def png_bytes(w=64, h=32) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(b, format="PNG")
    return b.getvalue()


def world_body() -> dict:
    return {
        "world_id": WORLD_ID, "display_name": "RoomScape test", "world_marble_url": f"https://marble.worldlabs.ai/world/{WORLD_ID}",
        "model": "marble-1.0-draft",
        "assets": {
            "caption": "A living room.", "thumbnail_url": f"{CDN}/thumb.png",
            "splats": {"spz_urls": {"100k": f"{CDN}/100k.spz", "500k": f"{CDN}/500k.spz", "full_res": f"{CDN}/full.spz"},
                       "semantics_metadata": {"metric_scale_factor": 1.1, "ground_plane_offset": 0.4}},
            "mesh": {"collider_mesh_url": f"{CDN}/collider.glb"},
            "imagery": {"pano_url": f"{CDN}/pano.png"},
        },
    }


class FakeMarble:
    """Programmable fake of the Marble API + CDN behind an httpx.MockTransport."""

    def __init__(self, tmp: Path, balance: float = 7000.0):
        self.balance = balance
        self.calls: list[tuple[str, str]] = []
        self.polls_until_done = 2
        self.overrides: dict[tuple[str, str], list] = {}  # (method, path) -> [status or exception, ...]
        self.operation_error: dict | None = None
        self.listed: list[dict] = []
        self.spz = make_tiny_spz([(0.0, 0.0, 1.0), (0.5, -0.2, 1.5), (-0.4, 0.1, 2.0)])
        self.glb = tiny_glb(tmp)
        self.png = png_bytes()
        self.generated_bodies: list[dict] = []
        self.seen_keys: list[str | None] = []

    def count(self, method: str, path_prefix: str) -> int:
        return sum(1 for m, p in self.calls if m == method and p.startswith(path_prefix))

    def handler(self, request: httpx.Request) -> httpx.Response:
        m, url = request.method, request.url
        path = url.path
        self.calls.append((m, path))
        if url.host == "api.worldlabs.ai":
            self.seen_keys.append(request.headers.get("WLT-Api-Key"))
        key = (m, path)
        if self.overrides.get(key):
            nxt = self.overrides[key].pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            if isinstance(nxt, int):
                return httpx.Response(nxt, json={"detail": f"simulated {nxt}"})
            return nxt
        if url.host == "cdn.test.invalid":
            data = {"/100k.spz": self.spz, "/500k.spz": self.spz, "/full.spz": self.spz, "/collider.glb": self.glb,
                    "/pano.png": self.png, "/thumb.png": self.png}[path]
            return httpx.Response(200, content=data)
        if url.host == "upload.test.invalid":
            return httpx.Response(200)
        if path == "/marble/v1/credits":
            return httpx.Response(200, json={"remaining_credits": self.balance})
        if path == "/marble/v1/media-assets:prepare_upload":
            body = json.loads(request.content)
            n = self.count("POST", "/marble/v1/media-assets:prepare_upload")
            return httpx.Response(200, json={
                "media_asset": {"media_asset_id": f"ma-{n}", "file_name": body["file_name"], "kind": "image", "extension": body["extension"]},
                "upload_info": {"upload_url": f"https://upload.test.invalid/u/{n}", "upload_method": "PUT",
                                "required_headers": {"x-goog-content-length-range": "0,1048576000"}}})
        if path == "/marble/v1/worlds:generate":
            self.generated_bodies.append(json.loads(request.content))
            self.balance -= 250
            return httpx.Response(200, json={"operation_id": OP_ID, "done": False, "error": None, "metadata": None,
                                             "response": None})
        if path == f"/marble/v1/operations/{OP_ID}":
            n = self.count("GET", f"/marble/v1/operations/{OP_ID}")
            done = n >= self.polls_until_done
            meta = {"progress": {"status": "SUCCEEDED" if done else "IN_PROGRESS", "description": "x"}, "world_id": WORLD_ID}
            if done and self.operation_error:
                return httpx.Response(200, json={"operation_id": OP_ID, "done": True, "error": self.operation_error,
                                                 "metadata": meta, "response": None})
            return httpx.Response(200, json={"operation_id": OP_ID, "done": done, "error": None, "metadata": meta,
                                             "response": world_body() if done else None,
                                             "cost": {"total_credits": 250, "line_items": [
                                                 {"name": "pano generation", "credits": 100},
                                                 {"name": "draft world generation", "credits": 150}]} if done else None})
        if path == f"/marble/v1/worlds/{WORLD_ID}":
            return httpx.Response(200, json={"world": world_body()})
        if path == "/marble/v1/worlds:list":
            return httpx.Response(200, json={"worlds": self.listed, "next_page_token": None})
        return httpx.Response(404, json={"detail": "not found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
