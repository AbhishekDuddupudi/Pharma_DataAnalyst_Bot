"""
Version / info endpoint.
"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["info"])


@router.get("/version")
async def version() -> dict:
    """Return basic application metadata."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
    }
