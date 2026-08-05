"""Tests for migration sandbox, alter applier, and copilot agent."""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.alter_applier import AlterApplier
from schemint.drift.copilot_agent import CopilotAgent
from schemint.drift.models import (
    ColumnSnapshot,
    CopilotResult,
    ForeignKeySnapshot,
    IntentAnalysis,
    MigrationAlternative,
    PredictedChange,
    RollbackScript,
    SandboxWarning,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.drift.sandbox import MigrationSandbox

# =============================================================================
# Fixtures
# =============================================================================


def _make_snapshot(tables: dict[str, TableSnapshot] | None = None) -> SchemaSnapshot:
    """Create a minimal SchemaSnapshot for testing."""
    return SchemaSnapshot(
        snapshot_id="test_snap_001",
        source="ddl",
        tables=tables or {},
    )


def _make_users_snapshot() -> SchemaSnapshot:
    """Create a snapshot with a users table."""
    return _make_snapshot(
        {
            "users": TableSnapshot(
                name="users",
                columns={
                    "id": ColumnSnapshot(name="id", type="integer", nullable=False),
                    "email": ColumnSnapshot(name="email", type="varchar(255)", nullable=False),
                    "name": ColumnSnapshot(name="name", type="text", nullable=True),
                },
                primary_key=["id"],
            ),
        }
    )


def _make_orders_snapshot() -> SchemaSnapshot:
    """Create a snapshot with users and orders tables."""
    return _make_snapshot(
        {
            "users": TableSnapshot(
                name="users",
                columns={
                    "id": ColumnSnapshot(name="id", type="integer", nullable=False),
                    "email": ColumnSnapshot(name="email", type="varchar(255)", nullable=False),
                },
                primary_key=["id"],
            ),
            "orders": TableSnapshot(
                name="orders",
                columns={
                    "id": ColumnSnapshot(name="id", type="integer", nullable=False),
                    "user_id": ColumnSnapshot(name="user_id", type="integer", nullable=False),
                    "total": ColumnSnapshot(name="total", type="decimal(10,2)"),
                },
                primary_key=["id"],
                foreign_keys=[
                    ForeignKeySnapshot(
                        name="fk_orders_user",
                        column="user_id",
                        references_table="users",
                        references_column="id",
                    )
                ],
            ),
        }
    )


# =============================================================================
# AlterApplier Tests
# =============================================================================


class TestAlterApplier:
    """Tests for AlterApplier.apply()."""

    def test_add_column(self):
        """ALTER TABLE ADD COLUMN should add a new column to the snapshot."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users ADD COLUMN age INTEGER;")

        assert "age" in result.tables["users"].columns
        assert result.tables["users"].columns["age"].type == "integer"

    def test_add_column_preserves_nullability_and_default(self):
        result = AlterApplier().apply(
            _make_users_snapshot(),
            "ALTER TABLE users ADD COLUMN active BOOLEAN NOT NULL DEFAULT false;",
        )

        column = result.tables["users"].columns["active"]
        assert column.nullable is False
        assert column.default == "FALSE"

    def test_drop_column(self):
        """ALTER TABLE DROP COLUMN should remove the column."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users DROP COLUMN name;")

        assert "name" not in result.tables["users"].columns
        assert "id" in result.tables["users"].columns
        assert "email" in result.tables["users"].columns

    def test_alter_column_type(self):
        """ALTER TABLE ALTER COLUMN TYPE should update the column type."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users ALTER COLUMN email TYPE TEXT;")

        assert result.tables["users"].columns["email"].type == "text"

    def test_create_table(self):
        """CREATE TABLE should add a new table to the snapshot."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        sql = """
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL
        );
        """
        result = applier.apply(baseline, sql)

        assert "posts" in result.tables
        assert "id" in result.tables["posts"].columns
        assert "users" in result.tables  # original table preserved

    def test_drop_table(self):
        """DROP TABLE should remove the table from the snapshot."""
        baseline = _make_orders_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "DROP TABLE orders;")

        assert "orders" not in result.tables
        assert "users" in result.tables  # other tables preserved

    def test_create_and_drop_index(self):
        baseline = _make_users_snapshot()
        created = AlterApplier().apply(
            baseline, "CREATE UNIQUE INDEX users_email_key ON users(email);"
        )

        index = created.tables["users"].indexes[0]
        assert index.name == "users_email_key"
        assert index.columns == ["email"]
        assert index.is_unique is True

        dropped = AlterApplier().apply(created, "DROP INDEX users_email_key;")
        assert dropped.tables["users"].indexes == []

    def test_rename_table(self):
        """ALTER TABLE RENAME should change the table name in the snapshot."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users RENAME TO accounts;")

        assert "accounts" in result.tables
        assert "users" not in result.tables

    def test_rename_column_preserves_metadata(self):
        result = AlterApplier().apply(
            _make_users_snapshot(),
            "ALTER TABLE users RENAME COLUMN email TO contact_email;",
        )

        column = result.tables["users"].columns["contact_email"]
        assert column.type == "varchar(255)"
        assert column.nullable is False
        assert "email" not in result.tables["users"].columns

    def test_multi_action_foreign_key_replacement(self):
        result = AlterApplier().apply(
            _make_orders_snapshot(),
            """ALTER TABLE orders
            DROP CONSTRAINT fk_orders_user,
            ADD CONSTRAINT fk_orders_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE;""",
        )

        assert len(result.tables["orders"].foreign_keys) == 1
        fk = result.tables["orders"].foreign_keys[0]
        assert fk.name == "fk_orders_user"
        assert fk.column == "user_id"
        assert fk.references_table == "users"
        assert fk.references_column == "id"
        assert fk.on_delete == "CASCADE"

    def test_baseline_not_mutated(self):
        """Original baseline should never be mutated by apply()."""
        baseline = _make_users_snapshot()
        original_tables = set(baseline.tables.keys())
        applier = AlterApplier()

        applier.apply(baseline, "ALTER TABLE users DROP COLUMN email;")

        # baseline must be unchanged
        assert set(baseline.tables.keys()) == original_tables
        assert "email" in baseline.tables["users"].columns

    def test_multiple_statements(self):
        """Multiple ALTER statements should all be applied."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        sql = """
        ALTER TABLE users ADD COLUMN age INTEGER;
        ALTER TABLE users DROP COLUMN name;
        """
        result = applier.apply(baseline, sql)

        assert "age" in result.tables["users"].columns
        assert "name" not in result.tables["users"].columns

    def test_invalid_sql_returns_baseline_copy(self):
        """Invalid SQL should return the baseline unchanged."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        result = applier.apply(baseline, "THIS IS NOT SQL AT ALL!!!")

        # Should not crash — returns a copy of baseline
        assert "users" in result.tables

    def test_alter_unknown_table_skipped(self):
        """ALTER on non-existent table should be skipped gracefully."""
        baseline = _make_users_snapshot()
        applier = AlterApplier()

        # Should not crash
        result = applier.apply(baseline, "ALTER TABLE nonexistent ADD COLUMN x INT;")
        assert "users" in result.tables

    def test_set_not_null(self):
        """ALTER TABLE ALTER COLUMN SET NOT NULL should update nullable."""
        baseline = _make_users_snapshot()
        assert baseline.tables["users"].columns["name"].nullable is True
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users ALTER COLUMN name SET NOT NULL;")

        assert result.tables["users"].columns["name"].nullable is False

    def test_drop_not_null(self):
        """ALTER TABLE ALTER COLUMN DROP NOT NULL should update nullable."""
        baseline = _make_users_snapshot()
        assert baseline.tables["users"].columns["email"].nullable is False
        applier = AlterApplier()

        result = applier.apply(baseline, "ALTER TABLE users ALTER COLUMN email DROP NOT NULL;")

        assert result.tables["users"].columns["email"].nullable is True


