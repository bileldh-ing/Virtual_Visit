"""Panorama-tour pipeline (Mode A).

Accepts genuine equirectangular 360 images (2:1). Produces EXIF-stripped web
derivatives and a tour manifest with one node per panorama. Edges/hotspots are
*not* inferred: the owner creates them in the editor.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from .artifacts import build_manifest, safe_child
from .errors import ErrorCategory, ReconError
from .quality import PANO_ASPECT, PANO_TOLERANCE, load_normalized

MAX_WEB_WIDTH = 4096


def is_equirectangular(w: int, h: int) -> bool:
    return abs(w / max(h, 1) - PANO_ASPECT) <= PANO_TOLERANCE * PANO_ASPECT and w >= 2000


def run_panorama(input_dir: Path, out_dir: Path, on_stage: Callable[[str, str, dict], None] = lambda *a: None,
                 image_map: dict[str, str] | None = None) -> dict:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    on_stage("INPUT_VALIDATION", "started", {})
    nodes, rejected = [], []
    for p in sorted(input_dir.iterdir()):
        if not p.is_file():
            continue
        try:
            img = load_normalized(p.read_bytes())
        except Exception:
            rejected.append({"name": p.name, "reason": "DECODE_FAILED"})
            continue
        if not is_equirectangular(*img.size):
            rejected.append({"name": p.name, "reason": "NOT_EQUIRECTANGULAR"})
            continue
        nodes.append((p, img))
    if not nodes:
        raise ReconError(ErrorCategory.UNSUPPORTED_INPUT,
                         "No equirectangular 360 panoramas (2:1 aspect) were found.")
    on_stage("INPUT_VALIDATION", "completed", {"panoramas": len(nodes), "rejected": len(rejected)})
    on_stage("TEXTURE_OR_APPEARANCE_PROCESSING", "started", {})
    entries, tour_nodes = [], []
    for i, (p, img) in enumerate(nodes):
        if img.width > MAX_WEB_WIDTH:
            img = img.resize((MAX_WEB_WIDTH, MAX_WEB_WIDTH // 2), Image.LANCZOS)
        name = f"pano_{i:03d}.jpg"
        # Saving a fresh RGB image drops all EXIF/GPS metadata from the public derivative.
        img.save(safe_child(out_dir, name), format="JPEG", quality=88, optimize=True, progressive=True)
        thumb = img.resize((512, 256), Image.LANCZOS)
        tname = f"pano_{i:03d}_thumb.jpg"
        thumb.save(safe_child(out_dir, tname), format="JPEG", quality=80)
        entries += [{"path": name, "kind": "PANORAMA"}, {"path": tname, "kind": "PANORAMA_THUMB"}]
        tour_nodes.append({"index": i, "source": p.name, "upload_id": (image_map or {}).get(p.name),
                           "image": name, "thumb": tname, "width": img.width, "height": img.height})
    on_stage("TEXTURE_OR_APPEARANCE_PROCESSING", "completed", {})
    on_stage("ARTIFACT_VALIDATION", "started", {})
    tour = {"schema": "1.0", "mode": "PANORAMA_TOUR", "nodes": tour_nodes, "edges": [],
            "note": "Panorama tour: not 3D geometry. Connections are created by the owner."}
    (out_dir / "tour.json").write_text(json.dumps(tour, indent=2))
    entries.append({"path": "tour.json", "kind": "TOUR"})
    report = {"status": "SUCCEEDED", "output_type": "panorama_tour",
              "metrics": {"panoramas": len(tour_nodes), "rejected": rejected,
                          "processing_seconds": round(time.monotonic() - t0, 2), "scale": "not applicable",
                          "compute_mode": "CPU"},
              "warnings": [{"code": "INPUT_REJECTED", **r} for r in rejected],
              "limitations": ["A panorama tour shows the captured 360 photos only; it is not a 3D model.",
                              "Rooms and doors between panoramas are linked manually by the owner."],
              "recapture_suggestions": []}
    (out_dir / "quality_report.json").write_text(json.dumps(report, indent=2))
    entries.append({"path": "quality_report.json", "kind": "QUALITY_REPORT"})
    manifest = build_manifest(out_dir, entries, {"name": "panorama", "version": "1.0", "cuda": False})
    on_stage("ARTIFACT_VALIDATION", "completed", {"artifacts": len(manifest["artifacts"])})
    report["artifacts"] = manifest["artifacts"]
    return report
