"""Custom exception hierarchy for schema drift detection.

Provides specific exception types so callers can handle different failure
modes (DDL parse errors vs. live DB errors vs. store errors) distinctly.
"""

from __future__ import annotations


class DriftError(Exception):
    """Base exception for all schema drift detection errors."""


# =============================================================================
# Snapshot Errors (Phase 0)
# =============================================================================


class SnapshotError(DriftError):
    """Error during schema snapshot capture."""


class DDLParseError(SnapshotError):
    """Failed to parse DDL SQL for snapshot capture."""


class LiveDBError(SnapshotError):
    """Failed to introspect a live database for snapshot capture."""


# =============================================================================
# Dependency Errors (Phase 0)
# =============================================================================


class DependencyError(DriftError):
    """Error during dependency graph construction."""


class SqlParseError(DependencyError):
    """sqlglot failed to parse SQL for dependency extraction."""


class ManifestParseError(DependencyError):
    """Failed to parse a dbt manifest.json for dependency extraction."""


# =============================================================================
# Diff Errors (Phase 1)
# =============================================================================


class DiffError(DriftError):
    """Error during schema diff computation."""


# =============================================================================
# Store Errors
# =============================================================================


class StoreError(DriftError):
    """Error during drift store operations (save/load snapshots or graphs)."""
