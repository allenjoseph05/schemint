"""Tests for RunTelemetry model and AgentController timing."""

from unittest.mock import MagicMock, patch

from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DependencyCoverage,
    ExecutionPlan,
    ExecutionReport,
    ExecutionResult,
    ImpactMetrics,
    PlanStep,
    RunTelemetry,
    SchemaChangeEvent,
    VerificationReport,
)


def _make_context() -> ContextPackage:
    return ContextPackage(
        schema_change=SchemaChangeEvent(
            change_type="column_dropped",
            table="users",
            column="email",
            change_risk="breaking",
        ),
        impacted_dependencies=[],
        impact_metrics=ImpactMetrics(
            downstream_table_count=1,
            downstream_model_count=0,
            row_count_estimate=100,
            has_fk_references=False,
            criticality="medium",
        ),
        dependency_coverage=DependencyCoverage(
            tables_total=3,
            tables_with_lineage=3,
            coverage_pct=100.0,
        ),
        context_quality="complete",
    )


def _make_decision(severity: str = "low") -> AgentDecision:
    return AgentDecision(
        severity=severity,
        confidence_in_decision=0.9,
        requires_human_review=False,
        rationale=["test"],
        recommended_action_categories=["monitor_only"],
        context_quality="complete",
    )


def _make_plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan=[PlanStep(step=1, action="notify_table_owner", target="users", notes="")],
        requires_execution_approval=False,
        source_severity="low",
        source_requires_human_review=False,
    )


def _make_exec_report() -> ExecutionReport:
    return ExecutionReport(
        execution_id="exec_test",
        overall_status="success",
        step_results=[
            ExecutionResult(step=1, action="notify_table_owner", status="success", reversible=True)
        ],
        requires_rollback=False,
    )


def _make_verification_report(satisfied: bool = True) -> VerificationReport:
    return VerificationReport(
        execution_id="exec_test",
        goal_satisfied=satisfied,
        requires_rollback=False,
        requires_human_escalation=False,
    )


# =============================================================================
# RunTelemetry model
# =============================================================================


class TestRunTelemetryModel:
    def test_default_values(self):
        t = RunTelemetry(run_id="r1", project_id="p1", status="complete")
        assert t.total_duration_ms == 0
        assert t.phase_durations_ms == {}
        assert t.step_count == 0
        assert t.retry_count == 0
        assert t.copilot_enriched is False
        assert t.severity is None

    def test_with_phase_durations(self):
        t = RunTelemetry(
            run_id="r1",
            project_id="p1",
            status="complete",
            severity="low",
            total_duration_ms=500,
            phase_durations_ms={"judging": 200, "planning": 150, "executing_0": 150},
            step_count=2,
        )
        assert t.total_duration_ms == 500
        assert t.phase_durations_ms["judging"] == 200
        assert t.step_count == 2

    def test_copilot_enriched_flag(self):
        t = RunTelemetry(run_id="r", project_id="p", status="complete", copilot_enriched=True)
        assert t.copilot_enriched is True


# =============================================================================
# AgentController telemetry attachment
# =============================================================================


_NO_COPILOT = patch("schemint.drift.copilot_service.get_copilot_service", return_value=None)


class TestAgentControllerTelemetry:
    def _make_controller(self, judge=None, planner=None, executor=None, verifier=None):
        from schemint.drift.agent_controller import AgentController

        return AgentController(
            judge=judge,
            planner=planner,
            executor=executor,
            verifier=verifier,
            auto_approve_severities={"low", "medium", "high", "critical"},
        )

    def _run(self, controller):
        """Run controller with copilot disabled (no side effects from real Claude API)."""
        with _NO_COPILOT:
            return controller.run(_make_context(), project_id="test-project")

    def test_telemetry_attached_to_result(self):
        """After run(), result.telemetry is populated."""
        mock_judge = MagicMock()
        mock_judge.judge.return_value = _make_decision("low")

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _make_plan()

        mock_executor = MagicMock()
        mock_executor.execute.return_value = _make_exec_report()

        mock_verifier = MagicMock()
        mock_verifier.verify.return_value = _make_verification_report()

        controller = self._make_controller(
            judge=mock_judge,
            planner=mock_planner,
            executor=mock_executor,
            verifier=mock_verifier,
        )
        result = self._run(controller)

        assert result.telemetry is not None
        assert result.telemetry.run_id == result.run_id
        assert result.telemetry.project_id == "test-project"
        assert result.telemetry.severity == "low"
        assert result.telemetry.total_duration_ms >= 0
        assert result.telemetry.step_count == 1

    def test_telemetry_phase_durations_populated(self):
        """Phase durations dict is filled with judging/planning/executing/verifying."""
        mock_judge = MagicMock()
        mock_judge.judge.return_value = _make_decision("low")

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _make_plan()

        mock_executor = MagicMock()
        mock_executor.execute.return_value = _make_exec_report()

        mock_verifier = MagicMock()
        mock_verifier.verify.return_value = _make_verification_report()

        controller = self._make_controller(
            judge=mock_judge,
            planner=mock_planner,
            executor=mock_executor,
            verifier=mock_verifier,
        )
        result = self._run(controller)

        phases = result.telemetry.phase_durations_ms
        assert "judging" in phases
        assert "planning" in phases
        assert "executing_0" in phases
        assert "verifying_0" in phases
        for v in phases.values():
            assert v >= 0

    def test_telemetry_copilot_enriched_false_without_agent(self):
        """copilot_enriched=False when CopilotAgent is unavailable."""
        mock_judge = MagicMock()
        mock_judge.judge.return_value = _make_decision("low")

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _make_plan()

        mock_executor = MagicMock()
        mock_executor.execute.return_value = _make_exec_report()

        mock_verifier = MagicMock()
        mock_verifier.verify.return_value = _make_verification_report()

        controller = self._make_controller(
            judge=mock_judge,
            planner=mock_planner,
            executor=mock_executor,
            verifier=mock_verifier,
        )
        result = self._run(controller)

        assert result.telemetry.copilot_enriched is False

    def test_telemetry_status_matches_run_status(self):
        """telemetry.status matches result.status."""
        mock_judge = MagicMock()
        mock_judge.judge.return_value = _make_decision("low")

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _make_plan()

        mock_executor = MagicMock()
        mock_executor.execute.return_value = _make_exec_report()

        mock_verifier = MagicMock()
        mock_verifier.verify.return_value = _make_verification_report(satisfied=True)

        controller = self._make_controller(
            judge=mock_judge,
            planner=mock_planner,
            executor=mock_executor,
            verifier=mock_verifier,
        )
        result = self._run(controller)

        assert result.telemetry.status == result.status
