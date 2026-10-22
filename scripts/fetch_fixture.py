#!/usr/bin/env python3
"""Download the licensed reconstruction fixture and verify SHA-256 checksums.

Source: Eyeful Tower dataset, scene "apartment", colmap/images_4 (Meta Platforms,
Inc. and affiliates, MIT License — https://github.com/facebookresearch/EyefulTower).
Images are not committed to git; this script fetches them on demand.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(name: str = "eyeful_apartment_48") -> int:
    fx = ROOT / "tests" / "fixtures" / "reconstruction" / name
    manifest = json.loads((fx / "manifest.json").read_text())
    out = fx / "images"
    out.mkdir(exist_ok=True)
    for f in manifest["files"]:
        dest = out / f["name"]
        if not (dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == f["sha256"]):
            data = urllib.request.urlopen(f["url"], timeout=60).read()
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                print(f"checksum mismatch for {f['name']}", file=sys.stderr)
                return 1
            dest.write_bytes(data)
    print(f"{len(manifest['files'])} images ready in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
