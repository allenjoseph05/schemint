"""
Pattern Hashing Utilities.

This module provides functions to create deterministic hashes for findings
and patterns WITHOUT storing the actual SQL content.

The hash is based on:
- Finding type/category
- Table name
- Column name (if applicable)
- Data type (if applicable)
- Semantic markers

The hash is NOT based on:
- Actual SQL text
- Line numbers
- File paths
- Timestamps
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemint.models.issue import Issue


def normalize_pattern(finding: Issue) -> dict[str, Any]:
    """
    Extract a normalized pattern from a finding.

    The pattern contains only the structural elements that define
    the "shape" of the finding, not the specific SQL.

    Args:
        finding: An Issue from analysis

    Returns:
        Dictionary with normalized pattern components
    """
    # Extract semantic markers from column name
    semantic_markers = _extract_semantic_markers(
        finding.column_name,
        finding.table_name,
    )

    pattern = {
        # Core identifiers
        "category": finding.category.value,
        "severity": finding.severity.value,
        # Structural location fields
        "table": finding.table_name.lower() if finding.table_name else None,
        "column": finding.column_name.lower() if finding.column_name else None,
        # Semantic context
        "semantic_markers": sorted(semantic_markers),
    }

    # Add type info if available from the finding description
    if finding.description:
        type_info = _extract_type_info(finding.description)
        if type_info:
            pattern["data_type"] = type_info

    return pattern


def compute_finding_hash(finding: Issue) -> str:
    """
    Compute a deterministic SHA256 hash for a finding pattern.

    Two findings with the same pattern hash are considered "the same issue"
    for the purposes of memory lookup.

    Args:
        finding: An Issue from analysis

    Returns:
        64-character hex string (SHA256 hash)
    """
    pattern = normalize_pattern(finding)
    return compute_pattern_hash(pattern)


def compute_pattern_hash(pattern: dict[str, Any]) -> str:
    """
    Compute SHA256 hash from a pattern dictionary.

    Args:
        pattern: Normalized pattern dictionary

    Returns:
        64-character hex string (SHA256 hash)
    """
    # Create deterministic JSON string
    canonical = json.dumps(pattern, sort_keys=True, separators=(",", ":"))

    # Compute hash
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_semantic_markers(
    column_name: str | None,
    table_name: str | None,
) -> list[str]:
    """
    Extract semantic markers from column/table names.

    These markers help identify the PURPOSE of a column,
    not just its technical structure.
    """
    markers = []

    if column_name:
        col_lower = column_name.lower()

        # Money-related
        if any(
            kw in col_lower
            for kw in ["price", "cost", "amount", "total", "balance", "fee", "payment"]
        ):
            markers.append("money")

        # Date/time-related
        if any(kw in col_lower for kw in ["_at", "date", "time", "created", "updated", "deleted"]):
            markers.append("temporal")

        # ID-related
        if col_lower == "id" or col_lower.endswith("_id"):
            markers.append("identifier")

        # Status-related
        if any(kw in col_lower for kw in ["status", "state", "type", "kind"]):
            markers.append("categorical")

        # Metrics-related
        if any(
            kw in col_lower
            for kw in ["count", "total", "avg", "sum", "metric", "rate", "percentage"]
        ):
            markers.append("metric")

        # PII-related
        if any(kw in col_lower for kw in ["email", "phone", "ssn", "password", "name", "address"]):
            markers.append("pii")

    if table_name:
        tbl_lower = table_name.lower()

        # Audit/system tables
        if any(kw in tbl_lower for kw in ["log", "audit", "history", "archive"]):
            markers.append("audit")

        # Lookup/reference tables
        if any(kw in tbl_lower for kw in ["type", "status", "category", "lookup"]):
            markers.append("reference")

    return markers


def _extract_type_info(description: str) -> str | None:
    """
    Extract data type information from finding description.

    Args:
        description: Finding description text

    Returns:
        Extracted type or None
    """
    description_upper = description.upper()

    # Common types to look for
    types = [
        "FLOAT",
        "DOUBLE",
        "DECIMAL",
        "INT",
        "BIGINT",
        "VARCHAR",
        "TEXT",
        "DATETIME",
        "TIMESTAMP",
    ]

    for dtype in types:
        if dtype in description_upper:
            return dtype

    return None


def patterns_match(pattern1: dict[str, Any], pattern2: dict[str, Any]) -> bool:
    """
    Check if two patterns match (for pattern-scoped acceptances).

    This is more lenient than hash equality - patterns match if they
    have the same category and semantic markers, regardless of table.

    Args:
        pattern1: First pattern
        pattern2: Second pattern

    Returns:
        True if patterns are considered equivalent
    """
    # Must have same category
    if pattern1.get("category") != pattern2.get("category"):
        return False

    # Must have same semantic markers
    markers1 = set(pattern1.get("semantic_markers", []))
    markers2 = set(pattern2.get("semantic_markers", []))

    if markers1 != markers2:
        return False

    # Must have same data type (if specified)
    return pattern1.get("data_type") == pattern2.get("data_type")


def create_rule_pattern(finding_type: str) -> dict[str, Any]:
    """
    Create a pattern for rule-scoped matching.

    Rule-scoped patterns match ANY finding of the same type,
    regardless of table, column, or semantics.

    Args:
        finding_type: The finding category/type

    Returns:
        Pattern dictionary for rule-level matching
    """
    return {
        "category": finding_type,
        "scope": "rule",
    }
