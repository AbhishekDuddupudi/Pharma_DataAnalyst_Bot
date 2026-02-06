"""
Health-check endpoint.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok"}
