"""
Project Memory Store Module.

This module provides durable, project-scoped storage for:
- Accepted findings (false positives, intentional patterns)
- Known-safe patterns
- Business rules
- Schema semantics
- Historical inflection points

IMPORTANT: This module NEVER stores raw SQL or source code.
Only structured conclusions and pattern hashes are persisted.
"""

from schemint.memory.models import (
    AcceptedFinding,
    AnalysisHistory,
    BusinessRule,
    FeedbackScope,
    HistoricalInflectionPoint,
    KnownSafePattern,
    Project,
    SchemaSemantics,
)
from schemint.memory.patterns import compute_finding_hash, normalize_pattern
from schemint.memory.store import MemoryStore, get_memory_store

__all__ = [
    # Models
    "Project",
    "AcceptedFinding",
    "KnownSafePattern",
    "BusinessRule",
    "SchemaSemantics",
    "HistoricalInflectionPoint",
    "AnalysisHistory",
    "FeedbackScope",
    # Store
    "MemoryStore",
    "get_memory_store",
    # Utilities
    "compute_finding_hash",
    "normalize_pattern",
]
