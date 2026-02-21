"""Tests for the AgentController orchestration loop."""

from __future__ import annotations

import pytest

from schemint.drift.agent_controller import (
    AgentController,
    _FallbackJudge,
    _FallbackPlanner,
)
from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DriftRunResult,
    DriftRunStatus,
    ExecutionPlan,
    ExecutionReport,
    ExecutionResult,
    ImpactMetrics,
    MemoryContext,
    PlanStep,
    SchemaChangeEvent,
    VerificationReport,
)

# =============================================================================
# Stub implementations for protocol-based DI
# =============================================================================


class StubJudge:
    """Stub judge that returns a configurable decision."""

    def __init__(self, severity: str = "low", requires_human: bool = False) -> None:
        self.severity = severity
        self.requires_human = requires_human
        self.call_count = 0

    def judge(self, _context: ContextPackage) -> AgentDecision:
        self.call_count += 1
        return AgentDecision(
            severity=self.severity,
            confidence_in_decision=0.8,
            requires_human_review=self.requires_human,
            rationale=["test rationale"],
            recommended_action_categories=["monitor_only"],
            context_quality="complete",
        )


class StubPlanner:
    """Stub planner that returns a configurable plan."""

    def __init__(self, steps: list[PlanStep] | None = None) -> None:
        self.steps = steps or [
            PlanStep(step=1, action="notify_table_owner", target="users", reversible=True)
        ]
        self.call_count = 0

    def plan(self, decision: AgentDecision, _context: ContextPackage) -> ExecutionPlan:
        self.call_count += 1
        return ExecutionPlan(
            plan=self.steps,
            requires_execution_approval=True,
            source_severity=decision.severity,
            source_requires_human_review=decision.requires_human_review,
        )


class StubExecutor:
    """Stub executor that returns a configurable report."""

    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.call_count = 0

    def execute(self, plan: ExecutionPlan) -> ExecutionReport:
        self.call_count += 1
        results = [
            ExecutionResult(step=s.step, action=s.action, status=self.status) for s in plan.plan
        ]
        return ExecutionReport(
            execution_id=f"exec_test_{self.call_count}",
            overall_status=self.status,
            step_results=results,
        )


class StubVerifier:
    """Stub verifier with configurable outcome."""

    def __init__(
        self,
        goal_satisfied: bool = True,
        requires_rollback: bool = False,
        requires_escalation: bool = False,
    ) -> None:
        self.goal_satisfied = goal_satisfied
        self.requires_rollback = requires_rollback
        self.requires_escalation = requires_escalation
        self.call_count = 0

    def verify(self, execution_report: ExecutionReport, **_kwargs: object) -> VerificationReport:
        self.call_count += 1
        return VerificationReport(
            execution_id=execution_report.execution_id,
            schema_valid=True,
            dependency_valid=True,
            tests_passed=True,
            goal_satisfied=self.goal_satisfied,
            requires_rollback=self.requires_rollback,
            requires_human_escalation=self.requires_escalation,
        )


class StubPersistence:
    """Stub persistence that records saved runs."""

    def __init__(self) -> None:
        self.saved_runs: list[DriftRunResult] = []

    def save_drift_run(self, result: DriftRunResult) -> str:
        self.saved_runs.append(result)
        return "test-row-id"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_context() -> ContextPackage:
    return ContextPackage(
        schema_change=SchemaChangeEvent(
            change_type="column_added",
            table="users",
            column="email_verified",
        ),
        impact_metrics=ImpactMetrics(
            downstream_tables=2,
            downstream_columns=3,
            max_depth=1,
            criticality="low",
        ),
    )


# =============================================================================
# Tests
# =============================================================================


