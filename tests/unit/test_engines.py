"""Engine interface, selection and the fallback chain (marble_world -> depth_room -> colmap_sparse)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from marble_fixtures import FakeMarble  # noqa: E402
from roomscape_recon.engines import (  # noqa: E402
    Capabilities,
    CostEstimate,
    EngineContext,
    EngineInput,
    EngineRegistry,
    EngineResult,
    Honesty,
    ReconstructionEngine,
    default_registry,
    fallback_notice,
    plan_chain,
    run_chain,
)
from roomscape_recon.engines.marble import MarbleConfig, MarbleWorldEngine  # noqa: E402
from roomscape_recon.errors import ErrorCategory, ReconError  # noqa: E402


@dataclass
class StubEngine(ReconstructionEngine):
    """TEST DOUBLE for a local engine: records calls, succeeds or raises a given category."""

    stub_name: str = "depth_room"
    fail_with: ErrorCategory | None = None
    available: bool = True

    def __post_init__(self):
        self.name = self.stub_name
        self.label = self.stub_name
        self.honesty = Honesty.DEPTH_ESTIMATED
        self.output_type = "stub"
        self.calls = 0

    def capabilities(self) -> Capabilities:
        return Capabilities(available=self.available, reason=None if self.available else "stub unavailable",
                            accepts=["photos"], min_inputs=1)

    def estimate(self, inputs):
        return CostEstimate(usd=0.0, minutes=1.0)

    def run(self, ctx: EngineContext) -> EngineResult:
        self.calls += 1
        if self.fail_with:
            raise ReconError(self.fail_with, f"{self.name} failed")
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        return EngineResult(status="SUCCEEDED", output_type="stub", honesty=self.honesty.value, metrics={},
                            artifacts=[], out_dir=ctx.out_dir, engine={"name": self.name})


def photos(tmp: Path, n=3) -> list[EngineInput]:
    out = []
    for i in range(n):
        p = tmp / f"p{i}.jpg"
        Image.fromarray(np.random.default_rng(i).integers(0, 255, (64, 48, 3), dtype=np.uint8)).save(p)
        out.append(EngineInput(path=p, name=p.name, upload_id=f"u{i}", width=48, height=64))
    return out


def marble(fake: FakeMarble, key="k") -> MarbleWorldEngine:
    return MarbleWorldEngine(cfg=MarbleConfig(api_key=key, model="marble-1.0-draft", poll_initial_s=0, poll_max_s=0,
                                              max_retries=1),
                             transport=fake.transport(), sleep=lambda s: None)


def make_ctx(tmp: Path, inputs):
    return lambda name: EngineContext(inputs=inputs, work_dir=tmp / "w" / name, out_dir=tmp / "o" / name,
                                      job_id="job-chain")


def run(reg, tmp, inputs, requested="auto", allow_fallback=True, events=None):
    plan = plan_chain(reg, inputs, requested, allow_fallback)
    return run_chain(reg, plan, make_ctx(tmp, inputs), on_event=(lambda k, d: events.append((k, d))) if events is not None else (lambda k, d: None))


# ---------------------------------------------------------------- interface

def test_every_default_engine_implements_the_interface(tmp_path):
    reg = default_registry(marble_cfg=MarbleConfig(api_key=None), depth_allow_download=False, probe_local=False)
    assert set(reg.names()) == {"marble_world", "depth_room", "colmap_sparse", "panorama_tour", "gaussian_splat_local"}
    for d in reg.describe(photos(tmp_path)):
        assert d["label"] and d["honesty"] in {h.value for h in Honesty} and d["honesty_text"]
        assert isinstance(d["capabilities"]["available"], bool) and "estimate" in d and "runnable" in d
    md = next(d for d in reg.describe(photos(tmp_path)) if d["name"] == "marble_world")
    assert md["honesty"] == "ai_generated" and "AI-generated world" in md["honesty_text"]
    assert md["capabilities"]["paid"] is True and md["runnable"] is False
    dd = next(d for d in reg.describe(photos(tmp_path)) if d["name"] == "depth_room")
    assert dd["honesty"] == "depth_estimated" and dd["estimate"]["usd"] == 0.0
    gd = next(d for d in reg.describe(photos(tmp_path)) if d["name"] == "gaussian_splat_local")
    assert gd["runnable"] is False and "GPU" in gd["not_runnable_reason"]


def test_auto_selection_prefers_marble_then_free(tmp_path):
    fake = FakeMarble(tmp_path)
    reg = EngineRegistry([marble(fake), StubEngine(stub_name="depth_room"), StubEngine(stub_name="colmap_sparse")])
    plan = plan_chain(reg, photos(tmp_path), "auto")
    assert plan.engines == ["marble_world", "depth_room", "colmap_sparse"] and plan.intended == "marble_world"
    reg2 = EngineRegistry([MarbleWorldEngine(cfg=MarbleConfig(api_key=None)), StubEngine(stub_name="depth_room"),
                           StubEngine(stub_name="colmap_sparse")])
    plan2 = plan_chain(reg2, photos(tmp_path), "auto")
    assert plan2.primary == "depth_room" and plan2.skipped[0]["engine"] == "marble_world"


def test_user_override_puts_engine_first_and_can_disable_fallback(tmp_path):
    reg = EngineRegistry([marble(FakeMarble(tmp_path)), StubEngine(stub_name="depth_room"), StubEngine(stub_name="colmap_sparse")])
    assert plan_chain(reg, photos(tmp_path), "colmap_sparse").engines == ["colmap_sparse", "marble_world", "depth_room"]
    assert plan_chain(reg, photos(tmp_path), "depth_room", allow_fallback=False).engines == ["depth_room"]


# ---------------------------------------------------------------- fallback on mocked provider failures

@pytest.mark.parametrize("override,code,text", [
    ([401, 401], "PROVIDER_AUTH", "API key invalid, expired or revoked"),
    ([403, 403], "PROVIDER_AUTH", "API key invalid, expired or revoked"),
    ([402, 402], "PROVIDER_CREDITS", "out of credits"),
    ([503, 503], "PROVIDER_UNAVAILABLE", "service unavailable"),
    ([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")], "PROVIDER_UNAVAILABLE", "service unavailable"),
])
def test_chain_falls_back_to_free_engine(tmp_path, override, code, text):
    fake = FakeMarble(tmp_path)
    fake.overrides[("GET", "/marble/v1/credits")] = list(override)
    depth, colmap = StubEngine(stub_name="depth_room"), StubEngine(stub_name="colmap_sparse")
    events: list = []
    out = run(EngineRegistry([marble(fake), depth, colmap]), tmp_path, photos(tmp_path), events=events)
    assert out.engine == "depth_room" and depth.calls == 1 and colmap.calls == 0
    assert out.fallback["from"] == "marble_world" and out.fallback["reason_code"] == code
    assert out.fallback["notice"] == f"Marble unavailable: {text} — generated with free depth engine"
    assert [a["outcome"] for a in out.attempts] == ["failed", "succeeded"]
    assert ("engine_fallback", out.fallback) in events
    assert fake.count("POST", "/marble/v1/worlds:generate") == 0  # nothing was paid for


def test_low_balance_falls_back_without_generating(tmp_path):
    fake = FakeMarble(tmp_path, balance=10)
    out = run(EngineRegistry([marble(fake), StubEngine(stub_name="depth_room")]), tmp_path, photos(tmp_path))
    assert out.engine == "depth_room" and out.fallback["reason_code"] == "PROVIDER_CREDITS"
    assert "out of credits" in out.fallback["notice"] and fake.count("POST", "/marble/v1/worlds:generate") == 0


def test_marble_operation_timeout_falls_back(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.polls_until_done = 10**9
    m = MarbleWorldEngine(cfg=MarbleConfig(api_key="k", model="marble-1.0-draft", poll_initial_s=0, poll_max_s=0,
                                           operation_timeout_s=0.0), transport=fake.transport(), sleep=lambda s: None)
    out = run(EngineRegistry([m, StubEngine(stub_name="depth_room")]), tmp_path, photos(tmp_path))
    assert out.engine == "depth_room" and out.fallback["reason_code"] == "PROVIDER_TIMEOUT"
    assert "timed out" in out.fallback["notice"]


def test_not_configured_marble_is_recorded_as_skipped_fallback(tmp_path):
    reg = EngineRegistry([MarbleWorldEngine(cfg=MarbleConfig(api_key=None)), StubEngine(stub_name="depth_room")])
    out = run(reg, tmp_path, photos(tmp_path))
    assert out.engine == "depth_room" and out.attempts[0]["outcome"] == "skipped"
    assert out.fallback["notice"] == "Marble unavailable: not configured — generated with free depth engine"


def test_free_engine_failure_falls_through_to_colmap(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.overrides[("GET", "/marble/v1/credits")] = [401]
    depth = StubEngine(stub_name="depth_room", fail_with=ErrorCategory.ENGINE_UNAVAILABLE)
    colmap = StubEngine(stub_name="colmap_sparse")
    out = run(EngineRegistry([marble(fake), depth, colmap]), tmp_path, photos(tmp_path))
    assert out.engine == "colmap_sparse" and [a["engine"] for a in out.attempts] == ["marble_world", "depth_room", "colmap_sparse"]
    assert out.fallback["reason_code"] == "PROVIDER_AUTH"


def test_cancellation_and_worker_errors_do_not_fall_back(tmp_path):
    for cat in (ErrorCategory.CANCELLED, ErrorCategory.OUT_OF_MEMORY, ErrorCategory.INTERNAL):
        first = StubEngine(stub_name="depth_room", fail_with=cat)
        second = StubEngine(stub_name="colmap_sparse")
        with pytest.raises(ReconError) as e:
            run(EngineRegistry([first, second]), tmp_path, photos(tmp_path))
        assert e.value.category == cat and second.calls == 0


def test_all_engines_failing_raises_last_error(tmp_path):
    reg = EngineRegistry([StubEngine(stub_name="depth_room", fail_with=ErrorCategory.RECONSTRUCTION_FAILED),
                          StubEngine(stub_name="colmap_sparse", fail_with=ErrorCategory.INSUFFICIENT_OVERLAP)])
    with pytest.raises(ReconError) as e:
        run(reg, tmp_path, photos(tmp_path))
    assert e.value.category == ErrorCategory.INSUFFICIENT_OVERLAP


def test_no_runnable_engine_is_engine_unavailable(tmp_path):
    reg = EngineRegistry([StubEngine(stub_name="depth_room", available=False)])
    with pytest.raises(ReconError) as e:
        run(reg, tmp_path, photos(tmp_path))
    assert e.value.category == ErrorCategory.ENGINE_UNAVAILABLE


def test_panorama_inputs_use_panorama_chain(tmp_path):
    inp = [EngineInput(path=tmp_path / "x.jpg", name="x.jpg", is_panorama=True)]
    reg = EngineRegistry([MarbleWorldEngine(cfg=MarbleConfig(api_key=None)), StubEngine(stub_name="depth_room"),
                          StubEngine(stub_name="panorama_tour")])
    reg.get("panorama_tour").capabilities = lambda: Capabilities(available=True, accepts=["panoramas"])  # type: ignore[method-assign]
    plan = plan_chain(reg, inp, "auto")
    assert plan.engines == ["panorama_tour"]


def test_fallback_notice_text():
    assert fallback_notice("marble_world", "colmap_sparse", "PROVIDER_CREDITS") == \
        "Marble unavailable: out of credits — generated with COLMAP sparse"