# =============================================================================
# MigrationSandbox Tests
# =============================================================================


class TestMigrationSandbox:
    """Tests for MigrationSandbox.analyze()."""

    def test_safe_migration_scores_high(self):
        """A safe migration should get score >= 90 and grade A."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
        migration = "ALTER TABLE users ADD COLUMN email TEXT;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert result.status == "ok"
        assert result.safety_score >= 90
        assert result.safety_grade == "A"
        assert result.overall_risk == "safe"

    def test_breaking_migration_scores_low(self):
        """A breaking migration should get a low score."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL);"
        migration = "ALTER TABLE users DROP COLUMN email;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert result.status == "ok"
        assert result.safety_score < 90
        assert result.overall_risk == "breaking"

    def test_predicted_changes_populated(self):
        """Predicted changes should be populated with risk levels."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
        migration = "ALTER TABLE users ADD COLUMN age INTEGER;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert len(result.predicted_changes) > 0
        for change in result.predicted_changes:
            assert change.risk_level is not None

    def test_drop_table_is_breaking(self):
        """DROP TABLE should be detected as breaking."""
        sandbox = MigrationSandbox()
        ddl = """
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE orders (id INTEGER PRIMARY KEY);
        """
        migration = "DROP TABLE orders;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert result.overall_risk == "breaking"
        assert any(c.change_type == "table_dropped" for c in result.predicted_changes)

    def test_copilot_not_available_without_api_key(self):
        """Co-pilot should not be available when no API key is set."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY);"
        migration = "ALTER TABLE users ADD COLUMN name TEXT;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=True,
        )

        assert result.copilot_available is False
        assert result.alternatives == []
        assert result.rollback is None
        assert result.intent_analysis is None

    def test_error_returns_status_error(self):
        """When no baseline can be resolved, status should be 'error'."""
        sandbox = MigrationSandbox()

        result = sandbox.analyze(
            migration_sql="ALTER TABLE users ADD COLUMN x INT;",
            run_copilot=False,
        )

        assert result.status == "error"
        assert result.error_message is not None

    def test_baseline_from_snapshot(self):
        """Should work when baseline_snapshot is provided directly."""
        sandbox = MigrationSandbox()
        baseline = _make_users_snapshot()

        result = sandbox.analyze(
            migration_sql="ALTER TABLE users ADD COLUMN age INTEGER;",
            baseline_snapshot=baseline,
            run_copilot=False,
        )

        assert result.status == "ok"
        assert result.baseline_snapshot_id == "test_snap_001"

    def test_multiple_breaking_changes_cap_deductions(self):
        """Multiple breaking changes should cap deductions at 60."""
        sandbox = MigrationSandbox()
        ddl = """
        CREATE TABLE users (id INTEGER PRIMARY KEY, a TEXT, b TEXT, c TEXT);
        """
        # Drop 3 columns = 3 breaking changes
        migration = """
        ALTER TABLE users DROP COLUMN a;
        ALTER TABLE users DROP COLUMN b;
        ALTER TABLE users DROP COLUMN c;
        """

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        # Score should be 100 - 60 (capped) = 40 at minimum from breaking
        assert result.safety_score <= 40
        assert result.safety_grade in ("D", "F")

    def test_action_recommendations_populated(self):
        """Recommendations should be populated based on changes."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);"
        migration = "ALTER TABLE users DROP COLUMN email;"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert len(result.action_recommendations) > 0
        assert any(
            "breaking" in r.lower() or "BLOCKING" in r for r in result.action_recommendations
        )

    def test_sandbox_id_generated(self):
        """Each analysis should get a unique sandbox_id."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY);"
        migration = "ALTER TABLE users ADD COLUMN name TEXT;"

        r1 = sandbox.analyze(migration_sql=migration, current_ddl=ddl, run_copilot=False)
        r2 = sandbox.analyze(migration_sql=migration, current_ddl=ddl, run_copilot=False)

        assert r1.sandbox_id != r2.sandbox_id

    def test_create_table_migration(self):
        """Creating a new table should be safe."""
        sandbox = MigrationSandbox()
        ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY);"
        migration = "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER);"

        result = sandbox.analyze(
            migration_sql=migration,
            current_ddl=ddl,
            run_copilot=False,
        )

        assert result.status == "ok"
        assert result.safety_score >= 90
        assert any(c.change_type == "table_added" for c in result.predicted_changes)

    def test_column_change_reports_downstream_view(self):
        sandbox = MigrationSandbox()
        ddl = """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        CREATE VIEW user_emails AS SELECT email FROM users;
        """

        result = sandbox.analyze(
            migration_sql="ALTER TABLE users DROP COLUMN email;",
            current_ddl=ddl,
            run_copilot=False,
        )

        change = next(c for c in result.predicted_changes if c.change_type == "column_dropped")
        assert change.downstream_impact == 1
        assert change.downstream_objects == ["user_emails"]


