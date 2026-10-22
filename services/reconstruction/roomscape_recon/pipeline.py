"""Photogrammetry pipeline: inputs -> COLMAP SfM -> validated artifacts.

The pipeline reports progress through ``on_stage(stage, event, data)`` where
``event`` is ``started`` | ``completed`` | ``skipped``. The worker persists each
event before notifying clients. Nothing here fabricates progress: a stage is
reported complete only after its process exits successfully and its outputs are
checked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from . import __version__
from .artifacts import (build_manifest, build_viewer_metadata, filter_for_web, render_preview, safe_child,
                        sha256_file, validate_ply, write_ply)
from .colmap_engine import ColmapConfig, ColmapEngine, EngineInfo, detect
from .colmap_io import read_text_model
from .errors import ErrorCategory, ReconError
from .quality import load_normalized, sniff_mime

StageCallback = Callable[[str, str, dict], None]
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MIN_REGISTERED = 3


@dataclass
class PipelineResult:
    status: str  # SUCCEEDED | PARTIAL_SUCCESS
    output_type: str
    stages_completed: list[str]
    metrics: dict
    warnings: list[dict]
    limitations: list[str]
    recapture_suggestions: list[str]
    artifacts: list[dict]
    duration_s: float
    engine: dict
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _noop(stage: str, event: str, data: dict) -> None:
    pass


def prepare_inputs(src: Path, images_dir: Path) -> tuple[list[dict], list[dict]]:
    """Copy decodable images into the job workspace with safe names.

    Returns (manifest entries, rejected entries). EXIF orientation is applied to the
    processing copy; originals are never modified.
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    accepted, rejected = [], []
    for p in sorted(src.iterdir()):
        if not p.is_file():
            continue
        if not SAFE_NAME.match(p.name):
            rejected.append({"name": p.name, "reason": "UNSAFE_NAME"})
            continue
        data = p.read_bytes()
        mime = sniff_mime(data[:16])
        if mime is None:
            rejected.append({"name": p.name, "reason": "UNSUPPORTED_FORMAT"})
            continue
        try:
            img = load_normalized(data)
        except Exception:
            rejected.append({"name": p.name, "reason": "DECODE_FAILED"})
            continue
        dest = safe_child(images_dir, Path(p.name).stem + ".jpg")
        img.save(dest, format="JPEG", quality=95)
        accepted.append({"name": p.name, "processing_name": dest.name, "sha256": sha256_file(p),
                         "width": img.width, "height": img.height, "mime": mime})
    return accepted, rejected


