"""API v1 routes."""

from fastapi import APIRouter

from schemint.api.v1 import analysis, health

router = APIRouter()
router.include_router(health.router, tags=["Health"])
router.include_router(analysis.router, prefix="/analyze", tags=["Analysis"])
