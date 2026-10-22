"""The one stable interface every reconstruction engine implements.

An engine declares what it can do (``capabilities``), what it accepts
(``accepts``), what it will cost (``estimate``) and how honest its output is
(``honesty``), and turns a set of input photos into validated artifacts
(``run``). The worker never special-cases an engine: it builds a chain with
:mod:`roomscape_recon.engines.registry` and runs it.

Honesty levels are part of the contract and are shown to viewers verbatim:

* ``reconstructed``   geometry measured from the photos (COLMAP camera poses, triangulated points)
* ``captured``        imagery shown as captured (360 panoramas), no geometry claimed
* ``depth_estimated`` per-photo depth predicted by a monocular model and stitched at solved poses
* ``ai_generated``    a generative world model produced the space; areas may be inferred
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

StageCallback = Callable[[str, str, dict], None]


class Honesty(str, Enum):
    RECONSTRUCTED = "reconstructed"
    CAPTURED = "captured"
    DEPTH_ESTIMATED = "depth_estimated"
    AI_GENERATED = "ai_generated"


HONESTY_TEXT = {
    Honesty.RECONSTRUCTED: "Reconstructed from your photos: camera positions and sparse points are measured, not invented.",
    Honesty.CAPTURED: "Captured imagery: the panoramas are shown as taken; no 3D geometry is claimed.",
    Honesty.DEPTH_ESTIMATED: "Depth-estimated room (lower fidelity): each photo's depth was predicted by an AI model and "
                             "stitched at the solved camera positions. Shapes are approximate; nothing beyond the photos is added.",
    Honesty.AI_GENERATED: "AI-generated world — areas may be inferred. A generative model built this space from your photos; "
                          "parts the photos never showed were filled in plausibly and may not match the real property.",
}


@dataclass
class EngineInput:
    path: Path
    name: str
    upload_id: str | None = None
    width: int | None = None
    height: int | None = None
    is_panorama: bool = False
    mime: str = "image/jpeg"
    bytes: int = 0


@dataclass
class Capabilities:
    available: bool
    reason: str | None = None  # why not available (shown to the user)
    requires_gpu: bool = False
    paid: bool = False  # spends third-party credits
    accepts: list[str] = field(default_factory=lambda: ["photos"])  # photos | panoramas
    min_inputs: int = 1
    max_inputs: int | None = None  # None = unlimited; extra inputs are subsampled by the engine
    outputs: list[str] = field(default_factory=list)  # artifact kinds produced on success

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CostEstimate:
    credits: float | None = None  # third-party credits (World Labs), None if not applicable
    usd: float | None = None
    minutes: float | None = None  # expected wall-clock minutes on this deployment
    breakdown: list[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineContext:
    inputs: list[EngineInput]
    work_dir: Path
    out_dir: Path
    on_stage: StageCallback = lambda *a: None
    should_cancel: Callable[[], bool] = lambda: False
    # Durable provider state (operation ids, media ids...). ``save_checkpoint`` must persist it
    # *before* returning so that a worker restart resumes instead of paying twice.
    checkpoint: dict = field(default_factory=dict)
    save_checkpoint: Callable[[dict], None] = lambda cp: None
    options: dict = field(default_factory=dict)
    job_id: str = "local"


@dataclass
class EngineResult:
    status: str  # SUCCEEDED | PARTIAL_SUCCESS
    output_type: str
    honesty: str
    metrics: dict
    artifacts: list[dict]  # manifest entries (path relative to out_dir, kind, bytes, sha256, mime, ...)
    out_dir: Path
    engine: dict
    warnings: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    recapture_suggestions: list[str] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)  # [{kind, quantity, unit, key, note}]
    tour_nodes: list[dict] = field(default_factory=list)  # [{upload_id, kind, ref}]
    steps: list[dict] = field(default_factory=list)
    stages_completed: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"status": self.status, "output_type": self.output_type, "honesty": self.honesty,
                "honesty_text": HONESTY_TEXT[Honesty(self.honesty)], "metrics": self.metrics,
                "warnings": self.warnings, "limitations": self.limitations,
                "recapture_suggestions": self.recapture_suggestions, "engine": self.engine,
                "stages_completed": self.stages_completed, "steps": self.steps}


class ReconstructionEngine(abc.ABC):
    """Base class. Subclasses set the class attributes and implement the four methods."""

    name: str = ""
    label: str = ""
    honesty: Honesty = Honesty.RECONSTRUCTED
    output_type: str = ""
    description: str = ""

    @abc.abstractmethod
    def capabilities(self) -> Capabilities: ...

    def accepts(self, inputs: list[EngineInput]) -> str | None:
        """Return ``None`` if the engine can run on these inputs, else a user-facing reason."""
        caps = self.capabilities()
        photos = [i for i in inputs if not i.is_panorama]
        panos = [i for i in inputs if i.is_panorama]
        usable = (photos if "photos" in caps.accepts else []) + (panos if "panoramas" in caps.accepts else [])
        if not usable:
            kinds = " or ".join(caps.accepts)
            return f"{self.label} needs {kinds}."
        if len(usable) < caps.min_inputs:
            return f"{self.label} needs at least {caps.min_inputs} input image(s); {len(usable)} usable."
        return None

    @abc.abstractmethod
    def estimate(self, inputs: list[EngineInput]) -> CostEstimate: ...

    @abc.abstractmethod
    def run(self, ctx: EngineContext) -> EngineResult: ...

    def describe(self, inputs: list[EngineInput] | None = None) -> dict[str, Any]:
        caps = self.capabilities()
        d: dict[str, Any] = {"name": self.name, "label": self.label, "honesty": self.honesty.value,
                             "honesty_text": HONESTY_TEXT[self.honesty], "output_type": self.output_type,
                             "description": self.description, "capabilities": caps.to_dict()}
        if inputs is not None:
            reason = self.accepts(inputs) if caps.available else caps.reason
            d["runnable"] = caps.available and reason is None
            d["not_runnable_reason"] = None if d["runnable"] else (reason or caps.reason)
            d["estimate"] = self.estimate(inputs).to_dict()
        return d
