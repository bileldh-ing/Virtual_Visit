#!/usr/bin/env python3
"""Add/refresh `heading_alignment` in an existing Marble world.json (no provider call, no credits).

Usage: realign_world_heading.py <job_dir_with_world.json_and_pano> <uploads_dir>
`uploads_dir` holds <upload_id>/original.jpg (local storage layout). Prints the new size and sha256 of world.json so
the artifact row can be updated.
"""

import hashlib
import json
import sys
from pathlib import Path

from roomscape_recon.engines.heading import estimate_heading_offset


def main() -> None:
    job, uploads = Path(sys.argv[1]), Path(sys.argv[2])
    wpath = job / "world.json"
    w = json.loads(wpath.read_text())
    pano = job / (w.get("assets", {}).get("pano", {}).get("file") or "pano.png")
    photos = []
    for p in w.get("input_photos") or []:
        if p.get("azimuth_deg") is None or not p.get("upload_id"):
            continue
        d = uploads / p["upload_id"]
        src = next((d / n for n in ("web.jpg", "original.jpg") if (d / n).exists()), None)
        if src:
            photos.append((src, float(p["azimuth_deg"])))
    r = estimate_heading_offset(pano, photos)
    w["heading_alignment"] = r.as_dict()
    wpath.write_text(json.dumps(w, indent=2))
    data = wpath.read_bytes()
    print(json.dumps({"heading_alignment": w["heading_alignment"], "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()}))


if __name__ == "__main__":
    main()
