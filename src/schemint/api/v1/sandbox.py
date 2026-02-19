"""API endpoints for migration sandbox + co-pilot analysis."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from schemint.drift.models import CopilotResult
from schemint.drift.sandbox import MigrationSandbox

router = APIRouter()


class SandboxAnalyzeRequest(BaseModel):
    migration_sql: str
    current_ddl: str | None = None
    run_copilot: bool = True


@router.post("/analyze", response_model=CopilotResult)
async def analyze_migration(request: SandboxAnalyzeRequest) -> CopilotResult:
    """Analyze a migration SQL against current DDL.

    Always returns HTTP 200 — errors are in the `status` field.
    """
    sandbox = MigrationSandbox()
    return sandbox.analyze(
        migration_sql=request.migration_sql,
        current_ddl=request.current_ddl,
        run_copilot=request.run_copilot,
    )


@router.post("/analyze/{project_id}", response_model=CopilotResult)
async def analyze_migration_for_project(
    project_id: str, request: SandboxAnalyzeRequest
) -> CopilotResult:
    """Analyze a migration SQL using a stored project snapshot as baseline.

    Always returns HTTP 200 — errors are in the `status` field.
    """
    sandbox = MigrationSandbox()
    return sandbox.analyze(
        migration_sql=request.migration_sql,
        current_ddl=request.current_ddl,
        project_id=project_id,
        run_copilot=request.run_copilot,
    )
