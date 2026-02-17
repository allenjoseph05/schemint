"""Tests for ExecutionEngine (Phase 5) — deterministic plan execution.

Tests verify:
- Pending approval gate
- Sequential execution
- Failure halts execution
- Skipped steps after failure
- Unknown action rejection
- Rollback computation
- All tool adapters
- Irreversible step warnings
- Execution ID generation
"""

from schemint.drift.execution_engine import (
    CIPipelineRunner,
    DBTRunner,
    ExecutionEngine,
    NotificationService,
    SQLRunner,
    ToolAdapter,
)
from schemint.drift.models import (
    ExecutionPlan,
    ExecutionResult,
    PlanStep,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plan(
    steps: list[dict] | None = None,
    requires_approval: bool = False,
    severity: str = "medium",
) -> ExecutionPlan:
    if steps is None:
        steps = [
            {"step": 1, "action": "notify_table_owner", "target": "users"},
        ]
    return ExecutionPlan(
        plan=[PlanStep(**s) for s in steps],
        requires_execution_approval=requires_approval,
        source_severity=severity,
        source_requires_human_review=False,
    )


class FailingAdapter(ToolAdapter):
    """Adapter that always fails — for testing failure paths."""

    def supports_action(self, action_id: str) -> bool:
        return action_id == "notify_table_owner"

    def execute(self, step: PlanStep) -> ExecutionResult:
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status="failed",
            error_message="Simulated failure",
            reversible=step.reversible,
        )


# ---------------------------------------------------------------------------
# Approval Gate Tests
# ---------------------------------------------------------------------------


class TestApprovalGate:
    def test_pending_approval_blocks_execution(self):
        """requires_execution_approval → pending_approval, no steps run."""
        engine = ExecutionEngine()
        plan = _make_plan(requires_approval=True)

        report = engine.execute(plan)

        assert report.overall_status == "pending_approval"
        assert report.step_results == []
        assert report.requires_rollback is False

    def test_no_approval_required_executes(self):
        """requires_execution_approval=False → steps are executed."""
        engine = ExecutionEngine()
        plan = _make_plan(requires_approval=False)

        report = engine.execute(plan)

        assert report.overall_status == "success"
        assert len(report.step_results) == 1


# ---------------------------------------------------------------------------
# Sequential Execution Tests
# ---------------------------------------------------------------------------


class TestSequentialExecution:
    def test_all_steps_execute_in_order(self):
        engine = ExecutionEngine()
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "users"},
                {"step": 2, "action": "log_drift_event", "target": "users"},
                {"step": 3, "action": "notify_downstream_teams", "target": "users"},
            ]
        )

        report = engine.execute(plan)

        assert report.overall_status == "success"
        assert len(report.step_results) == 3
        for i, result in enumerate(report.step_results, 1):
            assert result.step == i
            assert result.status == "success"

    def test_empty_plan_succeeds(self):
        engine = ExecutionEngine()
        plan = _make_plan(steps=[])

        report = engine.execute(plan)

        assert report.overall_status == "success"
        assert len(report.step_results) == 0


# ---------------------------------------------------------------------------
# Failure Handling Tests
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_failure_halts_execution(self):
        """When a step fails, remaining steps are skipped."""
        engine = ExecutionEngine(adapters=[FailingAdapter()])
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "users"},
                {"step": 2, "action": "log_drift_event", "target": "users"},
            ]
        )

        report = engine.execute(plan)

        assert report.step_results[0].status == "failed"
        assert report.step_results[1].status == "skipped"
        assert "prior step failure" in report.step_results[1].error_message

    def test_failure_sets_overall_failed(self):
        engine = ExecutionEngine(adapters=[FailingAdapter()])
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "users"},
            ]
        )

        report = engine.execute(plan)

        assert report.overall_status == "failed"

    def test_unknown_action_rejected(self):
        """Action not in registry → immediate failure."""
        engine = ExecutionEngine()
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "invented_action", "target": "users"},
            ]
        )

        report = engine.execute(plan)

        assert report.overall_status == "failed"
        assert "Unknown action" in report.step_results[0].error_message


# ---------------------------------------------------------------------------
# Rollback Computation Tests
# ---------------------------------------------------------------------------


