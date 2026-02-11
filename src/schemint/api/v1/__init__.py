"""API v1 routes."""

from fastapi import APIRouter

from schemint.api.v1 import analysis, ci, drift, health, projects

router = APIRouter()
router.include_router(health.router, tags=["Health"])
router.include_router(analysis.router, prefix="/analyze", tags=["Analysis"])
router.include_router(projects.router, prefix="/projects", tags=["Projects"])
router.include_router(ci.router, prefix="/ci", tags=["CI Integration"])
router.include_router(drift.router, prefix="/drift", tags=["Schema Drift"])
