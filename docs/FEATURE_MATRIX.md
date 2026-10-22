# Feature matrix

Status legend: **Working** = implemented and exercised against the real API/worker in this repo ·
**Partial** = works with a stated limitation · **Blocked** = needs hardware or a third-party account not available here ·
**Not built** = not implemented.

Evidence column names the test, CI job or screenshot (`docs/screenshots/`) that shows it.

## Reconstruction
| Feature | Status | Evidence / notes |
|---|---|---|
| Capture-quality report (blur, exposure, duplicates, ORB overlap, panorama detection) | Working | `tests/unit/test_quality.py`; screenshot 06; seeded run: 48 photos, 44 warnings |
| COLMAP SfM: features, exhaustive/sequential matching, incremental mapper (CPU) | Working | `recon-acceptance` CI (21 photos → 14 registered, 1,389 pts); spike 48 → 42 registered, 3,519 pts |
| Artifact contract: web PLY, raw PLY, `cameras.json`, top-down preview, quality report, SHA-256 manifest | Working | `roomscape-recon verify`; `tests/unit/test_artifacts.py` |
| Honest labels: reconstructed / inferred / unavailable | Working | `apps/web/tests/unit/quality.test.ts`; viewer “What you’re seeing” panel (screenshot 12) |
| Dense stereo + Poisson mesh | Blocked | COLMAP PatchMatch requires CUDA; stage recorded as `skipped` with reason |
| Gaussian splatting | Blocked | GPU-gated; API returns `MODE_UNAVAILABLE` |
| 360° panorama tour (equirectangular) | Working | `roomscape-recon panorama`; `PanoramaViewer` component. Not in the demo video (sample set has no panoramas) |
| Metric scale / measurements | Not built | Monocular reconstruction has no scale; measure button disabled with explanation |

## Jobs
| Feature | Status | Evidence / notes |
|---|---|---|
| Queue (RQ), leases, heartbeats, retries with backoff, reconciler for lost workers | Working | `tests/unit/test_state_machine.py`, integration tests; observed live: worker OOM-killed → `WORKER_LOST` → requeued |
| Failure classification (OUT_OF_MEMORY, TIMEOUT, INSUFFICIENT_OVERLAP, …) | Working | Observed live (`OUT_OF_MEMORY` when the sandbox ran out of RAM); CI first run showed `INSUFFICIENT_OVERLAP` |
| Cancel / retry (keeps attempt history) | Working | Integration tests; job page buttons |
| Live progress from persisted events (SSE + polling fallback, no fake %) | Working | screenshot 07, clip `03_job_progress_live.webm` |
| Usage metering (charged only on success) | Working | Integration tests; billing page ledger |

## Product
| Feature | Status | Evidence / notes |
|---|---|---|
| Sign up / sign in / sign out, email verification, password reset | Working | e2e `flow.spec.ts` (sign-up + verify); screenshots 02, 03, 18, 19 |
| Workspaces, roles (owner/admin/editor/viewer), invitations, audit | Working | Integration tests; screenshot 15 |
| Projects CRUD, search/filter/sort, optimistic versioning, verified deletion | Working | Integration tests; screenshots 04, 05 |
| Uploads with byte-level progress, dedupe, quotas, EXIF-free derivatives | Working | e2e upload; screenshot 06 |
| 3D viewer: real PLY + poses, orbit & first-person walk, frustum hotspots, filmstrip, grid, auto-tour, minimap with live position, fullscreen, source-photo overlay | Working | screenshots 09–12; clips `05_viewer_*` |
| Viewer in headless/CI browsers | Partial | Renders with SwiftShader (software WebGL), slower than a GPU |
| Share links: unlisted/restricted, expiry, revoke, password, embed + origin list, view count | Working | Integration tests; screenshots 13, 14 |
| Public tour page + embed page, privacy report form | Working | screenshot 14; e2e smoke (invalid token) |
| Plans & billing (test mode) | Working | screenshot 16 |
| Stripe checkout + webhooks | Partial | Code path + signed-webhook tests; no live Stripe account configured |
| Admin: overview, jobs, users, privacy reports, audit, set landing sample | Working | screenshot 17 |
| SMTP email delivery | Partial | Implemented; without `ROOMSCAPE_SMTP_URL` mail goes to the dev outbox |
| Per-link CSP `frame-ancestors` | Not built | Embed allow-list enforced in-app only (see SECURITY.md) |

## Platform
| Feature | Status | Evidence / notes |
|---|---|---|
| Docker images (api, worker, web) + compose (postgres, redis, minio, api, worker, web) | Working | `docker` CI builds all three images; compose not run in this sandbox (no Docker here) |
| CI: web, api, recon acceptance, e2e, docker, security, Lighthouse | Working | GitHub Actions, see README badges |
| Deploy staging/production | Partial | Workflow ready; skips with a notice until deploy secrets are configured |
