"""
CI Integration Models.

Data models for CI/CD integration (Phase 2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class CIEventType(str, Enum):
    """Types of CI events that trigger analysis."""

    PULL_REQUEST = "pull_request"
    PUSH = "push"
    MIGRATION = "migration"
    PRE_DEPLOY = "pre_deploy"
    MANUAL = "manual"


class GitProvider(str, Enum):
    """Supported git providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    AZURE_DEVOPS = "azure_devops"
    GENERIC = "generic"


class DecisionStatus(str, Enum):
    """Status of an analysis decision."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    ERROR = "error"


# =============================================================================
# Request Models
# =============================================================================


class CIIngestRequest(BaseModel):
    """
    Request to ingest a CI event for analysis.

    This is the PRIMARY entry point for CI integration.
    """

    project_id: str = Field(
        ...,
        description="Project identifier (e.g., 'github:org/repo')"
    )
    event_type: CIEventType = Field(
        ...,
        description="Type of CI event"
    )
    ref: str = Field(
        ...,
        description="Git ref to analyze (commit SHA, branch, PR ref)"
    )
    base_ref: str = Field(
        ...,
        description="Base ref for diff calculation (e.g., 'main')"
    )
    provider: GitProvider = Field(
        ...,
        description="Git provider"
    )
    provider_token: str | None = Field(
        None,
        description="Token for git provider access"
    )

    # Optional context
    pr_number: int | None = Field(None, description="PR number if applicable")
    pr_title: str | None = Field(None, description="PR title if applicable")
    author: str | None = Field(None, description="Author of changes")

    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "github:acme/ecommerce",
                "event_type": "pull_request",
                "ref": "refs/pull/123/head",
                "base_ref": "main",
                "provider": "github",
                "pr_number": 123,
                "pr_title": "Add new payments table"
            }
        }


# =============================================================================
# Diff Models
# =============================================================================


class FileChange(BaseModel):
    """A single file change in a diff."""

    path: str = Field(..., description="File path")
    change_type: str = Field(..., description="added | modified | deleted")
    additions: int = Field(0, description="Lines added")
    deletions: int = Field(0, description="Lines deleted")


class SQLChange(BaseModel):
    """
    A SQL change extracted from a diff.

    NOTE: Contains parsed structure, NOT raw SQL.
    """

    file_path: str = Field(..., description="Source file path")
    change_type: str = Field(..., description="added | modified | deleted")

    # Parsed structure (NOT raw SQL)
    tables_added: list[str] = Field(default_factory=list)
    tables_modified: list[str] = Field(default_factory=list)
    tables_dropped: list[str] = Field(default_factory=list)
    columns_added: list[str] = Field(default_factory=list, description="Format: table.column")
    columns_modified: list[str] = Field(default_factory=list)
    columns_dropped: list[str] = Field(default_factory=list)


class SchemaDiff(BaseModel):
    """
    Complete schema diff for analysis.

    Contains structured information about what changed,
    NOT the raw SQL content.
    """

    ref: str = Field(..., description="Head ref")
    base_ref: str = Field(..., description="Base ref")

    # File changes
    files_changed: list[FileChange] = Field(default_factory=list)
    sql_files: list[str] = Field(default_factory=list, description="SQL files in diff")

    # Parsed changes
    sql_changes: list[SQLChange] = Field(default_factory=list)

    # Summary
    total_tables_affected: int = Field(0)
    total_columns_affected: int = Field(0)


# =============================================================================
# Response Models
# =============================================================================


class FindingLocation(BaseModel):
    """Location of a finding in the diff."""

    file: str | None = Field(None, description="File path")
    line: int | None = Field(None, description="Line number")
    table: str | None = Field(None, description="Table name")
    column: str | None = Field(None, description="Column name")


class AnalysisFinding(BaseModel):
    """A single finding from analysis."""

    id: str = Field(default_factory=lambda: f"find_{uuid4().hex[:8]}")
    type: str = Field(..., description="Finding type")
    severity: str = Field(..., description="critical | warning | suggestion")
    title: str = Field(..., description="Short title")
    description: str = Field(..., description="Detailed description")
    location: FindingLocation = Field(default_factory=FindingLocation)

    # Memory context
    memory_context: str | None = Field(
        None,
        description="How memory affected this finding"
    )
    suppressed_by_memory: bool = Field(
        False,
        description="Was this suppressed by memory?"
    )

    # Actions
    suggested_action: str = Field("warn", description="block | warn | info")
    feedback_url: str | None = Field(None, description="URL to submit feedback")


class AnalysisDecision(BaseModel):
    """
    Complete analysis decision for a CI event.

    This is returned from the /ci/ingest endpoint.
    """

    decision_id: str = Field(default_factory=lambda: f"dec_{uuid4().hex[:12]}")
    project_id: str = Field(..., description="Project identifier")
    ref: str = Field(..., description="Analyzed ref")

    # Result
    status: DecisionStatus = Field(..., description="Overall status")
    findings: list[AnalysisFinding] = Field(default_factory=list)

    # Counts
    critical_count: int = Field(0)
    warning_count: int = Field(0)
    suggestion_count: int = Field(0)
    suppressed_count: int = Field(0, description="Findings suppressed by memory")

    # Memory
    memory_applied: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Memory items that affected this analysis"
    )

    # Metadata
    duration_ms: int = Field(0)
    created_at: datetime = Field(default_factory=_utc_now)

    # URLs
    check_url: str | None = Field(None, description="URL to view full results")
    feedback_base_url: str | None = Field(None, description="Base URL for feedback")

    class Config:
        json_schema_extra = {
            "example": {
                "decision_id": "dec_abc123def456",
                "project_id": "github:acme/ecommerce",
                "ref": "abc123",
                "status": "warn",
                "findings": [
                    {
                        "id": "find_001",
                        "type": "missing_primary_key",
                        "severity": "critical",
                        "title": "Table 'payments' has no primary key",
                        "location": {"table": "payments"},
                        "memory_context": "No prior exceptions for this pattern"
                    }
                ],
                "critical_count": 1,
                "warning_count": 0,
                "suppressed_count": 0,
                "memory_applied": []
            }
        }