def run_photogrammetry(input_dir: Path, work_dir: Path, out_dir: Path, config: ColmapConfig | None = None,
                       on_stage: StageCallback = _noop, should_cancel: Callable[[], bool] = lambda: False,
                       engine_info: EngineInfo | None = None, image_map: dict[str, str] | None = None,
                       ) -> PipelineResult:
    t0 = time.monotonic()
    cfg = config or ColmapConfig()
    info = engine_info or detect()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    logs = work_dir / "logs"
    logs.mkdir(exist_ok=True)
    stages: list[str] = []
    warnings: list[dict] = []
    limitations: list[str] = [
        "Scale is unknown: distances are relative, not metres.",
        "Only surfaces visible in the uploaded photos can be reconstructed; unseen areas are absent.",
    ]

    def stage(name: str, event: str, **data):
        on_stage(name, event, data)
        if event == "completed":
            stages.append(name)

    def check_cancel():
        if should_cancel():
            raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")

    # 1. inputs ---------------------------------------------------------
    stage("INPUT_VALIDATION", "started")
    images_dir = work_dir / "images"
    accepted, rejected = prepare_inputs(input_dir, images_dir)
    for r in rejected:
        warnings.append({"code": "INPUT_REJECTED", "image": r["name"], "reason": r["reason"]})
    if len(accepted) < MIN_REGISTERED:
        raise ReconError(ErrorCategory.INSUFFICIENT_IMAGES,
                         f"Only {len(accepted)} usable image(s); at least {MIN_REGISTERED} are required "
                         "and many more are recommended.")
    input_manifest = {"images": accepted, "rejected": rejected}
    (work_dir / "input_manifest.json").write_text(json.dumps(input_manifest, indent=2))
    stage("INPUT_VALIDATION", "completed", accepted=len(accepted), rejected=len(rejected))
    check_cancel()

    engine = ColmapEngine(info, cfg, logs, should_cancel)
    db = work_dir / "database.db"

    # 2. camera pose estimation (features + matching) --------------------
    stage("CAMERA_POSE_ESTIMATION", "started", engine="colmap", cuda=info.cuda)
    engine.extract_features(db, images_dir)
    check_cancel()
    engine.match(db)
    stage("CAMERA_POSE_ESTIMATION", "completed")
    check_cancel()

    # 3. sparse reconstruction --------------------------------------------
    stage("SPARSE_RECONSTRUCTION", "started")
    sparse = work_dir / "sparse"
    engine.map(db, images_dir, sparse)
    models = sorted([d for d in sparse.iterdir() if d.is_dir() and (d / "points3D.bin").exists()],
                    key=lambda d: d.name)
    if not models:
        raise ReconError(ErrorCategory.INSUFFICIENT_OVERLAP,
                         "No camera poses could be estimated. The photos probably do not overlap enough "
                         "or lack texture.")
    analyses = []
    for m in models:
        stats = engine.analyze(m)
        analyses.append((stats.get("registered_images", 0), stats.get("points", 0), m, stats))
    analyses.sort(key=lambda t: (t[0], t[1]), reverse=True)
    reg, npts, best, stats = analyses[0]
    if reg < MIN_REGISTERED:
        raise ReconError(ErrorCategory.INSUFFICIENT_OVERLAP,
                         f"Only {reg} photos could be connected. Add overlapping photos between views.")
    text_dir = work_dir / "sparse_txt"
    engine.to_text(best, text_dir)
    model = read_text_model(text_dir)
    stage("SPARSE_RECONSTRUCTION", "completed", registered_images=reg, points=npts, models=len(models))
    check_cancel()

    # 4. dense / mesh -----------------------------------------------------
    output_type = "sparse_point_cloud"
    dense_entry = None
    if cfg.dense != "off" and info.cuda:
        stage("DENSE_RECONSTRUCTION", "started")
        dense_dir = work_dir / "dense"
        engine.dense(images_dir, best, dense_dir)
        fused = dense_dir / "fused.ply"
        mesh = dense_dir / "meshed-poisson.ply"
        validate_ply(fused)
        stage("DENSE_RECONSTRUCTION", "completed")
        stage("MESH_OR_SPLAT_GENERATION", "started")
        validate_ply(mesh, expect_faces=True)
        shutil.copy(mesh, out_dir / "mesh_poisson.ply")
        shutil.copy(fused, out_dir / "dense_points.ply")
        dense_entry = True
        output_type = "mesh"
        stage("MESH_OR_SPLAT_GENERATION", "completed")
    else:
        if cfg.dense == "require":
            raise ReconError(ErrorCategory.GPU_UNAVAILABLE,
                             "Dense reconstruction was required but this worker has no CUDA GPU.")
        reason = "COLMAP dense stereo requires CUDA; this worker is CPU-only." if not info.cuda else "disabled"
        stage("DENSE_RECONSTRUCTION", "skipped", reason=reason)
        stage("MESH_OR_SPLAT_GENERATION", "skipped", reason="no dense point cloud")
        limitations.append("No dense point cloud or mesh: " + reason + " Output is a sparse point cloud "
                           "with registered camera viewpoints.")

    # 5. optimisation -----------------------------------------------------
    stage("ARTIFACT_OPTIMIZATION", "started")
    write_ply(out_dir / "sparse_points_raw.ply", model.xyz, model.rgb)
    wxyz, wrgb, wstats = filter_for_web(model)
    write_ply(out_dir / "sparse_points_web.ply", wxyz, wrgb)
    meta = build_viewer_metadata(model, image_map)
    (out_dir / "cameras.json").write_text(json.dumps(meta, indent=2))
    stage("ARTIFACT_OPTIMIZATION", "completed", **wstats)

    # 6. preview ----------------------------------------------------------
    stage("PREVIEW_GENERATION", "started")
    render_preview(model, meta, out_dir / "preview_topdown.png")
    stage("PREVIEW_GENERATION", "completed")

    # 7. validation + manifest -------------------------------------------
    stage("ARTIFACT_VALIDATION", "started")
    raw_info = validate_ply(out_dir / "sparse_points_raw.ply")
    web_info = validate_ply(out_dir / "sparse_points_web.ply")
    if raw_info.vertex_count != len(model.xyz):
        raise ReconError(ErrorCategory.ARTIFACT_INVALID, "Point cloud vertex count mismatch.")
    with Image.open(out_dir / "preview_topdown.png") as im:
        im.verify()
    registered_names = {im.name for im in model.images.values()}
    processing_to_input = {a["processing_name"]: a["name"] for a in accepted}
    unregistered = sorted(processing_to_input[n] for n in processing_to_input if n not in registered_names)
    metrics = {
        "images_uploaded": len(accepted) + len(rejected),
        "images_decoded": len(accepted),
        "images_registered": reg,
        "registered_fraction": round(reg / len(accepted), 3),
        "unregistered_images": unregistered,
        "sparse_points": int(len(model.xyz)),
        "web_points": web_info.vertex_count,
        "dense_points": None,
        "mean_reprojection_error_px": stats.get("mean_reprojection_error_px"),
        "mean_track_length": stats.get("mean_track_length"),
        "observations": stats.get("observations"),
        "disconnected_models": len(models),
        "scale": "unknown",
        "compute_mode": "GPU (CUDA)" if info.cuda else "CPU",
    }
    if dense_entry:
        metrics["dense_points"] = validate_ply(out_dir / "dense_points.ply").vertex_count
    entries = [
        {"path": "sparse_points_web.ply", "kind": "POINT_CLOUD_WEB", "vertex_count": web_info.vertex_count},
        {"path": "sparse_points_raw.ply", "kind": "POINT_CLOUD_RAW", "vertex_count": raw_info.vertex_count},
        {"path": "cameras.json", "kind": "VIEWPOINTS"},
        {"path": "preview_topdown.png", "kind": "PREVIEW"},
    ]
    if dense_entry:
        entries += [{"path": "dense_points.ply", "kind": "POINT_CLOUD_DENSE"},
                    {"path": "mesh_poisson.ply", "kind": "MESH"}]
    suggestions = []
    if unregistered:
        suggestions.append(f"{len(unregistered)} photo(s) could not be placed. Retake them with more overlap "
                           "with neighbouring shots (move a step, keep ~60% of the previous view in frame).")
    if len(models) > 1:
        suggestions.append("The photos formed several disconnected groups. Capture the transitions between "
                           "areas (doorways, corridors) so the groups can be joined.")
    if metrics["registered_fraction"] < 0.8:
        warnings.append({"code": "PARTIAL_REGISTRATION",
                         "message": f"{reg} of {len(accepted)} photos were registered."})
    status = "PARTIAL_SUCCESS" if metrics["registered_fraction"] < 0.5 else "SUCCEEDED"
    duration = round(time.monotonic() - t0, 2)
    metrics["processing_seconds"] = duration
    quality_report = {"status": status, "output_type": output_type, "metrics": metrics, "warnings": warnings,
                      "limitations": limitations, "recapture_suggestions": suggestions}
    (out_dir / "quality_report.json").write_text(json.dumps(quality_report, indent=2))
    entries.append({"path": "quality_report.json", "kind": "QUALITY_REPORT"})
    engine_dict = {**info.to_dict(), "adapter": f"roomscape-recon {__version__}",
                   "config": cfg.__dict__, "binary": None}
    manifest = build_manifest(out_dir, entries, engine_dict)
    stage("ARTIFACT_VALIDATION", "completed", artifacts=len(manifest["artifacts"]))

    steps = [{"name": s.name, "argv": s.argv, "seconds": s.seconds, "returncode": s.returncode}
             for s in engine.run_record.steps]
    (out_dir / "run_log.json").write_text(json.dumps({"steps": steps}, indent=2))
    return PipelineResult(status=status, output_type=output_type, stages_completed=stages, metrics=metrics,
                          warnings=warnings, limitations=limitations, recapture_suggestions=suggestions,
                          artifacts=manifest["artifacts"], duration_s=duration, engine=engine_dict, steps=steps)


def cleanup_workspace(work_dir: Path, keep_logs: bool = True) -> None:
    """Remove intermediate files (database, undistorted images) after a job."""
    for child in work_dir.iterdir() if work_dir.exists() else []:
        if keep_logs and child.name == "logs":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                os.remove(child)
            except OSError:
                pass
