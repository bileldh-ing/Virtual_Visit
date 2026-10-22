"""roomscape-recon CLI.

    roomscape-recon analyze IMAGES_DIR [--json OUT]
    roomscape-recon run IMAGES_DIR OUT_DIR [--work WORK] [--max-image-size N] [--matcher M]
    roomscape-recon panorama IMAGES_DIR OUT_DIR
    roomscape-recon verify OUT_DIR
    roomscape-recon doctor

Exit codes: 0 success, 2 partial success, 3 invalid/insufficient input,
4 reconstruction failed, 5 engine/GPU unavailable, 6 artifact invalid, 1 other.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .artifacts import verify_manifest
from .colmap_engine import ColmapConfig, detect
from .errors import ErrorCategory, ReconError
from .panorama import run_panorama
from .pipeline import run_photogrammetry
from .quality import build_report_from_dir
from .splat import capabilities

EXIT = {
    ErrorCategory.INVALID_INPUT: 3, ErrorCategory.INSUFFICIENT_IMAGES: 3, ErrorCategory.UNSUPPORTED_INPUT: 3,
    ErrorCategory.INSUFFICIENT_OVERLAP: 4, ErrorCategory.RECONSTRUCTION_FAILED: 4,
    ErrorCategory.ENGINE_UNAVAILABLE: 5, ErrorCategory.GPU_UNAVAILABLE: 5, ErrorCategory.ARTIFACT_INVALID: 6,
}


def _log(event: str, **kw) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "event": event, **kw}), file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="roomscape-recon")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("images", type=Path)
    a.add_argument("--json", type=Path)
    r = sub.add_parser("run")
    r.add_argument("images", type=Path)
    r.add_argument("out", type=Path)
    r.add_argument("--work", type=Path)
    r.add_argument("--max-image-size", type=int, default=1600)
    r.add_argument("--matcher", choices=["exhaustive", "sequential"], default="exhaustive")
    r.add_argument("--threads", type=int, default=2)
    r.add_argument("--dense", choices=["auto", "off", "require"], default="auto")
    p = sub.add_parser("panorama")
    p.add_argument("images", type=Path)
    p.add_argument("out", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("out", type=Path)
    sub.add_parser("doctor")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    try:
        if args.cmd == "analyze":
            rep = build_report_from_dir(args.images)
            text = json.dumps(rep, indent=2)
            if args.json:
                args.json.write_text(text)
            print(json.dumps({"status": rep["status"], **rep["summary"]}))
            return 0
        if args.cmd == "doctor":
            print(json.dumps({"colmap": detect().to_dict(), "splat": capabilities(), "version": __version__}))
            return 0
        if args.cmd == "verify":
            problems = verify_manifest(args.out)
            print(json.dumps({"ok": not problems, "problems": problems}))
            return 0 if not problems else 6
        if args.cmd == "panorama":
            rep = run_panorama(args.images, args.out, on_stage=lambda s, e, d: _log("stage", stage=s, status=e, **d))
            print(json.dumps({k: rep[k] for k in ("status", "output_type", "metrics")}))
            return 0
        cfg = ColmapConfig(max_image_size=args.max_image_size, matcher=args.matcher, num_threads=args.threads,
                           dense=args.dense)
        work = args.work or Path(tempfile.mkdtemp(prefix="roomscape-"))
        res = run_photogrammetry(args.images, work, args.out, cfg,
                                 on_stage=lambda s, e, d: _log("stage", stage=s, status=e, **d))
        (args.out / "result.json").write_text(json.dumps(res.to_dict(), indent=2, default=str))
        print(json.dumps({"status": res.status, "output_type": res.output_type, "duration_s": res.duration_s,
                          "metrics": res.metrics}, default=str))
        return 0 if res.status == "SUCCEEDED" else 2
    except ReconError as e:
        _log("failed", **e.to_dict())
        print(json.dumps({"status": "FAILED", "error": e.to_dict()}))
        return EXIT.get(e.category, 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
