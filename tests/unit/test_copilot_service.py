"""Tests for CopilotService — plan enrichment with AI-generated SQL."""

from unittest.mock import MagicMock, patch

from schemint.drift.copilot_service import CopilotService, _describe_step, _step_to_change_event
from schemint.drift.models import (
    ContextPackage,
    DependencyCoverage,
    ImpactMetrics,
    MigrationAlternative,
    PlanStep,
    RollbackScript,
    SchemaChangeEvent,
)


def _make_context(table: str = "users") -> ContextPackage:
    """Build a minimal ContextPackage for testing."""
    change = SchemaChangeEvent(
        change_type="column_type_change",
        table=table,
        column="email",
        change_risk="potentially_breaking",
    )
    return ContextPackage(
        schema_change=change,
        impacted_dependencies=[],
        impact_metrics=ImpactMetrics(
            downstream_table_count=2,
            downstream_model_count=0,
            row_count_estimate=1000,
            has_fk_references=False,
            criticality="medium",
        ),
        dependency_coverage=DependencyCoverage(
            tables_total=5,
            tables_with_lineage=4,
            coverage_pct=80.0,
        ),
        context_quality="complete",
    )


def _make_plan_step(action: str = "add_column_alias", target: str = "users.email") -> PlanStep:
    return PlanStep(
        step=1,
        action=action,
        target=target,
        notes="new_name=user_email",
        reversible=True,
    )


def _make_execution_plan(steps=None):
    from schemint.drift.models import ExecutionPlan

    return ExecutionPlan(
        plan=steps or [_make_plan_step()],
        requires_execution_approval=False,
        source_severity="medium",
        source_requires_human_review=False,
    )


# =============================================================================
# _step_to_change_event
# =============================================================================


class TestStepToChangeEvent:
    def test_add_column_alias_maps_to_column_type_change(self):
        step = _make_plan_step("add_column_alias", "users.email")
        context = _make_context()
        event = _step_to_change_event(step, context)
        assert event is not None
        assert event.change_type == "column_type_change"
        assert event.table == "users"
        assert event.column == "email"

    def test_add_default_value_maps_to_column_default_change(self):
        step = _make_plan_step("add_default_value", "orders.status")
        event = _step_to_change_event(step, _make_context())
        assert event is not None
        assert event.change_type == "column_default_change"

    def test_create_migration_view_maps_to_table_renamed(self):
        step = _make_plan_step("create_migration_view", "legacy_users")
        event = _step_to_change_event(step, _make_context())
        assert event is not None
        assert event.change_type == "table_renamed"

    def test_unknown_action_returns_none(self):
        step = _make_plan_step("notify_table_owner", "users")
        event = _step_to_change_event(step, _make_context())
        assert event is None

    def test_target_without_dot_sets_no_column(self):
        step = _make_plan_step("create_migration_view", "legacy_users")
        event = _step_to_change_event(step, _make_context())
        assert event is not None
        assert event.table == "legacy_users"
        assert not event.column


# =============================================================================
# _describe_step
# =============================================================================


class TestDescribeStep:
    def test_includes_action_target_notes(self):
        step = PlanStep(step=1, action="add_column_alias", target="users.email", notes="new_name=x")
        desc = _describe_step(step)
        assert "add_column_alias" in desc
        assert "users.email" in desc
        assert "new_name=x" in desc


# =============================================================================
# CopilotService.enrich_plan
# =============================================================================


