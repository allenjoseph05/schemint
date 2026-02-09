"""
Memory Store Data Models.

These models define the structure of project memory.
All models are designed to store CONCLUSIONS, not raw code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class FeedbackScope(str, Enum):
    """Scope of feedback application."""

    ONCE = "once"          # Just this specific instance
    PATTERN = "pattern"    # Similar patterns in this project
    RULE = "rule"          # All instances of this rule type


class FindingSeverity(str, Enum):
    """Finding severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"
    IGNORE = "ignore"


class ElementType(str, Enum):
    """Type of schema element."""

    TABLE = "table"
    COLUMN = "column"
    RELATIONSHIP = "relationship"


class EventType(str, Enum):
    """Type of historical event."""

    LEGACY_MIGRATION = "legacy_migration"
    STANDARD_CHANGE = "standard_change"
    EXCEPTION_GRANTED = "exception_granted"
    CONVENTION_ADOPTED = "convention_adopted"
    CONVENTION_RETIRED = "convention_retired"


# =============================================================================
# Core Models
# =============================================================================


class Project(BaseModel):
    """
    A registered project in the memory store.

    Projects are identified by their external ID (e.g., "github:org/repo").
    """

    id: UUID = Field(default_factory=uuid4)
    external_id: str = Field(..., description="External identifier (e.g., 'github:org/repo')")
    name: str = Field(..., description="Human-readable project name")
    created_at: datetime = Field(default_factory=_utc_now)
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Project-level settings"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "external_id": "github:acme/ecommerce",
                "name": "ACME E-Commerce Platform",
                "settings": {
                    "default_severity": "warning",
                    "auto_block_rules": ["missing_primary_key"],
                },
            }
        }
    )


class AcceptedFinding(BaseModel):
    """
    A finding that was accepted (marked as false positive or intentional).

    When a finding is accepted, it won't be reported again for the same pattern.
    The scope determines how broadly the acceptance applies.
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # What was accepted
    finding_type: str = Field(..., description="Type of finding (e.g., 'missing_primary_key')")
    pattern_hash: str = Field(..., description="SHA256 hash of the normalized pattern")

    # How broadly to apply
    scope: FeedbackScope = Field(..., description="Scope of acceptance")

    # Context
    reason: str = Field(..., description="Human explanation for acceptance")
    accepted_by: str = Field(..., description="User who accepted")
    accepted_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = Field(None, description="Optional expiration")

    # Additional context (NO raw SQL here)
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context (table name, semantic tags, etc.)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "finding_type": "wrong_data_type_float",
                "pattern_hash": "a1b2c3d4...",
                "scope": "pattern",
                "reason": "FLOAT is acceptable for metrics, not financial data",
                "accepted_by": "alice@example.com",
                "context": {
                    "table": "metrics",
                    "column": "value",
                    "semantic_tags": ["metrics", "non_financial"],
                },
            }
        }
    )


class KnownSafePattern(BaseModel):
    """
    A pattern that is known to be safe for this project.

    Unlike AcceptedFinding (which is reactive), this is proactive:
    patterns marked safe won't even generate findings.
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # Pattern identification
    pattern_type: str = Field(..., description="Category of pattern")
    pattern_hash: str = Field(..., description="SHA256 hash of the pattern")

    # Documentation
    description: str = Field(..., description="What this pattern is and why it's safe")
    created_by: str = Field(..., description="User who created")
    created_at: datetime = Field(default_factory=_utc_now)

    # Examples (structured, not raw SQL)
    examples: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Examples of this pattern (table, column, rationale)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "pattern_type": "float_for_percentages",
                "description": "FLOAT is acceptable for percentage values (0.0-100.0)",
                "examples": [
                    {"table": "metrics", "column": "cpu_usage", "rationale": "CPU percentage"}
                ],
            }
        }
    )


