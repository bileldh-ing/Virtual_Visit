from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_prefix="ROOMSCAPE_", env_file=".env", extra="ignore")

    env: str = "development"  # development | test | staging | production
    database_url: str = "postgresql+psycopg://roomscape:roomscape@localhost:5432/roomscape"
    redis_url: str | None = "redis://localhost:6379/0"
    # rq: enqueue to Redis for the worker process. inline: run tasks in-process (dev/test only).
    queue_mode: str = "rq"
    queue_name: str = "reconstruction"

    secret_key: str = Field(default="change-me-in-env", min_length=16)
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    cookie_domain: str | None = None
    cors_origins: list[str] = ["http://localhost:3000"]
    public_web_url: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8000"

    storage_backend: str = "local"  # local | s3
    storage_local_root: str = "./.data/storage"
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_bucket: str = "roomscape-private"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    signed_url_ttl_s: int = 600

    worker_workspace: str = "./.data/jobs"
    job_max_attempts: int = 3
    job_lease_s: int = 90
    job_step_timeout_s: int = 3600
    colmap_max_image_size: int = 1600
    colmap_threads: int = 2

    # ---- 2.0 engines -------------------------------------------------------------------------
    default_engine: str = "auto"  # auto | marble_world | depth_room | colmap_sparse | panorama_tour
    # World Labs Marble. The key is read from WLT_API_KEY (never logged, never returned by the API).
    wlt_api_key: str | None = Field(default=None, validation_alias=AliasChoices("WLT_API_KEY", "ROOMSCAPE_WLT_API_KEY"))
    marble_auth_mode: str = "header"  # header | gateway (an egress proxy injects the key) | off
    marble_base_url: str = "https://api.worldlabs.ai"
    marble_model: str = "marble-1.1"  # marble-1.0-draft | marble-1.0 | marble-1.1 | marble-1.1-plus
    marble_operation_timeout_s: int = 1800
    marble_spz_variants: str = "100k,500k"
    marble_live_balance: bool = True  # GET /marble/v1/credits when listing engines (cached)
    # Models a user may pick per job in the engine picker (first = default when the job does not say).
    marble_models_allowed: str = "marble-1.0-draft,marble-1.1"
    # Dev/test only: make every World Labs call fail as if the key were revoked ("auth") or the account empty
    # ("credits"), without touching the real key. Ignored outside development/test.
    marble_simulate_failure: str = ""
    # Free local fallback (Depth Anything V2 Small, Apache-2.0)
    depth_engine_enabled: bool = True
    depth_model_path: str = "./.data/models/depth-anything-v2-small/model.onnx"
    depth_model_download: bool = True

    max_upload_bytes: int = 20 * 1024 * 1024  # per file; World Labs and the web viewer never need more
    max_image_pixels: int = 100_000_000  # decompression-bomb guard (header check before any decode)
    min_image_side: int = 256
    max_files_per_request: int = 60

    smtp_url: str | None = None  # smtp://user:pass@host:587 ; unset -> dev outbox only
    mail_from: str = "RoomScape AI <no-reply@roomscape.local>"

    billing_mode: str = "test"  # test | stripe
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_pro: str | None = None
    stripe_price_agency: str | None = None

    bootstrap_admin_email: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.env in {"development", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
