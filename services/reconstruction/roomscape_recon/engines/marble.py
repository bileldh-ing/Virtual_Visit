"""World Labs Marble engine (``marble_world``): photos -> generative explorable world.

Request flow (documented at https://docs.worldlabs.ai/api, verified 2026-10-22):

1. ``GET  /marble/v1/credits``                       preflight balance check (free)
2. ``POST /marble/v1/media-assets:prepare_upload``   one per photo (free), then ``PUT`` bytes to the signed URL
3. ``POST /marble/v1/worlds:generate``               returns an Operation; *this* is the paid call
4. ``GET  /marble/v1/operations/{id}``               poll until ``done`` (free)
5. ``GET  /marble/v1/worlds/{id}``                   full world: SPZ urls, collider GLB, pano, thumbnail, scale metadata
6. download + validate every asset (magic, structure, checksum)

Durability: the operation id is checkpointed (persisted by the worker) before the
first poll, and an *intent* tag is checkpointed before the paid call. A worker
that restarts resumes polling the same operation; if it died between the paid
call and the checkpoint it finds the world by its unique tag via
``POST /marble/v1/worlds:list`` instead of generating (and paying) again.

Auth: the key is read from ``WLT_API_KEY`` by the caller and sent as the
``WLT-Api-Key`` header. ``auth_mode="gateway"`` sends no header (for
environments where an egress gateway injects it). The key is never logged.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import shutil
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageOps

from ..artifacts import build_manifest, sha256_file
from ..errors import ErrorCategory, ReconError
from .base import (
    Capabilities,
    CostEstimate,
    EngineContext,
    EngineInput,
    EngineResult,
    Honesty,
    ReconstructionEngine,
)
from .formats import IMAGE_EXT, sniff, validate_glb, validate_image, validate_spz

DEFAULT_BASE_URL = "https://api.worldlabs.ai"
MAX_INPUT_BYTES = 20 * 1024 * 1024  # World Labs prompt-image limit
RECOMMENDED_LONG_SIDE = 1024
MAX_MULTI_IMAGES = 8  # with reconstruct_images=true ("Auto Layout"); 4 otherwise
CREDITS_PER_USD = 1250.0

# Pricing table from https://docs.worldlabs.ai/api/pricing (retrieved 2026-10-22).
WORLD_CREDITS = {"marble-1.0-draft": 150, "marble-1.0": 1500, "marble-1.1": 1500, "marble-1.1-plus": 1500}
PLUS_VARIABLE_MAX = 1500
PANO_GEN_CREDITS = {"pano": 0, "image": 80, "multi-image": 100, "text": 80, "video": 100}


def price(model: str, input_type: str) -> dict:
    """Return the documented credit cost for one generation (max for marble-1.1-plus)."""
    world = WORLD_CREDITS.get(model, 1500)
    pano = PANO_GEN_CREDITS.get(input_type, 100)
    variable = PLUS_VARIABLE_MAX if model == "marble-1.1-plus" else 0
    total = world + pano + variable
    return {"pano_generation": pano, "world_generation": world, "variable_max": variable, "total": total,
            "usd": round(total / CREDITS_PER_USD, 3)}


# ------------------------------------------------------------------------------------------ HTTP client

@dataclass
class MarbleConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    auth_mode: str = "header"  # header | gateway
    model: str = "marble-1.1"
    request_timeout_s: float = 60.0
    operation_timeout_s: float = 1800.0  # whole generation budget before falling back
    poll_initial_s: float = 5.0
    poll_max_s: float = 20.0
    max_retries: int = 4
    spz_variants: tuple[str, ...] = ("100k", "500k")
    download_max_bytes: int = 400 * 1024 * 1024
    min_balance_margin: float = 0.0  # extra credits required above the estimate
    azimuths: str = "colmap"  # colmap | none

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self.auth_mode == "gateway"


class MarbleClient:
    """Thin, typed client. Maps provider HTTP errors to ReconError categories."""

    def __init__(self, cfg: MarbleConfig, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.sleep = sleep
        headers = {"Accept": "application/json", "User-Agent": "roomscape-ai/2.0"}
        if cfg.api_key and cfg.auth_mode != "gateway":
            headers["WLT-Api-Key"] = cfg.api_key
        self._http = httpx.Client(base_url=cfg.base_url, headers=headers, timeout=cfg.request_timeout_s,
                                  transport=transport, follow_redirects=False)
        # Signed upload / CDN download URLs are on other hosts and must not receive the key.
        self._raw = httpx.Client(timeout=httpx.Timeout(cfg.request_timeout_s * 5, connect=20.0), transport=transport,
                                 follow_redirects=True)
        self.calls: list[dict] = []  # request log (method, path, status, ms) — never contains secrets

    def close(self) -> None:
        self._http.close()
        self._raw.close()

    # -- error mapping ------------------------------------------------------------------------
    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return resp.text[:300]
        d = body.get("detail") if isinstance(body, dict) else body
        if isinstance(d, list):
            return "; ".join(f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}" for e in d)[:500]
        return str(d)[:500]

    def map_error(self, resp: httpx.Response) -> ReconError:
        s, detail = resp.status_code, self._detail(resp)
        if s in (401, 403):
            return ReconError(ErrorCategory.PROVIDER_AUTH, "The World Labs API key is missing, invalid, expired or revoked.",
                              f"HTTP {s}: {detail}")
        if s == 402:
            return ReconError(ErrorCategory.PROVIDER_CREDITS, "The World Labs account is out of API credits.",
                              f"HTTP 402: {detail}")
        if s == 429:
            return ReconError(ErrorCategory.PROVIDER_RATE_LIMITED, "World Labs rate limit reached.", f"HTTP 429: {detail}")
        if s in (400, 413, 415, 422):
            return ReconError(ErrorCategory.PROVIDER_REJECTED, "World Labs rejected the request or input photos.",
                              f"HTTP {s}: {detail}")
        if s == 404:
            return ReconError(ErrorCategory.PROVIDER_FAILED, "The World Labs resource was not found.", f"HTTP 404: {detail}")
        return ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "World Labs is unavailable right now.", f"HTTP {s}: {detail}")

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(120.0, float(retry_after))
            except ValueError:
                pass
        return min(60.0, 2.0 * 2 ** attempt) * (0.75 + random.random() * 0.5)

    def request(self, method: str, path: str, *, json_body: Any = None, retry: bool = True,
                on_ambiguous: Callable[[], Any] | None = None) -> Any:
        """Send a request with bounded retries on 429/5xx/network errors.

        ``on_ambiguous`` is called before retrying a non-idempotent request whose
        outcome is unknown (server error or timeout after sending); if it returns a
        value, that value is used instead of retrying (no double charge).
        """
        last: ReconError | None = None
        for attempt in range(self.cfg.max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = self._http.request(method, path, json=json_body)
            except httpx.ConnectError as e:  # request never reached the server: always safe to retry
                last = ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "World Labs is unreachable.", f"connect: {type(e).__name__}")
                self.calls.append({"method": method, "path": path, "status": "connect_error"})
            except httpx.TimeoutException as e:
                last = ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "World Labs did not respond in time.", f"timeout: {type(e).__name__}")
                self.calls.append({"method": method, "path": path, "status": "timeout"})
                if on_ambiguous is not None:
                    found = on_ambiguous()
                    if found is not None:
                        return found
            except httpx.TransportError as e:
                last = ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "Network error talking to World Labs.", type(e).__name__)
                self.calls.append({"method": method, "path": path, "status": "transport_error"})
            else:
                self.calls.append({"method": method, "path": path, "status": resp.status_code,
                                   "ms": round((time.monotonic() - t0) * 1000)})
                if resp.status_code < 400:
                    return resp.json() if resp.content else {}
                err = self.map_error(resp)
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise err
                last = err
                if resp.status_code >= 500 and on_ambiguous is not None:
                    found = on_ambiguous()
                    if found is not None:
                        return found
                if not retry or attempt >= self.cfg.max_retries:
                    raise err
                self.sleep(self._backoff(attempt, resp.headers.get("Retry-After")))
                continue
            if not retry or attempt >= self.cfg.max_retries:
                break
            self.sleep(self._backoff(attempt, None))
        assert last is not None
        raise last

    # -- endpoints ------------------------------------------------------------------------------
    def credits(self) -> float:
        return float(self.request("GET", "/marble/v1/credits")["remaining_credits"])

    def prepare_upload(self, file_name: str, extension: str) -> dict:
        return self.request("POST", "/marble/v1/media-assets:prepare_upload",
                            json_body={"file_name": file_name[:64], "kind": "image", "extension": extension})

    def put_signed(self, upload_info: dict, data: bytes) -> None:
        method = (upload_info.get("upload_method") or "PUT").upper()
        headers = dict(upload_info.get("required_headers") or {})
        for attempt in range(self.cfg.max_retries + 1):
            try:
                r = self._raw.request(method, upload_info["upload_url"], content=data, headers=headers)
            except httpx.TransportError as e:
                if attempt >= self.cfg.max_retries:
                    raise ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "Uploading a photo to World Labs failed.",
                                     type(e).__name__) from e
                self.sleep(self._backoff(attempt, None))
                continue
            self.calls.append({"method": method, "path": "<signed upload url>", "status": r.status_code})
            if r.status_code < 300:
                return
            if r.status_code >= 500 and attempt < self.cfg.max_retries:
                self.sleep(self._backoff(attempt, None))
                continue
            raise ReconError(ErrorCategory.PROVIDER_UNAVAILABLE if r.status_code >= 500 else ErrorCategory.PROVIDER_REJECTED,
                             "Uploading a photo to World Labs failed.", f"signed upload HTTP {r.status_code}")

    def generate(self, body: dict, on_ambiguous: Callable[[], Any] | None = None) -> dict:
        return self.request("POST", "/marble/v1/worlds:generate", json_body=body, on_ambiguous=on_ambiguous)

    def get_operation(self, op_id: str) -> dict:
        return self.request("GET", f"/marble/v1/operations/{op_id}")

    def get_world(self, world_id: str) -> dict:
        body = self.request("GET", f"/marble/v1/worlds/{world_id}")
        return body.get("world", body)

    def find_world_by_tag(self, tag: str) -> dict | None:
        body = self.request("POST", "/marble/v1/worlds:list", json_body={"tags": [tag], "page_size": 5})
        worlds = body.get("worlds") or []
        return worlds[0] if worlds else None

    def download(self, url: str, dest: Path) -> dict:
        h = hashlib.sha256()
        n = 0
        t0 = time.monotonic()
        for attempt in range(self.cfg.max_retries + 1):
            try:
                with self._raw.stream("GET", url) as r:
                    if r.status_code >= 400:
                        raise ReconError(ErrorCategory.PROVIDER_UNAVAILABLE if r.status_code >= 500 else ErrorCategory.PROVIDER_FAILED,
                                         "Downloading a world asset failed.", f"asset HTTP {r.status_code}")
                    h, n = hashlib.sha256(), 0
                    with dest.open("wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            n += len(chunk)
                            if n > self.cfg.download_max_bytes:
                                raise ReconError(ErrorCategory.ARTIFACT_INVALID, "A world asset is larger than allowed.",
                                                 f"{dest.name} > {self.cfg.download_max_bytes} bytes")
                            h.update(chunk)
                            fh.write(chunk)
                break
            except httpx.TransportError as e:
                if attempt >= self.cfg.max_retries:
                    raise ReconError(ErrorCategory.PROVIDER_UNAVAILABLE, "Downloading a world asset failed.",
                                     type(e).__name__) from e
                self.sleep(self._backoff(attempt, None))
        self.calls.append({"method": "GET", "path": f"<asset {dest.name}>", "status": 200, "bytes": n,
                           "ms": round((time.monotonic() - t0) * 1000)})
        return {"bytes": n, "sha256": h.hexdigest()}


def _id(obj: dict | None, *keys: str) -> str | None:
    """Ids per the OpenAPI spec (``media_asset_id``, ``world_id``); the quickstart examples show ``id``."""
    for k in (*keys, "id"):
        if obj and obj.get(k):
            return str(obj[k])
    return None


# ------------------------------------------------------------------------------------------ inputs

def common_aspect(sizes: list[tuple[int, int]]) -> float:
    """Most common aspect ratio (rounded) among inputs: Auto Layout needs identical size and aspect."""
    ratios = Counter(round(w / h, 2) for w, h in sizes)
    return ratios.most_common(1)[0][0]


def prepare_marble_images(inputs: list[EngineInput], dest: Path, long_side: int = RECOMMENDED_LONG_SIDE) -> list[dict]:
    """Resize *copies* to identical size (center-crop to the common aspect, long side 1024 px), strip EXIF.

    Originals are never modified. Raises INVALID_INPUT for undecodable images and
    if a prepared copy would still exceed the 20 MB provider limit.
    """
    dest.mkdir(parents=True, exist_ok=True)
    opened = []
    for inp in inputs:
        try:
            im = Image.open(inp.path)
            im = ImageOps.exif_transpose(im).convert("RGB")
        except Exception as e:
            raise ReconError(ErrorCategory.INVALID_INPUT, f"Photo {inp.name} could not be decoded.", repr(e)) from e
        opened.append((inp, im))
    aspect = common_aspect([im.size for _, im in opened])
    if aspect >= 1:
        tw, th = long_side, int(round(long_side / aspect))
    else:
        tw, th = int(round(long_side * aspect)), long_side
    out = []
    for i, (inp, im) in enumerate(opened):
        w, h = im.size
        if w / h > aspect:  # too wide: crop width
            cw = int(round(h * aspect))
            box = ((w - cw) // 2, 0, (w - cw) // 2 + cw, h)
        else:
            ch = int(round(w / aspect))
            box = (0, (h - ch) // 2, w, (h - ch) // 2 + ch)
        cropped = im.crop(box).resize((tw, th), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=90, optimize=True)
        data = buf.getvalue()
        if len(data) > MAX_INPUT_BYTES:
            raise ReconError(ErrorCategory.INVALID_INPUT, f"Photo {inp.name} is too large for World Labs even after resizing.")
        p = dest / f"marble_{i:02d}.jpg"
        p.write_bytes(data)
        out.append({"path": p, "name": inp.name, "upload_id": inp.upload_id, "width": tw, "height": th,
                    "bytes": len(data), "source_size": [w, h], "crop": list(box)})
    return out


def select_inputs(photos: list[EngineInput], limit: int) -> list[EngineInput]:
    """Evenly subsample (by name order) when more photos than the provider accepts were uploaded."""
    photos = sorted(photos, key=lambda p: p.name)
    if len(photos) <= limit:
        return photos
    idx = np.linspace(0, len(photos) - 1, limit).round().astype(int)
    return [photos[i] for i in sorted(set(idx.tolist()))]


def azimuths_from_viewpoints(viewpoints: list[dict]) -> dict[str, float]:
    """Relative azimuth (degrees, clockwise = to the right, 0 = first photo) from COLMAP camera rotations.

    Uses the mean camera up-vector as vertical. Returned keys are viewpoint names.
    """
    if not viewpoints:
        return {}
    rots = [np.asarray(v["rotation_c2w"], dtype=float) for v in viewpoints]
    up = np.mean([-r[:, 1] for r in rots], axis=0)
    up /= max(np.linalg.norm(up), 1e-9)

    def flat(v: np.ndarray) -> np.ndarray:
        v = v - up * float(v @ up)
        return v / max(np.linalg.norm(v), 1e-9)

    ref_f = flat(rots[0][:, 2])
    ref_r = flat(rots[0][:, 0])
    out = {}
    for v, r in zip(viewpoints, rots, strict=False):
        f = flat(r[:, 2])
        out[v["name"]] = float(np.degrees(np.arctan2(float(f @ ref_r), float(f @ ref_f))) % 360.0)
    return out


# ------------------------------------------------------------------------------------------ engine

@dataclass
class MarbleWorldEngine(ReconstructionEngine):
    cfg: MarbleConfig = field(default_factory=MarbleConfig)
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    pose_solver: Callable[[list[Path], Path], list[dict]] | None = None  # returns COLMAP viewpoints

    name = "marble_world"
    label = "Marble world (World Labs)"
    honesty = Honesty.AI_GENERATED
    output_type = "gaussian_splat_world"
    description = ("Generative 3D world from 2-8 photos (or one 360 panorama) via the World Labs Marble API: "
                   "Gaussian splats + collider mesh + panorama. Explorable with collision. Areas the photos never "
                   "showed are inferred by the model.")

    def capabilities(self) -> Capabilities:
        if not self.cfg.configured:
            return Capabilities(available=False, reason="World Labs API key (WLT_API_KEY) is not configured.", paid=True,
                                accepts=["photos", "panoramas"], min_inputs=1, max_inputs=MAX_MULTI_IMAGES,
                                outputs=["SPLAT_SPZ", "COLLIDER_MESH", "PANORAMA", "THUMBNAIL", "WORLD"])
        return Capabilities(available=True, paid=True, accepts=["photos", "panoramas"], min_inputs=1,
                            max_inputs=MAX_MULTI_IMAGES,
                            outputs=["SPLAT_SPZ", "COLLIDER_MESH", "PANORAMA", "THUMBNAIL", "WORLD"])

    def _plan(self, inputs: list[EngineInput]) -> tuple[str, list[EngineInput]]:
        panos = [i for i in inputs if i.is_panorama]
        photos = [i for i in inputs if not i.is_panorama]
        if photos and len(photos) >= len(panos):
            chosen = select_inputs(photos, MAX_MULTI_IMAGES)
            return ("image" if len(chosen) == 1 else "multi-image"), chosen
        return "pano", panos[:1]

    def estimate(self, inputs: list[EngineInput]) -> CostEstimate:
        kind, chosen = self._plan(inputs)
        p = price(self.cfg.model, kind)
        note = (f"{self.cfg.model}, {kind} input ({len(chosen)} image(s)). World Labs list price; "
                "marble-1.1-plus may charge up to the shown maximum.")
        if len([i for i in inputs if not i.is_panorama]) > MAX_MULTI_IMAGES:
            note += f" Only {MAX_MULTI_IMAGES} photos are sent (evenly selected)."
        return CostEstimate(credits=float(p["total"]), usd=p["usd"], minutes=6.0,
                            breakdown=[{"item": "pano generation", "credits": p["pano_generation"]},
                                       {"item": "world generation", "credits": p["world_generation"]},
                                       *([{"item": "variable world size (max)", "credits": p["variable_max"]}]
                                         if p["variable_max"] else [])],
                            note=note)

    # -- run ------------------------------------------------------------------------------------
    def run(self, ctx: EngineContext) -> EngineResult:
        t_start = self.clock()
        cp = dict(ctx.checkpoint or {})

        def save(**kw: Any) -> None:
            cp.update(kw)
            ctx.save_checkpoint(dict(cp))

        client = MarbleClient(self.cfg, transport=self.transport, sleep=self.sleep)
        try:
            return self._run(ctx, client, cp, save, t_start)
        except (KeyError, TypeError, AttributeError) as e:  # response shape we do not understand: typed, no blind retry
            raise ReconError(ErrorCategory.PROVIDER_FAILED, "World Labs returned an unexpected response.",
                             f"{type(e).__name__}: {e}") from e
        finally:
            client.close()

    def _check_cancel(self, ctx: EngineContext) -> None:
        if ctx.should_cancel():
            raise ReconError(ErrorCategory.CANCELLED, "Processing was cancelled.")

    def _run(self, ctx: EngineContext, client: MarbleClient, cp: dict, save, t_start: float) -> EngineResult:
        if not self.cfg.configured:
            raise ReconError(ErrorCategory.PROVIDER_AUTH, "The World Labs API key is not configured.", "WLT_API_KEY unset")
        reason = self.accepts(ctx.inputs)
        if reason:
            raise ReconError(ErrorCategory.UNSUPPORTED_INPUT, reason)
        kind, chosen = self._plan(ctx.inputs)
        est = self.estimate(ctx.inputs)
        work = ctx.work_dir / "marble"
        out = ctx.out_dir
        out.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float] = {}
        resumed = bool(cp.get("operation_id") or cp.get("world_id") or cp.get("intent_tag"))

        # 1. preflight: credits (skipped when resuming an already-paid operation)
        ctx.on_stage("INPUT_VALIDATION", "started", {"engine": self.name, "resumed": resumed})
        if not cp.get("operation_id") and not cp.get("world_id"):
            balance = client.credits()
            save(credits_before=balance)
            if balance < (est.credits or 0) + self.cfg.min_balance_margin:
                raise ReconError(ErrorCategory.PROVIDER_CREDITS,
                                 "The World Labs account does not have enough credits for this world.",
                                 f"balance {balance} < estimate {est.credits}")
        prepared: list[dict] = []
        azimuths: dict[str, float] = {}
        if not cp.get("operation_id") and not cp.get("world_id"):
            t0 = self.clock()
            prepared = prepare_marble_images(chosen, work / "inputs") if kind != "pano" else []
            if kind == "multi-image" and self.cfg.azimuths == "colmap" and self.pose_solver is not None:
                try:
                    # Solve on the originals (full resolution, more features); map back to the prepared copies.
                    vps = self.pose_solver([c.path for c in chosen], work / "poses")
                    by_file = {Path(k).stem: v for k, v in azimuths_from_viewpoints(vps).items()}
                    for c, p in zip(chosen, prepared, strict=True):
                        if c.path.stem in by_file:
                            azimuths[p["path"].name] = round(by_file[c.path.stem], 1)
                except ReconError as e:  # azimuths are optional: Auto Layout still works without them
                    ctx.on_stage("INPUT_VALIDATION", "progress", {"azimuths": "unavailable", "reason": e.category.value})
            timings["prepare_s"] = round(self.clock() - t0, 2)
        ctx.on_stage("INPUT_VALIDATION", "completed",
                     {"engine": self.name, "input_type": kind, "images": len(chosen),
                      "azimuths_known": len(azimuths), "estimate_credits": est.credits})
        self._check_cancel(ctx)

        # 2. upload media assets (free) and start the paid generation
        ctx.on_stage("MESH_OR_SPLAT_GENERATION", "started", {"engine": self.name, "provider": "worldlabs",
                                                              "model": self.cfg.model})
        if not cp.get("operation_id") and not cp.get("world_id"):
            tag = cp.get("intent_tag")
            if tag:  # died after the paid call but before checkpointing the operation: adopt, never re-pay
                found = client.find_world_by_tag(tag)
                if found:
                    save(world_id=_id(found, "world_id"), adopted_by_tag=True)
            if not cp.get("world_id"):
                t0 = self.clock()
                media_ids = list(cp.get("media_asset_ids") or [])
                if not media_ids:
                    srcs: list[dict[str, Any]] = (prepared if kind != "pano"
                                                  else [{"path": chosen[0].path, "name": chosen[0].name}])
                    for p in srcs:
                        ext = {"jpeg": "jpg", "png": "png", "webp": "webp"}.get(sniff(Path(p["path"])), "jpg")
                        prep = client.prepare_upload(Path(p["name"]).stem[:50] + "." + ext, ext)
                        client.put_signed(prep["upload_info"], Path(p["path"]).read_bytes())
                        mid = _id(prep.get("media_asset"), "media_asset_id")
                        if not mid or not (prep.get("upload_info") or {}).get("upload_url"):
                            raise ReconError(ErrorCategory.PROVIDER_FAILED, "World Labs returned an unexpected response.",
                                             f"prepare_upload keys: {sorted(prep)}")
                        media_ids.append(mid)
                    save(media_asset_ids=media_ids)
                timings["upload_s"] = round(self.clock() - t0, 2)
                body = self._request_body(ctx, kind, media_ids, prepared, azimuths)
                tag = f"rs-{ctx.job_id}"[:32]
                body["tags"] = [tag]
                save(intent_tag=tag, request=self._redacted(body), model=self.cfg.model, input_type=kind,
                     estimate_credits=est.credits)
                t0 = self.clock()
                op = client.generate(body, on_ambiguous=lambda: self._adopt(client, tag))
                timings["generate_call_s"] = round(self.clock() - t0, 2)
                if "operation_id" in op:
                    save(operation_id=op["operation_id"], operation_started=time.time())
                elif _id(op, "world_id"):  # adopted world from ambiguity check
                    save(world_id=_id(op, "world_id"), adopted_by_tag=True)
                else:
                    raise ReconError(ErrorCategory.PROVIDER_FAILED, "World Labs returned an unexpected response.",
                                     f"generate keys: {sorted(op)}")
        self._check_cancel(ctx)

        # 3. poll the operation
        world = None
        if cp.get("operation_id"):
            t0 = self.clock()
            world = self._poll(ctx, client, cp, save)
            timings["generation_wait_s"] = round(self.clock() - t0, 2)
        world_id = cp.get("world_id") or _id(world, "world_id")
        if not world_id:
            raise ReconError(ErrorCategory.PROVIDER_FAILED, "World Labs did not return a world.", json.dumps(cp)[:300])
        full = client.get_world(world_id)
        if not (full.get("assets") or {}).get("splats"):
            if world and (world.get("assets") or {}).get("splats"):
                full = {**world, **{k: v for k, v in full.items() if v}}
            else:
                full = self._wait_world_assets(ctx, client, world_id)
        ctx.on_stage("MESH_OR_SPLAT_GENERATION", "completed", {"world_id": world_id})
        self._check_cancel(ctx)

        # 4. download + validate assets
        ctx.on_stage("ARTIFACT_VALIDATION", "started", {"engine": self.name})
        t0 = self.clock()
        entries, asset_meta = self._download_assets(client, full, out)
        timings["download_s"] = round(self.clock() - t0, 2)
        credits_after = None
        try:
            credits_after = client.credits()
        except ReconError:
            pass
        measured = None
        if cp.get("credits_before") is not None and credits_after is not None and not resumed:
            measured = round(float(cp["credits_before"]) - credits_after, 2)
        assets = full.get("assets") or {}
        sem = (assets.get("splats") or {}).get("semantics_metadata") or {}
        photo_meta = []
        for i, inp in enumerate(chosen):
            fname = f"marble_{i:02d}.jpg"
            photo_meta.append({"name": inp.name, "upload_id": inp.upload_id, "azimuth_deg": azimuths.get(fname),
                               "azimuth_source": "colmap" if fname in azimuths else None})
        world_doc = {
            "schema": "roomscape.world/1",
            "engine": self.name,
            "honesty": self.honesty.value,
            "provider": "worldlabs",
            "world_id": world_id,
            "world_marble_url": full.get("world_marble_url"),
            "model": full.get("model") or cp.get("model") or self.cfg.model,
            "caption": assets.get("caption"),
            "semantics_metadata": sem,
            "coordinate_convention": "marble_raw_opencv (OpenCV-style: +y down, +z forward, per World Labs docs); viewers rotate 180° about X",
            "input_type": cp.get("input_type", kind),
            "input_photos": photo_meta,
            "photo_viewpoints_note": "Marble does not return input camera poses; photo hotspots are placed at the world "
                                     "origin at their COLMAP-derived azimuth (approximate).",
            "assets": asset_meta,
            "measurement": {"enabled": False, "reason": "AI-generated geometry: dimensions are not reliable."},
        }
        (out / "world.json").write_text(json.dumps(world_doc, indent=2))
        entries.append({"path": "world.json", "kind": "WORLD"})
        elapsed = round(self.clock() - t_start, 2)
        metrics = {
            "provider": "worldlabs", "world_id": world_id, "model": world_doc["model"],
            "input_type": world_doc["input_type"], "input_images": len(chosen),
            "azimuths_known": sum(1 for p in photo_meta if p["azimuth_deg"] is not None),
            "estimated_credits": cp.get("estimate_credits", est.credits),
            "credits_before": cp.get("credits_before"), "credits_after": credits_after,
            "measured_credits": measured, "settled_credits": cp.get("settled_credits"),
            "cost_line_items": cp.get("cost_line_items"), "timings_s": timings, "processing_seconds": elapsed,
            "resumed": resumed, "adopted_by_tag": bool(cp.get("adopted_by_tag")),
            "splat_points": {k: v.get("num_points") for k, v in asset_meta.items() if k.startswith("spz_")},
            "collider_triangles": (asset_meta.get("collider") or {}).get("triangles"),
            "scale": "metric (provider estimate)" if sem.get("metric_scale_factor") else "unknown",
        }
        quality = {"status": "SUCCEEDED", "output_type": self.output_type, "honesty": self.honesty.value,
                   "metrics": metrics, "warnings": [], "limitations": self._limitations(),
                   "recapture_suggestions": []}
        (out / "quality_report.json").write_text(json.dumps(quality, indent=2))
        entries.append({"path": "quality_report.json", "kind": "QUALITY_REPORT"})
        (out / "provider_calls.json").write_text(json.dumps(client.calls, indent=2))
        entries.append({"path": "provider_calls.json", "kind": "PROVIDER_LOG"})
        manifest = build_manifest(out, entries, {"name": self.name, "provider": "worldlabs", "model": world_doc["model"]})
        ctx.on_stage("ARTIFACT_VALIDATION", "completed", {"artifacts": len(manifest["artifacts"])})
        settled = cp.get("settled_credits")
        if settled is not None:
            charged, basis = float(settled), "settled operation cost"
        elif measured and measured > 0:
            charged, basis = measured, "measured balance delta"
        else:
            charged, basis = float(cp.get("estimate_credits") or est.credits or 0), "list price"
        usage = [{"kind": "marble_credits", "quantity": charged, "unit": "credit",
                  "key": f"marble:{cp.get('operation_id') or world_id}",
                  "note": f"World Labs {world_doc['model']} {world_doc['input_type']} world {world_id} ({basis})"}]
        return EngineResult(status="SUCCEEDED", output_type=self.output_type, honesty=self.honesty.value,
                            metrics=metrics, artifacts=manifest["artifacts"], out_dir=out,
                            engine={"name": self.name, "label": self.label, "provider": "worldlabs",
                                    "model": world_doc["model"]},
                            limitations=self._limitations(), usage=usage,
                            tour_nodes=[{"upload_id": p["upload_id"], "kind": "PHOTO_AT_AZIMUTH", "ref": p["name"]}
                                        for p in photo_meta if p["upload_id"]],
                            stages_completed=["INPUT_VALIDATION", "MESH_OR_SPLAT_GENERATION", "ARTIFACT_VALIDATION"])

    @staticmethod
    def _limitations() -> list[str]:
        return ["AI-generated world: areas the photos did not show are inferred by the model and may not match the property.",
                "Dimensions are not reliable; measurement is disabled.",
                "Input photo positions are approximate (azimuth only)."]

    def _request_body(self, ctx: EngineContext, kind: str, media_ids: list[str], prepared: list[dict],
                      azimuths: dict[str, float]) -> dict:
        display = (ctx.options.get("display_name") or f"RoomScape {ctx.job_id[:8]}")[:64]
        body: dict[str, Any] = {"display_name": display, "model": self.cfg.model, "permission": {"public": False}}
        if ctx.options.get("seed") is not None:
            body["seed"] = int(ctx.options["seed"])
        text = ctx.options.get("text_prompt")
        if kind == "multi-image":
            items = []
            for mid, p in zip(media_ids, prepared, strict=False):
                item: dict[str, Any] = {"content": {"source": "media_asset", "media_asset_id": mid}}
                az = azimuths.get(Path(p["path"]).name)
                if az is not None:
                    item["azimuth"] = az
                items.append(item)
            wp: dict[str, Any] = {"type": "multi-image", "multi_image_prompt": items,
                                  "reconstruct_images": len(items) > 1}
        else:
            wp = {"type": "image", "image_prompt": {"source": "media_asset", "media_asset_id": media_ids[0]},
                  "is_pano": kind == "pano"}
        if text:
            wp["text_prompt"] = text
        body["world_prompt"] = wp
        return body

    @staticmethod
    def _redacted(body: dict) -> dict:
        return json.loads(json.dumps(body))  # bodies never contain the key; copy for the checkpoint

    def _adopt(self, client: MarbleClient, tag: str) -> dict | None:
        try:
            return client.find_world_by_tag(tag)
        except ReconError:
            return None

    def _poll(self, ctx: EngineContext, client: MarbleClient, cp: dict, save) -> dict | None:
        op_id = cp["operation_id"]
        started = float(cp.get("operation_started") or time.time())
        delay = self.cfg.poll_initial_s
        last_status = None
        while True:
            self._check_cancel(ctx)
            if time.time() - started > self.cfg.operation_timeout_s:
                raise ReconError(ErrorCategory.PROVIDER_TIMEOUT,
                                 "World Labs did not finish the world in time.",
                                 f"operation {op_id} exceeded {self.cfg.operation_timeout_s}s")
            try:
                op = client.get_operation(op_id)
            except ReconError as e:
                if e.category == ErrorCategory.PROVIDER_FAILED and cp.get("world_id"):
                    return None  # operation expired (404) but the world id is known
                raise
            meta = op.get("metadata") or {}
            if meta.get("world_id") and meta["world_id"] != cp.get("world_id"):
                save(world_id=meta["world_id"])
            prog = (meta.get("progress") or {})
            status = prog.get("status")
            if status != last_status:
                ctx.on_stage("MESH_OR_SPLAT_GENERATION", "progress",
                             {"provider_status": status, "description": prog.get("description"), "world_id": meta.get("world_id")})
                last_status = status
            if op.get("done"):
                err = op.get("error")
                if err:
                    save(operation_error=err)
                    raise ReconError(ErrorCategory.PROVIDER_FAILED, "World Labs could not generate this world.",
                                     f"code={err.get('code')} message={str(err.get('message'))[:300]}")
                resp = op.get("response") or {}
                if _id(resp, "world_id") and not cp.get("world_id"):
                    save(world_id=_id(resp, "world_id"))
                cost = op.get("cost") or {}
                save(operation_done=True, settled_credits=cost.get("total_credits"),
                     cost_line_items=cost.get("line_items"))
                return resp
            self.sleep(delay)
            delay = min(self.cfg.poll_max_s, delay * 1.5)

    def _wait_world_assets(self, ctx: EngineContext, client: MarbleClient, world_id: str) -> dict:
        t_end = time.time() + self.cfg.operation_timeout_s
        delay = self.cfg.poll_initial_s
        while time.time() < t_end:
            self._check_cancel(ctx)
            w = client.get_world(world_id)
            if (w.get("assets") or {}).get("splats"):
                return w
            self.sleep(delay)
            delay = min(self.cfg.poll_max_s, delay * 1.5)
        raise ReconError(ErrorCategory.PROVIDER_TIMEOUT, "World Labs did not publish the world assets in time.", world_id)

    def _download_assets(self, client: MarbleClient, world: dict, out: Path) -> tuple[list[dict], dict]:
        assets = world.get("assets") or {}
        splats = (assets.get("splats") or {}).get("spz_urls") or {}
        entries: list[dict] = []
        meta: dict[str, dict] = {}
        wanted = [v for v in self.cfg.spz_variants if splats.get(v)]
        if not wanted:  # fall back to whatever variant exists
            wanted = [k for k in ("500k", "100k", "full_res") if splats.get(k)][:1]
        if not wanted:
            raise ReconError(ErrorCategory.PROVIDER_FAILED, "The world has no splat file.", json.dumps(list(splats)))
        for v in wanted:
            p = out / f"world_{v}.spz"
            dl = client.download(splats[v], p)
            info = validate_spz(p)
            if sha256_file(p) != dl["sha256"]:
                raise ReconError(ErrorCategory.ARTIFACT_INVALID, "A world asset changed after download.", p.name)
            meta[f"spz_{v}"] = {**info, "bytes": dl["bytes"], "sha256": dl["sha256"], "file": p.name}
            entries.append({"path": p.name, "kind": "SPLAT_SPZ", "variant": v, "num_points": info["num_points"],
                            "spz_version": info["spz_version"]})
        col = (assets.get("mesh") or {}).get("collider_mesh_url")
        if col:
            p = out / "collider.glb"
            dl = client.download(col, p)
            info = validate_glb(p)
            meta["collider"] = {**info, "sha256": dl["sha256"], "file": p.name}
            entries.append({"path": p.name, "kind": "COLLIDER_MESH", "triangles": info["triangles"],
                            "bounds": info["bounds"]})
        for key, url, kind, stem in (("pano", (assets.get("imagery") or {}).get("pano_url"), "PANORAMA", "pano"),
                                     ("thumbnail", assets.get("thumbnail_url"), "THUMBNAIL", "thumbnail")):
            if not url:
                continue
            tmp = out / f"{stem}.bin"
            dl = client.download(url, tmp)
            info = validate_image(tmp)
            final = out / f"{stem}{IMAGE_EXT[info['format']]}"
            shutil.move(tmp, final)
            meta[key] = {**info, "bytes": dl["bytes"], "sha256": dl["sha256"], "file": final.name}
            entries.append({"path": final.name, "kind": kind, "width": info["width"], "height": info["height"]})
        return entries, meta
