"""Autonomous Agent Controller — orchestrates the full drift pipeline.

Connects Phases 3-6 into a closed loop:
    detect → judge → plan → act → verify → retry/escalate → learn

Design constraints:
    - Protocol-based DI: constructor takes judge, planner, executor, verifier.
      Existing classes satisfy the protocols without modification.
    - State machine: JUDGING → PLANNING → [AWAITING_APPROVAL] → EXECUTING →
      VERIFYING → COMPLETE / ESCALATED / FAILED
    - Approval gate: auto-approve configurable severities, escalate the rest.
    - Retry: on verification failure (goal_satisfied=False, no rollback),
      re-plan + re-execute, max N retries.
    - Rollback/escalation signal → ESCALATED immediately.
    - Synchronous — Sprint 2 moves to run_in_executor.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DriftRunResult,
    DriftRunStatus,
    ExecutionPlan,
    ExecutionReport,
    MemoryContext,
    NotificationConfig,
    PlanStep,
    RunTelemetry,
    StateTransition,
    VerificationReport,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Protocols — existing classes satisfy these without modification
# =============================================================================


@runtime_checkable
class JudgeProtocol(Protocol):
    """Protocol for Phase 3 judge (DriftAgent satisfies this)."""

    def judge(self, context: ContextPackage) -> AgentDecision: ...


@runtime_checkable
class PlannerProtocol(Protocol):
    """Protocol for Phase 4 planner (PlanningAgent satisfies this)."""

    def plan(self, decision: AgentDecision, context: ContextPackage) -> ExecutionPlan: ...


@runtime_checkable
class ExecutorProtocol(Protocol):
    """Protocol for Phase 5 executor (ExecutionEngine satisfies this)."""

    def execute(self, plan: ExecutionPlan) -> ExecutionReport: ...


@runtime_checkable
class VerifierProtocol(Protocol):
    """Protocol for Phase 6 verifier (VerificationEngine satisfies this)."""

    def verify(
        self,
        execution_report: ExecutionReport,
        expected_snapshot: Any = None,
        actual_snapshot: Any = None,
        expected_graph: Any = None,
        actual_graph: Any = None,
        ci_results: Any = None,
        source_requires_human_review: bool = False,
    ) -> VerificationReport: ...


@runtime_checkable
class PersistenceProtocol(Protocol):
    """Protocol for drift run persistence (DriftStore satisfies this)."""

    def save_drift_run(self, result: DriftRunResult) -> str: ...

    def get_drift_run(self, run_id: str) -> DriftRunResult | None: ...

    def get_drift_runs(self, project_id: str, limit: int = 20) -> list[DriftRunResult]: ...


# =============================================================================
# Fallback implementations (when Claude is unavailable)
# =============================================================================


class _FallbackJudge:
    """Deterministic judge — uses impact criticality as severity."""

    def judge(self, context: ContextPackage) -> AgentDecision:
        return AgentDecision(
            severity=context.impact_metrics.criticality,
            confidence_in_decision=0.0,
            requires_human_review=True,
            rationale=["AI service unavailable — deterministic fallback"],
            recommended_action_categories=["notify_owner"],
            context_quality=context.context_quality,
        )


class _FallbackPlanner:
    """Deterministic planner — notification-only plan."""

    def plan(self, decision: AgentDecision, context: ContextPackage) -> ExecutionPlan:
        steps = [
            PlanStep(
                step=1,
                action="notify_table_owner",
                target=context.schema_change.table,
                notes="AI unavailable — fallback notification plan",
                reversible=True,
            ),
        ]
        if decision.severity == "critical":
            steps.append(
                PlanStep(
                    step=2,
                    action="block_deploy",
                    target=context.schema_change.table,
                    notes="Critical severity — deploy block required",
                    reversible=False,
                )
            )
        return ExecutionPlan(
            plan=steps,
            requires_execution_approval=decision.severity == "critical",
            source_severity=decision.severity,
            source_requires_human_review=decision.requires_human_review,
        )


# =============================================================================
# Agent Controller
# =============================================================================


class AgentController:
    """Orchestrates the full drift pipeline as a state machine.

    Usage:
        controller = AgentController(judge, planner, executor, verifier)
        result = controller.run(context, project_id="my-project")
    """

    def __init__(
        self,
        judge: JudgeProtocol | None = None,
        planner: PlannerProtocol | None = None,
        executor: ExecutorProtocol | None = None,
        verifier: VerifierProtocol | None = None,
        persistence: PersistenceProtocol | None = None,
        auto_approve_severities: set[str] | None = None,
        max_retries: int = 2,
    ) -> None:
        self.judge = judge or _FallbackJudge()
        self.planner = planner or _FallbackPlanner()
        self.executor = executor
        self.verifier = verifier
        self.persistence = persistence
        self.auto_approve_severities = auto_approve_severities or {"low", "medium"}
        self.max_retries = max_retries

    def run(
        self,
        context: ContextPackage,
        project_id: str,
        memory_context: MemoryContext | None = None,
    ) -> DriftRunResult:
        """Run the full autonomous drift pipeline.

        Returns a DriftRunResult with the complete audit trail.
        """
        run_id = (
            f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )

        result = DriftRunResult(
            run_id=run_id,
            project_id=project_id,
            status=DriftRunStatus.JUDGING,
            memory_context=memory_context,
        )

        # Inject memory into context if provided
        if memory_context and context.memory_context is None:
            context = context.model_copy(update={"memory_context": memory_context})

        t_start = time.monotonic()
        phase_durations: dict[str, int] = {}

        try:
            self._run_pipeline(context, result, phase_durations)
        except Exception as e:
            logger.error("Agent controller pipeline error: %s", e)
            self._transition(result, DriftRunStatus.FAILED, reason=str(e))
            result.error = str(e)

        result.completed_at = datetime.now(timezone.utc)
        total_ms = int((time.monotonic() - t_start) * 1000)

        _plan = result.plan
        result.telemetry = RunTelemetry(
            run_id=run_id,
            project_id=project_id,
            status=result.status,
            severity=result.decision.severity if result.decision else None,
            total_duration_ms=total_ms,
            phase_durations_ms=phase_durations,
            step_count=len(_plan.plan) if _plan else 0,
            retry_count=result.retry_count,
            copilot_enriched=any(s.generated_sql for s in (_plan.plan if _plan else [])),
        )

        self._persist(result)
        return result

    def resume(
        self,
        run_id: str,
        approved: bool,
        approver: str,
        reason: str = "",
    ) -> DriftRunResult:
        """Resume a run that is AWAITING_APPROVAL.

        If approved=True, execution continues from the EXECUTING phase.
        If approved=False, the run is terminated with status ESCALATED.

        Raises ValueError if the run is not in AWAITING_APPROVAL status
        or if no persistence backend is configured.
        """
        if self.persistence is None:
            raise ValueError("Cannot resume run: no persistence backend configured")

        result = self.persistence.get_drift_run(run_id)
        if result is None:
            raise ValueError(f"Run '{run_id}' not found")

        if result.status != DriftRunStatus.AWAITING_APPROVAL:
            raise ValueError(f"Run '{run_id}' is not awaiting approval (status='{result.status}')")

        if not approved:
            self._transition(
                result,
                DriftRunStatus.ESCALATED,
                reason=f"Rejected by {approver}: {reason}" if reason else f"Rejected by {approver}",
            )
            result.completed_at = datetime.now(timezone.utc)
            self._persist(result)
            return result

        # Approved — resume from EXECUTING
        if result.plan is None or result.decision is None:
            self._transition(
                result, DriftRunStatus.FAILED, reason="Plan or decision missing from stored run"
            )
            result.error = "Plan or decision missing — cannot resume"
            result.completed_at = datetime.now(timezone.utc)
            self._persist(result)
            return result

        self._transition(
            result,
            DriftRunStatus.EXECUTING,
            reason=f"Approved by {approver}",
        )

        plan = result.plan.model_copy(update={"requires_execution_approval": False})
        result.plan = plan

        try:
            self._execute_and_verify_loop(result, plan, result.decision)
        except Exception as exc:
            logger.error("Agent controller resume error: %s", exc)
            self._transition(result, DriftRunStatus.FAILED, reason=str(exc))
            result.error = str(exc)

        result.completed_at = datetime.now(timezone.utc)
        self._persist(result)
        return result

    def _run_pipeline(
        self,
        context: ContextPackage,
        result: DriftRunResult,
        phase_durations: dict[str, int] | None = None,
    ) -> None:
        """Execute the state machine pipeline."""
        _pd = phase_durations if phase_durations is not None else {}

        # Phase 3: Judge
        self._transition(result, DriftRunStatus.JUDGING, reason="Starting judgment")
        t0 = time.monotonic()
        decision = self.judge.judge(context)
        _pd["judging"] = int((time.monotonic() - t0) * 1000)
        result.decision = decision

        # Phase 4: Plan
        self._transition(result, DriftRunStatus.PLANNING, reason="Generating plan")
        t0 = time.monotonic()
        plan = self.planner.plan(decision, context)
        _pd["planning"] = int((time.monotonic() - t0) * 1000)
        result.plan = plan

        # Phase 4.5 (optional): CopilotService enrichment — best-effort
        try:
            from schemint.drift.copilot_service import get_copilot_service

            copilot = get_copilot_service()
            if copilot is not None:
                t0 = time.monotonic()
                plan = copilot.enrich_plan(plan, context)
                _pd["copilot_enrichment"] = int((time.monotonic() - t0) * 1000)
                result.plan = plan
        except Exception as exc:
            logger.debug("CopilotService enrichment skipped: %s", exc)

        # Approval gate
        if not self._check_approval(decision, result):
            return  # AWAITING_APPROVAL — persisted, waiting for resume()

        # Override plan's approval flag — controller has already approved
        plan = plan.model_copy(update={"requires_execution_approval": False})
        result.plan = plan

        self._execute_and_verify_loop(result, plan, decision, context, _pd)

    def _execute_and_verify_loop(
        self,
        result: DriftRunResult,
        plan: ExecutionPlan,
        decision: AgentDecision,
        context: ContextPackage | None = None,
        phase_durations: dict[str, int] | None = None,
    ) -> None:
        """Execute → Verify → Retry loop shared by run() and resume().

        When context is None (resume path), retries re-execute the same
        plan rather than re-planning, since the original context isn't
        available after serialization.
        """
        _pd = phase_durations if phase_durations is not None else {}
        retry_count = result.retry_count  # preserve retry count across resumes

        while True:
            # Phase 5: Execute
            if self.executor is None:
                self._transition(result, DriftRunStatus.FAILED, reason="No executor configured")
                result.error = "No executor configured"
                return

            self._transition(result, DriftRunStatus.EXECUTING, reason="Executing plan")
            t0 = time.monotonic()
            exec_report = self.executor.execute(plan)
            _pd[f"executing_{retry_count}"] = int((time.monotonic() - t0) * 1000)
            result.execution_report = exec_report

            # Phase 6: Verify
            if self.verifier is None:
                if exec_report.overall_status == "success":
                    self._transition(result, DriftRunStatus.COMPLETE, reason="Execution succeeded")
                else:
                    self._transition(
                        result,
                        DriftRunStatus.FAILED,
                        reason=f"Execution {exec_report.overall_status}",
                    )
                    result.error = f"Execution {exec_report.overall_status}"
                return

            self._transition(result, DriftRunStatus.VERIFYING, reason="Verifying outcome")
            t0 = time.monotonic()
            verification = self.verifier.verify(
                execution_report=exec_report,
                source_requires_human_review=decision.requires_human_review,
            )
            _pd[f"verifying_{retry_count}"] = int((time.monotonic() - t0) * 1000)
            result.verification_report = verification

            if verification.goal_satisfied:
                self._transition(result, DriftRunStatus.COMPLETE, reason="Goal satisfied")
                return

            if verification.requires_rollback or verification.requires_human_escalation:
                if verification.requires_rollback and result.execution_report:
                    self._execute_rollback(result)
                self._transition(
                    result,
                    DriftRunStatus.ESCALATED,
                    reason="Rollback or escalation required",
                )
                return

            retry_count += 1
            result.retry_count = retry_count

            if retry_count > self.max_retries:
                self._transition(
                    result,
                    DriftRunStatus.ESCALATED,
                    reason=f"Max retries ({self.max_retries}) exhausted",
                )
                return

            self._transition(
                result,
                DriftRunStatus.RETRYING,
                reason=f"Retry {retry_count}/{self.max_retries}",
            )
            if context is not None:
                # Re-plan with the original context
                plan = self.planner.plan(decision, context)
            # else: re-execute the same plan (resume path, no context available)
            plan = plan.model_copy(update={"requires_execution_approval": False})
            result.plan = plan

    def _check_approval(self, decision: AgentDecision, result: DriftRunResult) -> bool:
        """Check if the decision severity is auto-approvable.

        Returns True if auto-approved and execution should continue.
        Returns False if paused at AWAITING_APPROVAL — the run is persisted
        and must be resumed via resume().
        """
        if decision.severity in self.auto_approve_severities:
            return True

        # Pause at AWAITING_APPROVAL — persist so resume() can load it later
        self._transition(
            result,
            DriftRunStatus.AWAITING_APPROVAL,
            reason=f"Severity '{decision.severity}' requires human approval",
        )
        result.completed_at = None  # not completed yet
        self._persist(result)
        self._notify_awaiting_approval(result)
        return False

    def _execute_rollback(self, result: DriftRunResult) -> None:
        """Run the rollback engine and attach the report to execution_report."""
        try:
            from schemint.drift.execution_engine import RollbackEngine

            rollback_engine = RollbackEngine()
            rollback_report = rollback_engine.rollback(result.execution_report)  # type: ignore[arg-type]
            result.execution_report.rollback_report = rollback_report  # type: ignore[union-attr]

            if rollback_report.overall_status != "success":
                logger.error(
                    "Rollback incomplete for run %s: status=%s",
                    result.run_id,
                    rollback_report.overall_status,
                )
            else:
                logger.info("Rollback successful for run %s", result.run_id)
        except Exception as exc:
            logger.error("Rollback execution failed for run %s: %s", result.run_id, exc)

    def _notify_awaiting_approval(self, result: DriftRunResult) -> None:
        """Send a Slack notification when a run is paused for approval."""
        try:
            from schemint.config import get_settings
            from schemint.drift.notification_backends import SlackNotifier

            settings = get_settings()
            slack = SlackNotifier(webhook_url=settings.webhook_url)

            decision = result.decision
            severity = decision.severity if decision else "unknown"
            change_target = (
                result.plan.plan[0].target if result.plan and result.plan.plan else "unknown"
            )

            message = (
                f":eyes: *Schema drift approval required*\n"
                f"Project: `{result.project_id}`  |  Run: `{result.run_id}`\n"
                f"Change on: `{change_target}`  |  Severity: *{severity}*\n\n"
                f"To approve: `POST /api/v1/drift/approve/{result.run_id}`\n"
                f"To reject:  `POST /api/v1/drift/reject/{result.run_id}`"
            )
            slack.send(message)
        except Exception as exc:
            logger.warning("Could not send approval notification: %s", exc)

    def _transition(self, result: DriftRunResult, to_status: str, reason: str = "") -> None:
        """Record a state transition."""
        from_status = result.status
        result.state_transitions.append(
            StateTransition(
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )
        result.status = to_status
        logger.info(
            "Drift run %s: %s → %s (%s)",
            result.run_id,
            from_status,
            to_status,
            reason,
        )

    def _persist(self, result: DriftRunResult) -> None:
        """Persist the run result if a persistence backend is configured."""
        if self.persistence is None:
            return
        try:
            self.persistence.save_drift_run(result)
        except Exception as e:
            logger.error("Failed to persist drift run %s: %s", result.run_id, e)


# =============================================================================
# Factory
# =============================================================================


def build_agent_controller(
    notification_config: NotificationConfig
    | None = None,  # kept for API compat; adapters read from settings
    persistence: PersistenceProtocol | None = None,
    auto_approve_severities: set[str] | None = None,
    max_retries: int | None = None,
) -> AgentController:
    """Build an AgentController with real or fallback components.

    Wires up all phase components:
    - Phase 3: DriftAgent (or fallback)
    - Phase 4: PlanningAgent (or fallback)
    - Phase 5: ExecutionEngine with real adapters
    - Phase 6: VerificationEngine
    - Persistence: DriftStore (if database_url is configured)
    """
    from schemint.config import get_settings

    settings = get_settings()

    # Phase 3: Judge
    judge: JudgeProtocol
    try:
        from schemint.drift.agent_brain import get_drift_agent

        agent = get_drift_agent()
        judge = agent if agent is not None else _FallbackJudge()
    except Exception:
        judge = _FallbackJudge()

    # Phase 4: Planner
    planner: PlannerProtocol
    try:
        from schemint.drift.planning_agent import get_planning_agent

        planning_agent = get_planning_agent()
        planner = planning_agent if planning_agent is not None else _FallbackPlanner()
    except Exception:
        planner = _FallbackPlanner()

    # Phase 5: Executor — use real adapters directly (Slack-native + commit status API)
    from schemint.drift.execution_engine import (
        CIPipelineRunner,
        DBTRunner,
        ExecutionEngine,
        NotificationService,
        SQLRunner,
    )

    executor = ExecutionEngine(
        adapters=[
            NotificationService(),
            SQLRunner(),
            CIPipelineRunner(),
            DBTRunner(),
        ]
    )

    # Phase 6: Verifier
    from schemint.drift.verification import VerificationEngine

    verifier = VerificationEngine()

    # Persistence
    if persistence is None:
        try:
            from schemint.drift.store import get_drift_store

            persistence = get_drift_store()
        except Exception:
            persistence = None

    # Config
    if auto_approve_severities is None:
        auto_approve_severities = set(settings.drift_auto_approve_severities.split(","))

    if max_retries is None:
        max_retries = settings.drift_max_retries

    return AgentController(
        judge=judge,
        planner=planner,
        executor=executor,
        verifier=verifier,
        persistence=persistence,
        auto_approve_severities=auto_approve_severities,
        max_retries=max_retries,
    )