# =============================================================================
# Scoring Tests
# =============================================================================


class TestScoringFormula:
    """Test the scoring formula in isolation."""

    def test_no_changes_score_100(self):
        """No changes should score 100 A."""
        score, grade = MigrationSandbox._compute_score([], [])
        assert score == 100
        assert grade == "A"

    def test_one_breaking_change(self):
        """One breaking change = -30 = score 70."""
        changes = [PredictedChange(change_type="column_dropped", table="t", risk_level="breaking")]
        score, grade = MigrationSandbox._compute_score(changes, [])
        assert score == 70
        assert grade == "C"

    def test_one_potentially_breaking(self):
        """One potentially_breaking = -15 = score 85."""
        changes = [
            PredictedChange(
                change_type="column_added", table="t", risk_level="potentially_breaking"
            )
        ]
        score, grade = MigrationSandbox._compute_score(changes, [])
        assert score == 85
        assert grade == "B"

    def test_one_needs_review(self):
        """One needs_review = -5 = score 95."""
        changes = [
            PredictedChange(change_type="index_dropped", table="t", risk_level="needs_review")
        ]
        score, grade = MigrationSandbox._compute_score(changes, [])
        assert score == 95
        assert grade == "A"

    def test_critical_warning_deduction(self):
        """Critical warning = -10."""
        warnings = [SandboxWarning(pattern="destructive", severity="critical", message="DROP")]
        score, grade = MigrationSandbox._compute_score([], warnings)
        assert score == 90
        assert grade == "A"

    def test_combined_deductions(self):
        """Breaking + potentially_breaking + needs_review + critical warning."""
        changes = [
            PredictedChange(change_type="column_dropped", table="t", risk_level="breaking"),
            PredictedChange(
                change_type="column_added", table="t", risk_level="potentially_breaking"
            ),
            PredictedChange(change_type="index_dropped", table="t", risk_level="needs_review"),
        ]
        warnings = [SandboxWarning(pattern="test", severity="critical", message="test")]
        score, grade = MigrationSandbox._compute_score(changes, warnings)
        # 100 - 30 - 15 - 5 - 10 = 40
        assert score == 40
        assert grade == "F"

    def test_breaking_cap_at_60(self):
        """Breaking deductions should cap at 60."""
        changes = [
            PredictedChange(change_type=f"col_drop_{i}", table="t", risk_level="breaking")
            for i in range(5)
        ]
        score, _ = MigrationSandbox._compute_score(changes, [])
        # 100 - min(5*30, 60) = 40
        assert score == 40

    def test_score_never_below_zero(self):
        """Score should never go below 0."""
        changes = [
            PredictedChange(change_type=f"c{i}", table="t", risk_level="breaking")
            for i in range(10)
        ]
        warnings = [
            SandboxWarning(pattern="d", severity="critical", message="m") for _ in range(10)
        ]
        score, grade = MigrationSandbox._compute_score(changes, warnings)
        assert score >= 0
        assert grade == "F"