class TestRollbackComputation:
    def test_no_rollback_on_success(self):
        engine = ExecutionEngine()
        plan = _make_plan()

        report = engine.execute(plan)

        assert report.requires_rollback is False

    def test_rollback_when_reversible_step_succeeded_before_failure(self):
        """If a reversible step succeeded before failure → rollback needed."""

        class MixedAdapter(ToolAdapter):
            def __init__(self):
                self.call_count = 0

            def supports_action(self, _action_id: str) -> bool:
                return True

            def execute(self, step: PlanStep) -> ExecutionResult:
                self.call_count += 1
                if self.call_count == 2:
                    return ExecutionResult(
                        step=step.step,
                        action=step.action,
                        status="failed",
                        error_message="boom",
                        reversible=step.reversible,
                    )
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="success",
                    reversible=True,
                )

        engine = ExecutionEngine(adapters=[MixedAdapter()])
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "t", "reversible": True},
                {"step": 2, "action": "log_drift_event", "target": "t"},
            ]
        )

        report = engine.execute(plan)

        assert report.requires_rollback is True

    def test_no_rollback_if_only_failure_step(self):
        """If first step fails with no prior success → no rollback."""
        engine = ExecutionEngine(adapters=[FailingAdapter()])
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "users"},
            ]
        )

        report = engine.execute(plan)

        assert report.requires_rollback is False


# ---------------------------------------------------------------------------
# Tool Adapter Tests
# ---------------------------------------------------------------------------


class TestToolAdapters:
    def test_notification_service_supports_all_notification_actions(self):
        adapter = NotificationService()
        assert adapter.supports_action("notify_table_owner")
        assert adapter.supports_action("notify_downstream_teams")
        assert adapter.supports_action("create_review_ticket")
        assert adapter.supports_action("update_downstream_query")
        assert adapter.supports_action("update_downstream_model")
        assert adapter.supports_action("regenerate_api_contract")
        assert adapter.supports_action("add_monitoring_alert")
        assert adapter.supports_action("log_drift_event")
        assert not adapter.supports_action("block_deploy")

    def test_sql_runner_supports_structural_actions(self):
        adapter = SQLRunner()
        assert adapter.supports_action("add_column_alias")
        assert adapter.supports_action("add_default_value")
        assert adapter.supports_action("create_migration_view")
        assert not adapter.supports_action("notify_table_owner")

    def test_ci_pipeline_runner_supports_enforcement(self):
        adapter = CIPipelineRunner()
        assert adapter.supports_action("block_deploy")
        assert adapter.supports_action("require_migration_review")
        assert not adapter.supports_action("notify_table_owner")

    def test_dbt_runner_currently_empty(self):
        adapter = DBTRunner()
        assert not adapter.supports_action("notify_table_owner")
        assert not adapter.supports_action("block_deploy")

    def test_notification_service_executes_successfully(self):
        adapter = NotificationService()
        step = PlanStep(step=1, action="notify_table_owner", target="users")
        result = adapter.execute(step)
        assert result.status == "success"

    def test_sql_runner_executes_successfully(self):
        adapter = SQLRunner()
        step = PlanStep(step=1, action="add_column_alias", target="users")
        result = adapter.execute(step)
        assert result.status == "success"

    def test_ci_runner_executes_successfully(self):
        adapter = CIPipelineRunner()
        step = PlanStep(step=1, action="block_deploy", target="users", reversible=False)
        result = adapter.execute(step)
        assert result.status == "success"


# ---------------------------------------------------------------------------
# Execution ID Tests
# ---------------------------------------------------------------------------


class TestExecutionMetadata:
    def test_execution_id_generated(self):
        engine = ExecutionEngine()
        plan = _make_plan()

        report = engine.execute(plan)

        assert report.execution_id.startswith("exec_")
        assert len(report.execution_id) > 10

    def test_execution_has_timestamp(self):
        engine = ExecutionEngine()
        plan = _make_plan()

        report = engine.execute(plan)

        assert report.executed_at is not None

    def test_partial_failure_status(self):
        """Mix of success and failure → partial_failure."""

        class PartialAdapter(ToolAdapter):
            def __init__(self):
                self.call_count = 0

            def supports_action(self, _action_id: str) -> bool:
                return True

            def execute(self, step: PlanStep) -> ExecutionResult:
                self.call_count += 1
                if self.call_count == 2:
                    return ExecutionResult(
                        step=step.step,
                        action=step.action,
                        status="failed",
                        error_message="boom",
                        reversible=True,
                    )
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="success",
                    reversible=True,
                )

        engine = ExecutionEngine(adapters=[PartialAdapter()])
        plan = _make_plan(
            steps=[
                {"step": 1, "action": "notify_table_owner", "target": "t"},
                {"step": 2, "action": "log_drift_event", "target": "t"},
            ]
        )

        report = engine.execute(plan)

        assert report.overall_status == "partial_failure"
