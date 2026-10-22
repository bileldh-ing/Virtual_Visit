# Reconstruction spike: can ordinary photos become real 3D on our hardware?

**Answer: yes for camera poses + a sparse point cloud on CPU; no for a dense mesh without a CUDA GPU.**
Everything below comes from one real, reproducible run. No number is estimated.

## Setup

| Item | Value |
|---|---|
| Dataset | Eyeful Tower, scene `apartment`, `colmap/images_4` (Meta Platforms, MIT licence) |
| Subset | 48 photos: rig cameras 10, 11, 12 × first 16 capture positions (`tests/fixtures/reconstruction/eyeful_apartment_48/manifest.json`, SHA-256 pinned) |
| Resolution | 1481 × 2243 px, low-light interior |
| Engine | COLMAP 3.10 (Debian package, **no CUDA**) via `roomscape-recon run` |
| Settings | SIFT, max image size 1600, peak threshold 0.004, `SIMPLE_RADIAL`, exhaustive matching, 2 threads |
| Machine | 2 vCPU, 7 GB RAM shared, no GPU |

## Results

| Metric | Value |
|---|---|
| Photos uploaded / decoded | 48 / 48 |
| Photos registered (camera pose solved) | **42 (87.5 %)** |
| Unregistered | 11_DSC0001, 11_DSC0010, 11_DSC0019, 11_DSC0028, 11_DSC0037, 12_DSC0001 |
| Sparse 3D points | **3,519** (3,448 after 98th-percentile outlier filter for the web viewer) |
| Observations | 18,024 |
| Mean track length | 5.12 views per point |
| Mean reprojection error | **0.598 px** |
| Disconnected models | 2 (largest one is used) |
| Scale | unknown (monocular) |
| Total wall time | **300 s** (299.54 s) |

Stage timings (from `run_log.json`):

| Step | Seconds |
|---|---|
| `feature_extractor` | 82.2 |
| `exhaustive_matcher` | 157.4 |
| `mapper` | 56.6 |
| Optimisation, preview, validation | < 1 |

Artifacts written and checksummed (`manifest.json`): `sparse_points_web.ply` (51,962 B), `sparse_points_raw.ply`,
`cameras.json` (42 viewpoints + viewer transform), `preview_topdown.png`, `quality_report.json`.

## What this means for the product

* The viewer shows **real camera positions and real triangulated points**. The UI labels them *reconstructed*.
* "Up" is **inferred** from the average camera orientation and scale is **relative**, so measurement is disabled in the UI.
* **Dense stereo (PatchMatch) and meshing are skipped** because COLMAP's dense stereo requires CUDA.
  The job event log records `DENSE_RECONSTRUCTION: skipped, reason "COLMAP dense stereo requires CUDA; this worker is CPU-only."`
  The UI shows "Sparse point cloud · dense mesh unavailable on CPU".
* Gaussian splatting is GPU-gated: the API refuses `GAUSSIAN_SPLAT` jobs with `MODE_UNAVAILABLE` on CPU deployments.

## Failure modes observed or designed for

| Failure | Cause | Product behaviour |
|---|---|---|
| Photos not registered (6 here) | Too little overlap with neighbours, dark frames | Listed in the result; recapture suggestion "move a step, keep ~60 % overlap" |
| Several disconnected models (2 here) | No photos bridging areas (doorways) | Largest model shown; suggestion to capture transitions |
| Underexposure warnings (45 of 48) | Low-light interior | Capture check flags `UNDEREXPOSED` with a fix before spending minutes |
| < 3 usable photos | Not enough views | API rejects with `NEEDS_MORE_IMAGES` |
| Registered fraction < 50 % | Weak capture | Job ends `PARTIAL_SUCCESS`, never shown as a full success |
| COLMAP step exceeds limit | Huge sets on CPU | `TIMEOUT` category, retried with backoff, then `FAILED` |
| Worker crash / OOM | Memory pressure | Lease expires, reconciler re-queues; attempts are versioned |
| No CUDA | CPU worker | Dense/mesh stages `skipped` with reason; honest labels |

## Reproduce

```bash
python scripts/fetch_fixture.py eyeful_apartment_48
roomscape-recon run tests/fixtures/reconstruction/eyeful_apartment_48/images out/ --threads 2
roomscape-recon verify out/
```
CI runs the same pipeline on a 21-photo subset (capture positions ≥ 82, all three rig cameras) in
`.github/workflows/recon-acceptance.yml`. First green run on a GitHub-hosted runner (4 vCPU):
**21 photos → 14 registered, 1,389 points, 0.507 px, 31.9 s**; all 6 acceptance checks passed
(status, registered ≥ 50 %, points ≥ 150, reprojection ≤ 2 px, dense honestly absent, manifest checksums).
An earlier attempt using the first 16 photos of a single rig camera failed with `INSUFFICIENT_OVERLAP`
(only 2 photos connected): consecutive positions of one camera overlap too little, which is exactly the
capture guidance the product gives users.
