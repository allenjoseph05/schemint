"""Phase 5: Execution Layer — deterministic, auditable plan execution.

Core invariant: NO LLM reasoning. All execution is deterministic.

Executes an ExecutionPlan safely using approved tool adapters.
Steps execute sequentially. On failure, execution halts immediately.
All results are recorded in an immutable ExecutionReport.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from schemint.drift.action_templates import validate_action_id
from schemint.drift.models import (
    ExecutionPlan,
    ExecutionReport,
    ExecutionResult,
    PlanStep,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Adapter Interface
# =============================================================================


class ToolAdapter(ABC):
    """Base class for all execution tool adapters.

    Every adapter must:
    - Be deterministic (no LLM calls)
    - Return structured ExecutionResult
    - Never throw uncaught exceptions
    - Log all actions for audit
    """

    @abstractmethod
    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a single plan step and return the result."""

    @abstractmethod
    def supports_action(self, action_id: str) -> bool:
        """Whether this adapter handles the given action_id."""


# =============================================================================
# Concrete Tool Adapters
# =============================================================================


class NotificationService(ToolAdapter):
    """Handles notification-only actions (no schema mutation).

    Covers: notify_table_owner, notify_downstream_teams,
    create_review_ticket, update_downstream_query,
    update_downstream_model, regenerate_api_contract,
    add_monitoring_alert, log_drift_event.
    """

    _SUPPORTED_ACTIONS = {
        "notify_table_owner",
        "notify_downstream_teams",
        "create_review_ticket",
        "update_downstream_query",
        "update_downstream_model",
        "regenerate_api_contract",
        "add_monitoring_alert",
        "log_drift_event",
    }

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a notification action.

        In production, this would integrate with Slack, Jira, PagerDuty, etc.
        Currently logs the notification for audit trail.
        """
        try:
            logger.info(
                "NOTIFICATION [%s]: target=%s notes=%s",
                step.action,
                step.target,
                step.notes,
            )
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="success",
                reversible=True,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(e),
                reversible=True,
                executed_at=datetime.now(timezone.utc),
            )


class SQLRunner(ToolAdapter):
    """Handles SQL-based structural actions.

    Covers: add_column_alias, add_default_value, create_migration_view.
    """

    _SUPPORTED_ACTIONS = {
        "add_column_alias",
        "add_default_value",
        "create_migration_view",
    }

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a SQL structural action.

        In production, this would generate and run deterministic SQL.
        No dynamic SQL generation — actions are template-based only.
        Currently logs the action for audit trail.
        """
        try:
            logger.info(
                "SQL_ACTION [%s]: target=%s notes=%s",
                step.action,
                step.target,
                step.notes,
            )
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="success",
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(e),
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )


class CIPipelineRunner(ToolAdapter):
    """Handles CI/deployment enforcement actions.

    Covers: block_deploy, require_migration_review.
    """

    _SUPPORTED_ACTIONS = {
        "block_deploy",
        "require_migration_review",
    }

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a CI enforcement action.

        In production, this would set CI status checks, create GitHub
        required reviews, or gate deployment pipelines.
        Currently logs the action for audit trail.
        """
        if not step.reversible:
            logger.warning(
                "IRREVERSIBLE CI_ACTION [%s]: target=%s — increases rollback risk",
                step.action,
                step.target,
            )

        try:
            logger.info(
                "CI_ACTION [%s]: target=%s notes=%s",
                step.action,
                step.target,
                step.notes,
            )
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="success",
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(e),
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )


class DBTRunner(ToolAdapter):
    """Handles dbt-related actions.

    Currently no actions map directly to dbt, but this adapter is
    ready for future dbt-specific remediation steps.
    """

    _SUPPORTED_ACTIONS: set[str] = set()

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a dbt action."""
        try:
            logger.info(
                "DBT_ACTION [%s]: target=%s notes=%s",
                step.action,
                step.target,
                step.notes,
            )
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="success",
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(e),
                reversible=step.reversible,
                executed_at=datetime.now(timezone.utc),
            )


# =============================================================================
# Execution Engine
# =============================================================================