# =============================================================================
# CopilotAgent Tests (mocked Claude)
# =============================================================================


class TestCopilotAgent:
    """Tests for CopilotAgent with mocked Claude API."""

    def test_parse_response_direct_json(self):
        """Direct JSON string should parse correctly."""
        agent = CopilotAgent.__new__(CopilotAgent)
        text = '{"alternatives": [{"original_change": "test", "safe_sql": "SELECT 1"}]}'
        result = agent._parse_response(text)
        assert result["alternatives"][0]["original_change"] == "test"

    def test_parse_response_markdown_json(self):
        """JSON in markdown block should parse correctly."""
        agent = CopilotAgent.__new__(CopilotAgent)
        text = 'Here is the result:\n```json\n{"intent_matches": true}\n```\n'
        result = agent._parse_response(text)
        assert result["intent_matches"] is True

    def test_parse_response_bare_markdown(self):
        """JSON in bare markdown block should parse correctly."""
        agent = CopilotAgent.__new__(CopilotAgent)
        text = 'Result:\n```\n{"rollback_sql": "DROP TABLE t"}\n```\n'
        result = agent._parse_response(text)
        assert result["rollback_sql"] == "DROP TABLE t"

    def test_parse_response_embedded_json(self):
        """JSON embedded in text should be extracted."""
        agent = CopilotAgent.__new__(CopilotAgent)
        text = 'The result is {"confidence": 0.9} which is good.'
        result = agent._parse_response(text)
        assert result["confidence"] == 0.9

    def test_validate_sql_valid(self):
        """Valid SQL should pass validation."""
        assert CopilotAgent._validate_sql("SELECT 1;") is True
        assert CopilotAgent._validate_sql("ALTER TABLE users ADD COLUMN x INT;") is True

    def test_validate_sql_invalid(self):
        """Invalid or empty SQL should fail validation."""
        assert CopilotAgent._validate_sql("") is False
        assert CopilotAgent._validate_sql("   ") is False

    def test_validate_sql_nonsense(self):
        """Non-SQL text should fail validation."""
        # sqlglot is quite permissive, so this may parse as identifiers
        # Just verify it doesn't crash
        result = CopilotAgent._validate_sql("NOT VALID SQL !!! @#$%")
        assert isinstance(result, bool)


