"""Reconstruction engines behind one interface (see base.py) and the selection/fallback chain (registry.py)."""

from __future__ import annotations

from pathlib import Path

from .base import (
    HONESTY_TEXT,
    Capabilities,
    CostEstimate,
    EngineContext,
    EngineInput,
    EngineResult,
    Honesty,
    ReconstructionEngine,
)
from .registry import DEFAULT_CHAINS, FALLBACK_CATEGORIES, ChainPlan, EngineRegistry, fallback_notice, plan_chain, run_chain

__all__ = ["HONESTY_TEXT", "Capabilities", "CostEstimate", "EngineContext", "EngineInput", "EngineResult", "Honesty",
           "ReconstructionEngine", "DEFAULT_CHAINS", "FALLBACK_CATEGORIES", "ChainPlan", "EngineRegistry",
           "fallback_notice", "plan_chain", "run_chain", "default_registry"]


def default_registry(*, marble_cfg=None, depth_model_path: str | None = None, depth_allow_download: bool = True,
                     depth_enabled: bool = True, colmap_cfg=None, marble_transport=None,
                     probe_local: bool = True) -> EngineRegistry:
    from .depth_room import DepthRoomEngine
    from .local import ColmapSparseEngine, GaussianSplatLocalEngine, PanoramaTourEngine, colmap_pose_solver
    from .marble import MarbleConfig, MarbleWorldEngine

    engines: list[ReconstructionEngine] = [
        MarbleWorldEngine(cfg=marble_cfg or MarbleConfig(), transport=marble_transport, pose_solver=colmap_pose_solver),
        DepthRoomEngine(model_path=Path(depth_model_path) if depth_model_path else DepthRoomEngine.model_path,
                        allow_download=depth_allow_download, enabled=depth_enabled, probe_local=probe_local),
        ColmapSparseEngine(**({"config": colmap_cfg} if colmap_cfg else {}), probe_local=probe_local),
        PanoramaTourEngine(),
        GaussianSplatLocalEngine(),
    ]
    return EngineRegistry(engines)
