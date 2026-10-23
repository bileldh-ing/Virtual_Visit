# Marble spike: 8 real photos → walkable world

Everything below was measured on real runs through the RoomScape job system (API → RQ worker → World Labs Marble
API → artifact validation → viewer), October 2026. Nothing here is estimated unless it says so.

## Input

8 photos of one apartment living area from the [Eyeful Tower](https://github.com/facebookresearch/EyefulTower)
`apartment` scene (MIT): `10_DSC0217`, `11_DSC0172`, `11_DSC0181`, `11_DSC0190`, `11_DSC0208`, `11_DSC0217`,
`12_DSC0208`, `12_DSC0217` (rig camera `NN_` + frame). Portrait, low capture height, wide lens. The same 8 photos were
used for every run below.

Before the paid call the worker solves camera poses for the photos with COLMAP and turns them into relative azimuths
(degrees, clockwise, 0 = first photo). 7 of 8 photos registered (`10_DSC0217`, the TV wall, did not), so 7 photos
were sent with an azimuth and 1 without. Marble requests use `multi-image` with `reconstruct_images: true`
(Auto Layout, allows up to 8 images).

## Runs

| | Draft | Full quality |
|---|---|---|
| Model | `marble-1.0-draft` | `marble-1.1` |
| Job / world | `9fc8e78d` / `9f31fb84-119e-4a65-a4b3-8aed4bdbf5bd` | `0bad6c5a` / `341c8dd4-24c1-4537-a21b-e80a56d9d5a8` |
| Azimuths sent | 0 (draft run predates the azimuth step) | 7 of 8 |
| Wall-clock in the worker | **101.4 s** (prepare 12.0, upload 3.2, generate call 15.7, wait 61.9, download 7.6) | 6 min 50 s over 3 attempts (two OOM kills, see below); final attempt 52 s |
| Credits | **250** (settled operation cost; balance 7,000 → 6,750) | **1,600** (balance 6,750 → 5,150) |
| Scale | none: `semantics_metadata` is empty for drafts | **metric** (provider estimate): `metric_scale_factor` 1.00067, `ground_plane_offset` 0.747 |
| Splats | 99,846 (`100k`) + 497,664 (`500k`) | 98,304 (`100k`) + 500,000 (`500k`) |
| Collider | 192,827 triangles | 209,910 triangles |

### Artifacts (as downloaded and validated by the worker)

Draft (`9fc8e78d`):

| File | Bytes | SHA-256 |
|---|---:|---|
| `world_100k.spz` | 1,187,346 | `e9a037038da0b61c394e685d9e671be2ea16fc3d7fcfe2d20db62098c1221fd1` |
| `world_500k.spz` | 5,590,415 | `a8794d45ea8608f11ed29bdda5d1194e8791a29fa13c36a12077af94faef85a6` |
| `collider.glb` | 5,029,556 | `3c62a6e5863d69acfe034661dd6536edc6dcca6b2b1c567eb085b1cd341ae00c` |
| `pano.png` | 9,491,933 | `1204d3fb39f8d70baaad83dfd5ca1f212181fb1cdf1d504b99ec4a2a6db02e8a` |
| `thumbnail.webp` | 25,388 | `4d275cd6100212390c3c9203c84525564ec00082dca349b02fc6a4f017c1e8e9` |

marble-1.1 (`0bad6c5a`):

| File | Bytes | SHA-256 |
|---|---:|---|
| `world_100k.spz` | 1,348,030 | `aca6b8ca95c285e8c7cce0b9d8ceb0a1c5d82d29ae5e1f576ddfbdbf4bc9f662` |
| `world_500k.spz` | 7,786,870 | `840ca01117ae69062401d4f0f418c26999bd54f73d9d269ae2904489b9a3b382` |
| `collider.glb` | 5,479,068 | `3cd4253d732662351774496e04efcc07a306cf0d9726f18fc4b0ea20da36a1f4` |
| `pano.png` (4608 × 2304) | 9,701,011 | `5376f2dc3c352537093c14238fda0a7b57ae345565b7798bf3fc4e25302ddea3` |
| `thumbnail.webp` (720 × 480) | 20,724 | `a8b62c37346d218227736b06022ea95c736777b48d3df43ac7bb5e5216c48f4a` |

All SPZ files are gzip-wrapped SPZ v2, SH degree 0, 12 fractional bits. Total download per world ≈ 24 MB; the viewer
streams the 1.3 MB `100k` file first (and stops there on software WebGL).

## The OOM retry was not double-charged

The marble-1.1 job ran in a sandbox shared with other workloads:

| Attempt | Outcome | Duration |
|---|---|---|
| 1 | `WORKER_LOST` (worker killed by the host's OOM killer during generation; lease expired) | 2 min 16 s |
| 2 | `WORKER_LOST` (killed again while waiting on generation; resumed from the checkpoint) | 3 min 23 s |
| 3 | **SUCCEEDED**: adopted the already-paid world by its intent tag, downloaded and validated assets | 52 s |

The adapter writes a checkpoint (operation id, world id, intent tag, sent azimuths, pre-payment balance) before and
after every paid step. On attempt 3 it found the world by tag *before* preflight, so no second
`worlds:generate` was sent: one World Labs operation, one ledger row (1,600 credits, `list price` basis because the
balance read on the retry already reflected the charge), balance 5,150 afterwards. Fix landed in `9b390dd`.

## Observations

* **Inferred room.** The generated world contains a blue-lit room with a desk, monitors and an office chair behind
  the doorway next to the TV. None of the 8 photos looks through that door: Marble invented it. The viewer labels
  any view no photo covers as *Inferred area · no photo looked this way*, and the "What you're seeing" panel marks
  the whole 3D world `inferred`.
* **No scale in drafts.** `marble-1.0-draft` returned no `semantics_metadata`, so the viewer derives eye height and
  body size from the collider's floor/ceiling instead of metres. `marble-1.1` returned a metric scale estimate; the UI
  still does not offer measurements (it is an estimate, not a survey).
* **Auto Layout picks its own frame.** With `reconstruct_images: true`, azimuth 0 did not face the world's forward
  axis: every photo's content sat about 60–75° clockwise of its azimuth. The worker now registers the photos against
  the returned panorama (`roomscape_recon/engines/heading.py`: joint Lab + gradient match over all photos, no provider
  call) and stores one `heading_alignment.offset_deg` with a confidence test. On this world it found **+75°**
  (peak z 1.55, margin 0.70 over any heading > 45° away); by eye the content sits at ≈ +60–70°, so overlays are now
  within roughly 10–15° instead of ~65° off. Existing worlds can be updated with
  `scripts/realign_world_heading.py` (no credits).
* **Positions are unknown.** Marble does not return input camera poses, so every photo hotspot sits at the world
  origin; only its heading is meaningful. Photos taken metres apart along the room therefore all open from the spawn
  point.
* **Software rendering.** On SwiftShader (no GPU) the 100k splat renders at about 1 fps at half resolution; the viewer
  detects it, keeps the preview splats and exposes `setManual()/advance()` so tests and capture can step frames
  deterministically.
* **Collision.** The collider keeps the walker inside the room: walking forward from the spawn on marble-1.1 slid
  along a wall for 3.5 m and stopped at x = 3.25, z = 1.42 (no tunnelling in 14 half-second steps).

## Cost guard rails in the product

* The engine picker shows the live balance and a per-model estimate before anything is spent; the job stores
  `cost_estimate` and the settled cost.
* `ROOMSCAPE_MARBLE_SIMULATE_FAILURE=auth|credits` (development/test only) makes every World Labs call fail without
  touching the real key, to exercise the fallback.
* CI never has a key and never calls World Labs: the adapter tests use a mocked transport and the viewer e2e uses a
  committed synthetic fixture.
