#!/usr/bin/env python3
"""Reconstruction acceptance check (used by CI): real COLMAP on a subset of the licensed fixture.

    python scripts/recon_acceptance.py --from-position 82 --out recon-out

Copies the fixture photos from a capture position onwards (all three rig cameras, which overlap),
runs ``roomscape-recon run`` on CPU, then asserts the artifact contract:
status, registered images, sparse points, reprojection error, and manifest checksums
(``roomscape-recon verify``). Thresholds are deliberately conservative; real numbers
are printed so regressions are visible in the job log.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="eyeful_apartment_48")
    ap.add_argument("--from-position", type=int, default=82,
                    help="use every rig camera from this capture position on (positions >= 82: 7 positions x 3 cameras = 21 photos)")
    ap.add_argument("--out", type=Path, default=Path("recon-out"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--min-registered-fraction", type=float, default=0.5)
    ap.add_argument("--min-points", type=int, default=150)
    ap.add_argument("--max-reproj", type=float, default=2.0)
    a = ap.parse_args()
    src = ROOT / "tests" / "fixtures" / "reconstruction" / a.fixture / "images"
    files = sorted(f for f in src.glob("*.jpg") if int(f.stem.split("DSC")[-1]) >= a.from_position)
    if len(files) < 3:
        print(f"fixture has only {len(files)} images; run scripts/fetch_fixture.py first", file=sys.stderr)
        return 1
    imgs = Path(tempfile.mkdtemp(prefix="acc-imgs-"))
    for f in files:
        shutil.copy(f, imgs / f.name)
    a.out.mkdir(parents=True, exist_ok=True)
    print(f"reconstructing {len(files)} photos: {', '.join(f.name for f in files)}")
    run = subprocess.run(["roomscape-recon", "run", str(imgs), str(a.out), "--threads", str(a.threads), "--dense", "off"],
                         capture_output=True, text=True)
    print(run.stdout)
    print(run.stderr[-4000:], file=sys.stderr)
    if not (a.out / "result.json").exists():
        print("FAIL  reconstruction produced no result.json")
        return 1
    res = json.loads((a.out / "result.json").read_text())
    m = res["metrics"]
    checks = {
        "status ok": res["status"] in {"SUCCEEDED", "PARTIAL_SUCCESS"},
        f"registered >= {a.min_registered_fraction:.0%}": (m.get("images_registered") or 0) >= a.min_registered_fraction * len(files),
        f"sparse points >= {a.min_points}": (m.get("sparse_points") or 0) >= a.min_points,
        f"reprojection <= {a.max_reproj}px": (m.get("mean_reprojection_error_px") or 99) <= a.max_reproj,
        "dense honestly absent on CPU": m.get("dense_points") in (None, 0),
    }
    ver = subprocess.run(["roomscape-recon", "verify", str(a.out)], capture_output=True, text=True)
    checks["manifest checksums verify"] = ver.returncode == 0
    for k, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {k}")
    print(json.dumps({k: m.get(k) for k in ("images_uploaded", "images_registered", "sparse_points", "mean_reprojection_error_px",
                                            "processing_seconds")}))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