class BusinessRule(BaseModel):
    """
    A project-specific rule that overrides or modifies default behavior.

    Business rules can:
    - Change severity of certain findings
    - Add new requirements (e.g., require tenant_id column)
    - Exempt certain tables from rules
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # Rule definition
    rule_type: str = Field(..., description="Type of rule")
    rule_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Rule-specific configuration"
    )

    # Severity override
    severity: FindingSeverity = Field(..., description="Severity to apply")

    # Scope
    applies_to: dict[str, Any] = Field(
        default_factory=lambda: {"tables": ["*"]},
        description="Which tables this applies to"
    )

    # Documentation
    rationale: str = Field(..., description="Why this rule exists")
    created_by: str = Field(..., description="User who created")
    created_at: datetime = Field(default_factory=_utc_now)
    active: bool = Field(True, description="Whether rule is active")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rule_type": "require_tenant_id",
                "rule_config": {"column_name": "tenant_id", "type": "UUID"},
                "severity": "critical",
                "applies_to": {"tables": ["*"], "except": ["migrations", "schema_versions"]},
                "rationale": "Multi-tenant architecture requires tenant isolation",
            }
        }
    )


class SchemaSemantics(BaseModel):
    """
    Semantic meaning attached to schema elements.

    This helps the reasoning engine understand WHAT data means,
    not just its technical structure.
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # Element identification
    element_type: ElementType = Field(..., description="Type of element")
    element_path: str = Field(..., description="Path (e.g., 'orders.total' or 'users')")

    # Semantic information
    semantic_tags: list[str] = Field(
        default_factory=list,
        description="Semantic tags (e.g., ['money', 'usd', 'immutable'])"
    )
    description: str = Field(..., description="Human description of purpose")

    # Constraints implied by semantics
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Semantic constraints (e.g., {'currency': 'USD', 'precision': 4})"
    )

    updated_at: datetime = Field(default_factory=_utc_now)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "element_type": "column",
                "element_path": "orders.total",
                "semantic_tags": ["money", "usd", "customer_facing"],
                "description": "Total order amount in USD, shown to customers",
                "constraints": {"currency": "USD", "min_precision": 2},
            }
        }
    )


class HistoricalInflectionPoint(BaseModel):
    """
    A major event that affects how we interpret the schema.

    Examples:
    - "Before 2023, we used FLOAT for money" → lenient for old tables
    - "We adopted snake_case in v2.0" → strict for new tables
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # Event details
    event_type: EventType = Field(..., description="Type of event")
    event_date: datetime = Field(..., description="When this happened")
    description: str = Field(..., description="What happened")

    # Impact on analysis
    impact: dict[str, Any] = Field(
        default_factory=dict,
        description="How this affects analysis"
    )

    # Scope
    affected_tables: list[str] = Field(
        default_factory=list,
        description="Tables affected (empty = all)"
    )

    created_at: datetime = Field(default_factory=_utc_now)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_type": "convention_adopted",
                "event_date": "2023-06-01T00:00:00Z",
                "description": "Adopted DECIMAL for all money columns",
                "impact": {
                    "before_date": {"float_for_money": "warning"},
                    "after_date": {"float_for_money": "critical"},
                },
                "affected_tables": [],  # All tables
            }
        }
    )


class AnalysisHistory(BaseModel):
    """
    Record of an analysis run.

    Stores metadata about the analysis, not the actual SQL.
    Used for trends and understanding project health over time.
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID = Field(..., description="Project this belongs to")

    # Reference
    ref: str = Field(..., description="Commit SHA or PR ref")
    event_type: str = Field(..., description="Type of CI event")

    # Result
    status: str = Field(..., description="pass | fail | warn")
    finding_count: int = Field(..., description="Number of findings")
    findings_hash: str = Field(..., description="Hash of finding types for dedup")

    # Memory usage
    memory_applied: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Which memory items were applied"
    )

    # Performance
    duration_ms: int = Field(..., description="Analysis duration")
    created_at: datetime = Field(default_factory=_utc_now)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ref": "abc123def",
                "event_type": "pull_request",
                "status": "pass",
                "finding_count": 0,
                "findings_hash": "d41d8cd98f00b204e9800998ecf8427e",
                "memory_applied": [
                    {"type": "accepted_finding", "id": "...", "reason": "..."}
                ],
                "duration_ms": 1234,
            }
        }
    )


# =============================================================================
# API Request/Response Models
# =============================================================================


class ProjectRegistration(BaseModel):
    """Request to register a new project."""

    external_id: str = Field(..., description="External identifier")
    name: str = Field(..., description="Human-readable name")
    settings: dict[str, Any] = Field(default_factory=dict)


class FindingFeedback(BaseModel):
    """Feedback on a specific finding."""

    finding_id: str = Field(..., description="ID of the finding")
    action: str = Field(..., description="accept | override")
    reason: str = Field(..., description="Why this decision was made")
    scope: FeedbackScope = Field(FeedbackScope.ONCE, description="How broadly to apply")


class MemorySummary(BaseModel):
    """Summary of project memory state."""

    project_id: UUID
    project_name: str
    accepted_findings_count: int
    safe_patterns_count: int
    business_rules_count: int
    semantic_entries_count: int
    inflection_points_count: int
    last_analysis: datetime | None
    total_analyses: int