class TestAgentControllerHappyPath:
    """Test the basic happy path: judge → plan → execute → verify → complete."""

    def test_happy_path_complete(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(status="success"),
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test-project")

        assert result.status == DriftRunStatus.COMPLETE
        assert result.decision is not None
        assert result.plan is not None
        assert result.execution_report is not None
        assert result.verification_report is not None
        assert result.error is None
        assert result.retry_count == 0
        assert result.completed_at is not None

    def test_state_transitions_recorded(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(status="success"),
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test-project")

        statuses = [t.to_status for t in result.state_transitions]
        assert DriftRunStatus.JUDGING in statuses
        assert DriftRunStatus.PLANNING in statuses
        assert DriftRunStatus.EXECUTING in statuses
        assert DriftRunStatus.VERIFYING in statuses
        assert DriftRunStatus.COMPLETE in statuses


class TestApprovalGate:
    """Test the approval gate logic."""

    def test_low_severity_auto_approved(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.COMPLETE

    def test_medium_severity_auto_approved(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="medium"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.COMPLETE

    def test_high_severity_pauses_for_approval(self, sample_context: ContextPackage) -> None:
        """High severity → AWAITING_APPROVAL (not ESCALATED — human must approve/reject)."""
        executor = StubExecutor()
        controller = AgentController(
            judge=StubJudge(severity="high"),
            planner=StubPlanner(),
            executor=executor,
            verifier=StubVerifier(),
        )

        result = controller.run(sample_context, project_id="test")

        assert result.status == DriftRunStatus.AWAITING_APPROVAL
        assert executor.call_count == 0  # Never reached execution — waiting for approval

    def test_critical_severity_pauses_for_approval(self, sample_context: ContextPackage) -> None:
        """Critical severity → AWAITING_APPROVAL so a human can approve or reject."""
        controller = AgentController(
            judge=StubJudge(severity="critical"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(),
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.AWAITING_APPROVAL

    def test_custom_auto_approve_severities(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="high"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
            auto_approve_severities={"low", "medium", "high"},
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.COMPLETE


class TestRetryLogic:
    """Test retry on verification failure."""

    def test_retry_on_verification_failure(self, sample_context: ContextPackage) -> None:
        """Verification fails first time, succeeds second time."""

        class FailThenSucceedVerifier:
            def __init__(self) -> None:
                self.call_count = 0

            def verify(
                self, execution_report: ExecutionReport, **_kwargs: object
            ) -> VerificationReport:
                self.call_count += 1
                return VerificationReport(
                    execution_id=execution_report.execution_id,
                    goal_satisfied=self.call_count >= 2,
                )

        verifier = FailThenSucceedVerifier()
        planner = StubPlanner()
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=planner,
            executor=StubExecutor(),
            verifier=verifier,
        )

        result = controller.run(sample_context, project_id="test")

        assert result.status == DriftRunStatus.COMPLETE
        assert result.retry_count == 1
        assert planner.call_count == 2  # Initial + 1 retry

    def test_max_retries_exhausted(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=False),
            max_retries=2,
        )

        result = controller.run(sample_context, project_id="test")

        assert result.status == DriftRunStatus.ESCALATED
        assert result.retry_count == 3  # 1 initial + 2 retries (counter increments past max)

    def test_rollback_escalation_no_retry(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(
                goal_satisfied=False,
                requires_rollback=True,
            ),
        )

        result = controller.run(sample_context, project_id="test")

        assert result.status == DriftRunStatus.ESCALATED
        assert result.retry_count == 0  # No retry — immediate escalation


class TestFallbackMode:
    """Test fallback when AI is unavailable."""

    def test_fallback_judge(self, sample_context: ContextPackage) -> None:
        judge = _FallbackJudge()
        decision = judge.judge(sample_context)

        assert decision.severity == "low"
        assert decision.confidence_in_decision == 0.0
        assert decision.requires_human_review is True

    def test_fallback_planner(self, sample_context: ContextPackage) -> None:
        planner = _FallbackPlanner()
        decision = AgentDecision(
            severity="medium",
            confidence_in_decision=0.5,
            requires_human_review=True,
            rationale=["test"],
            recommended_action_categories=["monitor_only"],
            context_quality="complete",
        )

        plan = planner.plan(decision, sample_context)
        assert len(plan.plan) == 1
        assert plan.plan[0].action == "notify_table_owner"

    def test_fallback_planner_critical(self, sample_context: ContextPackage) -> None:
        planner = _FallbackPlanner()
        decision = AgentDecision(
            severity="critical",
            confidence_in_decision=0.5,
            requires_human_review=True,
            rationale=["test"],
            recommended_action_categories=["block_deploy"],
            context_quality="complete",
        )

        plan = planner.plan(decision, sample_context)
        assert len(plan.plan) == 2
        assert plan.plan[1].action == "block_deploy"

    def test_controller_with_no_components(self, sample_context: ContextPackage) -> None:
        """Controller with default fallback components."""
        controller = AgentController()  # All fallbacks

        result = controller.run(sample_context, project_id="test")

        # Should fail because no executor
        assert result.status == DriftRunStatus.FAILED
        assert result.decision is not None


class TestPersistence:
    """Test that results are persisted."""

    def test_persistence_called(self, sample_context: ContextPackage) -> None:
        persistence = StubPersistence()
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
            persistence=persistence,
        )

        result = controller.run(sample_context, project_id="test")

        assert len(persistence.saved_runs) == 1
        assert persistence.saved_runs[0].run_id == result.run_id

    def test_persistence_failure_non_fatal(self, sample_context: ContextPackage) -> None:
        class FailingPersistence:
            def save_drift_run(self, _result: DriftRunResult) -> str:
                raise ConnectionError("DB down")

        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
            persistence=FailingPersistence(),
        )

        # Should not raise
        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.COMPLETE


class TestMemoryContext:
    """Test memory context injection."""

    def test_memory_injected_into_context(self, sample_context: ContextPackage) -> None:
        memory = MemoryContext(
            accepted_findings=["safe to drop old_email"],
            business_rules=["users table is critical"],
        )

        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(),
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test", memory_context=memory)

        assert result.memory_context is not None
        assert result.memory_context.accepted_findings == ["safe to drop old_email"]

    def test_no_executor_configured(self, sample_context: ContextPackage) -> None:
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=None,
            verifier=StubVerifier(),
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.FAILED
        assert "No executor configured" in (result.error or "")

    def test_no_verifier_success(self, sample_context: ContextPackage) -> None:
        """Without a verifier, execution success → complete."""
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(status="success"),
            verifier=None,
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.COMPLETE

    def test_no_verifier_failure(self, sample_context: ContextPackage) -> None:
        """Without a verifier, execution failure → failed."""
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=StubExecutor(status="failed"),
            verifier=None,
        )

        result = controller.run(sample_context, project_id="test")
        assert result.status == DriftRunStatus.FAILED


class TestPlanApprovalOverride:
    """Test that controller overrides plan.requires_execution_approval."""

    def test_plan_approval_overridden(self, sample_context: ContextPackage) -> None:
        """The planner always sets requires_execution_approval=True,
        but the controller should override it after approval gate passes."""
        executor = StubExecutor()
        controller = AgentController(
            judge=StubJudge(severity="low"),
            planner=StubPlanner(),
            executor=executor,
            verifier=StubVerifier(goal_satisfied=True),
        )

        result = controller.run(sample_context, project_id="test")

        # Executor was called (plan approval was overridden)
        assert executor.call_count == 1
        assert result.status == DriftRunStatus.COMPLETE
        # The stored plan should have approval=False
        assert result.plan is not None
        assert result.plan.requires_execution_approval is False
