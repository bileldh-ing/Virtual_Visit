"""COLMAP engine adapter.

Runs the COLMAP command-line tool as subprocesses (argument arrays, never a
shell string), with timeouts, cooperative cancellation (process-group kill) and
log capture. Detects whether the binary was built with CUDA; dense MVS
(``patch_match_stereo``) requires CUDA and is skipped with an explicit,
recorded limitation when unavailable.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import ErrorCategory, ReconError

CancelCheck = Callable[[], bool]


@dataclass
class EngineInfo:
    available: bool
    binary: str | None
    version: str | None
    cuda: bool

    def to_dict(self) -> dict:
        return {"name": "colmap", "available": self.available, "version": self.version, "cuda": self.cuda}


@dataclass
class StepResult:
    name: str
    argv: list[str]
    seconds: float
    returncode: int
    log_path: str


@dataclass
class ColmapConfig:
    max_image_size: int = 1600
    peak_threshold: float = 0.004
    num_threads: int = 2
    matcher: str = "exhaustive"  # exhaustive | sequential
    camera_model: str = "SIMPLE_RADIAL"
    step_timeout_s: int = 3600
    dense: str = "auto"  # auto | off | require


@dataclass
class ColmapRun:
    steps: list[StepResult] = field(default_factory=list)


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def detect(binary: str | None = None) -> EngineInfo:
    exe = binary or os.environ.get("COLMAP_BIN") or shutil.which("colmap")
    if not exe:
        return EngineInfo(False, None, None, False)
    try:
        out = subprocess.run([exe, "help"], capture_output=True, text=True, timeout=30, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return EngineInfo(False, exe, None, False)
    text = out.stdout + out.stderr
    m = re.search(r"COLMAP\s+([0-9][\w.\-]*)", text)
    cuda = "without CUDA" not in text and "with CUDA" in text
    return EngineInfo(True, exe, m.group(1) if m else None, cuda)


class ColmapEngine:
    def __init__(self, info: EngineInfo, config: ColmapConfig, log_dir: Path,
                 should_cancel: CancelCheck | None = None):
        if not info.available or not info.binary:
            raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE,
                             "The reconstruction engine is not installed on this worker.")
        self.info = info
        self.cfg = config
        self.log_dir = log_dir
        self.should_cancel = should_cancel or (lambda: False)
        self.run_record = ColmapRun()

    # -------------------------------------------------------------- process

    def _run(self, name: str, args: list[str]) -> StepResult:
        argv = [self.info.binary, *args]  # type: ignore[list-item]
        log_path = self.log_dir / f"colmap_{name}.log"
        start = time.monotonic()
        with log_path.open("wb") as log:
            proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=_env(),
                                    start_new_session=True)
            try:
                while True:
                    try:
                        rc = proc.wait(timeout=1.0)
                        break
                    except subprocess.TimeoutExpired:
                        if self.should_cancel():
                            self._kill(proc)
                            raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")
                        if time.monotonic() - start > self.cfg.step_timeout_s:
                            self._kill(proc)
                            raise ReconError(ErrorCategory.TIMEOUT,
                                             f"The {name} step exceeded its time limit.")
            except BaseException:
                if proc.poll() is None:
                    self._kill(proc)
                raise
        res = StepResult(name, [Path(argv[0]).name, *args], round(time.monotonic() - start, 2), rc, log_path.name)
        self.run_record.steps.append(res)
        if rc != 0:
            tail = log_path.read_text(errors="replace")[-2000:]
            if rc == -signal.SIGKILL:
                raise ReconError(ErrorCategory.OUT_OF_MEMORY,
                                 f"The {name} step was killed by the system (likely out of memory).", tail)
            if "No space left" in tail:
                raise ReconError(ErrorCategory.DISK_FULL, "The worker ran out of disk space.", tail)
            raise ReconError(ErrorCategory.RECONSTRUCTION_FAILED,
                             f"The {name} step failed.", tail)
        return res

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # -------------------------------------------------------------- stages

    def extract_features(self, db: Path, images: Path) -> StepResult:
        return self._run("feature_extractor", [
            "feature_extractor", "--database_path", str(db), "--image_path", str(images),
            "--ImageReader.camera_model", self.cfg.camera_model,
            "--SiftExtraction.use_gpu", "1" if self.info.cuda else "0",
            "--SiftExtraction.num_threads", str(self.cfg.num_threads),
            "--SiftExtraction.max_image_size", str(self.cfg.max_image_size),
            "--SiftExtraction.peak_threshold", str(self.cfg.peak_threshold),
        ])

    def match(self, db: Path) -> StepResult:
        cmd = "sequential_matcher" if self.cfg.matcher == "sequential" else "exhaustive_matcher"
        return self._run(cmd, [
            cmd, "--database_path", str(db),
            "--SiftMatching.use_gpu", "1" if self.info.cuda else "0",
            "--SiftMatching.num_threads", str(self.cfg.num_threads),
        ])

    def map(self, db: Path, images: Path, sparse_dir: Path) -> StepResult:
        sparse_dir.mkdir(parents=True, exist_ok=True)
        return self._run("mapper", [
            "mapper", "--database_path", str(db), "--image_path", str(images),
            "--output_path", str(sparse_dir), "--Mapper.num_threads", str(self.cfg.num_threads),
        ])

    def to_text(self, model_dir: Path, out_dir: Path) -> StepResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        return self._run("model_converter", [
            "model_converter", "--input_path", str(model_dir), "--output_path", str(out_dir),
            "--output_type", "TXT",
        ])

    def analyze(self, model_dir: Path) -> dict:
        res = self._run("model_analyzer", ["model_analyzer", "--path", str(model_dir)])
        text = (self.log_dir / res.log_path).read_text(errors="replace")
        stats = {}
        for key, label in [("cameras", "Cameras"), ("images", "Images"), ("registered_images", "Registered images"),
                           ("points", "Points"), ("observations", "Observations")]:
            m = re.search(rf"\] {label}: (\d+)", text)
            if m:
                stats[key] = int(m.group(1))
        m = re.search(r"Mean reprojection error: ([0-9.]+)px", text)
        if m:
            stats["mean_reprojection_error_px"] = float(m.group(1))
        m = re.search(r"Mean track length: ([0-9.]+)", text)
        if m:
            stats["mean_track_length"] = float(m.group(1))
        return stats

    def dense(self, images: Path, model_dir: Path, dense_dir: Path) -> list[StepResult]:
        """Undistort -> PatchMatch stereo -> fusion -> Poisson mesh. Requires CUDA."""
        if not self.info.cuda:
            raise ReconError(ErrorCategory.GPU_UNAVAILABLE,
                             "Dense reconstruction needs a CUDA GPU; this worker is CPU-only.")
        steps = [
            self._run("image_undistorter", ["image_undistorter", "--image_path", str(images),
                                            "--input_path", str(model_dir), "--output_path", str(dense_dir),
                                            "--output_type", "COLMAP"]),
            self._run("patch_match_stereo", ["patch_match_stereo", "--workspace_path", str(dense_dir),
                                             "--workspace_format", "COLMAP",
                                             "--PatchMatchStereo.geom_consistency", "true"]),
            self._run("stereo_fusion", ["stereo_fusion", "--workspace_path", str(dense_dir),
                                        "--workspace_format", "COLMAP", "--input_type", "geometric",
                                        "--output_path", str(dense_dir / "fused.ply")]),
            self._run("poisson_mesher", ["poisson_mesher", "--input_path", str(dense_dir / "fused.ply"),
                                         "--output_path", str(dense_dir / "meshed-poisson.ply")]),
        ]
        return steps