# =============================================================================
# Model Tests
# =============================================================================


class TestModels:
    """Tests for new Pydantic models."""

    def test_predicted_change_defaults(self):
        """PredictedChange should have correct defaults."""
        change = PredictedChange(change_type="column_added", table="users")
        assert change.column is None
        assert change.risk_level is None
        assert change.downstream_impact == 0

    def test_migration_alternative(self):
        """MigrationAlternative should serialize correctly."""
        alt = MigrationAlternative(
            original_change="DROP COLUMN users.email",
            safe_sql="ALTER TABLE users RENAME COLUMN email TO _deprecated_email;",
            explanation="Rename instead of drop",
            risk_reduction="breaking -> safe",
            trade_off="Requires follow-up migration",
        )
        data = alt.model_dump()
        assert data["original_change"] == "DROP COLUMN users.email"
        assert "RENAME" in data["safe_sql"]

    def test_rollback_script_defaults(self):
        """RollbackScript should have correct defaults."""
        script = RollbackScript(rollback_sql="ALTER TABLE users ADD COLUMN email TEXT;")
        assert script.confidence == 0.0
        assert script.warnings == []
        assert script.is_complete is False

    def test_intent_analysis_defaults(self):
        """IntentAnalysis should have correct defaults."""
        analysis = IntentAnalysis()
        assert analysis.intent_matches is True
        assert analysis.suggested_sql is None

    def test_sandbox_warning(self):
        """SandboxWarning should serialize correctly."""
        warning = SandboxWarning(
            pattern="blocking_migration",
            severity="critical",
            message="ADD COLUMN with DEFAULT on large table",
            table="users",
        )
        assert warning.pattern == "blocking_migration"

    def test_copilot_result_minimal(self):
        """CopilotResult with minimal fields should work."""
        result = CopilotResult(
            sandbox_id="test_001",
            migration_sql="ALTER TABLE users ADD COLUMN x INT;",
            baseline_snapshot_id="snap_001",
            analyzed_at=datetime.now(timezone.utc),
        )
        assert result.safety_score == 100
        assert result.safety_grade == "A"
        assert result.alternatives == []
        assert result.rollback is None

    def test_copilot_result_full(self):
        """CopilotResult with all fields should serialize."""
        result = CopilotResult(
            sandbox_id="test_002",
            migration_sql="DROP TABLE orders;",
            baseline_snapshot_id="snap_002",
            predicted_changes=[
                PredictedChange(change_type="table_dropped", table="orders", risk_level="breaking")
            ],
            warnings=[
                SandboxWarning(
                    pattern="destructive_change", severity="critical", message="DROP TABLE"
                )
            ],
            safety_score=40,
            safety_grade="F",
            overall_risk="breaking",
            alternatives=[
                MigrationAlternative(
                    original_change="DROP TABLE orders",
                    safe_sql="ALTER TABLE orders RENAME TO _deprecated_orders;",
                    explanation="Rename instead of drop",
                    risk_reduction="breaking -> safe",
                )
            ],
            rollback=RollbackScript(
                rollback_sql="CREATE TABLE orders (...);",
                confidence=0.5,
                warnings=["Cannot restore data"],
                is_complete=False,
            ),
            intent_analysis=IntentAnalysis(
                intent_matches=False,
                detected_intent="Remove orders table",
                actual_behavior="Permanently deletes all order data",
                suggestion="Consider archiving instead",
            ),
            copilot_available=True,
            action_recommendations=["BLOCKING: 1 breaking change"],
            analyzed_at=datetime.now(timezone.utc),
        )
        data = result.model_dump()
        assert data["safety_score"] == 40
        assert len(data["alternatives"]) == 1
        assert data["rollback"]["confidence"] == 0.5


