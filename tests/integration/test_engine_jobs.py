"""2.0 engine jobs through the real API + worker + DB with a fake World Labs transport (no network, no key).

* a Marble job whose worker dies mid-poll is recovered by the reconciler and resumes the *same*
  provider operation (one paid call, one ledger entry)
* an expired key / exhausted credits switch the job to the free engine and the job records why
* the engines endpoint shows estimates and the chain before a job starts
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).parent))
from marble_fixtures import OP_ID, WORLD_ID, FakeMarble  # noqa: E402
from test_api_flows import _photos, new_project  # noqa: E402


class CrashForTest(BaseException):
    """Simulates the worker process dying (not an Exception, so nothing in the worker catches it)."""


@pytest.fixture
def marble_env(monkeypatch, tmp_path):
    from roomscape_api import engines as api_engines
    from roomscape_api.config import get_settings
    from roomscape_worker import tasks

    monkeypatch.setenv("WLT_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ROOMSCAPE_MARBLE_MODEL", "marble-1.0-draft")
    get_settings.cache_clear()
    api_engines.reset_status_cache()
    fake = FakeMarble(tmp_path)
    real = api_engines.build_registry

    def build(probe_local, s=None, marble_transport=None):
        reg = real(probe_local=probe_local, s=s, marble_transport=fake.transport())
        m = reg.get("marble_world")
        m.sleep = lambda _s: None
        m.cfg.poll_initial_s = m.cfg.poll_max_s = 0
        m.pose_solver = None
        return reg

    monkeypatch.setattr(tasks, "build_registry", build)
    monkeypatch.setattr(api_engines, "build_registry", build)
    from roomscape_api.routers import jobs as jobs_router

    monkeypatch.setattr(jobs_router, "build_registry", build)
    yield fake
    monkeypatch.delenv("WLT_API_KEY", raising=False)
    get_settings.cache_clear()
    api_engines.reset_status_cache()


def _project(signup, n=4):
    c, org, _ = signup()
    p = new_project(c, org)
    assert c.post(f"/api/v1/projects/{p['id']}/uploads", files=_photos(n)).status_code == 201
    return c, org, p


def _start(c, p, engine="marble_world", allow_fallback=True):
    return c.post(f"/api/v1/projects/{p['id']}/reconstruction-jobs",
                  json={"engine": engine, "allow_fallback": allow_fallback, "idempotency_key": uuid.uuid4().hex,
                        "confirm_warnings": True})


def test_engines_endpoint_shows_estimates_and_chain(marble_env, signup):
    c, _, p = _project(signup)
    r = c.get(f"/api/v1/projects/{p['id']}/engines").json()
    names = {e["name"]: e for e in r["engines"]}
    assert names["marble_world"]["runnable"] is True
    assert names["marble_world"]["estimate"]["credits"] == 250  # draft, multi-image (documented price)
    assert names["gaussian_splat_local"]["runnable"] is False
    assert r["chain"]["primary"] == "marble_world" and r["marble"]["configured"] is True


def test_marble_job_resumes_same_operation_after_worker_crash(marble_env, signup, monkeypatch):
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.models import JobAttempt, ReconstructionJob, UsageLedger
    from roomscape_recon.engines.marble import MarbleWorldEngine
    from roomscape_worker import reconciler, tasks
    from sqlalchemy import select

    fake = marble_env
    fake.polls_until_done = 3
    c, org, p = _project(signup)
    j = _start(c, p).json()
    assert j["engine"] == "marble_world" and j["cost_estimate"]["chain"]["primary"] == "marble_world"

    real_poll = MarbleWorldEngine._poll

    def crash(self, *a, **kw):
        raise CrashForTest()

    monkeypatch.setattr(MarbleWorldEngine, "_poll", crash)
    with pytest.raises(CrashForTest):
        tasks.process_job(j["id"])
    with get_sessionmaker()() as db:
        job = db.get(ReconstructionJob, uuid.UUID(j["id"]))
        assert job.provider_state["marble_world"]["operation_id"] == OP_ID  # persisted before polling
        att = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id))
        assert att.state == "RUNNING"
        att.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)  # the dead worker stops heart-beating
        db.commit()
    monkeypatch.setattr(MarbleWorldEngine, "_poll", real_poll)
    assert reconciler.reconcile_once()["requeued"] == 1
    assert tasks.process_job(j["id"]) == "SUCCEEDED"
    assert fake.count("POST", "/marble/v1/worlds:generate") == 1  # resumed, never paid twice
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["engine_used"] == "marble_world" and job["fallback"] is None and job["attempt_count"] == 2
    assert job["provider"]["world_id"] == WORLD_ID and job["result"]["metrics"]["resumed"] is True
    assert job["result"]["honesty"] == "ai_generated"
    arts = {a["kind"] for a in c.get(f"/api/v1/projects/{p['id']}/artifacts").json()["artifacts"]}
    assert {"SPLAT_SPZ", "COLLIDER_MESH", "PANORAMA", "WORLD"} <= arts
    with get_sessionmaker()() as db:
        led = db.scalars(select(UsageLedger).where(UsageLedger.job_id == uuid.UUID(j["id"]))).all()
        assert [(entry.kind, entry.idempotency_key) for entry in led] == [("marble_credits", f"job:{j['id']}:marble:{OP_ID}")]
    tour = c.get(f"/api/v1/projects/{p['id']}/tour").json()
    assert len(tour["nodes"]) == 4


@pytest.mark.parametrize("override,code", [([401], "PROVIDER_AUTH"), ([402], "PROVIDER_CREDITS")])
def test_expired_key_or_no_credits_switches_to_free_engine(marble_env, signup, monkeypatch, override, code):
    from roomscape_recon.engines import local as local_engines
    from roomscape_worker import tasks
    from test_api_flows import _colmap_present, fake_photogrammetry

    fake = marble_env
    monkeypatch.setattr(local_engines, "run_photogrammetry", fake_photogrammetry)
    monkeypatch.setattr(local_engines, "detect", _colmap_present)
    c, _, p = _project(signup)
    j = _start(c, p).json()
    fake.overrides[("GET", "/marble/v1/credits")] = list(override) * 3
    assert tasks.process_job(j["id"]) == "SUCCEEDED"
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    # depth engine is disabled in the test environment, so the chain ends at COLMAP sparse
    assert job["engine_used"] == "colmap_sparse"
    assert job["fallback"]["from"] == "marble_world" and job["fallback"]["reason_code"] == code
    assert job["fallback"]["notice"].startswith("Marble unavailable:")
    kinds = [e["event"] for e in job["events"]]
    assert "engine_fallback" in kinds and "engine_failed" in kinds
    assert fake.count("POST", "/marble/v1/worlds:generate") == 0


def test_explicit_engine_without_fallback_fails_cleanly(marble_env, signup):
    from roomscape_worker import tasks

    fake = marble_env
    c, _, p = _project(signup)
    j = _start(c, p, allow_fallback=False).json()
    fake.overrides[("GET", "/marble/v1/credits")] = [401]
    assert tasks.process_job(j["id"]) == "PROVIDER_AUTH"
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["state"] == "FAILED" and job["error"]["category"] == "PROVIDER_AUTH"
    assert "test-key-not-real" not in str(job)