class ExecutionEngine:
    """Deterministic execution engine for schema drift remediation plans.

    Invariants:
    - No execution without approval (if requires_execution_approval)
    - Steps execute sequentially
    - On failure: stop immediately, mark failed, compute rollback need
    - Every step must be in the approved action registry
    - No dynamic SQL generation
    - No LLM calls
    - All results are auditable
    """

    def __init__(
        self,
        adapters: list[ToolAdapter] | None = None,
    ) -> None:
        self.adapters = adapters or [
            NotificationService(),
            SQLRunner(),
            CIPipelineRunner(),
            DBTRunner(),
        ]

    def execute(self, plan: ExecutionPlan) -> ExecutionReport:
        """Execute a plan and return an immutable report.

        If requires_execution_approval is True, returns pending_approval
        without executing any steps.
        """
        execution_id = self._generate_execution_id()

        # Gate: no execution without approval
        if plan.requires_execution_approval:
            logger.info(
                "Execution %s: plan requires approval — marking pending",
                execution_id,
            )
            return ExecutionReport(
                execution_id=execution_id,
                overall_status="pending_approval",
                step_results=[],
                requires_rollback=False,
                executed_at=datetime.now(timezone.utc),
            )

        # Validate all actions before executing any
        for step in plan.plan:
            if not validate_action_id(step.action):
                logger.error(
                    "Execution %s: unknown action '%s' in step %d — aborting",
                    execution_id,
                    step.action,
                    step.step,
                )
                return ExecutionReport(
                    execution_id=execution_id,
                    overall_status="failed",
                    step_results=[
                        ExecutionResult(
                            step=step.step,
                            action=step.action,
                            status="failed",
                            error_message=f"Unknown action: {step.action}",
                            reversible=step.reversible,
                        )
                    ],
                    requires_rollback=False,
                    executed_at=datetime.now(timezone.utc),
                )

        # Execute steps sequentially
        results: list[ExecutionResult] = []
        failed = False

        for step in plan.plan:
            if failed:
                # Skip remaining steps after failure
                results.append(
                    ExecutionResult(
                        step=step.step,
                        action=step.action,
                        status="skipped",
                        error_message="Skipped due to prior step failure",
                        reversible=step.reversible,
                    )
                )
                continue

            # Log irreversible step warning
            if not step.reversible:
                logger.warning(
                    "Execution %s step %d: action '%s' is NOT reversible",
                    execution_id,
                    step.step,
                    step.action,
                )

            # Find adapter and execute
            result = self._execute_step(step)
            results.append(result)

            if result.status == "failed":
                failed = True
                logger.error(
                    "Execution %s step %d FAILED: %s — halting execution",
                    execution_id,
                    step.step,
                    result.error_message,
                )

        # Compute overall status and rollback need
        overall_status = self._compute_overall_status(results)
        requires_rollback = self._compute_rollback_need(results, failed)

        report = ExecutionReport(
            execution_id=execution_id,
            overall_status=overall_status,
            step_results=results,
            requires_rollback=requires_rollback,
            executed_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Execution %s complete: status=%s rollback=%s",
            execution_id,
            overall_status,
            requires_rollback,
        )

        return report

    def _execute_step(self, step: PlanStep) -> ExecutionResult:
        """Find the appropriate adapter and execute a step."""
        for adapter in self.adapters:
            if adapter.supports_action(step.action):
                return adapter.execute(step)

        # No adapter found — should not happen if action validation passed
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status="failed",
            error_message=f"No adapter found for action: {step.action}",
            reversible=step.reversible,
        )

    def _compute_overall_status(
        self, results: list[ExecutionResult]
    ) -> str:
        """Compute overall execution status from step results."""
        if not results:
            return "success"

        statuses = {r.status for r in results}
        if statuses == {"success"}:
            return "success"
        if "failed" in statuses:
            if "success" in statuses:
                return "partial_failure"
            return "failed"
        return "success"

    def _compute_rollback_need(
        self, results: list[ExecutionResult], had_failure: bool
    ) -> bool:
        """Determine if rollback is needed.

        Rollback is needed if any previously successful step was
        reversible and a subsequent step failed.
        """
        if not had_failure:
            return False

        # Check if any successful step before the failure was reversible
        for result in results:
            if result.status == "success" and result.reversible:
                return True
            if result.status == "failed":
                break

        return False

    def _generate_execution_id(self) -> str:
        """Generate a unique execution ID."""
        now = datetime.now(timezone.utc)
        return f"exec_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
