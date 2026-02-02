"""
Project Memory Store.

Provides durable storage for project memory using PostgreSQL.

Usage:
    from schemint.memory import get_memory_store

    store = get_memory_store()
    project = store.register_project("github:org/repo", "My Project")
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generator
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras

from schemint.memory.models import (
    AcceptedFinding,
    AnalysisHistory,
    BusinessRule,
    FeedbackScope,
    MemorySummary,
    Project,
    SchemaSemantics,
)
from schemint.memory.patterns import compute_finding_hash

if TYPE_CHECKING:
    from schemint.models.issue import Issue


class MemoryStore:
    """
    Project memory store with PostgreSQL backend.
    """

    def __init__(self, database_url: str):
        """
        Initialize the memory store.

        Args:
            database_url: PostgreSQL connection string
                         (e.g., postgresql://user:pass@localhost:5432/schemint)
        """
        if not database_url:
            raise ValueError(
                "DATABASE_URL is required. "
                "Set it in your .env file or environment variable.\n"
                "Example: postgresql://schemint:password@localhost:5432/schemint"
            )
        self.database_url = database_url
        self._init_database()

    @contextmanager
    def _get_connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Get a database connection."""
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _get_cursor(self, conn: psycopg2.extensions.connection):
        """Get a cursor that returns dictionaries."""
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        create_tables_sql = """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                external_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                settings JSONB NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS accepted_findings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                finding_type TEXT NOT NULL,
                pattern_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                reason TEXT NOT NULL,
                accepted_by TEXT NOT NULL,
                accepted_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ,
                context JSONB NOT NULL DEFAULT '{}',
                UNIQUE(project_id, pattern_hash, scope)
            );

            CREATE TABLE IF NOT EXISTS known_safe_patterns (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                pattern_type TEXT NOT NULL,
                pattern_hash TEXT NOT NULL,
                description TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                examples JSONB NOT NULL DEFAULT '[]',
                UNIQUE(project_id, pattern_hash)
            );

            CREATE TABLE IF NOT EXISTS business_rules (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                rule_type TEXT NOT NULL,
                rule_config JSONB NOT NULL DEFAULT '{}',
                severity TEXT NOT NULL,
                applies_to JSONB NOT NULL DEFAULT '{"tables": ["*"]}',
                rationale TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                active BOOLEAN NOT NULL DEFAULT true
            );

            CREATE TABLE IF NOT EXISTS schema_semantics (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                element_type TEXT NOT NULL,
                element_path TEXT NOT NULL,
                semantic_tags JSONB NOT NULL DEFAULT '[]',
                description TEXT NOT NULL,
                constraints JSONB NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(project_id, element_path)
            );

            CREATE TABLE IF NOT EXISTS historical_inflection_points (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                event_date DATE NOT NULL,
                description TEXT NOT NULL,
                impact JSONB NOT NULL DEFAULT '{}',
                affected_tables JSONB NOT NULL DEFAULT '[]',
                created_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_history (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                ref TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                finding_count INTEGER NOT NULL,
                findings_hash TEXT NOT NULL,
                memory_applied JSONB NOT NULL DEFAULT '[]',
                duration_ms INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );

            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_accepted_project ON accepted_findings(project_id);
            CREATE INDEX IF NOT EXISTS idx_accepted_hash ON accepted_findings(pattern_hash);
            CREATE INDEX IF NOT EXISTS idx_safe_patterns_project ON known_safe_patterns(project_id);
            CREATE INDEX IF NOT EXISTS idx_rules_project ON business_rules(project_id) WHERE active = true;
            CREATE INDEX IF NOT EXISTS idx_semantics_project ON schema_semantics(project_id);
            CREATE INDEX IF NOT EXISTS idx_history_project ON analysis_history(project_id, created_at DESC);
        """

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_tables_sql)

    # =========================================================================
    # Project Operations
    # =========================================================================

    def register_project(
        self,
        external_id: str,
        name: str,
        settings: dict[str, Any] | None = None,
    ) -> Project:
        """
        Register a new project or return existing.

        Args:
            external_id: External identifier (e.g., "github:org/repo")
            name: Human-readable name
            settings: Optional project settings

        Returns:
            Project object
        """
        # Check if exists
        existing = self.get_project_by_external_id(external_id)
        if existing:
            return existing

        project = Project(
            id=uuid4(),
            external_id=external_id,
            name=name,
            settings=settings or {},
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO projects (id, external_id, name, created_at, settings)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        str(project.id),
                        project.external_id,
                        project.name,
                        project.created_at,
                        json.dumps(project.settings),
                    ),
                )

        return project

    def get_project(self, project_id: UUID) -> Project | None:
        """Get project by ID."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    "SELECT * FROM projects WHERE id = %s",
                    (str(project_id),),
                )
                row = cur.fetchone()

        if not row:
            return None

        return self._row_to_project(row)

    def get_project_by_external_id(self, external_id: str) -> Project | None:
        """Get project by external ID."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    "SELECT * FROM projects WHERE external_id = %s",
                    (external_id,),
                )
                row = cur.fetchone()

        if not row:
            return None

        return self._row_to_project(row)

    def list_projects(self) -> list[Project]:
        """List all registered projects."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
                rows = cur.fetchall()

        return [self._row_to_project(row) for row in rows]

    def _row_to_project(self, row: dict) -> Project:
        """Convert database row to Project."""
        return Project(
            id=UUID(row["id"]),
            external_id=row["external_id"],
            name=row["name"],
            created_at=row["created_at"],
            settings=row["settings"] if isinstance(row["settings"], dict) else json.loads(row["settings"]),
        )

    # =========================================================================
    # Accepted Findings
    # =========================================================================

    def accept_finding(
        self,
        project_id: UUID,
        finding: "Issue",
        reason: str,
        accepted_by: str,
        scope: FeedbackScope = FeedbackScope.ONCE,
        expires_at: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> AcceptedFinding:
        """
        Accept a finding (mark as false positive or intentional).
        """
        pattern_hash = compute_finding_hash(finding)

        accepted = AcceptedFinding(
            id=uuid4(),
            project_id=project_id,
            finding_type=finding.category.value,
            pattern_hash=pattern_hash,
            scope=scope,
            reason=reason,
            accepted_by=accepted_by,
            expires_at=expires_at,
            context=context or {
                "table": finding.table_name,
                "column": finding.column_name,
            },
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO accepted_findings
                    (id, project_id, finding_type, pattern_hash, scope, reason,
                     accepted_by, accepted_at, expires_at, context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, pattern_hash, scope)
                    DO UPDATE SET
                        reason = EXCLUDED.reason,
                        accepted_by = EXCLUDED.accepted_by,
                        accepted_at = EXCLUDED.accepted_at,
                        expires_at = EXCLUDED.expires_at,
                        context = EXCLUDED.context
                    """,
                    (
                        str(accepted.id),
                        str(accepted.project_id),
                        accepted.finding_type,
                        accepted.pattern_hash,
                        accepted.scope.value,
                        accepted.reason,
                        accepted.accepted_by,
                        accepted.accepted_at,
                        accepted.expires_at,
                        json.dumps(accepted.context),
                    ),
                )

        return accepted

    def check_finding_accepted(
        self,
        project_id: UUID,
        finding: "Issue",
    ) -> AcceptedFinding | None:
        """Check if a finding is accepted in project memory."""
        pattern_hash = compute_finding_hash(finding)

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT * FROM accepted_findings
                    WHERE project_id = %s
                      AND (
                        (scope = 'once' AND pattern_hash = %s)
                        OR (scope = 'pattern' AND pattern_hash = %s)
                        OR (scope = 'rule' AND finding_type = %s)
                      )
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY accepted_at DESC
                    LIMIT 1
                    """,
                    (
                        str(project_id),
                        pattern_hash,
                        pattern_hash,
                        finding.category.value,
                    ),
                )
                row = cur.fetchone()

        if not row:
            return None

        return self._row_to_accepted_finding(row)

    def get_accepted_findings(self, project_id: UUID) -> list[AcceptedFinding]:
        """Get all accepted findings for a project."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT * FROM accepted_findings
                    WHERE project_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY accepted_at DESC
                    """,
                    (str(project_id),),
                )
                rows = cur.fetchall()

        return [self._row_to_accepted_finding(row) for row in rows]

    def delete_accepted_finding(self, project_id: UUID, finding_id: UUID) -> bool:
        """Delete an accepted finding."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    "DELETE FROM accepted_findings WHERE id = %s AND project_id = %s",
                    (str(finding_id), str(project_id)),
                )
        return True

    def _row_to_accepted_finding(self, row: dict) -> AcceptedFinding:
        """Convert database row to AcceptedFinding."""
        return AcceptedFinding(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            finding_type=row["finding_type"],
            pattern_hash=row["pattern_hash"],
            scope=FeedbackScope(row["scope"]),
            reason=row["reason"],
            accepted_by=row["accepted_by"],
            accepted_at=row["accepted_at"],
            expires_at=row["expires_at"],
            context=row["context"] if isinstance(row["context"], dict) else json.loads(row["context"]),
        )

    # =========================================================================
    # Business Rules
    # =========================================================================

    def add_business_rule(
        self,
        project_id: UUID,
        rule_type: str,
        severity: str,
        rationale: str,
        created_by: str,
        rule_config: dict[str, Any] | None = None,
        applies_to: dict[str, Any] | None = None,
    ) -> BusinessRule:
        """Add a business rule to the project."""
        rule = BusinessRule(
            id=uuid4(),
            project_id=project_id,
            rule_type=rule_type,
            rule_config=rule_config or {},
            severity=severity,  # type: ignore
            applies_to=applies_to or {"tables": ["*"]},
            rationale=rationale,
            created_by=created_by,
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO business_rules
                    (id, project_id, rule_type, rule_config, severity, applies_to,
                     rationale, created_by, created_at, active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(rule.id),
                        str(rule.project_id),
                        rule.rule_type,
                        json.dumps(rule.rule_config),
                        rule.severity.value,
                        json.dumps(rule.applies_to),
                        rule.rationale,
                        rule.created_by,
                        rule.created_at,
                        rule.active,
                    ),
                )

        return rule

    def get_business_rules(
        self,
        project_id: UUID,
        table_name: str | None = None,
    ) -> list[BusinessRule]:
        """Get active business rules for a project."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT * FROM business_rules
                    WHERE project_id = %s AND active = true
                    ORDER BY created_at DESC
                    """,
                    (str(project_id),),
                )
                rows = cur.fetchall()

        rules = []
        for row in rows:
            rule = self._row_to_business_rule(row)

            # Filter by table if specified
            if table_name:
                applies_to = rule.applies_to
                tables = applies_to.get("tables", ["*"])
                except_tables = applies_to.get("except", [])

                if table_name in except_tables:
                    continue
                if "*" not in tables and table_name not in tables:
                    continue

            rules.append(rule)

        return rules

    def _row_to_business_rule(self, row: dict) -> BusinessRule:
        """Convert database row to BusinessRule."""
        from schemint.memory.models import FindingSeverity

        applies_to = row["applies_to"]
        if isinstance(applies_to, str):
            applies_to = json.loads(applies_to)

        rule_config = row["rule_config"]
        if isinstance(rule_config, str):
            rule_config = json.loads(rule_config)

        return BusinessRule(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            rule_type=row["rule_type"],
            rule_config=rule_config,
            severity=FindingSeverity(row["severity"]),
            applies_to=applies_to,
            rationale=row["rationale"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            active=row["active"],
        )

    # =========================================================================
    # Schema Semantics
    # =========================================================================

    def set_schema_semantics(
        self,
        project_id: UUID,
        element_path: str,
        element_type: str,
        description: str,
        semantic_tags: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> SchemaSemantics:
        """Set semantic information for a schema element."""
        semantics = SchemaSemantics(
            id=uuid4(),
            project_id=project_id,
            element_type=element_type,  # type: ignore
            element_path=element_path,
            semantic_tags=semantic_tags or [],
            description=description,
            constraints=constraints or {},
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO schema_semantics
                    (id, project_id, element_type, element_path, semantic_tags,
                     description, constraints, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, element_path)
                    DO UPDATE SET
                        element_type = EXCLUDED.element_type,
                        semantic_tags = EXCLUDED.semantic_tags,
                        description = EXCLUDED.description,
                        constraints = EXCLUDED.constraints,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(semantics.id),
                        str(semantics.project_id),
                        semantics.element_type.value,
                        semantics.element_path,
                        json.dumps(semantics.semantic_tags),
                        semantics.description,
                        json.dumps(semantics.constraints),
                        semantics.updated_at,
                    ),
                )

        return semantics

    def get_schema_semantics(
        self,
        project_id: UUID,
        element_path: str | None = None,
    ) -> list[SchemaSemantics]:
        """Get schema semantics, optionally filtered by element path."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                if element_path:
                    cur.execute(
                        """
                        SELECT * FROM schema_semantics
                        WHERE project_id = %s AND element_path = %s
                        """,
                        (str(project_id), element_path),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM schema_semantics WHERE project_id = %s",
                        (str(project_id),),
                    )
                rows = cur.fetchall()

        return [self._row_to_schema_semantics(row) for row in rows]

    def _row_to_schema_semantics(self, row: dict) -> SchemaSemantics:
        """Convert database row to SchemaSemantics."""
        from schemint.memory.models import ElementType

        semantic_tags = row["semantic_tags"]
        if isinstance(semantic_tags, str):
            semantic_tags = json.loads(semantic_tags)

        constraints = row["constraints"]
        if isinstance(constraints, str):
            constraints = json.loads(constraints)

        return SchemaSemantics(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            element_type=ElementType(row["element_type"]),
            element_path=row["element_path"],
            semantic_tags=semantic_tags,
            description=row["description"],
            constraints=constraints,
            updated_at=row["updated_at"],
        )

    # =========================================================================
    # Memory Summary
    # =========================================================================

    def get_memory_summary(self, project_id: UUID) -> MemorySummary | None:
        """Get a summary of project memory state."""
        project = self.get_project(project_id)
        if not project:
            return None

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM accepted_findings WHERE project_id = %s",
                    (str(project_id),),
                )
                accepted_count = cur.fetchone()["cnt"]

                cur.execute(
                    "SELECT COUNT(*) as cnt FROM known_safe_patterns WHERE project_id = %s",
                    (str(project_id),),
                )
                patterns_count = cur.fetchone()["cnt"]

                cur.execute(
                    "SELECT COUNT(*) as cnt FROM business_rules WHERE project_id = %s AND active = true",
                    (str(project_id),),
                )
                rules_count = cur.fetchone()["cnt"]

                cur.execute(
                    "SELECT COUNT(*) as cnt FROM schema_semantics WHERE project_id = %s",
                    (str(project_id),),
                )
                semantics_count = cur.fetchone()["cnt"]

                cur.execute(
                    "SELECT COUNT(*) as cnt FROM historical_inflection_points WHERE project_id = %s",
                    (str(project_id),),
                )
                inflection_count = cur.fetchone()["cnt"]

                cur.execute(
                    """
                    SELECT MAX(created_at) as last_created, COUNT(*) as total
                    FROM analysis_history
                    WHERE project_id = %s
                    """,
                    (str(project_id),),
                )
                history_row = cur.fetchone()

        return MemorySummary(
            project_id=project_id,
            project_name=project.name,
            accepted_findings_count=accepted_count,
            safe_patterns_count=patterns_count,
            business_rules_count=rules_count,
            semantic_entries_count=semantics_count,
            inflection_points_count=inflection_count,
            last_analysis=history_row["last_created"] if history_row and history_row["last_created"] else None,
            total_analyses=history_row["total"] if history_row else 0,
        )

    # =========================================================================
    # Analysis History
    # =========================================================================

    def record_analysis(
        self,
        project_id: UUID,
        ref: str,
        event_type: str,
        status: str,
        finding_count: int,
        findings_hash: str,
        duration_ms: int,
        memory_applied: list[dict[str, Any]] | None = None,
    ) -> AnalysisHistory:
        """Record an analysis run."""
        history = AnalysisHistory(
            id=uuid4(),
            project_id=project_id,
            ref=ref,
            event_type=event_type,
            status=status,
            finding_count=finding_count,
            findings_hash=findings_hash,
            memory_applied=memory_applied or [],
            duration_ms=duration_ms,
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO analysis_history
                    (id, project_id, ref, event_type, status, finding_count,
                     findings_hash, memory_applied, duration_ms, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(history.id),
                        str(history.project_id),
                        history.ref,
                        history.event_type,
                        history.status,
                        history.finding_count,
                        history.findings_hash,
                        json.dumps(history.memory_applied),
                        history.duration_ms,
                        history.created_at,
                    ),
                )

        return history

    def get_analysis_history(
        self,
        project_id: UUID,
        limit: int = 50,
    ) -> list[AnalysisHistory]:
        """Get recent analysis history for a project."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT * FROM analysis_history
                    WHERE project_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(project_id), limit),
                )
                rows = cur.fetchall()

        results = []
        for row in rows:
            memory_applied = row["memory_applied"]
            if isinstance(memory_applied, str):
                memory_applied = json.loads(memory_applied)

            results.append(AnalysisHistory(
                id=UUID(row["id"]),
                project_id=UUID(row["project_id"]),
                ref=row["ref"],
                event_type=row["event_type"],
                status=row["status"],
                finding_count=row["finding_count"],
                findings_hash=row["findings_hash"],
                memory_applied=memory_applied,
                duration_ms=row["duration_ms"],
                created_at=row["created_at"],
            ))

        return results


# =============================================================================
# Global Store Instance
# =============================================================================

_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """
    Get the global memory store instance.

    Requires DATABASE_URL to be set in environment or .env file.
    """
    global _store
    if _store is None:
        from schemint.config import get_settings

        settings = get_settings()
        _store = MemoryStore(database_url=settings.database_url)
    return _store


def set_memory_store(store: MemoryStore) -> None:
    """Set the global memory store instance (for testing)."""
    global _store
    _store = store
