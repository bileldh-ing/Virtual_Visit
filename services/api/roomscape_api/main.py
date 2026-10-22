from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, errors
from .config import get_settings
from .observability import RequestContextMiddleware, setup_logging
from .routers import admin, auth, billing, jobs, orgs, projects, sharing, tour


def create_app() -> FastAPI:
    s = get_settings()
    if not s.is_dev and (s.secret_key == "change-me-in-env" or len(s.secret_key) < 32):
        raise RuntimeError("ROOMSCAPE_SECRET_KEY must be set to a strong random value outside development")
    setup_logging()
    app = FastAPI(title="RoomScape AI API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["content-type", "x-requested-with", "x-share-unlock", "x-request-id"])
    errors.install(app)
    for r in (auth.router, orgs.router, projects.router, jobs.router, tour.router, sharing.router, billing.router,
              admin.router, admin.ops):
        app.include_router(r)
    if s.is_dev:
        app.include_router(admin.dev)
    return app


app = create_app()
