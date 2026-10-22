"""API integration tests (PostgreSQL in CI, SQLite locally if no DB is configured).

The reconstruction engine is replaced by a clearly-labelled test double in the
job-lifecycle tests so orchestration (leases, retries, artifacts, accounting)
can be verified in seconds. The real COLMAP path is covered by
tests/integration/test_worker_colmap.py and the reconstruction acceptance job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from conftest import make_image


def _photos(n=10, offset=0):
    return [("files", (f"room_{i}.jpg", make_image(seed=i + offset), "image/jpeg")) for i in range(n)]


def outbox(client, email):
    return client.get("/api/v1/dev/outbox", params={"to": email}).json()["items"]


def verify(c, email):
    body = next(m for m in outbox(c, email) if m["kind"] == "verify_email")["body"]
    token = body.split("token=")[1].split()[0]
    assert c.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200


def new_project(c, org_id, title="Flat"):
    r = c.post("/api/v1/projects", json={"org_id": org_id, "title": title, "address_private": "1 Secret St"})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ test double engine

def fake_photogrammetry(input_dir, work_dir, out_dir, config=None, on_stage=lambda *a: None, should_cancel=lambda: False,
                        engine_info=None, image_map=None):
    """TEST DOUBLE: writes small but valid artifacts with the real writers/validators."""
    from roomscape_recon.artifacts import build_manifest, validate_ply, write_ply
    from roomscape_recon.pipeline import PipelineResult

    out_dir.mkdir(parents=True, exist_ok=True)
    for st in ("INPUT_VALIDATION", "CAMERA_POSE_ESTIMATION", "SPARSE_RECONSTRUCTION"):
        on_stage(st, "started", {})
        on_stage(st, "completed", {})
    on_stage("DENSE_RECONSTRUCTION", "skipped", {"reason": "test"})
    on_stage("ARTIFACT_OPTIMIZATION", "started", {})
    xyz = np.random.default_rng(0).normal(size=(50, 3)).astype(np.float32)
    write_ply(out_dir / "sparse_points_web.ply", xyz, np.full((50, 3), 128, np.uint8))
    names = sorted(p.stem for p in Path(input_dir).iterdir())
    vps = [{"name": f"{n}.jpg", "upload_id": n, "position": [i, 0, 0], "rotation_c2w": np.eye(3).tolist(), "width": 800,
            "height": 600, "focal_px": 700} for i, n in enumerate(names)]
    (out_dir / "cameras.json").write_text(json.dumps({"scale": "unknown", "viewpoints": vps,
                                                       "viewer_transform": {"origin": [0, 0, 0], "rotation": np.eye(3).tolist(), "extent": 2}}))
    (out_dir / "quality_report.json").write_text("{}")
    on_stage("ARTIFACT_VALIDATION", "started", {})
    validate_ply(out_dir / "sparse_points_web.ply")
    man = build_manifest(out_dir, [{"path": "sparse_points_web.ply", "kind": "POINT_CLOUD_WEB"},
                                   {"path": "cameras.json", "kind": "VIEWPOINTS"},
                                   {"path": "quality_report.json", "kind": "QUALITY_REPORT"}], {"name": "test-double"})
    if should_cancel():
        from roomscape_recon.errors import ErrorCategory, ReconError
        raise ReconError(ErrorCategory.CANCELLED, "cancelled")
    return PipelineResult(status="SUCCEEDED", output_type="sparse_point_cloud", stages_completed=["SPARSE_RECONSTRUCTION"],
                          metrics={"images_registered": len(names), "processing_seconds": 61.0, "sparse_points": 50},
                          warnings=[], limitations=["test double"], recapture_suggestions=[], artifacts=man["artifacts"],
                          duration_s=61.0, engine={"name": "test-double"}, steps=[])


@pytest.fixture
def ready_project(signup):
    c, org, email = signup()
    p = new_project(c, org)
    r = c.post(f"/api/v1/projects/{p['id']}/uploads", files=_photos(10))
    assert r.status_code == 201 and len(r.json()["created"]) == 10, r.text
    return c, org, email, p


# ------------------------------------------------------------------ auth

def test_signup_signin_session_and_csrf(client, signup):
    c, org, email = signup()
    me = c.get("/api/v1/me").json()
    assert me["user"]["email"] == email and me["workspaces"][0]["role"] == "OWNER"
    # cookie-authenticated unsafe request without the CSRF header is refused
    r = c.post("/api/v1/projects", json={"org_id": org, "title": "x"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_CHECK_FAILED"
    assert c.post("/api/v1/auth/signout").status_code == 200
    assert c.get("/api/v1/me").status_code == 401
    bad = client.post("/api/v1/auth/signin", json={"email": email, "password": "wrong-Password1"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert "request_id" in bad.json()["error"]
    ok = client.post("/api/v1/auth/signin", json={"email": email, "password": "Sup3r-secret-pw"})
    assert ok.status_code == 200


def test_weak_password_and_duplicate_email(client, signup):
    r = client.post("/api/v1/auth/signup", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"
    _, _, email = signup()
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": "Another-pass-123"})
    assert r.status_code == 409


def test_email_verification_and_password_reset(client, signup):
    c, _, email = signup()
    assert c.get("/api/v1/me").json()["user"]["email_verified"] is False
    verify(c, email)
    assert c.get("/api/v1/me").json()["user"]["email_verified"] is True
    assert client.post("/api/v1/auth/forgot-password", json={"email": email}).json() == {"ok": True}
    assert client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}).json() == {"ok": True}
    body = next(m for m in outbox(c, email) if m["kind"] == "password_reset")["body"]
    token = body.split("token=")[1].split()[0]
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "password": "Brand-new-pass-42"}).status_code == 200
    assert c.get("/api/v1/me").status_code == 401  # all sessions revoked
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "password": "Brand-new-pass-43"}).status_code == 400
    assert client.post("/api/v1/auth/signin", json={"email": email, "password": "Brand-new-pass-42"}).status_code == 200


def test_signin_rate_limited(client, signup):
    _, _, email = signup()
    codes = [client.post("/api/v1/auth/signin", json={"email": email, "password": "Wrong-pass-000"}).status_code for _ in range(12)]
    assert 429 in codes


def test_sessions_list_and_revoke(signup):
    c, _, _ = signup()
    sessions = c.get("/api/v1/auth/sessions").json()["sessions"]
    assert len(sessions) == 1 and sessions[0]["current"]
    assert c.delete(f"/api/v1/auth/sessions/{sessions[0]['id']}").status_code == 200
    assert c.get("/api/v1/me").status_code == 401


# ------------------------------------------------------------------ projects & authorization

def test_project_crud_and_optimistic_concurrency(signup):
    c, org, _ = signup()
    p = new_project(c, org, "Seaside flat")
    lst = c.get("/api/v1/projects", params={"org_id": org, "q": "seaside"}).json()
    assert lst["total"] == 1
    r = c.patch(f"/api/v1/projects/{p['id']}", json={"version": p["version"], "title": "Renamed"})
    assert r.status_code == 200 and r.json()["version"] == p["version"] + 1
    stale = c.patch(f"/api/v1/projects/{p['id']}", json={"version": p["version"], "title": "Stale"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_cross_user_access_is_404(signup):
    a, org_a, _ = signup()
    b, org_b, _ = signup()
    p = new_project(a, org_a)
    assert b.get(f"/api/v1/projects/{p['id']}").status_code == 404
    assert b.patch(f"/api/v1/projects/{p['id']}", json={"version": 1, "title": "x"}).status_code == 404
    assert b.delete(f"/api/v1/projects/{p['id']}").status_code == 404
    assert b.get("/api/v1/projects", params={"org_id": org_a}).status_code == 404
    assert b.post(f"/api/v1/projects/{p['id']}/uploads", files=_photos(1)).status_code == 404
    assert b.post("/api/v1/projects", json={"org_id": org_a, "title": "intrude"}).status_code == 404
    assert b.get(f"/api/v1/projects/{uuid.uuid4()}").status_code == 404


def test_team_roles_viewer_cannot_edit(signup):
    owner, org, _ = signup()
    viewer, _, vemail = signup()
    owner.post("/api/v1/billing/checkout", json={"org_id": org, "plan_code": "PRO"})  # test mode: seats
    r = owner.post(f"/api/v1/orgs/{org}/invitations", json={"email": vemail, "role": "VIEWER"})
    assert r.status_code == 201
    body = next(m for m in outbox(owner, vemail) if m["kind"] == "invitation")["body"]
    token = body.split("token=")[1].split()[0]
    assert viewer.post("/api/v1/invitations/accept", json={"token": token}).status_code == 200
    p = new_project(owner, org)
    assert viewer.get(f"/api/v1/projects/{p['id']}").status_code == 200
    assert viewer.patch(f"/api/v1/projects/{p['id']}", json={"version": 1, "title": "x"}).status_code == 403
    assert viewer.post(f"/api/v1/projects/{p['id']}/uploads", files=_photos(1)).status_code == 403
    members = owner.get(f"/api/v1/orgs/{org}/members").json()["members"]
    owner_m = next(m for m in members if m["role"] == "OWNER")
    r = owner.patch(f"/api/v1/orgs/{org}/members/{owner_m['id']}", json={"role": "ADMIN"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_OWNER"
    assert viewer.get(f"/api/v1/orgs/{org}/audit").status_code == 403
    actions = [e["action"] for e in owner.get(f"/api/v1/orgs/{org}/audit").json()["events"]]
    assert "org.member_invited" in actions and "org.member_joined" in actions


# ------------------------------------------------------------------ uploads

def test_upload_validation_dedupe_and_signed_files(signup):
    c, org, _ = signup()
    p = new_project(c, org)
    files = _photos(2) + [("files", ("../../etc/passwd.jpg", b"not an image at all", "image/jpeg")),
                          ("files", ("evil.png", make_image()[:300], "image/png"))]
    r = c.post(f"/api/v1/projects/{p['id']}/uploads", files=files).json()
    assert len(r["created"]) == 2
    codes = {x["code"] for x in r["rejected"]}
    assert codes == {"UNSUPPORTED_MEDIA_TYPE", "IMAGE_DECODE_FAILED"}
    assert all("/" not in x["name"] for x in r["rejected"])
    again = c.post(f"/api/v1/projects/{p['id']}/uploads", files=_photos(2)).json()
    assert len(again["duplicates"]) == 2 and not again["created"]
    up = r["created"][0]
    thumb = c.get(up["thumb_url"])
    assert thumb.status_code == 200 and thumb.content[:3] == b"\xff\xd8\xff"
    from PIL import Image
    import io
    assert not Image.open(io.BytesIO(c.get(up["web_url"]).content)).getexif()  # EXIF stripped
    tampered = up["thumb_url"].replace("sig=", "sig=0")
    assert c.get(tampered).status_code == 403


def test_capture_quality_report(ready_project):
    from roomscape_worker.tasks import analyze_capture

    c, _, _, p = ready_project
    r = c.post(f"/api/v1/projects/{p['id']}/capture-quality/analyze").json()
    assert r["status"] == "PENDING"
    analyze_capture(r["id"])
    rep = c.get(f"/api/v1/projects/{p['id']}/capture-quality").json()
    assert rep["status"] in {"READY", "READY_WITH_WARNINGS"} and rep["stale"] is False
    assert rep["report"]["summary"]["decoded"] == 10
    assert all(i["upload_id"] for i in rep["report"]["images"])
    again = c.post(f"/api/v1/projects/{p['id']}/capture-quality/analyze").json()
    assert again["id"] == r["id"]  # same inputs -> same report (idempotent)


# ------------------------------------------------------------------ jobs

def _start(c, p, key=None, confirm=True):
    return c.post(f"/api/v1/projects/{p['id']}/reconstruction-jobs",
                  json={"mode": "AUTO", "idempotency_key": key or uuid.uuid4().hex, "confirm_warnings": confirm})


def test_job_lifecycle_with_test_double(monkeypatch, ready_project):
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.models import UsageLedger
    from roomscape_worker import tasks
    from sqlalchemy import func, select

    monkeypatch.setattr(tasks, "run_photogrammetry", fake_photogrammetry)
    c, org, _, p = ready_project
    key = uuid.uuid4().hex
    j = _start(c, p, key).json()
    assert j["state"] == "QUEUED" and j["mode"] == "PHOTOGRAMMETRY"
    assert _start(c, p, key).json()["id"] == j["id"]  # idempotent create
    dup = _start(c, p)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "JOB_ALREADY_RUNNING"
    assert tasks.process_job(j["id"]) == "SUCCEEDED"
    assert tasks.process_job(j["id"]) == "skipped"  # duplicate queue message is harmless
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["state"] == "SUCCEEDED" and job["attempt_count"] == 1
    stages = [e["stage"] for e in job["events"]]
    assert stages.index("INPUT_VALIDATION") < stages.index("SPARSE_RECONSTRUCTION") < stages.index("SUCCEEDED")
    arts = c.get(f"/api/v1/projects/{p['id']}/artifacts").json()["artifacts"]
    kinds = {a["kind"] for a in arts}
    assert {"POINT_CLOUD_WEB", "VIEWPOINTS"} <= kinds and all(a["validation"]["ok"] for a in arts)
    ply = c.get(next(a["url"] for a in arts if a["kind"] == "POINT_CLOUD_WEB"))
    assert ply.status_code == 200 and ply.content.startswith(b"ply")
    assert hashlib.sha256(ply.content).hexdigest() == next(a["sha256"] for a in arts if a["kind"] == "POINT_CLOUD_WEB")
    tour = c.get(f"/api/v1/projects/{p['id']}/tour").json()
    assert len(tour["nodes"]) == 10 and all(n["source"] == "reconstruction" for n in tour["nodes"])
    with get_sessionmaker()() as db:
        n = db.scalar(select(func.count()).select_from(UsageLedger).where(UsageLedger.job_id == uuid.UUID(j["id"])))
        assert n == 1
    usage = c.get("/api/v1/usage", params={"org_id": org}).json()
    assert usage["usage"]["reconstruction_minutes"] == 2.0  # 61 s rounds up to 2 minutes, charged once


def test_confirmation_required_when_capture_has_warnings(ready_project):
    from roomscape_worker.tasks import analyze_capture

    c, _, _, p = ready_project
    c.post(f"/api/v1/projects/{p['id']}/uploads", files=[("files", ("dark.jpg", make_image(seed=99, dark=True), "image/jpeg"))])
    r = c.post(f"/api/v1/projects/{p['id']}/capture-quality/analyze").json()
    analyze_capture(r["id"])
    resp = _start(c, p, confirm=False)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_retryable_failure_then_success_keeps_history(monkeypatch, ready_project):
    from roomscape_recon.errors import ErrorCategory, ReconError
    from roomscape_worker import tasks

    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ReconError(ErrorCategory.OUT_OF_MEMORY, "killed", "detail /secret/path")
        return fake_photogrammetry(*a, **kw)

    monkeypatch.setattr(tasks, "run_photogrammetry", flaky)
    c, _, _, p = ready_project
    j = _start(c, p).json()
    assert tasks.process_job(j["id"]) == "OUT_OF_MEMORY"
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["state"] == "QUEUED" and job["next_attempt_at"]
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.models import ReconstructionJob
    with get_sessionmaker()() as db:
        db.get(ReconstructionJob, uuid.UUID(j["id"])).next_attempt_at = None
        db.commit()
    assert tasks.process_job(j["id"]) == "SUCCEEDED"
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert [a["state"] for a in job["attempts"]] == ["FAILED", "SUCCEEDED"]


def test_deterministic_failure_is_not_retried(monkeypatch, ready_project):
    from roomscape_recon.errors import ErrorCategory, ReconError
    from roomscape_worker import tasks

    def bad(*a, **kw):
        raise ReconError(ErrorCategory.INSUFFICIENT_OVERLAP, "No camera poses could be estimated.")

    monkeypatch.setattr(tasks, "run_photogrammetry", bad)
    c, _, _, p = ready_project
    j = _start(c, p).json()
    tasks.process_job(j["id"])
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["state"] == "FAILED" and job["error"]["category"] == "INSUFFICIENT_OVERLAP"
    assert "No camera poses" in job["error"]["message"]
    r = c.post(f"/api/v1/reconstruction-jobs/{j['id']}/retry")
    assert r.status_code == 202 and r.json()["state"] == "QUEUED"


def test_cancel_queued_job(ready_project):
    c, _, _, p = ready_project
    j = _start(c, p).json()
    r = c.post(f"/api/v1/reconstruction-jobs/{j['id']}/cancel").json()
    assert r["state"] == "CANCELLED"
    from roomscape_worker.tasks import process_job
    assert process_job(j["id"]) == "skipped"


def test_worker_crash_recovered_by_reconciler(ready_project):
    """Simulate a worker that claimed the job then died (no heartbeats)."""
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.jobs_service import transition
    from roomscape_api.models import JobAttempt, ReconstructionJob
    from roomscape_worker.reconciler import reconcile_once

    c, _, _, p = ready_project
    j = _start(c, p).json()
    with get_sessionmaker()() as db:
        job = db.get(ReconstructionJob, uuid.UUID(j["id"]))
        job.attempt_count = 1
        att = JobAttempt(job_id=job.id, number=1, worker_id="dead-worker:1",
                         lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=5))
        db.add(att)
        db.flush()
        transition(db, job, "INPUT_VALIDATION", attempt_id=att.id)
        transition(db, job, "SPARSE_RECONSTRUCTION", attempt_id=att.id)
        db.commit()
    out = reconcile_once()
    assert out["lost_attempts"] >= 1 and out["requeued"] >= 1
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert job["state"] == "QUEUED" and job["attempts"][0]["state"] == "LOST"
    assert any(e["data"].get("reason") == "WORKER_LOST" for e in job["events"])


# ------------------------------------------------------------------ sharing

@pytest.fixture
def finished_project(monkeypatch, ready_project):
    from roomscape_worker import tasks

    monkeypatch.setattr(tasks, "run_photogrammetry", fake_photogrammetry)
    c, org, email, p = ready_project
    j = _start(c, p).json()
    assert tasks.process_job(j["id"]) == "SUCCEEDED"
    return c, org, email, p


def test_share_link_requires_verification_and_consent(finished_project):
    c, _, email, p = finished_project
    r = c.post(f"/api/v1/projects/{p['id']}/share-links", json={"visibility": "UNLISTED"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    verify(c, email)
    r = c.post(f"/api/v1/projects/{p['id']}/share-links", json={"visibility": "UNLISTED"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_public_tour_share_and_revoke(client, finished_project):
    c, _, email, p = finished_project
    verify(c, email)
    link = c.post(f"/api/v1/projects/{p['id']}/share-links", json={"visibility": "UNLISTED", "publish_consent": True}).json()
    token = link["token"]
    assert "token" not in c.get(f"/api/v1/projects/{p['id']}/share-links").json()["items"][0]  # shown once
    anon = client.__class__(client.app)
    tour = anon.get(f"/api/v1/public/tours/{token}")
    assert tour.status_code == 200
    body = tour.json()
    assert "address_private" not in json.dumps(body) and "1 Secret St" not in json.dumps(body)
    art = next(a for a in body["artifacts"] if a["kind"] == "POINT_CLOUD_WEB")
    assert anon.get(art["url"]).status_code == 200
    img = body["tour"]["nodes"][0]["image_url"]
    assert anon.get(img).status_code == 200
    orig = img.replace("/web?", "/original?")
    assert anon.get(orig).status_code in {403, 404}
    assert c.delete(f"/api/v1/share-links/{link['id']}").status_code == 200
    assert anon.get(f"/api/v1/public/tours/{token}").status_code == 410
    assert anon.get(art["url"]).status_code == 410  # signed URL minted for the link stops working
    assert anon.get("/api/v1/public/tours/not-a-real-token").status_code == 404


def test_password_and_restricted_links(client, finished_project, signup):
    c, org, email, p = finished_project
    verify(c, email)
    c.post("/api/v1/billing/checkout", json={"org_id": org, "plan_code": "PRO"})
    pw = c.post(f"/api/v1/projects/{p['id']}/share-links",
                json={"visibility": "UNLISTED", "publish_consent": True, "password": "letmein-7"}).json()
    anon = client.__class__(client.app)
    assert anon.get(f"/api/v1/public/tours/{pw['token']}").json()["error"]["code"] == "PASSWORD_REQUIRED"
    assert anon.post(f"/api/v1/public/tours/{pw['token']}/unlock", json={"password": "nope"}).status_code == 401
    unlock = anon.post(f"/api/v1/public/tours/{pw['token']}/unlock", json={"password": "letmein-7"}).json()["unlock"]
    assert anon.get(f"/api/v1/public/tours/{pw['token']}", headers={"X-Share-Unlock": unlock}).status_code == 200
    rs = c.post(f"/api/v1/projects/{p['id']}/share-links", json={"visibility": "RESTRICTED"}).json()
    assert anon.get(f"/api/v1/public/tours/{rs['token']}").status_code == 401
    outsider, _, _ = signup()
    assert outsider.get(f"/api/v1/public/tours/{rs['token']}").status_code == 401
    assert c.get(f"/api/v1/public/tours/{rs['token']}").status_code == 200


def test_expired_link(client, finished_project):
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.models import ShareLink

    c, _, email, p = finished_project
    verify(c, email)
    link = c.post(f"/api/v1/projects/{p['id']}/share-links", json={"visibility": "UNLISTED", "publish_consent": True}).json()
    with get_sessionmaker()() as db:
        db.get(ShareLink, uuid.UUID(link["id"])).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.__class__(client.app).get(f"/api/v1/public/tours/{link['token']}").status_code == 410


# ------------------------------------------------------------------ deletion

def test_project_deletion_cleans_storage(finished_project):
    from roomscape_api.db import get_sessionmaker
    from roomscape_api.models import Project
    from roomscape_api.storage import get_storage, project_prefix
    from roomscape_worker.tasks import cleanup_project

    c, _, _, p = finished_project
    arts = c.get(f"/api/v1/projects/{p['id']}/artifacts").json()["artifacts"]
    with get_sessionmaker()() as db:
        proj = db.get(Project, uuid.UUID(p["id"]))
        prefix = project_prefix(proj.org_id, proj.id)
    assert get_storage().list(prefix)
    r = c.delete(f"/api/v1/projects/{p['id']}").json()
    assert c.get(f"/api/v1/projects/{p['id']}").status_code == 404  # access revoked immediately
    assert c.get(arts[0]["url"]).status_code == 404
    assert cleanup_project(r["deletion_request_id"]) == "done"
    st = c.get(f"/api/v1/deletion-requests/{r['deletion_request_id']}").json()
    assert st["status"] == "done" and st["verified_empty"] and st["keys_deleted"] > 0
    assert get_storage().list(prefix) == []


# ------------------------------------------------------------------ billing

def _sig(payload: bytes, secret="whsec_test_secret", ts=None):
    ts = ts or int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


def test_test_mode_plan_change_and_webhooks(client, signup):
    c, org, _ = signup()
    r = c.post("/api/v1/billing/checkout", json={"org_id": org, "plan_code": "PRO"}).json()
    assert r["mode"] == "test" and "no payment" in r["note"].lower()
    ev = {"id": "evt_1", "type": "invoice.payment_failed", "created": int(time.time()),
          "data": {"object": {"metadata": {"org_id": org}}}}
    payload = json.dumps(ev).encode()
    assert client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sig(payload, "bad")}).status_code == 400
    assert client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sig(payload)}).json() == {"ok": True}
    assert client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _sig(payload)}).json()["duplicate"]
    assert c.get("/api/v1/usage", params={"org_id": org}).json()["plan"]["status"] == "past_due"
    old = dict(ev, id="evt_0", type="customer.subscription.updated", created=int(time.time()) - 3600)
    old["data"] = {"object": {"metadata": {"org_id": org}, "status": "active"}}
    p2 = json.dumps(old).encode()
    client.post("/api/v1/billing/webhook", content=p2, headers={"Stripe-Signature": _sig(p2)})
    assert c.get("/api/v1/usage", params={"org_id": org}).json()["plan"]["status"] == "past_due"  # out-of-order ignored
    stale = _sig(payload, ts=int(time.time()) - 3600)
    assert client.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": stale}).status_code == 400


# ------------------------------------------------------------------ admin & ops

def test_admin_routes_require_platform_admin(signup):
    user, _, _ = signup()
    assert user.get("/api/v1/admin/overview").status_code == 403
    assert user.get("/api/v1/admin/jobs").status_code == 403
    admin, _, _ = signup(email="admin@example.com")
    ov = admin.get("/api/v1/admin/overview")
    assert ov.status_code == 200 and "jobs_by_state" in ov.json()
    assert admin.get("/api/v1/admin/audit").status_code == 200


def test_health_metrics_and_error_shape(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert "roomscape_http_requests_total" in client.get("/metrics").text
    r = client.get("/api/v1/projects/not-a-uuid")
    body = r.json()["error"]
    assert r.status_code in {401, 422} and {"code", "message", "request_id", "retryable"} <= set(body)
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_account_export_and_deletion(signup):
    c, org, email = signup()
    new_project(c, org)
    exp = c.get("/api/v1/me/export").json()
    assert exp["user"]["email"] == email and len(exp["projects"]) == 1
    assert c.post("/api/v1/me/delete", json={"password": "wrong", "confirm": "DELETE"}).status_code == 403
    assert c.post("/api/v1/me/delete", json={"password": "Sup3r-secret-pw", "confirm": "DELETE"}).status_code == 200
    assert c.get("/api/v1/me").status_code == 401
