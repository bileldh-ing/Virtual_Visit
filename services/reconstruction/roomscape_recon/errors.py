"""Typed error categories for the reconstruction pipeline.

Every failure that leaves the pipeline is a :class:`ReconError` carrying a stable
``category`` (machine readable, safe to show to users) and a ``user_message``.
Raw tool output and filesystem paths never go into ``user_message``.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"  # deterministic: do not retry
    INSUFFICIENT_IMAGES = "INSUFFICIENT_IMAGES"  # deterministic
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"  # deterministic
    INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"  # deterministic for this input
    RECONSTRUCTION_FAILED = "RECONSTRUCTION_FAILED"  # engine ran but produced nothing usable
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"  # native dependency missing
    GPU_UNAVAILABLE = "GPU_UNAVAILABLE"  # stage requires CUDA
    TIMEOUT = "TIMEOUT"  # retryable
    CANCELLED = "CANCELLED"
    DISK_FULL = "DISK_FULL"  # retryable after ops action
    OUT_OF_MEMORY = "OUT_OF_MEMORY"  # process killed (SIGKILL), usually by the OOM killer; retryable
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    WORKER_LOST = "WORKER_LOST"  # retryable
    INTERNAL = "INTERNAL"  # retryable once
    # Hosted-provider failures (World Labs Marble). These never retry the same paid call blindly;
    # the engine chain falls back to the next engine instead (see engines/registry.py).
    PROVIDER_AUTH = "PROVIDER_AUTH"  # 401/403: key missing, invalid, expired or revoked
    PROVIDER_CREDITS = "PROVIDER_CREDITS"  # 402 or balance below the estimated cost
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"  # 429 after bounded backoff
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"  # 5xx / network errors after bounded retries
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"  # operation did not finish within the configured budget
    PROVIDER_REJECTED = "PROVIDER_REJECTED"  # 400/422: input or content-policy rejection
    PROVIDER_FAILED = "PROVIDER_FAILED"  # operation finished with an error


RETRYABLE = {
    ErrorCategory.TIMEOUT,
    ErrorCategory.DISK_FULL,
    ErrorCategory.OUT_OF_MEMORY,
    ErrorCategory.WORKER_LOST,
    ErrorCategory.INTERNAL,
}


class ReconError(Exception):
    def __init__(self, category: ErrorCategory, user_message: str, detail: str | None = None):
        super().__init__(f"{category.value}: {user_message}")
        self.category = category
        self.user_message = user_message
        # ``detail`` is for operators (redacted logs); never return it to end users.
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.user_message,
            "retryable": self.retryable,
        }
