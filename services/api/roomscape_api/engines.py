"""Engine registry built from settings, shared by the API (selection, estimates) and the worker (runs)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from roomscape_recon.colmap_engine import ColmapConfig
from roomscape_recon.engines import EngineInput, EngineRegistry, default_registry
from roomscape_recon.engines.marble import MarbleClient, MarbleConfig
from roomscape_recon.errors import ReconError

from .config import Settings, get_settings

ENGINE_NAMES = ("auto", "marble_world", "depth_room", "colmap_sparse", "panorama_tour", "gaussian_splat_local")
LEGACY_MODE_TO_ENGINE = {"PHOTOGRAMMETRY": "colmap_sparse", "PANORAMA_TOUR": "panorama_tour",
                         "GAUSSIAN_SPLAT": "gaussian_splat_local"}
ENGINE_TO_MODE = {"marble_world": "WORLD", "depth_room": "WORLD", "colmap_sparse": "PHOTOGRAMMETRY",
                  "panorama_tour": "PANORAMA_TOUR", "gaussian_splat_local": "GAUSSIAN_SPLAT"}


def marble_config(s: Settings | None = None) -> MarbleConfig:
    s = s or get_settings()
    mode = s.marble_auth_mode
    key = s.wlt_api_key if mode == "header" else None
    if mode == "off":
        key = None
    return MarbleConfig(base_url=s.marble_base_url, api_key=key, auth_mode="off" if mode == "off" else mode,
                        model=s.marble_model, operation_timeout_s=float(s.marble_operation_timeout_s),
                        spz_variants=tuple(v.strip() for v in s.marble_spz_variants.split(",") if v.strip()))


def build_registry(*, probe_local: bool, s: Settings | None = None, marble_transport=None) -> EngineRegistry:
    s = s or get_settings()
    return default_registry(
        marble_cfg=marble_config(s), depth_model_path=s.depth_model_path, depth_allow_download=s.depth_model_download,
        depth_enabled=s.depth_engine_enabled,
        colmap_cfg=ColmapConfig(max_image_size=s.colmap_max_image_size, num_threads=s.colmap_threads,
                                step_timeout_s=s.job_step_timeout_s),
        marble_transport=marble_transport, probe_local=probe_local)


# ---- live Marble status (balance / key validity), cached so the UI can show it cheaply -------------

_status_lock = threading.Lock()
_status_cache: dict = {"at": 0.0, "value": None}


def marble_status(ttl_s: float = 60.0, transport=None) -> dict:
    """{'configured', 'ok', 'balance', 'error_category', 'checked_at'} — GET /marble/v1/credits is free."""
    s = get_settings()
    cfg = marble_config(s)
    if not cfg.configured:
        return {"configured": False, "ok": False, "balance": None, "error_category": "NOT_CONFIGURED",
                "checked_at": None}
    if not s.marble_live_balance:
        return {"configured": True, "ok": True, "balance": None, "error_category": None, "checked_at": None}
    with _status_lock:
        if _status_cache["value"] is not None and time.time() - _status_cache["at"] < ttl_s:
            return _status_cache["value"]
    cfg.max_retries = 1
    cfg.request_timeout_s = 8.0
    client = MarbleClient(cfg, transport=transport, sleep=lambda _s: None)
    try:
        bal = client.credits()
        value = {"configured": True, "ok": True, "balance": bal, "error_category": None, "checked_at": time.time()}
    except ReconError as e:
        value = {"configured": True, "ok": False, "balance": None, "error_category": e.category.value,
                 "checked_at": time.time()}
    finally:
        client.close()
    with _status_lock:
        _status_cache.update(at=time.time(), value=value)
    return value


def reset_status_cache() -> None:
    with _status_lock:
        _status_cache.update(at=0.0, value=None)


def inputs_from_uploads(uploads) -> list[EngineInput]:
    return [EngineInput(path=Path(u.storage_key), name=u.display_name, upload_id=str(u.id), width=u.width,
                        height=u.height, is_panorama=bool(u.is_panorama), mime=u.mime, bytes=u.bytes) for u in uploads]
