"""Health check endpoints."""

from fastapi import APIRouter

from schemint.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, object]:
    """Health check endpoint."""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": "schemint",
        "version": settings.app_version,
        "ai_enabled": settings.ai_enabled,
    }
