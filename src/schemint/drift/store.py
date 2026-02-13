"""Drift store — PostgreSQL persistence for snapshots and dependency graphs.

Immutability semantics:
    - Stored snapshots are immutable facts. Once saved, a snapshot is never
      updated — a new snapshot is captured instead.
    - Dependency graphs are rebuilt, not mutated. save_dependency_graph()
      replaces all edges for a project (idempotent rebuild), but this
      creates a new version — the old edges are deleted, not patched.
    - Coverage metrics and build timestamps (built_at) must remain
      auditable: they record when the graph was computed, not when
      the underlying schema was captured.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras

from schemint.drift.models import (
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    SchemaSnapshot,
)


class DriftStore:
    """PostgreSQL storage for schema snapshots and dependency graphs."""

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("DATABASE_URL is required for DriftStore.")
        self.database_url = database_url
        self._init_database()

    @contextmanager
    def _get_connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_database(self) -> None:
        """Create drift tables if they don't exist."""
        create_sql = """
            CREATE TABLE IF NOT EXISTS schema_snapshots (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                source TEXT NOT NULL,
                database_type TEXT NOT NULL,
                snapshot_data JSONB NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dependency_edges (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                from_element TEXT NOT NULL,
                to_element TEXT NOT NULL,
                usage_type TEXT NOT NULL,
                sources JSONB NOT NULL DEFAULT '[]',
                final_confidence FLOAT NOT NULL DEFAULT 0.0,
                built_at TIMESTAMPTZ NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_project
                ON schema_snapshots(project_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_edges_project
                ON dependency_edges(project_id);
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_sql)

    def save_snapshot(self, project_id: str, snapshot: SchemaSnapshot) -> str:
        """Save a schema snapshot. Returns the row UUID."""
        row_id = str(uuid4())
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO schema_snapshots
                    (id, project_id, snapshot_id, source, database_type, snapshot_data, captured_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row_id,
                        project_id,
                        snapshot.snapshot_id,
                        snapshot.source,
                        snapshot.database_type,
                        snapshot.model_dump_json(),
                        snapshot.captured_at,
                    ),
                )
        return row_id

    def get_latest_snapshot(self, project_id: str) -> SchemaSnapshot | None:
        """Get the most recent snapshot for a project."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT snapshot_data FROM schema_snapshots
                    WHERE project_id = %s
                    ORDER BY captured_at DESC
                    LIMIT 1
                    """,
                    (project_id,),
                )
                row = cur.fetchone()

        if not row:
            return None

        data = row["snapshot_data"]
        if isinstance(data, str):
            data = json.loads(data)
        return SchemaSnapshot(**data)

    def get_snapshot(self, snapshot_id: str) -> SchemaSnapshot | None:
        """Get a snapshot by its snapshot_id."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT snapshot_data FROM schema_snapshots WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                row = cur.fetchone()

        if not row:
            return None

        data = row["snapshot_data"]
        if isinstance(data, str):
            data = json.loads(data)
        return SchemaSnapshot(**data)

    def save_dependency_graph(self, project_id: str, graph: DependencyGraph) -> None:
        """Save a dependency graph (replaces existing edges for project)."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Delete existing edges for this project
                cur.execute(
                    "DELETE FROM dependency_edges WHERE project_id = %s",
                    (project_id,),
                )
                # Insert new edges
                for edge in graph.edges:
                    cur.execute(
                        """
                        INSERT INTO dependency_edges
                        (id, project_id, from_element, to_element, usage_type,
                         sources, final_confidence, built_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            project_id,
                            edge.from_element,
                            edge.to_element,
                            edge.usage_type,
                            json.dumps([s.model_dump() for s in edge.sources], default=str),
                            edge.final_confidence,
                            graph.built_at,
                        ),
                    )

    def get_dependency_graph(self, project_id: str) -> DependencyGraph | None:
        """Get the current dependency graph for a project."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT from_element, to_element, usage_type, sources,
                           final_confidence, built_at
                    FROM dependency_edges
                    WHERE project_id = %s
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()

        if not rows:
            return None

        edges: list[DependencyEdge] = []
        built_at = None

        for row in rows:
            sources_data = row["sources"]
            if isinstance(sources_data, str):
                sources_data = json.loads(sources_data)

            sources = [DependencySource(**s) for s in sources_data]
            edges.append(DependencyEdge(
                from_element=row["from_element"],
                to_element=row["to_element"],
                usage_type=row["usage_type"],
                sources=sources,
                final_confidence=row["final_confidence"],
            ))
            if built_at is None:
                built_at = row["built_at"]

        return DependencyGraph(
            edges=edges,
            built_at=built_at or datetime.now(timezone.utc),
        )


# Global instance
_store: DriftStore | None = None


def get_drift_store(database_url: str | None = None) -> DriftStore:
    """Get the global drift store instance.

    Args:
        database_url: Optional explicit database URL. If provided, creates
            a new store with this URL (useful for testing). If None, uses
            the configured settings.
    """
    global _store
    if database_url is not None:
        _store = DriftStore(database_url=database_url)
        return _store
    if _store is None:
        from schemint.config import get_settings
        settings = get_settings()
        if not settings.database_url:
            raise ValueError("DATABASE_URL must be set for drift store.")
        _store = DriftStore(database_url=settings.database_url)
    return _store


def set_drift_store(store: DriftStore | None) -> None:
    """Set or clear the global drift store instance.

    Useful for test injection:
        set_drift_store(mock_store)
        # ... run tests ...
        set_drift_store(None)  # reset
    """
    global _store
    _store = store
