"""
Project Memory Store.

Provides durable storage for project memory using SQLite (development)
or PostgreSQL (production). Abstracts database operations behind a
clean interface.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator
from uuid import UUID, uuid4

from schemint.memory.models import (
    AcceptedFinding,
    AnalysisHistory,
    BusinessRule,
    FeedbackScope,
    HistoricalInflectionPoint,
    KnownSafePattern,
    MemorySummary,
    Project,
    SchemaSemantics,
)
from schemint.memory.patterns import compute_finding_hash

if TYPE_CHECKING:
    from schemint.models.issue import Issue


class MemoryStore:
    """
    Project memory store with SQLite backend.

    For production, this can be extended to support PostgreSQL
    by implementing a DatabaseBackend interface.
    """

    def __init__(self, db_path: str | Path = "schemint_memory.db"):
        """
        Initialize the memory store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._init_database()

    def _init_database(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    external_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    settings TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS accepted_findings (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    finding_type TEXT NOT NULL,
                    pattern_hash TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    accepted_by TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    expires_at TEXT,
                    context TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(project_id, pattern_hash, scope)
                );

                CREATE TABLE IF NOT EXISTS known_safe_patterns (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    pattern_type TEXT NOT NULL,
                    pattern_hash TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    examples TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(project_id, pattern_hash)
                );

                CREATE TABLE IF NOT EXISTS business_rules (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    rule_type TEXT NOT NULL,
                    rule_config TEXT NOT NULL DEFAULT '{}',
                    severity TEXT NOT NULL,
                    applies_to TEXT NOT NULL DEFAULT '{"tables": ["*"]}',
                    rationale TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS schema_semantics (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    element_type TEXT NOT NULL,
                    element_path TEXT NOT NULL,
                    semantic_tags TEXT NOT NULL DEFAULT '[]',
                    description TEXT NOT NULL,
                    constraints TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, element_path)
                );

                CREATE TABLE IF NOT EXISTS historical_inflection_points (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    event_type TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    impact TEXT NOT NULL DEFAULT '{}',
                    affected_tables TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analysis_history (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    ref TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    finding_count INTEGER NOT NULL,
                    findings_hash TEXT NOT NULL,
                    memory_applied TEXT NOT NULL DEFAULT '[]',
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_accepted_project
                    ON accepted_findings(project_id);
                CREATE INDEX IF NOT EXISTS idx_accepted_hash
                    ON accepted_findings(pattern_hash);
                CREATE INDEX IF NOT EXISTS idx_safe_patterns_project
                    ON known_safe_patterns(project_id);
                CREATE INDEX IF NOT EXISTS idx_rules_project
                    ON business_rules(project_id, active);
                CREATE INDEX IF NOT EXISTS idx_semantics_project
                    ON schema_semantics(project_id);
                CREATE INDEX IF NOT EXISTS idx_history_project
                    ON analysis_history(project_id, created_at);
            """)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

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
            conn.execute(
                """
                INSERT INTO projects (id, external_id, name, created_at, settings)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(project.id),
                    project.external_id,
                    project.name,
                    project.created_at.isoformat(),
                    json.dumps(project.settings),
                ),
            )

        return project

    def get_project(self, project_id: UUID) -> Project | None:
        """Get project by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?",
                (str(project_id),),
            ).fetchone()

        if not row:
            return None

        return self._row_to_project(row)

    def get_project_by_external_id(self, external_id: str) -> Project | None:
        """Get project by external ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE external_id = ?",
                (external_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_project(row)

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        """Convert database row to Project."""
        return Project(
            id=UUID(row["id"]),
            external_id=row["external_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            settings=json.loads(row["settings"]),
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

        Args:
            project_id: Project ID
            finding: The finding to accept
            reason: Why it's being accepted
            accepted_by: User who accepted
            scope: How broadly to apply
            expires_at: Optional expiration
            context: Additional context

        Returns:
            AcceptedFinding record
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
            conn.execute(
                """
                INSERT OR REPLACE INTO accepted_findings
                (id, project_id, finding_type, pattern_hash, scope, reason,
                 accepted_by, accepted_at, expires_at, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(accepted.id),
                    str(accepted.project_id),
                    accepted.finding_type,
                    accepted.pattern_hash,
                    accepted.scope.value,
                    accepted.reason,
                    accepted.accepted_by,
                    accepted.accepted_at.isoformat(),
                    accepted.expires_at.isoformat() if accepted.expires_at else None,
                    json.dumps(accepted.context),
                ),
            )

        return accepted

    def check_finding_accepted(
        self,
        project_id: UUID,
        finding: "Issue",
    ) -> AcceptedFinding | None:
        """
        Check if a finding is accepted in project memory.

        Args:
            project_id: Project ID
            finding: The finding to check

        Returns:
            AcceptedFinding if accepted, None otherwise
        """
        pattern_hash = compute_finding_hash(finding)

        with self._get_connection() as conn:
            # Check for exact match or pattern/rule match
            row = conn.execute(
                """
                SELECT * FROM accepted_findings
                WHERE project_id = ?
                  AND (
                    (scope = 'once' AND pattern_hash = ?)
                    OR (scope = 'pattern' AND pattern_hash = ?)
                    OR (scope = 'rule' AND finding_type = ?)
                  )
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY accepted_at DESC
                LIMIT 1
                """,
                (
                    str(project_id),
                    pattern_hash,
                    pattern_hash,
                    finding.category.value,
                    datetime.utcnow().isoformat(),
                ),
            ).fetchone()

        if not row:
            return None

        return self._row_to_accepted_finding(row)

    def get_accepted_findings(self, project_id: UUID) -> list[AcceptedFinding]:
        """Get all accepted findings for a project."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM accepted_findings
                WHERE project_id = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY accepted_at DESC
                """,
                (str(project_id), datetime.utcnow().isoformat()),
            ).fetchall()

        return [self._row_to_accepted_finding(row) for row in rows]

    def _row_to_accepted_finding(self, row: sqlite3.Row) -> AcceptedFinding:
        """Convert database row to AcceptedFinding."""
        return AcceptedFinding(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            finding_type=row["finding_type"],
            pattern_hash=row["pattern_hash"],
            scope=FeedbackScope(row["scope"]),
            reason=row["reason"],
            accepted_by=row["accepted_by"],
            accepted_at=datetime.fromisoformat(row["accepted_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            context=json.loads(row["context"]),
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
            conn.execute(
                """
                INSERT INTO business_rules
                (id, project_id, rule_type, rule_config, severity, applies_to,
                 rationale, created_by, created_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    rule.created_at.isoformat(),
                    1 if rule.active else 0,
                ),
            )

        return rule

    def get_business_rules(
        self,
        project_id: UUID,
        table_name: str | None = None,
    ) -> list[BusinessRule]:
        """Get active business rules for a project, optionally filtered by table."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM business_rules
                WHERE project_id = ? AND active = 1
                ORDER BY created_at DESC
                """,
                (str(project_id),),
            ).fetchall()

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

    def _row_to_business_rule(self, row: sqlite3.Row) -> BusinessRule:
        """Convert database row to BusinessRule."""
        from schemint.memory.models import FindingSeverity

        return BusinessRule(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            rule_type=row["rule_type"],
            rule_config=json.loads(row["rule_config"]),
            severity=FindingSeverity(row["severity"]),
            applies_to=json.loads(row["applies_to"]),
            rationale=row["rationale"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            active=bool(row["active"]),
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
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_semantics
                (id, project_id, element_type, element_path, semantic_tags,
                 description, constraints, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(semantics.id),
                    str(semantics.project_id),
                    semantics.element_type.value,
                    semantics.element_path,
                    json.dumps(semantics.semantic_tags),
                    semantics.description,
                    json.dumps(semantics.constraints),
                    semantics.updated_at.isoformat(),
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
            if element_path:
                rows = conn.execute(
                    """
                    SELECT * FROM schema_semantics
                    WHERE project_id = ? AND element_path = ?
                    """,
                    (str(project_id), element_path),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schema_semantics WHERE project_id = ?",
                    (str(project_id),),
                ).fetchall()

        return [self._row_to_schema_semantics(row) for row in rows]

    def _row_to_schema_semantics(self, row: sqlite3.Row) -> SchemaSemantics:
        """Convert database row to SchemaSemantics."""
        from schemint.memory.models import ElementType

        return SchemaSemantics(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            element_type=ElementType(row["element_type"]),
            element_path=row["element_path"],
            semantic_tags=json.loads(row["semantic_tags"]),
            description=row["description"],
            constraints=json.loads(row["constraints"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
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
            accepted_count = conn.execute(
                "SELECT COUNT(*) FROM accepted_findings WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()[0]

            patterns_count = conn.execute(
                "SELECT COUNT(*) FROM known_safe_patterns WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()[0]

            rules_count = conn.execute(
                "SELECT COUNT(*) FROM business_rules WHERE project_id = ? AND active = 1",
                (str(project_id),),
            ).fetchone()[0]

            semantics_count = conn.execute(
                "SELECT COUNT(*) FROM schema_semantics WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()[0]

            inflection_count = conn.execute(
                "SELECT COUNT(*) FROM historical_inflection_points WHERE project_id = ?",
                (str(project_id),),
            ).fetchone()[0]

            history_row = conn.execute(
                """
                SELECT created_at, COUNT(*) as total
                FROM analysis_history
                WHERE project_id = ?
                GROUP BY project_id
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(project_id),),
            ).fetchone()

        return MemorySummary(
            project_id=project_id,
            project_name=project.name,
            accepted_findings_count=accepted_count,
            safe_patterns_count=patterns_count,
            business_rules_count=rules_count,
            semantic_entries_count=semantics_count,
            inflection_points_count=inflection_count,
            last_analysis=datetime.fromisoformat(history_row["created_at"]) if history_row else None,
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
            conn.execute(
                """
                INSERT INTO analysis_history
                (id, project_id, ref, event_type, status, finding_count,
                 findings_hash, memory_applied, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    history.created_at.isoformat(),
                ),
            )

        return history


# Global store instance (can be overridden for testing)
_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """Get the global memory store instance."""
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def set_memory_store(store: MemoryStore) -> None:
    """Set the global memory store instance (for testing)."""
    global _store
    _store = store
