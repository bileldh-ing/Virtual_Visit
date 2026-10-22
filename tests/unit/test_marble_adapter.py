"""Marble adapter against recorded-shape HTTP fixtures (no network, no API key)."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from marble_fixtures import OP_ID, WORLD_ID, FakeMarble  # noqa: E402
from roomscape_recon.engines.base import EngineContext, EngineInput  # noqa: E402
from roomscape_recon.engines.marble import (  # noqa: E402
    MarbleConfig,
    MarbleWorldEngine,
    azimuths_from_viewpoints,
    prepare_marble_images,
    price,
)
from roomscape_recon.errors import ErrorCategory, ReconError  # noqa: E402


def photos(tmp: Path, n=4, sizes=None) -> list[EngineInput]:
    out = []
    for i in range(n):
        w, h = (sizes[i] if sizes else (1480, 2246))
        arr = np.random.default_rng(i).integers(0, 255, (h // 16, w // 16, 3), dtype=np.uint8)
        p = tmp / f"photo_{i}.jpg"
        Image.fromarray(arr).resize((w, h)).save(p, format="JPEG", quality=80)
        out.append(EngineInput(path=p, name=p.name, upload_id=f"up-{i}", width=w, height=h))
    return out


def engine(fake: FakeMarble, **cfg) -> MarbleWorldEngine:
    c = MarbleConfig(api_key="test-key-not-real", model="marble-1.0-draft", poll_initial_s=0, poll_max_s=0, max_retries=2,
                     **cfg)
    return MarbleWorldEngine(cfg=c, transport=fake.transport(), sleep=lambda s: None)


def ctx(tmp: Path, inputs, checkpoint=None, saved=None) -> EngineContext:
    saved = saved if saved is not None else []
    return EngineContext(inputs=inputs, work_dir=tmp / "work", out_dir=tmp / "out", checkpoint=checkpoint or {},
                         save_checkpoint=lambda cp: saved.append(json.loads(json.dumps(cp))), job_id="job-1234")


def test_happy_path_generates_downloads_and_validates(tmp_path):
    fake = FakeMarble(tmp_path)
    saved: list[dict] = []
    r = engine(fake).run(ctx(tmp_path, photos(tmp_path), saved=saved))
    assert r.status == "SUCCEEDED" and r.honesty == "ai_generated"
    kinds = {a["kind"] for a in r.artifacts}
    assert {"SPLAT_SPZ", "COLLIDER_MESH", "PANORAMA", "THUMBNAIL", "WORLD", "QUALITY_REPORT"} <= kinds
    spz = [a for a in r.artifacts if a["kind"] == "SPLAT_SPZ"]
    assert {a["variant"] for a in spz} == {"100k", "500k"} and all(a["num_points"] == 3 for a in spz)
    assert all(len(a["sha256"]) == 64 and a["bytes"] > 0 for a in r.artifacts)
    # paid call happened once; the operation id was checkpointed before polling finished
    assert fake.count("POST", "/marble/v1/worlds:generate") == 1
    assert any(cp.get("operation_id") == OP_ID and not cp.get("operation_done") for cp in saved)
    assert any(cp.get("intent_tag") and "operation_id" not in cp for cp in saved)  # intent before the paid call
    body = fake.generated_bodies[0]
    assert body["world_prompt"]["type"] == "multi-image" and body["world_prompt"]["reconstruct_images"] is True
    assert len(body["world_prompt"]["multi_image_prompt"]) == 4 and body["tags"] == ["rs-job-1234"]
    assert body["permission"] == {"public": False}
    assert r.metrics["measured_credits"] == 250 and r.usage[0]["quantity"] == 250
    assert r.usage[0]["key"] == f"marble:{OP_ID}"
    world = json.loads((r.out_dir / "world.json").read_text())
    assert world["world_id"] == WORLD_ID and world["semantics_metadata"]["metric_scale_factor"] == 1.1
    assert world["measurement"]["enabled"] is False
    # key sent only to the API host, never to upload/CDN hosts
    assert set(fake.seen_keys) == {"test-key-not-real"}


def test_gateway_mode_sends_no_key(tmp_path):
    fake = FakeMarble(tmp_path)
    c = MarbleConfig(api_key=None, auth_mode="gateway", model="marble-1.0-draft", poll_initial_s=0, poll_max_s=0)
    MarbleWorldEngine(cfg=c, transport=fake.transport(), sleep=lambda s: None).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert set(fake.seen_keys) == {None}


def test_resume_after_worker_restart_polls_without_paying_again(tmp_path):
    fake = FakeMarble(tmp_path)
    cp = {"intent_tag": "rs-job-1234", "operation_id": OP_ID, "operation_started": time.time(), "credits_before": 7000,
          "estimate_credits": 250, "model": "marble-1.0-draft", "input_type": "multi-image"}
    r = engine(fake).run(ctx(tmp_path, photos(tmp_path), checkpoint=cp))
    assert r.status == "SUCCEEDED"
    assert fake.count("POST", "/marble/v1/worlds:generate") == 0
    assert fake.count("POST", "/marble/v1/media-assets:prepare_upload") == 0
    assert r.metrics["resumed"] is True and r.metrics["measured_credits"] is None
    assert r.usage[0]["key"] == f"marble:{OP_ID}"  # same idempotency key as the first attempt: never charged twice


def test_crash_between_paid_call_and_checkpoint_adopts_world_by_tag(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.listed = [{"id": WORLD_ID}]
    cp = {"intent_tag": "rs-job-1234", "media_asset_ids": ["ma-0", "ma-1"]}
    r = engine(fake).run(ctx(tmp_path, photos(tmp_path, 2), checkpoint=cp))
    assert r.status == "SUCCEEDED" and r.metrics["adopted_by_tag"] is True
    assert fake.count("POST", "/marble/v1/worlds:generate") == 0


def test_ambiguous_500_on_generate_checks_tag_before_retrying(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.overrides[("POST", "/marble/v1/worlds:generate")] = [500]
    fake.listed = [{"id": WORLD_ID}]
    r = engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert r.status == "SUCCEEDED" and fake.count("POST", "/marble/v1/worlds:generate") == 1


def test_insufficient_balance_fails_before_any_upload(tmp_path):
    fake = FakeMarble(tmp_path, balance=100)
    with pytest.raises(ReconError) as e:
        engine(fake).run(ctx(tmp_path, photos(tmp_path)))
    assert e.value.category == ErrorCategory.PROVIDER_CREDITS
    assert fake.count("POST", "/marble/v1/media-assets") == 0 and fake.count("POST", "/marble/v1/worlds:generate") == 0


@pytest.mark.parametrize("status,category", [(401, ErrorCategory.PROVIDER_AUTH), (403, ErrorCategory.PROVIDER_AUTH),
                                             (402, ErrorCategory.PROVIDER_CREDITS), (422, ErrorCategory.PROVIDER_REJECTED),
                                             (400, ErrorCategory.PROVIDER_REJECTED)])
def test_http_errors_map_to_typed_categories(tmp_path, status, category):
    fake = FakeMarble(tmp_path)
    fake.overrides[("POST", "/marble/v1/worlds:generate")] = [status]
    with pytest.raises(ReconError) as e:
        engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert e.value.category == category
    assert "test-key-not-real" not in (e.value.detail or "") + e.value.user_message


def test_expired_key_on_preflight_is_auth_error(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.overrides[("GET", "/marble/v1/credits")] = [401]
    with pytest.raises(ReconError) as e:
        engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert e.value.category == ErrorCategory.PROVIDER_AUTH


def test_rate_limit_and_outage_retry_then_map(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.overrides[("GET", "/marble/v1/credits")] = [429, 503]  # retried, then succeeds
    r = engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert r.status == "SUCCEEDED"
    fake2 = FakeMarble(tmp_path)
    fake2.overrides[("GET", "/marble/v1/credits")] = [503, 503, 503]
    with pytest.raises(ReconError) as e:
        engine(fake2).run(ctx(tmp_path / "b", photos(tmp_path, 2)))
    assert e.value.category == ErrorCategory.PROVIDER_UNAVAILABLE
    fake3 = FakeMarble(tmp_path)
    fake3.overrides[("GET", "/marble/v1/credits")] = [httpx.ConnectError("down")] * 3
    with pytest.raises(ReconError) as e:
        engine(fake3).run(ctx(tmp_path / "c", photos(tmp_path, 2)))
    assert e.value.category == ErrorCategory.PROVIDER_UNAVAILABLE


def test_operation_timeout_maps_to_provider_timeout(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.polls_until_done = 10**9
    cp = {"operation_id": OP_ID, "operation_started": time.time() - 100}
    with pytest.raises(ReconError) as e:
        engine(fake, operation_timeout_s=50).run(ctx(tmp_path, photos(tmp_path, 2), checkpoint=cp))
    assert e.value.category == ErrorCategory.PROVIDER_TIMEOUT


def test_operation_error_maps_to_provider_failed(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.operation_error = {"code": 3, "message": "content policy"}
    with pytest.raises(ReconError) as e:
        engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert e.value.category == ErrorCategory.PROVIDER_FAILED


def test_corrupt_asset_is_rejected(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.spz = b"not a splat at all"
    with pytest.raises(ReconError) as e:
        engine(fake).run(ctx(tmp_path, photos(tmp_path, 2)))
    assert e.value.category == ErrorCategory.ARTIFACT_INVALID


def test_cancel_during_polling(tmp_path):
    fake = FakeMarble(tmp_path)
    fake.polls_until_done = 10**9
    c = ctx(tmp_path, photos(tmp_path, 2))
    n = {"i": 0}

    def cancel():
        n["i"] += 1
        return n["i"] > 4

    c.should_cancel = cancel
    with pytest.raises(ReconError) as e:
        engine(fake).run(c)
    assert e.value.category == ErrorCategory.CANCELLED


def test_input_preparation_normalises_size_and_limits(tmp_path):
    inp = photos(tmp_path, 3, sizes=[(1480, 2246), (1476, 2240), (1481, 2243)])
    out = prepare_marble_images(inp, tmp_path / "prep")
    sizes = {(o["width"], o["height"]) for o in out}
    assert len(sizes) == 1 and max(next(iter(sizes))) == 1024  # identical size, 1024 px long side
    for o in out:
        with Image.open(o["path"]) as im:
            assert im.size == (o["width"], o["height"]) and not im.info.get("exif")
        assert o["bytes"] <= 20 * 1024 * 1024
    # originals untouched
    assert all(Image.open(i.path).size == (i.width, i.height) for i in inp)


def test_undecodable_photo_is_invalid_input(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"\xff\xd8\xff garbage")
    with pytest.raises(ReconError) as e:
        prepare_marble_images([EngineInput(path=p, name="bad.jpg")], tmp_path / "prep")
    assert e.value.category == ErrorCategory.INVALID_INPUT


def test_more_than_eight_photos_are_subsampled(tmp_path):
    fake = FakeMarble(tmp_path)
    engine(fake).run(ctx(tmp_path, photos(tmp_path, 11)))
    assert len(fake.generated_bodies[0]["world_prompt"]["multi_image_prompt"]) == 8


def test_azimuths_right_is_positive():
    def rot(yaw_deg):  # camera turned right by yaw around world -Y (COLMAP: y down)
        a = np.radians(yaw_deg)
        right = np.array([np.cos(a), 0, -np.sin(a)])
        fwd = np.array([np.sin(a), 0, np.cos(a)])
        down = np.array([0, 1.0, 0])
        return np.stack([right, down, fwd], axis=1).tolist()

    az = azimuths_from_viewpoints([{"name": "a", "rotation_c2w": rot(0)}, {"name": "b", "rotation_c2w": rot(70)},
                                   {"name": "c", "rotation_c2w": rot(-70)}])
    assert az["a"] == pytest.approx(0) and az["b"] == pytest.approx(70, abs=1e-6) and az["c"] == pytest.approx(290, abs=1e-6)


def test_price_table_matches_docs():
    assert price("marble-1.0-draft", "multi-image")["total"] == 250
    assert price("marble-1.1", "multi-image")["total"] == 1600
    assert price("marble-1.1", "pano")["total"] == 1500
    assert price("marble-1.1-plus", "multi-image")["total"] == 3100


def test_estimate_and_not_configured(tmp_path):
    e = MarbleWorldEngine(cfg=MarbleConfig(api_key=None))
    assert e.capabilities().available is False and "WLT_API_KEY" in (e.capabilities().reason or "")
    est = MarbleWorldEngine(cfg=MarbleConfig(api_key="k", model="marble-1.1")).estimate(photos(tmp_path, 3))
    assert est.credits == 1600 and est.usd == pytest.approx(1.28)


def test_single_pano_uses_image_prompt_with_is_pano(tmp_path):
    fake = FakeMarble(tmp_path)
    p = tmp_path / "pano.jpg"
    b = io.BytesIO()
    Image.new("RGB", (4096, 2048), (90, 90, 90)).save(b, format="JPEG")
    p.write_bytes(b.getvalue())
    engine(fake).run(ctx(tmp_path, [EngineInput(path=p, name="pano.jpg", upload_id="u", is_panorama=True)]))
    wp = fake.generated_bodies[0]["world_prompt"]
    assert wp["type"] == "image" and wp["is_pano"] is True
