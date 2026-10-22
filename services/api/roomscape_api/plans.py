"""Central, versioned plan definitions. Changing limits means adding a new version."""

from __future__ import annotations

PLAN_VERSION = "2026-10"

PLANS = {
    "FREE": {"name": "Free", "price_eur_month": 0, "active_projects": 3, "photo_storage_gb": 2,
             "reconstruction_minutes": 60, "marble_credits_month": 2000, "concurrent_jobs": 1, "seats": 1, "share_links": 2,
             "embeds": False, "password_links": False},
    "PRO": {"name": "Pro", "price_eur_month": 29, "active_projects": 25, "photo_storage_gb": 50,
            "reconstruction_minutes": 600, "marble_credits_month": 20000, "concurrent_jobs": 2, "seats": 3, "share_links": 50,
            "embeds": True, "password_links": True},
    "AGENCY": {"name": "Agency", "price_eur_month": 99, "active_projects": 200, "photo_storage_gb": 500,
               "reconstruction_minutes": 3000, "marble_credits_month": 100000, "concurrent_jobs": 4, "seats": 20, "share_links": 1000,
               "embeds": True, "password_links": True},
}

USAGE_POLICY = (
    "Reconstruction minutes are charged once per job, only when the job ends in SUCCEEDED or "
    "PARTIAL_SUCCESS, rounded up to the next whole minute of measured worker time. Failed and cancelled "
    "jobs are not charged. Retries of the same job are never charged twice. Marble worlds are charged in "
    "World Labs credits (measured balance delta, else list price) once per provider operation; when the "
    "monthly Marble allowance is used, new jobs fall back to the free depth engine."
)


def plan(code: str) -> dict:
    return PLANS.get(code, PLANS["FREE"])
