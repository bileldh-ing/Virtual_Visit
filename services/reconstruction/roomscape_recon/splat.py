"""Optional Gaussian-splatting adapter (Mode C) via Nerfstudio ``splatfacto``.

GPU-only. This adapter detects whether the worker can run it and otherwise
raises GPU_UNAVAILABLE / ENGINE_UNAVAILABLE. It is never silently replaced by
another output. Status in docs/FEATURE_MATRIX.md: blocked in this environment.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import ErrorCategory, ReconError


def capabilities() -> dict:
    gpu = False
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            gpu = subprocess.run([smi, "-L"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            gpu = False
    return {"gpu": gpu, "ns_train": shutil.which("ns-train") is not None,
            "ns_export": shutil.which("ns-export") is not None}


def run_splat(colmap_dir: Path, out_dir: Path, max_iters: int = 30000) -> dict:
    caps = capabilities()
    if not caps["gpu"]:
        raise ReconError(ErrorCategory.GPU_UNAVAILABLE, "Gaussian splatting requires an NVIDIA GPU.")
    if not (caps["ns_train"] and caps["ns_export"]):
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE, "Nerfstudio is not installed on this worker.")
    out_dir.mkdir(parents=True, exist_ok=True)
    train = ["ns-train", "splatfacto", "--max-num-iterations", str(max_iters), "--output-dir", str(out_dir / "runs"),
             "--viewer.quit-on-train-completion", "True", "colmap", "--data", str(colmap_dir)]
    subprocess.run(train, check=True)
    configs = sorted((out_dir / "runs").rglob("config.yml"))
    if not configs:
        raise ReconError(ErrorCategory.RECONSTRUCTION_FAILED, "Splat training produced no config.")
    subprocess.run(["ns-export", "gaussian-splat", "--load-config", str(configs[-1]),
                    "--output-dir", str(out_dir)], check=True)
    ply = out_dir / "splat.ply"
    if not ply.exists() or ply.stat().st_size == 0:
        raise ReconError(ErrorCategory.ARTIFACT_INVALID, "Splat export is missing or empty.")
    return {"artifact": ply.name, "bytes": ply.stat().st_size}
