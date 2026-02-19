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
import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

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

    def verify(self, execution_report: ExecutionReport, **kwargs: object) -> VerificationReport: ...


@runtime_checkable
class PersistenceProtocol(Protocol):
    """Protocol for drift run persistence (DriftStore satisfies this)."""

    def save_drift_run(self, result: DriftRunResult) -> str: ...


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

        try:
            self._run_pipeline(context, result)
        except Exception as e:
            logger.error("Agent controller pipeline error: %s", e)
            self._transition(result, DriftRunStatus.FAILED, reason=str(e))
            result.error = str(e)

        result.completed_at = datetime.now(timezone.utc)
        self._persist(result)
        return result

    def _run_pipeline(self, context: ContextPackage, result: DriftRunResult) -> None:
        """Execute the state machine pipeline."""
        # Phase 3: Judge
        self._transition(result, DriftRunStatus.JUDGING, reason="Starting judgment")
        decision = self.judge.judge(context)
        result.decision = decision

        # Phase 4: Plan
        self._transition(result, DriftRunStatus.PLANNING, reason="Generating plan")
        plan = self.planner.plan(decision, context)
        result.plan = plan

        # Approval gate
        if not self._check_approval(decision, result):
            return  # ESCALATED

        # Override plan's approval flag — controller has already approved
        plan = plan.model_copy(update={"requires_execution_approval": False})
        result.plan = plan

        # Execute + verify loop (with retries)
        retry_count = 0
        while True:
            # Phase 5: Execute
            if self.executor is None:
                self._transition(result, DriftRunStatus.FAILED, reason="No executor configured")
                result.error = "No executor configured"
                return

            self._transition(result, DriftRunStatus.EXECUTING, reason="Executing plan")
            exec_report = self.executor.execute(plan)
            result.execution_report = exec_report

            # Phase 6: Verify
            if self.verifier is None:
                # No verifier — treat execution success as completion
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
            verification = self.verifier.verify(
                execution_report=exec_report,
                source_requires_human_review=decision.requires_human_review,
            )
            result.verification_report = verification

            # Goal satisfied → COMPLETE
            if verification.goal_satisfied:
                self._transition(result, DriftRunStatus.COMPLETE, reason="Goal satisfied")
                return

            # Rollback or escalation needed → ESCALATED
            if verification.requires_rollback or verification.requires_human_escalation:
                self._transition(
                    result,
                    DriftRunStatus.ESCALATED,
                    reason="Rollback or escalation required",
                )
                return

            # Retry logic: goal not satisfied, no rollback needed
            retry_count += 1
            result.retry_count = retry_count

            if retry_count > self.max_retries:
                self._transition(
                    result,
                    DriftRunStatus.ESCALATED,
                    reason=f"Max retries ({self.max_retries}) exhausted",
                )
                return

            # Re-plan and retry
            self._transition(
                result,
                DriftRunStatus.RETRYING,
                reason=f"Retry {retry_count}/{self.max_retries}",
            )
            plan = self.planner.plan(decision, context)
            plan = plan.model_copy(update={"requires_execution_approval": False})
            result.plan = plan

    def _check_approval(self, decision: AgentDecision, result: DriftRunResult) -> bool:
        """Check if the decision severity is auto-approvable.

        Returns True if approved, False if escalated.
        """
        if decision.severity in self.auto_approve_severities:
            return True

        # Non-auto-approvable → escalate
        self._transition(
            result,
            DriftRunStatus.AWAITING_APPROVAL,
            reason=f"Severity '{decision.severity}' requires approval",
        )
        self._transition(
            result,
            DriftRunStatus.ESCALATED,
            reason=f"Auto-escalated: severity '{decision.severity}' not in auto-approve set",
        )
        return False

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


def _notification_config_from_settings() -> NotificationConfig | None:
    """Build NotificationConfig from environment settings.

    Returns None if no webhook_url or github_token is configured.
    """
    import contextlib
    import json as _json

    from schemint.config import get_settings

    settings = get_settings()

    if not settings.webhook_url and not settings.github_token:
        return None

    webhook_headers: dict[str, str] = {}
    with contextlib.suppress(ValueError, TypeError):
        webhook_headers = _json.loads(settings.notification_webhook_headers)

    return NotificationConfig(
        webhook_url=settings.webhook_url,
        webhook_headers=webhook_headers,
        github_repo=settings.github_repo,
        github_token=settings.github_token,
    )


def build_agent_controller(
    notification_config: NotificationConfig | None = None,
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

    # Phase 5: Executor — use per-request config, fall back to env settings
    from schemint.drift.adapters import build_adapters
    from schemint.drift.execution_engine import ExecutionEngine

    effective_config = notification_config or _notification_config_from_settings()
    adapters = build_adapters(effective_config)
    executor = ExecutionEngine(adapters=adapters)

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
