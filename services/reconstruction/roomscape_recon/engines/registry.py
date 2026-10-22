"""Engine registry, per-upload engine selection and the fallback chain.

Selection
    ``auto`` builds the default chain for the inputs and drops engines that are
    not runnable (not configured, no GPU, wrong input type). The first runnable
    engine is the primary. A user override puts the chosen engine first; with
    ``allow_fallback`` (default) the rest of the default chain follows it.

Fallback
    When an engine fails with a category in :data:`FALLBACK_CATEGORIES` (key
    invalid/expired/revoked, out of credits, provider down / timing out / rate
    limited, provider rejected the input, engine unavailable) the chain moves to
    the next engine and records why. Cancellation and worker-level failures
    (OOM, lost worker, internal errors) are *not* fallbacks: they go back to the
    job's normal retry policy so a paid provider call is resumed, not repeated.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import ErrorCategory, ReconError
from .base import EngineContext, EngineInput, EngineResult, ReconstructionEngine

DEFAULT_CHAINS = {
    "photos": ["marble_world", "depth_room", "colmap_sparse"],
    "panoramas": ["marble_world", "panorama_tour"],
}

FALLBACK_CATEGORIES = {
    ErrorCategory.PROVIDER_AUTH,
    ErrorCategory.PROVIDER_CREDITS,
    ErrorCategory.PROVIDER_RATE_LIMITED,
    ErrorCategory.PROVIDER_UNAVAILABLE,
    ErrorCategory.PROVIDER_TIMEOUT,
    ErrorCategory.PROVIDER_REJECTED,
    ErrorCategory.PROVIDER_FAILED,
    ErrorCategory.ENGINE_UNAVAILABLE,
    ErrorCategory.GPU_UNAVAILABLE,
    ErrorCategory.UNSUPPORTED_INPUT,
    ErrorCategory.INSUFFICIENT_OVERLAP,
    ErrorCategory.INSUFFICIENT_IMAGES,
    ErrorCategory.RECONSTRUCTION_FAILED,
}

REASON_TEXT = {
    ErrorCategory.PROVIDER_AUTH.value: "API key invalid, expired or revoked",
    ErrorCategory.PROVIDER_CREDITS.value: "out of credits",
    ErrorCategory.PROVIDER_RATE_LIMITED.value: "rate limited",
    ErrorCategory.PROVIDER_UNAVAILABLE.value: "service unavailable",
    ErrorCategory.PROVIDER_TIMEOUT.value: "timed out",
    ErrorCategory.PROVIDER_REJECTED.value: "input rejected",
    ErrorCategory.PROVIDER_FAILED.value: "generation failed",
    ErrorCategory.ENGINE_UNAVAILABLE.value: "not available on this deployment",
    ErrorCategory.GPU_UNAVAILABLE.value: "needs a GPU",
    ErrorCategory.UNSUPPORTED_INPUT.value: "unsupported input",
    ErrorCategory.INSUFFICIENT_OVERLAP.value: "photos did not connect",
    ErrorCategory.INSUFFICIENT_IMAGES.value: "not enough photos",
    ErrorCategory.RECONSTRUCTION_FAILED.value: "produced nothing usable",
    "NOT_CONFIGURED": "not configured",
}

SHORT_NAME = {"marble_world": "Marble", "depth_room": "free depth engine", "colmap_sparse": "COLMAP sparse",
              "panorama_tour": "panorama tour", "gaussian_splat_local": "local splatting"}


class EngineRegistry:
    def __init__(self, engines: list[ReconstructionEngine]):
        self._engines = {e.name: e for e in engines}

    def get(self, name: str) -> ReconstructionEngine:
        if name not in self._engines:
            raise ReconError(ErrorCategory.UNSUPPORTED_INPUT, f"Unknown engine '{name}'.")
        return self._engines[name]

    def names(self) -> list[str]:
        return list(self._engines)

    def all(self) -> list[ReconstructionEngine]:
        return list(self._engines.values())

    def describe(self, inputs: list[EngineInput]) -> list[dict]:
        return [e.describe(inputs) for e in self._engines.values()]


def input_family(inputs: list[EngineInput]) -> str:
    panos = sum(1 for i in inputs if i.is_panorama)
    return "panoramas" if panos and panos >= len(inputs) - panos else "photos"


@dataclass
class ChainPlan:
    requested: str
    engines: list[str]
    skipped: list[dict] = field(default_factory=list)  # [{engine, reason_code, reason}]
    intended: str | None = None  # the engine the user (or auto) wanted before availability filtering

    @property
    def primary(self) -> str | None:
        return self.engines[0] if self.engines else None

    def to_dict(self) -> dict:
        return {"requested": self.requested, "intended": self.intended, "engines": self.engines,
                "skipped": self.skipped, "primary": self.primary}


def plan_chain(registry: EngineRegistry, inputs: list[EngineInput], requested: str = "auto",
               allow_fallback: bool = True) -> ChainPlan:
    family = input_family(inputs)
    default = [n for n in DEFAULT_CHAINS[family] if n in registry.names()]
    if requested in ("auto", "AUTO", "", None):
        order = default
    else:
        registry.get(requested)
        order = [requested] + ([n for n in default if n != requested] if allow_fallback else [])
    engines, skipped = [], []
    for n in order:
        e = registry.get(n)
        caps = e.capabilities()
        if not caps.available:
            skipped.append({"engine": n, "reason_code": "NOT_CONFIGURED", "reason": caps.reason})
            continue
        why = e.accepts(inputs)
        if why:
            skipped.append({"engine": n, "reason_code": ErrorCategory.UNSUPPORTED_INPUT.value, "reason": why})
            continue
        engines.append(n)
    return ChainPlan(requested=requested or "auto", engines=engines, skipped=skipped,
                     intended=order[0] if order else None)


def fallback_notice(primary: str, used: str, reason_code: str) -> str:
    """User-facing notice, e.g. 'Marble unavailable: out of credits — generated with free depth engine'."""
    reason = REASON_TEXT.get(reason_code, reason_code.lower().replace("_", " "))
    return f"{SHORT_NAME.get(primary, primary)} unavailable: {reason} — generated with {SHORT_NAME.get(used, used)}"


@dataclass
class ChainOutcome:
    result: EngineResult
    engine: str
    attempts: list[dict]
    fallback: dict | None  # {from, to, reason_code, reason, notice}


def run_chain(registry: EngineRegistry, plan: ChainPlan, make_ctx: Callable[[str], EngineContext],
              on_event: Callable[[str, dict], None] = lambda kind, data: None) -> ChainOutcome:
    """Run engines in order until one succeeds. Raises the last error if all fail."""
    attempts: list[dict] = [{"engine": s["engine"], "outcome": "skipped", "category": s["reason_code"],
                             "message": s["reason"]} for s in plan.skipped]
    last_err: ReconError | None = None
    if not plan.engines:
        reason = "; ".join(f"{s['engine']}: {s['reason']}" for s in plan.skipped) or "no engines"
        raise ReconError(ErrorCategory.ENGINE_UNAVAILABLE, "No reconstruction engine can process these inputs.", reason)
    for name in plan.engines:
        engine = registry.get(name)
        ctx = make_ctx(name)
        if ctx.out_dir.exists():
            shutil.rmtree(ctx.out_dir, ignore_errors=True)
        on_event("engine_started", {"engine": name, "label": engine.label})
        t0 = time.monotonic()
        try:
            result = engine.run(ctx)
        except ReconError as e:
            att = {"engine": name, "outcome": "failed", "category": e.category.value, "message": e.user_message,
                   "seconds": round(time.monotonic() - t0, 2)}
            attempts.append(att)
            last_err = e
            if e.category not in FALLBACK_CATEGORIES or name == plan.engines[-1]:
                on_event("engine_failed", {**att, "fallback": False})
                raise
            on_event("engine_failed", {**att, "fallback": True})
            continue
        attempts.append({"engine": name, "outcome": "succeeded", "seconds": round(time.monotonic() - t0, 2)})
        fb = None
        why = next((a for a in attempts if a["engine"] == plan.intended and a["outcome"] != "succeeded"), None)
        if plan.intended and name != plan.intended and why is not None:
            fb = {"from": plan.intended, "to": name, "reason_code": why["category"], "reason": why["message"],
                  "notice": fallback_notice(plan.intended, name, why["category"])}
            on_event("engine_fallback", fb)
        return ChainOutcome(result=result, engine=name, attempts=attempts, fallback=fb)
    assert last_err is not None  # pragma: no cover
    raise last_err
