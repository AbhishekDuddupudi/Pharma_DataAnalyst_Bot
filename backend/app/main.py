"""
Pharma Data Analyst Bot – FastAPI entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import RequestIdMiddleware
from app.api.health import router as health_router
from app.api.version import router as version_router


def create_app() -> FastAPI:
    """Application factory."""

    setup_logging()

    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Middleware ────────────────────────────────────────────────
    application.add_middleware(RequestIdMiddleware)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    # ── Routers ──────────────────────────────────────────────────
    application.include_router(health_router, prefix="/api")
    application.include_router(version_router, prefix="/api")

    return application


app = create_app()