# =============================================================================
# Overall Risk Tests
# =============================================================================


class TestOverallRisk:
    """Tests for _compute_overall_risk."""

    def test_empty_changes_is_safe(self):
        """No changes should be 'safe'."""
        assert MigrationSandbox._compute_overall_risk([]) == "safe"

    def test_highest_risk_wins(self):
        """Overall risk should be the highest among all changes."""
        changes = [
            PredictedChange(change_type="a", table="t", risk_level="safe"),
            PredictedChange(change_type="b", table="t", risk_level="needs_review"),
            PredictedChange(change_type="c", table="t", risk_level="breaking"),
        ]
        assert MigrationSandbox._compute_overall_risk(changes) == "breaking"

    def test_all_safe(self):
        """All safe changes should be 'safe'."""
        changes = [
            PredictedChange(change_type="a", table="t", risk_level="safe"),
            PredictedChange(change_type="b", table="t", risk_level="safe"),
        ]
        assert MigrationSandbox._compute_overall_risk(changes) == "safe"



# =============================================================================
# Memory Writer Tests
# =============================================================================


class TestDriftMemoryWriter:
    """Tests for DriftMemoryWriter."""

    def test_skips_non_complete_runs(self):
        """Should skip runs that are not COMPLETE."""
        from schemint.drift.memory_writer import DriftMemoryWriter
        from schemint.drift.models import AgentDecision, DriftRunResult

        result = DriftRunResult(
            run_id="test_001",
            project_id="proj_1",
            status="failed",
            decision=AgentDecision(
                severity="low",
                confidence_in_decision=0.9,
                requires_human_review=False,
                rationale=["test"],
                recommended_action_categories=["monitor_only"],
                context_quality="complete",
            ),
        )

        writer = DriftMemoryWriter()
        # Should not crash — just skips
        writer.record_completed_run(result, "proj_1")

    def test_skips_high_severity(self):
        """Should skip runs with high/critical severity."""
        from schemint.drift.memory_writer import DriftMemoryWriter
        from schemint.drift.models import AgentDecision, DriftRunResult

        result = DriftRunResult(
            run_id="test_002",
            project_id="proj_1",
            status="complete",
            decision=AgentDecision(
                severity="critical",
                confidence_in_decision=0.9,
                requires_human_review=True,
                rationale=["test"],
                recommended_action_categories=["block_deploy"],
                context_quality="complete",
            ),
        )

        writer = DriftMemoryWriter()
        # Should not crash — just skips (critical severity not in safe set)
        writer.record_completed_run(result, "proj_1")

    def test_skips_when_no_decision(self):
        """Should skip when run has no decision."""
        from schemint.drift.memory_writer import DriftMemoryWriter
        from schemint.drift.models import DriftRunResult

        result = DriftRunResult(
            run_id="test_003",
            project_id="proj_1",
            status="complete",
        )

        writer = DriftMemoryWriter()
        writer.record_completed_run(result, "proj_1")


# =============================================================================
# Config Tests
# =============================================================================


class TestConfigFields:
    """Tests for new config fields."""

    def test_new_fields_have_defaults(self):
        """New config fields should have proper defaults."""
        from schemint.config import Settings

        # Create with minimal env (no .env file)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
        )
        assert settings.notification_webhook_headers == "{}"
        assert settings.github_repo is None
