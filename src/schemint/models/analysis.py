"""Analysis request and response models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from schemint.models.issue import Issue, IssueSeverity
from schemint.models.schema import Table


def _utc_now() -> datetime:
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class AnalysisContext(BaseModel):
    """Optional context for better analysis."""

    app_type: str | None = Field(
        None,
        description="Application type (e.g., 'ecommerce', 'saas', 'blog')",
    )
    expected_scale: str | None = Field(
        None,
        description="Expected scale (e.g., 'small', 'medium', 'large')",
    )


class AnalysisRequest(BaseModel):
    """Request to analyze a schema."""

    sql: str = Field(
        ...,
        min_length=10,
        max_length=100000,
        description="SQL CREATE TABLE statements",
    )
    database_type: Literal["mysql", "postgresql", "sqlite"] = Field(
        default="mysql",
        description="Target database type",
    )
    context: AnalysisContext | None = Field(
        None,
        description="Optional context for better analysis",
    )


class AnalysisScore(BaseModel):
    """Analysis scoring breakdown."""

    total: int = Field(..., ge=0, le=100, description="Overall score (0-100)")
    structural: int = Field(..., ge=0, le=100, description="Structural score")
    performance: int = Field(..., ge=0, le=100, description="Performance score")
    naming: int = Field(..., ge=0, le=100, description="Naming conventions score")
    best_practices: int = Field(..., ge=0, le=100, description="Best practices score")

    @property
    def grade(self) -> str:
        """Get letter grade."""
        if self.total >= 90:
            return "A"
        if self.total >= 80:
            return "B"
        if self.total >= 70:
            return "C"
        if self.total >= 60:
            return "D"
        return "F"

    @property
    def label(self) -> str:
        """Get human-readable label."""
        if self.total >= 90:
            return "Excellent"
        if self.total >= 80:
            return "Good"
        if self.total >= 70:
            return "Decent"
        if self.total >= 60:
            return "Needs Work"
        return "Poor"


class TableSummary(BaseModel):
    """Summary of a parsed table."""

    name: str
    column_count: int
    has_primary_key: bool
    has_timestamps: bool
    foreign_key_count: int
    index_count: int

    @classmethod
    def from_table(cls, table: Table) -> TableSummary:
        """Create summary from Table model."""
        return cls(
            name=table.name,
            column_count=len(table.columns),
            has_primary_key=table.has_primary_key(),
            has_timestamps=table.has_timestamps(),
            foreign_key_count=len(table.foreign_keys),
            index_count=len(table.indexes),
        )


class AnalysisResult(BaseModel):
    """Complete analysis result."""

    # Metadata
    id: str = Field(..., description="Analysis ID")
    created_at: datetime = Field(default_factory=_utc_now)
    duration_ms: int | None = Field(None, description="Analysis duration in ms")

    # Scores
    score: AnalysisScore = Field(..., description="Score breakdown")

    # Schema info
    tables: list[TableSummary] = Field(default_factory=list)
    table_count: int = Field(0)

    # Issues
    issues: list[Issue] = Field(default_factory=list)
    critical_count: int = Field(0)
    warning_count: int = Field(0)
    suggestion_count: int = Field(0)

    # Good practices
    good_practices: list[str] = Field(default_factory=list)

    # AI analysis (if enabled)
    ai_summary: str | None = Field(None, description="AI-generated summary")

    def get_issues_by_severity(self, severity: IssueSeverity) -> list[Issue]:
        """Get issues filtered by severity."""
        return [i for i in self.issues if i.severity == severity]

    def get_issues_by_table(self, table_name: str) -> list[Issue]:
        """Get issues for a specific table."""
        return [i for i in self.issues if i.table_name == table_name]

    @property
    def fix_script(self) -> str:
        """Get combined fix script for all issues."""
        scripts = []
        for issue in self.issues:
            if issue.fix_script:
                scripts.append(f"-- {issue.title}")
                scripts.append(issue.fix_script)
                scripts.append("")
        return "\n".join(scripts)


class AnalysisSummary(BaseModel):
    """Summary view for listing analyses."""

    id: str
    created_at: datetime
    score: int
    grade: str
    table_count: int
    issue_count: int
    critical_count: int
