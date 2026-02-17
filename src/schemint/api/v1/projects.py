"""
Project Management API Endpoints.

This module provides endpoints for:
- Registering new projects
- Retrieving project information
- Viewing project memory summary
- Managing project settings
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from schemint.memory import (
    MemoryStore,
    Project,
    get_memory_store,
)
from schemint.memory.models import ProjectRegistration

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class ProjectResponse(BaseModel):
    """Project information response."""

    id: str = Field(..., description="Internal project ID (UUID)")
    external_id: str = Field(..., description="External identifier")
    name: str = Field(..., description="Project name")
    created_at: str = Field(..., description="Creation timestamp")
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectListResponse(BaseModel):
    """List of projects response."""

    projects: list[ProjectResponse]
    total: int


class MemorySummaryResponse(BaseModel):
    """Project memory summary response."""

    project_id: str
    project_name: str
    accepted_findings_count: int
    safe_patterns_count: int
    business_rules_count: int
    semantic_entries_count: int
    inflection_points_count: int
    last_analysis: str | None
    total_analyses: int


class AcceptedFindingResponse(BaseModel):
    """Accepted finding response."""

    id: str
    finding_type: str
    pattern_hash: str
    scope: str
    reason: str
    accepted_by: str
    accepted_at: str
    expires_at: str | None
    context: dict[str, Any]


class BusinessRuleResponse(BaseModel):
    """Business rule response."""

    id: str
    rule_type: str
    rule_config: dict[str, Any]
    severity: str
    applies_to: dict[str, Any]
    rationale: str
    created_by: str
    active: bool


# =============================================================================
# Endpoints
# =============================================================================


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def register_project(request: ProjectRegistration) -> ProjectResponse:
    """
    Register a new project for analysis.

    Projects must be registered before they can use CI ingestion or memory features.
    If the project already exists (by external_id), returns the existing project.

    Example:
        POST /api/v1/projects
        {
            "external_id": "github:acme/ecommerce",
            "name": "ACME E-Commerce",
            "settings": {
                "default_severity": "warning"
            }
        }
    """
    store = get_memory_store()

    # Check if project already exists
    existing = store.get_project_by_external_id(request.external_id)
    if existing:
        return _project_to_response(existing)

    # Register new project
    project = store.register_project(
        external_id=request.external_id,
        name=request.name,
        settings=request.settings,
    )

    return _project_to_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str) -> ProjectResponse:
    """
    Get project information by ID.

    The project_id can be either:
    - Internal UUID (e.g., "550e8400-e29b-41d4-a716-446655440000")
    - External ID (e.g., "github:acme/ecommerce")
    """
    store = get_memory_store()
    project = _get_project(store, project_id)
    return _project_to_response(project)


@router.get("/{project_id}/memory", response_model=MemorySummaryResponse)
async def get_project_memory(project_id: str) -> MemorySummaryResponse:
    """
    Get project memory summary.

    Returns counts of all memory items and statistics about analysis history.
    """
    store = get_memory_store()
    project = _get_project(store, project_id)

    summary = store.get_memory_summary(project.id)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve memory summary",
        )

    return MemorySummaryResponse(
        project_id=str(summary.project_id),
        project_name=summary.project_name,
        accepted_findings_count=summary.accepted_findings_count,
        safe_patterns_count=summary.safe_patterns_count,
        business_rules_count=summary.business_rules_count,
        semantic_entries_count=summary.semantic_entries_count,
        inflection_points_count=summary.inflection_points_count,
        last_analysis=summary.last_analysis.isoformat() if summary.last_analysis else None,
        total_analyses=summary.total_analyses,
    )


@router.get("/{project_id}/memory/accepted", response_model=list[AcceptedFindingResponse])
async def get_accepted_findings(project_id: str) -> list[AcceptedFindingResponse]:
    """
    Get all accepted findings for a project.

    Accepted findings are issues that were marked as false positives or intentional.
    They won't be reported again in future analyses.
    """
    store = get_memory_store()
    project = _get_project(store, project_id)

    findings = store.get_accepted_findings(project.id)

    return [
        AcceptedFindingResponse(
            id=str(f.id),
            finding_type=f.finding_type,
            pattern_hash=f.pattern_hash,
            scope=f.scope.value,
            reason=f.reason,
            accepted_by=f.accepted_by,
            accepted_at=f.accepted_at.isoformat(),
            expires_at=f.expires_at.isoformat() if f.expires_at else None,
            context=f.context,
        )
        for f in findings
    ]


@router.get("/{project_id}/memory/rules", response_model=list[BusinessRuleResponse])
async def get_business_rules(project_id: str) -> list[BusinessRuleResponse]:
    """
    Get all business rules for a project.

    Business rules are project-specific rules that override default behavior.
    """
    store = get_memory_store()
    project = _get_project(store, project_id)

    rules = store.get_business_rules(project.id)

    return [
        BusinessRuleResponse(
            id=str(r.id),
            rule_type=r.rule_type,
            rule_config=r.rule_config,
            severity=r.severity.value,
            applies_to=r.applies_to,
            rationale=r.rationale,
            created_by=r.created_by,
            active=r.active,
        )
        for r in rules
    ]


@router.delete("/{project_id}/memory/accepted/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_accepted_finding(project_id: str, finding_id: str) -> None:
    """
    Remove an accepted finding from project memory.

    This will cause the finding to be reported again in future analyses.
    """
    store = get_memory_store()
    project = _get_project(store, project_id)

    # Verify finding exists and belongs to project
    findings = store.get_accepted_findings(project.id)
    finding_ids = [str(f.id) for f in findings]

    if finding_id not in finding_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Accepted finding '{finding_id}' not found in project",
        )

    # Delete using raw SQL (store doesn't have delete method yet)
    with store._get_connection() as conn:
        conn.execute(
            "DELETE FROM accepted_findings WHERE id = ? AND project_id = ?",
            (finding_id, str(project.id)),
        )


# =============================================================================
# Helper Functions
# =============================================================================


def _get_project(store: MemoryStore, project_id: str) -> Project:
    """
    Get project by ID (internal UUID or external ID).

    Raises HTTPException if not found.
    """
    project = None

    # Try as UUID first
    try:
        uuid = UUID(project_id)
        project = store.get_project(uuid)
    except ValueError:
        # Not a UUID, try as external ID
        project = store.get_project_by_external_id(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found",
        )

    return project


def _project_to_response(project: Project) -> ProjectResponse:
    """Convert Project model to response."""
    return ProjectResponse(
        id=str(project.id),
        external_id=project.external_id,
        name=project.name,
        created_at=project.created_at.isoformat(),
        settings=project.settings,
    )
