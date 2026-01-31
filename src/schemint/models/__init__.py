"""Data models for Schemint."""

from schemint.models.analysis import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisSummary,
)
from schemint.models.issue import (
    Issue,
    IssueCategory,
    IssueSeverity,
)
from schemint.models.schema import (
    Column,
    ForeignKey,
    Index,
    ParsedSchema,
    Table,
)

__all__ = [
    # Analysis
    "AnalysisRequest",
    "AnalysisResult",
    "AnalysisSummary",
    # Issues
    "Issue",
    "IssueCategory",
    "IssueSeverity",
    # Schema
    "ParsedSchema",
    "Table",
    "Column",
    "ForeignKey",
    "Index",
]
