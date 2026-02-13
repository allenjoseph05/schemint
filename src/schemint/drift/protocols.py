"""Protocol definitions for schema drift detection extension points.

These protocols enable dependency injection and testing without requiring
concrete implementations. They define the contracts that extractors,
introspectors, and stores must satisfy.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemint.drift.models import (
    ColumnSnapshot,
    DependencyEdge,
    DependencyGraph,
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
    ViewSnapshot,
)


@runtime_checkable
class DatabaseIntrospector(Protocol):
    """Abstract interface for database introspection.

    Implementations provide access to a specific database's schema metadata
    (columns, primary keys, indexes, foreign keys, views, triggers, etc.).
    """

    def fetch_table_names(self, schema_name: str) -> list[str]:
        """Return all user table names in the given schema."""
        ...

    def fetch_columns(
        self, schema_name: str, table_name: str
    ) -> dict[str, ColumnSnapshot]:
        """Return columns for a table, ordered by ordinal position."""
        ...

    def fetch_primary_key(self, schema_name: str, table_name: str) -> list[str]:
        """Return primary key column names, ordered by ordinal position."""
        ...

    def fetch_indexes(self, schema_name: str, table_name: str) -> list[dict]:
        """Return index metadata for a table."""
        ...

    def fetch_foreign_keys(self, schema_name: str, table_name: str) -> list[dict]:
        """Return foreign key metadata for a table."""
        ...

    def fetch_check_constraints(
        self, schema_name: str, table_name: str
    ) -> list[str]:
        """Return CHECK constraint expressions for a table."""
        ...

    def fetch_views(self, schema_name: str) -> dict[str, ViewSnapshot]:
        """Return all views in the given schema."""
        ...

    def fetch_triggers(self, schema_name: str) -> dict[str, TriggerSnapshot]:
        """Return all triggers in the given schema."""
        ...


@runtime_checkable
class EdgeExtractor(Protocol):
    """Abstract interface for dependency edge extraction strategies.

    Each implementation extracts edges from a specific source type
    (FK constraints, SQL AST, dbt manifests, view definitions, etc.).
    """

    def extract(self, **kwargs) -> list[DependencyEdge]:
        """Extract dependency edges from the given source."""
        ...


@runtime_checkable
class DriftStoreProtocol(Protocol):
    """Abstract interface for drift data persistence.

    Implementations handle saving and loading schema snapshots
    and dependency graphs.
    """

    def save_snapshot(self, project_id: str, snapshot: SchemaSnapshot) -> str:
        """Save a schema snapshot. Returns a storage identifier."""
        ...

    def get_latest_snapshot(self, project_id: str) -> SchemaSnapshot | None:
        """Get the most recent snapshot for a project."""
        ...

    def save_dependency_graph(
        self, project_id: str, graph: DependencyGraph
    ) -> None:
        """Save a dependency graph (replaces existing for project)."""
        ...

    def get_dependency_graph(self, project_id: str) -> DependencyGraph | None:
        """Get the current dependency graph for a project."""
        ...
