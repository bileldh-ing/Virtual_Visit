# Reconstruction engines

RoomScape 2.0 runs every job through one `ReconstructionEngine` interface
(`services/reconstruction/roomscape_recon/engines/base.py`). An engine declares what it accepts, whether it costs
money, its honesty level, and returns a checksummed artifact manifest. The worker runs a **chain** of engines and
records which one produced the result and why any earlier one was skipped.

## The engines

| Engine | Honesty label | Output | Cost | Needs |
|---|---|---|---|---|
| `marble_world`: World Labs Marble | `ai_generated` | Gaussian-splat world (SPZ 100k + 500k), collider GLB, 360° pano, thumbnail, `world.json` | World Labs credits (`marble-1.0-draft` 250, `marble-1.1` 1,600 per 8-photo world, measured) | `WLT_API_KEY` |
| `depth_room`: free local depth room | `depth_estimated` | Textured depth mesh per photo at COLMAP poses, walk collider, `world.json` | free (CPU; 42–86 s for 8 photos on 2 shared vCPUs) | Depth Anything V2 Small ONNX (downloaded at a pinned revision, SHA-256 checked) + COLMAP |
| `colmap_sparse`: camera poses + sparse points | `reconstructed` | PLY point cloud, `cameras.json`, top-down preview | free (CPU) | COLMAP |
| `panorama_tour` | `captured` | One node per uploaded equirectangular panorama | free | 2:1 panoramas |
| `gaussian_splat_local` | `reconstructed` | splatfacto on COLMAP poses | free | NVIDIA GPU worker: **not available in this deployment**, always skipped |

## Default chains

```
photos     : marble_world → depth_room → colmap_sparse
panoramas  : marble_world → panorama_tour
```

`auto` drops engines that cannot run (no key, no GPU, wrong input) before the job starts and records them as
`skipped` with a reason. Picking an engine in the UI puts it first; with **Fall back automatically** checked (default)
the rest of the default chain follows it.

## When the chain falls back

An engine failure moves to the next engine only for these categories (`registry.py: FALLBACK_CATEGORIES`):

| Category | Typical trigger | Text shown on the job |
|---|---|---|
| `PROVIDER_AUTH` | key missing, invalid, expired or revoked (HTTP 401/403) | "API key invalid, expired or revoked" |
| `PROVIDER_CREDITS` | balance below the estimate (preflight) or the provider's out-of-credits response | "out of credits" |
| `PROVIDER_RATE_LIMITED` | HTTP 429 after bounded retries | "rate limited" |
| `PROVIDER_UNAVAILABLE` / `PROVIDER_TIMEOUT` | 5xx, connect/read errors after retries / operation or asset publication past `ROOMSCAPE_MARBLE_OPERATION_TIMEOUT_S` | "service unavailable" / "timed out" |
| `PROVIDER_REJECTED` / `PROVIDER_FAILED` | input refused, generation failed or returned no world | "input rejected" / "generation failed" |
| `ENGINE_UNAVAILABLE` / `GPU_UNAVAILABLE` | engine not configured here | "not available on this deployment" / "needs a GPU" |
| `UNSUPPORTED_INPUT`, `INSUFFICIENT_IMAGES`, `INSUFFICIENT_OVERLAP`, `RECONSTRUCTION_FAILED` | the engine cannot use these photos | as named |

**Not** fallbacks: cancellation, `OUT_OF_MEMORY`, `WORKER_LOST`, `TIMEOUT` of the worker itself and internal errors.
Those go back to the job's retry policy so a paid Marble operation is **resumed** from its checkpoint instead of being
paid for twice (see [MARBLE_SPIKE.md](MARBLE_SPIKE.md#the-oom-retry-was-not-double-charged)).

The job stores `engine` (requested), `engine_used`, `fallback` (`{from, to, reason_code, reason}`) and
`cost_estimate`; the job page, job list and project overview show which engine ran and why.

## Seen live

* `9fc8e78d`: `marble_world` (draft), 101 s, 250 credits.
* `0bad6c5a`: `marble_world` (`marble-1.1`), 1,600 credits, two OOM kills of the worker, resumed without a second
  charge.
* `d88219ac`: requested `marble_world`, the key was rejected (`ROOMSCAPE_MARBLE_SIMULATE_FAILURE=auth`, development
  only) → **fell back to `depth_room`** with reason "API key invalid, expired or revoked", 42 s, no credits (before the
  floor-flattening fix).
* `8ed296df`: same fallback rerun after the floor fix: floor plane flattened in all 7 posed views (floor RMS
  0.17–0.40 → 0.011–0.028 relative units), walking held at eye height and stopped by the collider; 86 s on a CPU
  shared with other work, no credits.

## Viewer contract (`world.json`)

Both world engines write `roomscape.world/1`. Marble worlds stay in the provider's raw frame (OpenCV-style, +y down)
and the viewer applies `marbleMatrix()` (metric scale when given, ground plane at y = 0, 180° about X). Depth rooms are
written Y-up with the floor at y = 0 and the capture height at 1.5 relative units.

| Field | Meaning |
|---|---|
| `honesty` | drives the badge and the "What you're seeing" rows |
| `input_photos[].azimuth_deg` | Marble: COLMAP-relative heading sent to the provider (null = not placed) |
| `heading_alignment.offset_deg` | Marble Auto Layout frame offset found by matching photos to the pano; applied only when `confident` |
| `viewpoints[]` | depth room: real per-photo camera positions/rotations (used for photo hotspots and door transitions) |
| `semantics_metadata` | Marble scale estimate (`metric_scale_factor`, `ground_plane_offset`); empty for drafts |
| `measurement.enabled` | always `false` for generated or depth-estimated worlds |

## Adding an engine

Implement `describe()`, `capabilities()`, `estimate()` and `run()` in a `ReconstructionEngine` subclass, register it
in `registry.py`, and add unit tests with a mocked transport. Paid engines must checkpoint before and after every
paid step and must never be called from CI.
