"""Issue models for schema analysis results."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    """Issue severity levels."""

    CRITICAL = "critical"  # Will cause failures or data corruption
    WARNING = "warning"  # Performance or data integrity issues
    SUGGESTION = "suggestion"  # Best practice recommendations


class IssueCategory(str, Enum):
    """Categories of schema issues."""

    # Structural
    MISSING_PRIMARY_KEY = "missing_primary_key"
    MISSING_FOREIGN_KEY = "missing_foreign_key"
    ORPHANED_FOREIGN_KEY = "orphaned_foreign_key"
    MISSING_CONSTRAINT = "missing_constraint"

    # Performance
    MISSING_INDEX = "missing_index"
    WRONG_DATA_TYPE = "wrong_data_type"
    INEFFICIENT_TYPE = "inefficient_type"

    # Security
    SECURITY_RISK = "security_risk"
    PII_DETECTED = "pii_detected"

    # Naming
    NAMING_CONVENTION = "naming_convention"
    RESERVED_WORD = "reserved_word"

    # Best Practices
    MISSING_TIMESTAMPS = "missing_timestamps"
    NO_SOFT_DELETE = "no_soft_delete"
    MISSING_NOT_NULL = "missing_not_null"

    # Scalability
    NO_MULTI_TENANCY = "no_multi_tenancy"
    MISSING_CASCADE = "missing_cascade"

    # Other
    OTHER = "other"


class Issue(BaseModel):
    """Represents a single schema issue found during analysis."""

    severity: IssueSeverity = Field(..., description="Issue severity")
    category: IssueCategory = Field(..., description="Issue category")
    title: str = Field(..., description="Short issue title")
    description: str = Field(..., description="Detailed explanation")

    # Location
    table_name: str | None = Field(None, description="Affected table")
    column_name: str | None = Field(None, description="Affected column")

    # Impact
    impact: str | None = Field(None, description="Why this matters")
    example: str | None = Field(None, description="Real-world example")

    # Fix
    fix_description: str | None = Field(None, description="How to fix")
    fix_script: str | None = Field(None, description="SQL script to fix")

    @property
    def location(self) -> str:
        """Get human-readable location string."""
        if self.column_name:
            return f"{self.table_name}.{self.column_name}"
        if self.table_name:
            return self.table_name
        return "schema"

    @property
    def severity_emoji(self) -> str:
        """Get emoji for severity level."""
        return {
            IssueSeverity.CRITICAL: "🔴",
            IssueSeverity.WARNING: "🟠",
            IssueSeverity.SUGGESTION: "🟡",
        }[self.severity]
