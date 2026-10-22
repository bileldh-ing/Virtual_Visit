"""Shared pytest fixtures.

Integration tests use PostgreSQL when ROOMSCAPE_TEST_DATABASE_URL is set (CI
service container / local cluster) and fall back to SQLite otherwise. Storage
uses a temporary local directory. The queue runs in ``none`` mode so tests drive
the worker functions explicitly and deterministically.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for p in ("services/api", "services/worker", "services/reconstruction"):
    sys.path.insert(0, str(ROOT / p))

_tmp = tempfile.mkdtemp(prefix="roomscape-test-")
os.environ.setdefault("ROOMSCAPE_ENV", "test")
os.environ["ROOMSCAPE_DATABASE_URL"] = os.environ.get("ROOMSCAPE_TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["ROOMSCAPE_STORAGE_LOCAL_ROOT"] = f"{_tmp}/storage"
os.environ["ROOMSCAPE_WORKER_WORKSPACE"] = f"{_tmp}/jobs"
os.environ["ROOMSCAPE_QUEUE_MODE"] = "none"
os.environ["ROOMSCAPE_SECRET_KEY"] = "test-secret-key-0123456789abcdef"
os.environ["ROOMSCAPE_REDIS_URL"] = ""
os.environ["ROOMSCAPE_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["ROOMSCAPE_STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
# 2.0 engines: tests never call World Labs and do not download the depth model unless a test opts in.
os.environ.pop("WLT_API_KEY", None)
os.environ["ROOMSCAPE_MARBLE_AUTH_MODE"] = "header"
os.environ["ROOMSCAPE_MARBLE_LIVE_BALANCE"] = "false"
os.environ["ROOMSCAPE_DEPTH_ENGINE_ENABLED"] = "false"
os.environ["ROOMSCAPE_DEPTH_MODEL_DOWNLOAD"] = "false"


@pytest.fixture(scope="session")
def app():
    from roomscape_api.db import Base, get_engine
    from roomscape_api.main import create_app

    eng = get_engine()
    if eng.dialect.name == "postgresql":
        from roomscape_api.migrate import upgrade

        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        upgrade()  # real Alembic migrations against PostgreSQL
    else:
        Base.metadata.create_all(eng)
    return create_app()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from roomscape_api.ratelimit import reset_local

    reset_local()
    yield


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        c.headers["X-Requested-With"] = "roomscape"
        yield c


def make_image(w=800, h=600, seed=0, blur=False, dark=False, fmt="JPEG") -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
    img = Image.fromarray(arr).resize((w, h), Image.NEAREST)
    if blur:
        from PIL import ImageFilter
        img = img.filter(ImageFilter.GaussianBlur(14))
    if dark:
        img = Image.eval(img, lambda v: v // 12)
    b = io.BytesIO()
    img.save(b, format=fmt, quality=90)
    return b.getvalue()


@pytest.fixture
def signup(client):
    def _signup(email=None, password="Sup3r-secret-pw"):
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        from fastapi.testclient import TestClient

        c = TestClient(client.app)
        c.headers["X-Requested-With"] = "roomscape"
        r = c.post("/api/v1/auth/signup", json={"email": email, "password": password, "display_name": "Test"})
        assert r.status_code == 201, r.text
        org_id = r.json()["org_id"]
        return c, org_id, email
    return _signup
