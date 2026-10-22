"""Real-engine tests: API -> worker -> COLMAP -> artifacts. No test doubles.

Skipped automatically when COLMAP is not installed. The positive case also needs
the licensed sample fixture (scripts/fetch_fixture.py); set
ROOMSCAPE_FIXTURE_DIR to its images directory.
"""

from __future__ import annotations

import io
import os
import shutil
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(shutil.which("colmap") is None, reason="COLMAP not installed")


def _noise_jpegs(n=6):
    out = []
    for i in range(n):
        arr = np.random.default_rng(1000 + i).integers(0, 255, (480, 640, 3), dtype=np.uint8)
        b = io.BytesIO()
        Image.fromarray(arr).save(b, format="JPEG", quality=92)
        out.append(("files", (f"noise_{i}.jpg", b.getvalue(), "image/jpeg")))
    return out


def test_unrelated_images_fail_with_clear_category(signup):
    from roomscape_worker.tasks import process_job

    c, org, _ = signup()
    p = c.post("/api/v1/projects", json={"org_id": org, "title": "noise"}).json()
    c.post(f"/api/v1/projects/{p['id']}/uploads", files=_noise_jpegs())
    j = c.post(f"/api/v1/projects/{p['id']}/reconstruction-jobs",
               json={"idempotency_key": uuid.uuid4().hex, "confirm_warnings": True}).json()
    result = process_job(j["id"])
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    assert result in {"INSUFFICIENT_OVERLAP", "RECONSTRUCTION_FAILED"}
    assert job["state"] == "FAILED" and job["error"]["category"] in {"INSUFFICIENT_OVERLAP", "RECONSTRUCTION_FAILED"}
    assert c.get(f"/api/v1/projects/{p['id']}/artifacts").json()["artifacts"] == []


@pytest.mark.skipif(not os.environ.get("ROOMSCAPE_FIXTURE_DIR"), reason="sample fixture not downloaded")
def test_real_reconstruction_end_to_end(signup):
    from roomscape_worker.tasks import process_job

    images = sorted(Path(os.environ["ROOMSCAPE_FIXTURE_DIR"]).glob("*.jpg"))
    c, org, _ = signup()
    p = c.post("/api/v1/projects", json={"org_id": org, "title": "Eyeful apartment"}).json()
    for i in range(0, len(images), 20):
        files = [("files", (f.name, f.read_bytes(), "image/jpeg")) for f in images[i:i + 20]]
        assert c.post(f"/api/v1/projects/{p['id']}/uploads", files=files).status_code == 201
    j = c.post(f"/api/v1/projects/{p['id']}/reconstruction-jobs",
               json={"idempotency_key": uuid.uuid4().hex, "confirm_warnings": True}).json()
    assert process_job(j["id"]) in {"SUCCEEDED", "PARTIAL_SUCCESS"}
    job = c.get(f"/api/v1/reconstruction-jobs/{j['id']}").json()
    m = job["result"]["metrics"]
    assert m["images_registered"] >= 10 and m["sparse_points"] >= 500
    assert m["mean_reprojection_error_px"] < 2.0 and m["scale"] == "unknown"
    arts = {a["kind"]: a for a in c.get(f"/api/v1/projects/{p['id']}/artifacts").json()["artifacts"]}
    ply = c.get(arts["POINT_CLOUD_WEB"]["url"]).content
    assert ply.startswith(b"ply") and b"element vertex" in ply[:400]
    cams = c.get(arts["VIEWPOINTS"]["url"]).json()
    assert len(cams["viewpoints"]) == m["images_registered"]
