"""Initial schema — all 12 tables from DriftStore and MemoryStore.

Revision ID: 001
Revises:
Create Date: 2026-02-17
"""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =====================================================================
    # DriftStore tables (5)
    # =====================================================================
    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS schema_change_history (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            diff_data JSONB NOT NULL,
            change_count INTEGER NOT NULL DEFAULT 0,
            diffed_at TIMESTAMPTZ NOT NULL
        );
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    # =====================================================================
    # DriftStore indexes (7)
    # =====================================================================
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_project
            ON schema_snapshots(project_id, captured_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_project_env
            ON schema_snapshots(project_id, environment, captured_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_edges_project
            ON dependency_edges(project_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_change_history_project
            ON schema_change_history(project_id, diffed_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_desired_states_project_env
            ON desired_states(project_id, environment, active);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_migration_records_project_env
            ON migration_records(project_id, environment, applied_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_migration_records_checksum
            ON migration_records(project_id, environment, checksum);
    """)

    # =====================================================================
    # MemoryStore tables (7)
    # =====================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            external_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            settings JSONB NOT NULL DEFAULT '{}'
        );
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)

    # =====================================================================
    # MemoryStore indexes (6)
    # =====================================================================
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accepted_project
            ON accepted_findings(project_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accepted_hash
            ON accepted_findings(pattern_hash);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_safe_patterns_project
            ON known_safe_patterns(project_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_project
            ON business_rules(project_id) WHERE active = true;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_semantics_project
            ON schema_semantics(project_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_project
            ON analysis_history(project_id, created_at DESC);
    """)


def downgrade() -> None:
    # MemoryStore tables (reverse dependency order — children first)
    op.execute("DROP TABLE IF EXISTS analysis_history;")
    op.execute("DROP TABLE IF EXISTS historical_inflection_points;")
    op.execute("DROP TABLE IF EXISTS schema_semantics;")
    op.execute("DROP TABLE IF EXISTS business_rules;")
    op.execute("DROP TABLE IF EXISTS known_safe_patterns;")
    op.execute("DROP TABLE IF EXISTS accepted_findings;")
    op.execute("DROP TABLE IF EXISTS projects;")

    # DriftStore tables (no FK dependencies — any order is fine)
    op.execute("DROP TABLE IF EXISTS migration_records;")
    op.execute("DROP TABLE IF EXISTS desired_states;")
    op.execute("DROP TABLE IF EXISTS schema_change_history;")
    op.execute("DROP TABLE IF EXISTS dependency_edges;")
    op.execute("DROP TABLE IF EXISTS schema_snapshots;")
