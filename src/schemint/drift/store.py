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
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg2
import psycopg2.extras

from schemint.drift.models import (
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    DriftRunResult,
    MigrationRecord,
    SchemaDiffResult,
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
                environment TEXT NOT NULL DEFAULT 'default',
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

            CREATE TABLE IF NOT EXISTS schema_change_history (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                diff_data JSONB NOT NULL,
                change_count INTEGER NOT NULL DEFAULT 0,
                diffed_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS desired_states (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                environment TEXT NOT NULL DEFAULT 'default',
                source TEXT NOT NULL DEFAULT 'ddl',
                snapshot_data JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS migration_records (
                id TEXT PRIMARY KEY,
                migration_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                environment TEXT NOT NULL DEFAULT 'default',
                migration_type TEXT NOT NULL,
                migration_sql TEXT,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL,
                applied_by TEXT,
                execution_time_ms INTEGER,
                success BOOLEAN NOT NULL DEFAULT TRUE,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_project
                ON schema_snapshots(project_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshots_project_env
                ON schema_snapshots(project_id, environment, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_edges_project
                ON dependency_edges(project_id);
            CREATE INDEX IF NOT EXISTS idx_change_history_project
                ON schema_change_history(project_id, diffed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_desired_states_project_env
                ON desired_states(project_id, environment, active);
            CREATE INDEX IF NOT EXISTS idx_migration_records_project_env
                ON migration_records(project_id, environment, applied_at DESC);
            CREATE INDEX IF NOT EXISTS idx_migration_records_checksum
                ON migration_records(project_id, environment, checksum);

            CREATE TABLE IF NOT EXISTS drift_runs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                decision JSONB,
                plan JSONB,
                execution_report JSONB,
                verification_report JSONB,
                memory_context JSONB,
                retry_count INTEGER NOT NULL DEFAULT 0,
                state_transitions JSONB NOT NULL DEFAULT '[]',
                error TEXT,
                started_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ
            );

            CREATE INDEX IF NOT EXISTS idx_drift_runs_project
                ON drift_runs(project_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_drift_runs_run_id
                ON drift_runs(run_id);
        """
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(create_sql)

    def save_snapshot(self, project_id: str, snapshot: SchemaSnapshot) -> str:
        """Save a schema snapshot. Returns the row UUID."""
        row_id = str(uuid4())
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO schema_snapshots
                    (id, project_id, snapshot_id, source, database_type, environment,
                     snapshot_data, captured_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    row_id,
                    project_id,
                    snapshot.snapshot_id,
                    snapshot.source,
                    snapshot.database_type,
                    snapshot.environment,
                    snapshot.model_dump_json(),
                    snapshot.captured_at,
                ),
            )
        return row_id

    def get_latest_snapshot(self, project_id: str) -> SchemaSnapshot | None:
        """Get the most recent snapshot for a project."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
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
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
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
        with self._get_connection() as conn, conn.cursor() as cur:
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
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
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
            edges.append(
                DependencyEdge(
                    from_element=row["from_element"],
                    to_element=row["to_element"],
                    usage_type=row["usage_type"],
                    sources=sources,
                    final_confidence=row["final_confidence"],
                )
            )
            if built_at is None:
                built_at = row["built_at"]

        return DependencyGraph(
            edges=edges,
            built_at=built_at or datetime.now(timezone.utc),
        )

    def save_diff_result(self, project_id: str, diff: SchemaDiffResult) -> str:
        """Save a diff result to change history for trend analysis.

        Returns the row UUID. The AI agent uses change history to gauge
        table stability — frequently-changing tables deserve less scrutiny
        than long-stable tables that suddenly change.
        """
        row_id = str(uuid4())
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO schema_change_history
                    (id, project_id, diff_data, change_count, diffed_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                (
                    row_id,
                    project_id,
                    diff.model_dump_json(),
                    len(diff.changes),
                    diff.diffed_at,
                ),
            )
        return row_id

    def get_change_history(self, project_id: str, limit: int = 50) -> list[SchemaDiffResult]:
        """Get recent change history for a project, newest first."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(
                """
                SELECT diff_data FROM schema_change_history
                WHERE project_id = %s
                ORDER BY diffed_at DESC
                LIMIT %s
                """,
                (project_id, limit),
            )
            rows = cur.fetchall()

        results: list[SchemaDiffResult] = []
        for row in rows:
            data = row["diff_data"]
            if isinstance(data, str):
                data = json.loads(data)
            results.append(SchemaDiffResult(**data))
        return results

    def get_table_change_frequency(self, project_id: str, table_name: str, days: int = 90) -> int:
        """Count how many times a specific table has been changed recently.

        Used by the AI to determine table stability.
        """
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT COUNT(*) FROM schema_change_history
                    WHERE project_id = %s
                      AND diffed_at > NOW() - INTERVAL '%s days'
                      AND diff_data::text LIKE %s
                    """,
                (project_id, days, f'%"{table_name}"%'),
            )
            result = cur.fetchone()
        return result[0] if result else 0

    # =========================================================================
    # Environment-aware snapshot retrieval
    # =========================================================================

    def get_latest_snapshot_for_environment(
        self, project_id: str, environment: str
    ) -> SchemaSnapshot | None:
        """Get the most recent snapshot for a project in a specific environment."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(
                """
                SELECT snapshot_data FROM schema_snapshots
                WHERE project_id = %s AND environment = %s
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (project_id, environment),
            )
            row = cur.fetchone()

        if not row:
            return None

        data = row["snapshot_data"]
        if isinstance(data, str):
            data = json.loads(data)
        return SchemaSnapshot(**data)

    # =========================================================================
    # Desired state management
    # =========================================================================

    def save_desired_state(
        self, project_id: str, snapshot: SchemaSnapshot, environment: str = "default"
    ) -> str:
        """Save a desired state snapshot, deactivating any previous active state.

        Returns the row UUID.
        """
        row_id = str(uuid4())
        with self._get_connection() as conn, conn.cursor() as cur:
            # Deactivate previous active state for this project+environment
            cur.execute(
                """
                    UPDATE desired_states SET active = FALSE
                    WHERE project_id = %s AND environment = %s AND active = TRUE
                    """,
                (project_id, environment),
            )
            # Insert new active state
            cur.execute(
                """
                    INSERT INTO desired_states
                    (id, project_id, snapshot_id, environment, source, snapshot_data,
                     created_at, active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                    """,
                (
                    row_id,
                    project_id,
                    snapshot.snapshot_id,
                    environment,
                    snapshot.source,
                    snapshot.model_dump_json(),
                    snapshot.captured_at,
                ),
            )
        return row_id

    def get_desired_state(
        self, project_id: str, environment: str = "default"
    ) -> SchemaSnapshot | None:
        """Get the active desired state for a project and environment."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(
                """
                SELECT snapshot_data FROM desired_states
                WHERE project_id = %s AND environment = %s AND active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id, environment),
            )
            row = cur.fetchone()

        if not row:
            return None

        data = row["snapshot_data"]
        if isinstance(data, str):
            data = json.loads(data)
        return SchemaSnapshot(**data)

    # =========================================================================
    # Migration record management
    # =========================================================================

    def save_migration_record(self, record: MigrationRecord) -> str:
        """Save a migration record. Returns the row UUID."""
        row_id = str(uuid4())
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO migration_records
                    (id, migration_id, project_id, environment, migration_type,
                     migration_sql, checksum, applied_at, applied_by,
                     execution_time_ms, success, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    row_id,
                    record.migration_id,
                    record.project_id,
                    record.environment,
                    record.migration_type,
                    record.migration_sql,
                    record.checksum,
                    record.applied_at,
                    record.applied_by,
                    record.execution_time_ms,
                    record.success,
                    record.error_message,
                ),
            )
        return row_id

    def get_migration_history(
        self, project_id: str, environment: str, limit: int = 100
    ) -> list[MigrationRecord]:
        """Get migration history for a project and environment, newest first."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(
                """
                SELECT migration_id, project_id, environment, migration_type,
                       migration_sql, checksum, applied_at, applied_by,
                       execution_time_ms, success, error_message
                FROM migration_records
                WHERE project_id = %s AND environment = %s
                ORDER BY applied_at DESC
                LIMIT %s
                """,
                (project_id, environment, limit),
            )
            rows = cur.fetchall()

        return [MigrationRecord(**row) for row in rows]

    def has_migration_been_applied(self, project_id: str, environment: str, checksum: str) -> bool:
        """Check if a migration with this checksum has already been applied."""
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT 1 FROM migration_records
                    WHERE project_id = %s AND environment = %s AND checksum = %s
                      AND success = TRUE
                    LIMIT 1
                    """,
                (project_id, environment, checksum),
            )
            return cur.fetchone() is not None

    # =========================================================================
    # Drift run persistence
    # =========================================================================

    def save_drift_run(self, result: DriftRunResult) -> str:
        """Save or update a drift run result (UPSERT by run_id).

        Returns the row UUID.
        """
        row_id = str(uuid4())
        with self._get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO drift_runs
                    (id, run_id, project_id, status, decision, plan,
                     execution_report, verification_report, memory_context,
                     retry_count, state_transitions, error, started_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        decision = EXCLUDED.decision,
                        plan = EXCLUDED.plan,
                        execution_report = EXCLUDED.execution_report,
                        verification_report = EXCLUDED.verification_report,
                        memory_context = EXCLUDED.memory_context,
                        retry_count = EXCLUDED.retry_count,
                        state_transitions = EXCLUDED.state_transitions,
                        error = EXCLUDED.error,
                        completed_at = EXCLUDED.completed_at
                    """,
                (
                    row_id,
                    result.run_id,
                    result.project_id,
                    result.status,
                    result.decision.model_dump_json() if result.decision else None,
                    result.plan.model_dump_json() if result.plan else None,
                    result.execution_report.model_dump_json() if result.execution_report else None,
                    result.verification_report.model_dump_json()
                    if result.verification_report
                    else None,
                    result.memory_context.model_dump_json() if result.memory_context else None,
                    result.retry_count,
                    json.dumps([t.model_dump() for t in result.state_transitions], default=str),
                    result.error,
                    result.started_at,
                    result.completed_at,
                ),
            )
        return row_id

    def get_drift_run(self, run_id: str) -> DriftRunResult | None:
        """Get a drift run by its run_id."""
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            cur.execute(
                "SELECT * FROM drift_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()

        if not row:
            return None

        return self._row_to_drift_run(row)

    def get_drift_runs(self, project_id: str, limit: int = 20) -> list[DriftRunResult]:
        """Get recent drift runs for a project, newest first.

        If project_id is empty string, returns runs across all projects (for metrics).
        """
        with (
            self._get_connection() as conn,
            conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur,
        ):
            if project_id:
                cur.execute(
                    """
                    SELECT * FROM drift_runs
                    WHERE project_id = %s
                    ORDER BY started_at DESC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM drift_runs
                    ORDER BY started_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()

        return [self._row_to_drift_run(row) for row in rows]

    @staticmethod
    def _row_to_drift_run(row: dict[str, Any]) -> DriftRunResult:
        """Convert a database row to a DriftRunResult."""
        from schemint.drift.models import (
            AgentDecision,
            ExecutionPlan,
            ExecutionReport,
            MemoryContext,
            StateTransition,
            VerificationReport,
        )

        def _parse_json(val: str | dict[str, Any] | None) -> dict[str, Any] | None:
            if val is None:
                return None
            if isinstance(val, str):
                return json.loads(val)  # type: ignore[no-any-return]
            return val

        decision_data = _parse_json(row["decision"])
        plan_data = _parse_json(row["plan"])
        exec_data = _parse_json(row["execution_report"])
        verify_data = _parse_json(row["verification_report"])
        memory_data = _parse_json(row["memory_context"])
        _raw_transitions = row["state_transitions"]
        transitions_data: list[Any] = (
            json.loads(_raw_transitions) if isinstance(_raw_transitions, str) else _raw_transitions
        ) or []

        return DriftRunResult(
            run_id=row["run_id"],
            project_id=row["project_id"],
            status=row["status"],
            decision=AgentDecision(**decision_data) if decision_data else None,
            plan=ExecutionPlan(**plan_data) if plan_data else None,
            execution_report=ExecutionReport(**exec_data) if exec_data else None,
            verification_report=VerificationReport(**verify_data) if verify_data else None,
            memory_context=MemoryContext(**memory_data) if memory_data else None,
            retry_count=row["retry_count"],
            state_transitions=[StateTransition(**t) for t in transitions_data],
            error=row.get("error"),
            started_at=row["started_at"],
            completed_at=row.get("completed_at"),
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
