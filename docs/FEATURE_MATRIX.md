# Feature matrix (RoomScape AI 2.0)

Status legend: **Working** = implemented and exercised against the real API/worker in this repo ·
**Partial** = works with a stated limitation · **Blocked** = needs hardware or a third-party account not available here ·
**Not built** = not implemented.

Evidence names the test, CI workflow, recorded run or image in [`docs/media/`](media/). Run ids refer to the real runs
listed in the README (*Measured on this project*) and in [MARBLE_SPIKE.md](MARBLE_SPIKE.md).

## Engines
| Feature | Status | Evidence / notes |
|---|---|---|
| One `ReconstructionEngine` interface, engine chain with recorded `engine_used` and `fallback` | Working | `tests/unit/test_engines.py`, `tests/integration/test_engine_jobs.py`; [ENGINES.md](ENGINES.md) |
| `marble_world`: World Labs Marble (multi-image, `marble-1.0-draft` and `marble-1.1`) | Working | real runs `9fc8e78d` (draft, 101.4 s, 250 credits) and `0bad6c5a` (`marble-1.1`, 1,600 credits, metric scale estimate); adapter tests on a mocked transport (`tests/unit/test_marble_adapter.py`). Needs `WLT_API_KEY`; never called from CI |
| Credit preflight, live balance and per-model estimate in the picker | Working | `media/engine_picker.webp`; `GET /marble/v1/credits` is free |
| Paid-call checkpointing: resume after a worker crash without a second charge | Working | `0bad6c5a`: two worker OOM kills, adopted the world by intent tag, one ledger row (see MARBLE_SPIKE.md) |
| Fallback on 401/403, 402 / low credits, 429, 5xx outage, timeout, rejected/failed generation | Working | unit tests per category; live: `d88219ac`, `8ed296df` (key rejected via the dev-only `ROOMSCAPE_MARBLE_SIMULATE_FAILURE=auth`) → `depth_room`; `media/job_engine_fallback.webp` |
| Live fallback against a real revoked key / real empty account | Not verified | only simulated in development; the real key was not revoked and credits were not drained on purpose |
| `depth_room`: Depth Anything V2 Small (ONNX, CPU) at COLMAP poses, textured mesh + walk collider | Partial | `tests/unit/test_depth_room.py`; live 42 s (`d88219ac`) and 86 s (`8ed296df`). Lower fidelity than Marble; relative scale; 7 of 8 photos posed on the sample set |
| Depth room floor flattening | Working | `8ed296df`: floor RMS 0.17–0.40 → 0.011–0.028 (relative units) in all 7 views; walking held at eye height |
| `colmap_sparse`: COLMAP SfM, PLY + `cameras.json` + top-down preview | Working | `recon-acceptance` CI; spike 48 → 42 registered, 3,519 pts, 0.598 px |
| `panorama_tour` (2:1 equirectangular) | Working | `roomscape-recon panorama`; Marble also accepts one panorama. Not shown in `docs/media` (sample set has no panoramas) |
| `gaussian_splat_local` (splatfacto) | Blocked | needs an NVIDIA GPU worker; always skipped here with a recorded reason. The picker labels its cost "Free (CPU)" although it needs a GPU (wording bug, open) |
| Dense COLMAP stereo / meshing | Blocked | needs CUDA |
| Metric measurements | Not built (by design) | Marble scale is a provider estimate, depth rooms are relative; measuring disabled for every engine |

## Viewer
| Feature | Status | Evidence / notes |
|---|---|---|
| Gaussian-splat rendering (Spark, SPZ 100k / 500k) | Working | `media/walking.webp`; e2e `world.spec.ts` on a committed synthetic SPZ v2 fixture |
| Walking: WASD/arrows, drag-to-look, gravity, collision (three-mesh-bvh) | Working | e2e `world.spec.ts` (walks, stops at the wall); live on `0bad6c5a` (stopped at x 3.25, z 1.42) and `8ed296df` |
| Minimap with live position and click-to-walk | Working | `media/walking.webp` |
| Photo hotspots, original-photo overlay, door-fade transitions | Working | `media/photo_overlay.webp`; heading registered against the generated pano (`heading_alignment` +75°, `tests/unit/test_heading_alignment.py`) |
| Captured vs inferred view label | Working | `apps/web/tests/unit/world-trust.test.ts`; "Captured view · photo X" / "Inferred area · no photo looked this way" |
| "What you're seeing" trust panel | Working | `media/trust_panel.webp` |
| Auto-tour | Working | frame-stepped in the screenshot script; on Marble worlds all photos share one capture point, so the tour turns between headings rather than moving |
| COLMAP point-cloud viewer (orbit, frustums, filmstrip) | Working | 1.0 viewer kept for `colmap_sparse` results |
| Rendering in headless/CI browsers | Partial | SwiftShader software WebGL: works, but slow and softer; the viewer then loads the 100k preview |

## Jobs
| Feature | Status | Evidence / notes |
|---|---|---|
| Queue (RQ), leases, heartbeats, retries with backoff, reconciler for lost workers | Working | `tests/unit/test_state_machine.py`, integration tests; observed live (OOM → `WORKER_LOST` → resumed) |
| Failure classification (OUT_OF_MEMORY, TIMEOUT, INSUFFICIENT_OVERLAP, PROVIDER_* …) | Working | unit tests; observed live |
| Cancel / retry (keeps attempt history) | Working | integration tests; job page |
| Live progress from persisted stage events (SSE + polling, no fake %) | Working | engine-specific stage lists on the job page |
| Usage metering and Marble credit ledger | Working | integration tests; one ledger row per paid operation |

## Product
| Feature | Status | Evidence / notes |
|---|---|---|
| Sign up / in / out, email verification, password reset | Working | e2e `flow.spec.ts` |
| Workspaces, roles, invitations, audit | Working | integration tests |
| Projects CRUD, search/filter/sort, optimistic versioning | Working | integration tests |
| Uploads: progress, dedupe, quotas, EXIF-free derivatives; magic-byte/format match, ≤ 20 MB, ≤ 100 MP, min side 256 px | Working | e2e upload; server checks in `tests/unit/test_quality.py` and the API upload integration tests |
| Share links: expiry, revoke, password, embed allow-list | Working | integration tests |
| Plans & billing (test mode) | Working | integration tests |
| Stripe checkout + webhooks | Partial | signed-webhook tests; no live Stripe account configured |
| SMTP email | Partial | without `ROOMSCAPE_SMTP_URL` mail goes to the dev outbox |
| Per-link CSP `frame-ancestors` | Not built | embed allow-list enforced in-app only (see SECURITY.md) |
| Demo video for 2.0 | Not built yet | placeholder in the README |

## Platform
| Feature | Status | Evidence / notes |
|---|---|---|
| Docker images (api, worker with COLMAP + ONNX Runtime + pinned depth model, web) + compose | Working | `docker` CI builds all three; compose not run in this sandbox (no Docker here) |
| CI: web, api, e2e, recon-acceptance, security, lighthouse, docker, deploy | Working | GitHub Actions, README badges |
| Deploy staging/production | Partial | workflow runs; skips with a notice until deploy secrets are configured |
