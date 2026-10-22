#!/usr/bin/env python3
"""Seed a DEMO account + project through the public HTTP API (no DB shortcuts).

    python scripts/seed_demo.py --api http://localhost:8000 --images /path/to/jpgs [--title ...]

Everything this creates is demo data: a demo user, a project, real uploads of
the licensed Eyeful Tower sample photos, and a real reconstruction job that the
worker processes with COLMAP. State is written to --state (JSON) so later steps
(labelling rooms, sharing) can continue.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--email", default="demo@roomscape.dev")
    ap.add_argument("--password", default="Demo-Password-2026")
    ap.add_argument("--title", default="Eyeful Apartment (sample)")
    ap.add_argument("--state", type=Path, default=Path("/tmp/devdata/demo.json"))
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, headers={"X-Requested-With": "roomscape"}, timeout=300, trust_env=False)
    r = c.post("/api/v1/auth/signup", json={"email": a.email, "password": a.password, "display_name": "Demo Agent",
                                             "workspace_name": "Harbor & Pine Realty (demo)"})
    if r.status_code == 409:
        r = c.post("/api/v1/auth/signin", json={"email": a.email, "password": a.password})
    r.raise_for_status()
    me = c.get("/api/v1/me").json()
    org = me["workspaces"][0]["id"]
    mail = c.get("/api/v1/dev/outbox", params={"to": a.email}).json()["items"]
    tok = next((m["body"].split("token=")[1].split()[0] for m in mail if m["kind"] == "verify_email"), None)
    if tok:
        c.post("/api/v1/auth/verify-email", json={"token": tok})
    c.post("/api/v1/billing/checkout", json={"org_id": org, "plan_code": "PRO"})  # test mode, no money
    p = c.post("/api/v1/projects", json={"org_id": org, "title": a.title, "property_type": "apartment", "rooms_estimate": 4,
                                         "description": "Interior sample captured by the Eyeful Tower rig (Meta, MIT licence). "
                                                        "Reconstructed on CPU with COLMAP.",
                                         "address_private": "Demo data - no real address"}).json()
    files = sorted(a.images.glob("*.jpg"))
    t0 = time.time()
    for i in range(0, len(files), 12):
        batch = [("files", (f.name, f.read_bytes(), "image/jpeg")) for f in files[i:i + 12]]
        r = c.post(f"/api/v1/projects/{p['id']}/uploads", files=batch)
        r.raise_for_status()
    print(f"uploaded {len(files)} photos in {time.time() - t0:.1f}s")
    rep = c.post(f"/api/v1/projects/{p['id']}/capture-quality/analyze").json()
    for _ in range(120):
        rep = c.get(f"/api/v1/projects/{p['id']}/capture-quality").json()
        if rep["status"] not in {"PENDING", "NOT_ANALYZED"}:
            break
        time.sleep(2)
    print("capture report:", rep["status"], rep.get("report", {}).get("summary"))
    job = c.post(f"/api/v1/projects/{p['id']}/reconstruction-jobs",
                 json={"mode": "AUTO", "idempotency_key": uuid.uuid4().hex, "confirm_warnings": True}).json()
    print("job:", job.get("id"), job.get("state"))
    a.state.parent.mkdir(parents=True, exist_ok=True)
    a.state.write_text(json.dumps({"org_id": org, "project_id": p["id"], "job_id": job.get("id"), "email": a.email,
                                   "password": a.password}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
