"""Local engines wrapping the v1 pipelines: COLMAP sparse, panorama tour, GPU Gaussian splatting."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..colmap_engine import ColmapConfig, detect
from ..errors import ErrorCategory, ReconError
from ..panorama import run_panorama
from ..pipeline import run_photogrammetry
from ..splat import capabilities as splat_caps
from .base import Capabilities, CostEstimate, EngineContext, EngineInput, EngineResult, Honesty, ReconstructionEngine


def _stage_input_dir(ctx: EngineContext, panoramas: bool) -> tuple[Path, dict[str, str]]:
    d = ctx.work_dir / ("input_pano" if panoramas else "input_photos")
    d.mkdir(parents=True, exist_ok=True)
    image_map: dict[str, str] = {}
    for inp in ctx.inputs:
        if inp.is_panorama != panoramas:
            continue
        dest = d / inp.path.name
        if not dest.exists():
            try:
                dest.hardlink_to(inp.path)
            except OSError:
                shutil.copy(inp.path, dest)
        if inp.upload_id:
            image_map[inp.path.name] = inp.upload_id
            image_map[inp.path.stem + ".jpg"] = inp.upload_id
    return d, image_map


@dataclass
class ColmapSparseEngine(ReconstructionEngine):
    config: ColmapConfig = field(default_factory=ColmapConfig)
    probe_local: bool = True

    name = "colmap_sparse"
    label = "Camera poses + sparse points (COLMAP)"
    honesty = Honesty.RECONSTRUCTED
    output_type = "sparse_point_cloud"
    description = ("Structure-from-motion on CPU: real camera positions and triangulated sparse points. Evidence, "
                   "not a walkable room; the viewer navigates between solved viewpoints only.")

    def capabilities(self) -> Capabilities:
        ok = bool(detect().available) if self.probe_local else True
        return Capabilities(available=ok, reason=None if ok else "COLMAP is not installed on this worker.",
                            accepts=["photos"], min_inputs=3,
                            outputs=["POINT_CLOUD_WEB", "POINT_CLOUD_RAW", "VIEWPOINTS", "PREVIEW", "QUALITY_REPORT"])

    def estimate(self, inputs: list[EngineInput]) -> CostEstimate:
        n = len([i for i in inputs if not i.is_panorama])
        return CostEstimate(credits=None, usd=0.0, minutes=round(max(1.0, n * 6.5 / 60), 1),
                            note="Free (runs on this deployment's CPU worker). Charged as reconstruction minutes.")

    def run(self, ctx: EngineContext) -> EngineResult:
        reason = self.accepts(ctx.inputs)
        if reason:
            raise ReconError(ErrorCategory.INSUFFICIENT_IMAGES, reason)
        src, image_map = _stage_input_dir(ctx, panoramas=False)
        r = run_photogrammetry(src, ctx.work_dir / "colmap", ctx.out_dir, self.config, on_stage=ctx.on_stage,
                               should_cancel=ctx.should_cancel, image_map=image_map)
        cams = json.loads((ctx.out_dir / "cameras.json").read_text())
        nodes = [{"upload_id": v["upload_id"], "kind": "VIEWPOINT", "ref": v["name"]}
                 for v in cams["viewpoints"] if v.get("upload_id")]
        minutes = max(1, int(-(-float(r.metrics.get("processing_seconds", 0)) // 60)))
        steps = [{k: v for k, v in st.items() if k != "argv"} | {"command": st["argv"][1] if len(st["argv"]) > 1 else ""}
                 for st in r.steps]
        return EngineResult(status=r.status, output_type=r.output_type, honesty=self.honesty.value, metrics=r.metrics,
                            artifacts=r.artifacts, out_dir=ctx.out_dir, engine={"name": self.name, "label": self.label,
                                                                                 **{k: v for k, v in r.engine.items() if k != "config"}},
                            warnings=r.warnings, limitations=r.limitations, recapture_suggestions=r.recapture_suggestions,
                            usage=[{"kind": "reconstruction_minutes", "quantity": minutes, "unit": "minute",
                                    "key": "reconstruction_minutes", "note": "colmap_sparse"}],
                            tour_nodes=nodes, steps=steps, stages_completed=r.stages_completed)


@dataclass
class PanoramaTourEngine(ReconstructionEngine):
    name = "panorama_tour"
    label = "360° panorama tour"
    honesty = Honesty.CAPTURED
    output_type = "panorama_tour"
    description = "Shows uploaded equirectangular 360° panoramas as captured, one node per panorama."

    def capabilities(self) -> Capabilities:
        return Capabilities(available=True, accepts=["panoramas"], min_inputs=1,
                            outputs=["PANORAMA", "PANORAMA_THUMB", "TOUR", "QUALITY_REPORT"])

    def estimate(self, inputs: list[EngineInput]) -> CostEstimate:
        return CostEstimate(credits=None, usd=0.0, minutes=1.0, note="Free (local image processing).")

    def run(self, ctx: EngineContext) -> EngineResult:
        reason = self.accepts(ctx.inputs)
        if reason:
            raise ReconError(ErrorCategory.UNSUPPORTED_INPUT, reason)
        src, image_map = _stage_input_dir(ctx, panoramas=True)
        report = run_panorama(src, ctx.out_dir, on_stage=ctx.on_stage, image_map=image_map)
        tour = json.loads((ctx.out_dir / "tour.json").read_text())
        nodes = [{"upload_id": n["upload_id"], "kind": "PANORAMA", "ref": n["image"]} for n in tour["nodes"] if n.get("upload_id")]
        return EngineResult(status="SUCCEEDED", output_type=self.output_type, honesty=self.honesty.value,
                            metrics=report["metrics"], artifacts=report["artifacts"], out_dir=ctx.out_dir,
                            engine={"name": self.name, "label": self.label}, warnings=report["warnings"],
                            limitations=report["limitations"],
                            usage=[{"kind": "reconstruction_minutes", "quantity": 1, "unit": "minute",
                                    "key": "reconstruction_minutes", "note": "panorama_tour"}],
                            tour_nodes=nodes,
                            stages_completed=["INPUT_VALIDATION", "TEXTURE_OR_APPEARANCE_PROCESSING", "ARTIFACT_VALIDATION"])


@dataclass
class GaussianSplatLocalEngine(ReconstructionEngine):
    probe_local: bool = True
    name = "gaussian_splat_local"
    label = "Gaussian splatting (local GPU)"
    honesty = Honesty.RECONSTRUCTED
    output_type = "gaussian_splat"
    description = "Nerfstudio splatfacto trained on COLMAP poses. Needs an NVIDIA GPU worker."

    def capabilities(self) -> Capabilities:
        c = splat_caps()
        if not c["gpu"]:
            return Capabilities(available=False, reason="Needs an NVIDIA GPU worker; this deployment is CPU-only.",
                                requires_gpu=True, accepts=["photos"], min_inputs=20, outputs=["SPLAT_PLY"])
        if not (c["ns_train"] and c["ns_export"]):
            return Capabilities(available=False, reason="Nerfstudio is not installed on the GPU worker.",
                                requires_gpu=True, accepts=["photos"], min_inputs=20, outputs=["SPLAT_PLY"])
        return Capabilities(available=True, requires_gpu=True, accepts=["photos"], min_inputs=20, outputs=["SPLAT_PLY"])

    def estimate(self, inputs: list[EngineInput]) -> CostEstimate:
        return CostEstimate(credits=None, usd=0.0, minutes=30.0, note="GPU minutes on your own worker.")

    def run(self, ctx: EngineContext) -> EngineResult:
        caps = self.capabilities()
        if not caps.available:
            raise ReconError(ErrorCategory.GPU_UNAVAILABLE, caps.reason or "GPU unavailable.")
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE,  # pragma: no cover - needs GPU hardware
                         "Local splatting is wired but has not been validated on GPU hardware in this repo.")


def colmap_pose_solver(images: list[Path], work: Path) -> list[dict]:
    """Solve camera poses for a handful of photos (used by Marble for azimuths). Returns viewpoints."""
    src = work / "images"
    src.mkdir(parents=True, exist_ok=True)
    for p in images:
        d = src / p.name
        if not d.exists():
            shutil.copy(p, d)
    r = run_photogrammetry(src, work / "colmap", work / "out", ColmapConfig(max_image_size=1024, num_threads=2))
    _ = r
    return json.loads((work / "out" / "cameras.json").read_text())["viewpoints"]