class TestCopilotServiceEnrichPlan:
    def test_returns_original_plan_when_no_agent(self):
        """If get_copilot_agent() returns None, plan is returned unchanged."""
        service = CopilotService()
        plan = _make_execution_plan()
        context = _make_context()

        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=None):
            result = service.enrich_plan(plan, context)

        assert result is plan

    def test_returns_original_plan_when_no_sql_steps(self):
        """Plans with only notification steps are not enriched."""
        service = CopilotService()
        plan = _make_execution_plan(
            steps=[PlanStep(step=1, action="notify_table_owner", target="users", notes="")]
        )
        context = _make_context()

        mock_agent = MagicMock()
        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=mock_agent):
            result = service.enrich_plan(plan, context)

        # No SQL steps → agent never called
        mock_agent.generate_alternatives.assert_not_called()
        assert result is plan

    def test_enriches_sql_steps_with_generated_sql(self):
        """SQL steps get generated_sql from CopilotAgent alternatives."""
        service = CopilotService()
        plan = _make_execution_plan()
        context = _make_context()

        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = [
            MigrationAlternative(
                original_change="column_renamed on users",
                safe_sql="CREATE OR REPLACE VIEW users_compat AS SELECT *, user_email AS email FROM users;",
                explanation="Adds backward-compat view",
                risk_reduction="potentially_breaking -> safe",
                trade_off="Extra view to maintain",
            )
        ]
        mock_agent.generate_rollback.return_value = RollbackScript(
            rollback_sql="DROP VIEW IF EXISTS users_compat;",
            confidence=0.9,
            warnings=[],
            is_complete=True,
        )

        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=mock_agent):
            enriched = service.enrich_plan(plan, context)

        assert enriched is not plan  # new plan returned
        step = enriched.plan[0]
        assert step.generated_sql is not None
        assert "users_compat" in step.generated_sql
        assert step.rollback_sql is not None
        assert "DROP VIEW" in step.rollback_sql

    def test_skips_enrichment_on_agent_exception(self):
        """If agent raises, step is left unenriched (not failed)."""
        service = CopilotService()
        plan = _make_execution_plan()
        context = _make_context()

        mock_agent = MagicMock()
        mock_agent.generate_alternatives.side_effect = RuntimeError("API error")
        mock_agent.generate_rollback.side_effect = RuntimeError("API error")

        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=mock_agent):
            result = service.enrich_plan(plan, context)

        # Exception swallowed — original plan returned
        assert result is plan

    def test_incomplete_rollback_not_attached(self):
        """Rollback SQL is NOT attached when is_complete=False."""
        service = CopilotService()
        plan = _make_execution_plan()
        context = _make_context()

        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = []
        mock_agent.generate_rollback.return_value = RollbackScript(
            rollback_sql="-- incomplete",
            confidence=0.2,
            warnings=["Could not determine rollback"],
            is_complete=False,
        )

        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=mock_agent):
            result = service.enrich_plan(plan, context)

        # No alternatives AND incomplete rollback → no enrichment → original plan
        assert result is plan

    def test_mixed_steps_only_sql_steps_enriched(self):
        """Notification steps are skipped; only SQL steps get enriched."""
        from schemint.drift.models import ExecutionPlan

        service = CopilotService()
        steps = [
            PlanStep(step=1, action="notify_table_owner", target="users", notes=""),
            PlanStep(step=2, action="add_column_alias", target="users.email", notes="new_name=x"),
        ]
        plan = ExecutionPlan(
            plan=steps,
            requires_execution_approval=False,
            source_severity="medium",
            source_requires_human_review=False,
        )
        context = _make_context()

        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = [
            MigrationAlternative(
                original_change="x",
                safe_sql="SELECT 1;",
                explanation="test",
                risk_reduction="x->y",
                trade_off="none",
            )
        ]
        mock_agent.generate_rollback.return_value = RollbackScript(
            rollback_sql="DROP VIEW IF EXISTS x;",
            confidence=0.9,
            warnings=[],
            is_complete=True,
        )

        with patch("schemint.drift.copilot_service.get_copilot_agent", return_value=mock_agent):
            enriched = service.enrich_plan(plan, context)

        # Only generate_alternatives called once (for the SQL step)
        assert mock_agent.generate_alternatives.call_count == 1
        notify_step = enriched.plan[0]
        sql_step = enriched.plan[1]
        assert notify_step.generated_sql is None
        assert sql_step.generated_sql is not None
