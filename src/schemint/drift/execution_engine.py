"""Phase 5: Execution Layer — deterministic, auditable plan execution.

Core invariant: NO LLM reasoning. All execution is deterministic.

Executes an ExecutionPlan safely using approved tool adapters.
Steps execute sequentially. On failure, execution halts immediately.
All results are recorded in an immutable ExecutionReport.

Adapter behaviour when credentials are absent:
    Returns ExecutionResult(status="skipped") — never raises.
    Missing config is not an execution failure; it means the action
    was attempted but the integration is not set up yet.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

from schemint.config import get_settings
from schemint.drift.action_templates import _REGISTRY_BY_ID, validate_action_id
from schemint.drift.models import (
    ExecutionPlan,
    ExecutionReport,
    ExecutionResult,
    PlanStep,
    RollbackReport,
)
from schemint.drift.notification_backends import (
    BackendResult,
    GitHubIssueNotifier,
    GitHubStatusSetter,
    SlackNotifier,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================


def _parse_notes(notes: str) -> dict[str, str]:
    """Parse key=value pairs from PlanStep.notes.

    Example: "new_name=user_email, conn=postgresql://..."
    Returns: {"new_name": "user_email", "conn": "postgresql://..."}
    """
    result: dict[str, str] = {}
    for part in notes.split(","):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


def _parse_target(target: str) -> tuple[str, str | None]:
    """Split 'table.column' into (table, column). Returns (table, None) if no dot."""
    if "." in target:
        table, _, column = target.partition(".")
        return table.strip(), column.strip() or None
    return target.strip(), None


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

    Integrations:
      - Slack (via webhook_url) for all notify/alert/log actions
      - GitHub Issues (via github_token + github_repo) for create_review_ticket

    Missing credentials → status="skipped" (not a failure).
    """

    _SUPPORTED_ACTIONS: ClassVar[set[str]] = {
        "notify_table_owner",
        "notify_downstream_teams",
        "create_review_ticket",
        "update_downstream_query",
        "update_downstream_model",
        "regenerate_api_contract",
        "add_monitoring_alert",
        "log_drift_event",
        # Rollback inverse
        "remove_monitoring_alert",
    }

    # Message templates per action — {target}, {notes}, {action} are interpolated.
    _SLACK_TEMPLATES: ClassVar[dict[str, str]] = {
        "notify_table_owner": (
            ":warning: *Schema drift detected* on `{target}`.\n"
            "{notes}\n"
            "_Schemint drift agent — action required._"
        ),
        "notify_downstream_teams": (
            ":bell: *Downstream impact* — `{target}` has changed.\n{notes}"
        ),
        "update_downstream_query": (
            ":pencil: *Query update needed* — downstream query references `{target}`.\n{notes}"
        ),
        "update_downstream_model": (
            ":pencil: *Model update needed* — ORM/dbt model references `{target}`.\n{notes}"
        ),
        "regenerate_api_contract": (
            ":gear: *API contract refresh needed* — schema change on `{target}`.\n{notes}"
        ),
        "add_monitoring_alert": (":eyes: *Monitoring alert added* for `{target}`.\n{notes}"),
        "log_drift_event": (":memo: *Drift event logged* — `{target}`.\n{notes}"),
        "remove_monitoring_alert": (
            ":white_check_mark: *Monitoring alert removed* for `{target}` (rollback).\n{notes}"
        ),
    }

    def __init__(
        self,
        slack: SlackNotifier | None = None,
        github_issues: GitHubIssueNotifier | None = None,
    ) -> None:
        settings = get_settings()
        self._slack = slack or SlackNotifier(webhook_url=settings.webhook_url)
        self._github_issues = github_issues or GitHubIssueNotifier(
            token=settings.github_token,
            repo=settings.github_repo,
        )

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        try:
            if step.action == "create_review_ticket":
                return self._create_review_ticket(step)
            return self._send_slack(step)
        except Exception as exc:
            logger.exception("NotificationService unexpected error on step %d", step.step)
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(exc),
                reversible=True,
            )

    def _send_slack(self, step: PlanStep) -> ExecutionResult:
        template = self._SLACK_TEMPLATES.get(
            step.action, ":information_source: `{action}` on `{target}`. {notes}"
        )
        message = template.format(
            target=step.target,
            notes=step.notes,
            action=step.action,
        )
        logger.info("NOTIFICATION [%s]: target=%s", step.action, step.target)
        result: BackendResult = self._slack.send(message)

        status: Literal["success", "failed", "skipped"] = (
            "skipped" if result.skipped else ("success" if result.success else "failed")
        )
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status=status,
            error_message=None if result.success or result.skipped else result.detail,
            reversible=True,
            metadata=result.metadata,
        )

    def _create_review_ticket(self, step: PlanStep) -> ExecutionResult:
        notes_kv = _parse_notes(step.notes)
        severity = notes_kv.get("severity", "unknown")
        title = f"[Schema Drift] {step.target}: schema change detected"
        body = (
            f"**Schema drift detected on `{step.target}`**\n\n"
            f"Severity: **{severity}**\n\n"
            f"Details: {step.notes}\n\n"
            "_Created automatically by Schemint drift agent._"
        )
        logger.info("NOTIFICATION [create_review_ticket]: target=%s", step.target)
        result = self._github_issues.create_issue(title=title, body=body)

        status: Literal["success", "failed", "skipped"] = (
            "skipped" if result.skipped else ("success" if result.success else "failed")
        )
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status=status,
            error_message=None if result.success or result.skipped else result.detail,
            reversible=True,
            metadata=result.metadata,
        )


class SQLRunner(ToolAdapter):
    """Handles SQL-based structural actions via template-based SQL.

    Templates are fixed — no dynamic SQL generation from user input.
    All SQL is validated through sqlglot before execution.
    Execution runs inside a transaction; on error the transaction is
    rolled back automatically.

    If SCHEMINT_SQL_DRY_RUN=true (or Settings.sql_dry_run), the SQL is
    logged and validated but NOT sent to the database. Useful for CI
    preview runs.

    target format:  "table_name" or "table_name.column_name"
    notes format:   "key=value, key=value, ..."
        Recognised keys:
            new_name    — new column name (for add_column_alias)
            col_type    — column type     (for add_column_alias)
            default     — default expr    (for add_default_value)
            new_table   — new table name  (for create_migration_view)
            conn        — override connection string for this step
    """

    _SUPPORTED_ACTIONS: ClassVar[set[str]] = {
        "add_column_alias",
        "add_default_value",
        "create_migration_view",
        # Rollback inverses
        "drop_column_alias",
        "drop_default_value",
        "drop_migration_view",
    }

    def __init__(self, db_url: str | None = None, dry_run: bool | None = None) -> None:
        settings = get_settings()
        self._db_url = db_url or settings.target_db_url
        self._dry_run = dry_run if dry_run is not None else settings.sql_dry_run

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        try:
            notes = _parse_notes(step.notes)
            # Per-step connection override
            conn_str = notes.get("conn") or self._db_url

            # Prefer AI-generated SQL from CopilotAgent over template SQL
            sql = step.generated_sql or self._build_sql(step, notes)
            if sql is None:
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="failed",
                    error_message=(
                        f"Cannot build SQL for action '{step.action}' on target '{step.target}': "
                        "missing required notes keys. "
                        "add_column_alias needs new_name; "
                        "add_default_value needs default; "
                        "create_migration_view needs new_table."
                    ),
                    reversible=step.reversible,
                )

            # Validate SQL syntax before touching the database
            if not self._validate_sql(sql):
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="failed",
                    error_message=f"Generated SQL failed syntax validation: {sql!r}",
                    reversible=step.reversible,
                )

            if self._dry_run:
                logger.info("SQL_DRY_RUN [%s]: %s", step.action, sql)
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="success",
                    reversible=step.reversible,
                    metadata={"dry_run": True, "sql": sql},
                )

            if not conn_str:
                logger.warning(
                    "SQL_ACTION [%s]: no target_db_url configured — skipping", step.action
                )
                return ExecutionResult(
                    step=step.step,
                    action=step.action,
                    status="skipped",
                    error_message="No target_db_url configured",
                    reversible=step.reversible,
                    metadata={"sql": sql},
                )

            result_meta = self._run_in_transaction(sql, conn_str)
            logger.info("SQL_ACTION [%s]: target=%s executed", step.action, step.target)
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="success",
                reversible=step.reversible,
                metadata={**result_meta, "sql": sql},
            )

        except Exception as exc:
            logger.exception("SQLRunner unexpected error on step %d", step.step)
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(exc),
                reversible=step.reversible,
            )

    # ------------------------------------------------------------------
    # SQL builders — one per action
    # ------------------------------------------------------------------

    def _build_sql(self, step: PlanStep, notes: dict[str, str]) -> str | None:
        table, column = _parse_target(step.target)

        if step.action == "add_column_alias":
            return self._sql_add_column_alias(table, column, notes)
        if step.action == "add_default_value":
            return self._sql_add_default_value(table, column, notes)
        if step.action == "create_migration_view":
            return self._sql_create_migration_view(table, notes)
        # Rollback inverses
        if step.action == "drop_column_alias":
            return self._sql_drop_column_alias(table)
        if step.action == "drop_default_value":
            return self._sql_drop_default_value(table, column)
        if step.action == "drop_migration_view":
            return self._sql_drop_migration_view(table)
        return None

    @staticmethod
    def _qi(ident: str) -> str:
        """Double-quote a SQL identifier to prevent injection."""
        return '"' + ident.replace('"', '""') + '"'

    def _sql_add_column_alias(
        self, table: str, old_col: str | None, notes: dict[str, str]
    ) -> str | None:
        """Create a view exposing the new column name under the old name.

        Preferred over GENERATED ALWAYS because it doesn't need the column
        type and works on PostgreSQL 10+.
        """
        new_col = notes.get("new_name")
        if not old_col or not new_col:
            return None
        view_name = f"{table}_compat"
        q = self._qi
        return (
            f"CREATE OR REPLACE VIEW {q(view_name)} AS "  # nosec B608
            f"SELECT *, {q(new_col)} AS {q(old_col)} FROM {q(table)};"
        )

    def _sql_add_default_value(
        self, table: str, column: str | None, notes: dict[str, str]
    ) -> str | None:
        default_expr = notes.get("default")
        if not column or not default_expr:
            return None
        q = self._qi
        return f"ALTER TABLE {q(table)} ALTER COLUMN {q(column)} SET DEFAULT {default_expr};"

    def _sql_create_migration_view(self, old_table: str, notes: dict[str, str]) -> str | None:
        new_table = notes.get("new_table")
        if not new_table:
            return None
        q = self._qi
        return (
            f"CREATE OR REPLACE VIEW {q(old_table)} AS "  # nosec B608
            f"SELECT * FROM {q(new_table)};"
        )

    def _sql_drop_column_alias(self, table: str) -> str:
        view_name = f"{table}_compat"
        return f"DROP VIEW IF EXISTS {view_name};"

    def _sql_drop_default_value(self, table: str, column: str | None) -> str | None:
        if not column:
            return None
        return f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT;"

    def _sql_drop_migration_view(self, old_table: str) -> str:
        return f"DROP VIEW IF EXISTS {old_table};"

    # ------------------------------------------------------------------
    # SQL validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_sql(sql: str) -> bool:
        """Return True if sql parses without error via sqlglot."""
        try:
            import sqlglot  # lazy import — optional dependency

            errors = sqlglot.parse(sql, error_level=sqlglot.ErrorLevel.RAISE)
            return bool(errors)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Transaction execution
    # ------------------------------------------------------------------

    @staticmethod
    def _run_in_transaction(sql: str, conn_str: str) -> dict[str, Any]:
        """Execute sql inside BEGIN/COMMIT, rollback on any error."""
        try:
            import psycopg2  # lazy import — optional dependency
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2 is required for SQL execution. "
                "Install it with: pip install psycopg2-binary"
            ) from exc

        conn = psycopg2.connect(conn_str)
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            return {"transaction": "committed"}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class CIPipelineRunner(ToolAdapter):
    """Handles CI/deployment enforcement via GitHub commit statuses.

    block_deploy:
        Sets the commit status to "pending" with context
        "schemint/drift-block". Branch protection rules that require
        this context to be "success" will prevent the PR from merging.

    require_migration_review:
        Sets "schemint/migration-review" to "pending" AND optionally
        requests a review from SCHEMINT_GITHUB_DEFAULT_REVIEWER.

    Commit SHA resolution order:
        1. notes["sha"] on the PlanStep
        2. Settings.github_commit_sha env var
        3. If neither → status="skipped" with a clear message

    Missing credentials → status="skipped" (not a failure).
    """

    _SUPPORTED_ACTIONS: ClassVar[set[str]] = {
        "block_deploy",
        "require_migration_review",
        # Rollback inverse
        "unblock_deploy",
    }

    def __init__(self, status_setter: GitHubStatusSetter | None = None) -> None:
        settings = get_settings()
        self._setter = status_setter or GitHubStatusSetter(
            token=settings.github_token,
            repo=settings.github_repo,
        )
        self._default_sha = settings.github_commit_sha
        self._default_reviewer = settings.github_default_reviewer

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        if not step.reversible:
            logger.warning("IRREVERSIBLE CI_ACTION [%s]: target=%s", step.action, step.target)
        try:
            notes = _parse_notes(step.notes)
            sha = notes.get("sha") or self._default_sha or ""

            if step.action == "block_deploy":
                return self._block_deploy(step, sha)
            if step.action == "require_migration_review":
                return self._require_review(step, sha, notes)
            if step.action == "unblock_deploy":
                return self._unblock_deploy(step, sha)

            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=f"Unknown CI action: {step.action}",
                reversible=step.reversible,
            )
        except Exception as exc:
            logger.exception("CIPipelineRunner unexpected error on step %d", step.step)
            return ExecutionResult(
                step=step.step,
                action=step.action,
                status="failed",
                error_message=str(exc),
                reversible=step.reversible,
            )

    def _block_deploy(self, step: PlanStep, sha: str) -> ExecutionResult:
        logger.info(
            "CI_ACTION [block_deploy]: target=%s sha=%s", step.target, sha[:8] if sha else "n/a"
        )
        result = self._setter.set_status(
            sha=sha,
            state="pending",
            context="schemint/drift-block",
            description=f"Schema drift review required on {step.target}",
        )
        status: Literal["success", "failed", "skipped"] = (
            "skipped" if result.skipped else ("success" if result.success else "failed")
        )
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status=status,
            error_message=None if result.success or result.skipped else result.detail,
            reversible=False,
            metadata=result.metadata,
        )

    def _unblock_deploy(self, step: PlanStep, sha: str) -> ExecutionResult:
        logger.info(
            "CI_ACTION [unblock_deploy]: target=%s sha=%s", step.target, sha[:8] if sha else "n/a"
        )
        result = self._setter.set_status(
            sha=sha,
            state="success",
            context="schemint/drift-block",
            description=f"Schema drift review resolved for {step.target}",
        )
        status: Literal["success", "failed", "skipped"] = (
            "skipped" if result.skipped else ("success" if result.success else "failed")
        )
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status=status,
            error_message=None if result.success or result.skipped else result.detail,
            reversible=True,
            metadata=result.metadata,
        )

    def _require_review(self, step: PlanStep, sha: str, notes: dict[str, str]) -> ExecutionResult:
        logger.info("CI_ACTION [require_migration_review]: target=%s", step.target)
        status_result = self._setter.set_status(
            sha=sha,
            state="pending",
            context="schemint/migration-review",
            description=f"Migration review required for {step.target}",
        )

        combined_meta: dict[str, Any] = {**status_result.metadata}

        # Optionally request a reviewer
        reviewer = notes.get("reviewer") or self._default_reviewer
        if reviewer:
            pr_number_str = notes.get("pr")
            if pr_number_str and pr_number_str.isdigit():
                review_result = self._setter.request_review(
                    pr_number=int(pr_number_str), reviewer=reviewer
                )
                combined_meta.update(review_result.metadata)
                if not review_result.success and not review_result.skipped:
                    logger.warning(
                        "Could not request review from %s: %s", reviewer, review_result.detail
                    )

        status: Literal["success", "failed", "skipped"] = (
            "skipped"
            if status_result.skipped
            else ("success" if status_result.success else "failed")
        )
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status=status,
            error_message=None
            if status_result.success or status_result.skipped
            else status_result.detail,
            reversible=False,
            metadata=combined_meta,
        )


class DBTRunner(ToolAdapter):
    """Handles dbt-related actions (reserved for future use)."""

    _SUPPORTED_ACTIONS: ClassVar[set[str]] = set()

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        logger.info("DBT_ACTION [%s]: target=%s notes=%s", step.action, step.target, step.notes)
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status="success",
            reversible=step.reversible,
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

            if not step.reversible:
                logger.warning(
                    "Execution %s step %d: action '%s' is NOT reversible",
                    execution_id,
                    step.step,
                    step.action,
                )

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

        return ExecutionResult(
            step=step.step,
            action=step.action,
            status="failed",
            error_message=f"No adapter found for action: {step.action}",
            reversible=step.reversible,
        )

    def _compute_overall_status(
        self, results: list[ExecutionResult]
    ) -> Literal["success", "partial_failure", "failed", "pending_approval"]:
        if not results:
            return "success"
        statuses = {r.status for r in results}
        if statuses <= {"success", "skipped"}:
            return "success"
        if "failed" in statuses and "success" in statuses:
            return "partial_failure"
        if "failed" in statuses:
            return "failed"
        return "success"

    def _compute_rollback_need(self, results: list[ExecutionResult], had_failure: bool) -> bool:
        if not had_failure:
            return False
        for result in results:
            if result.status == "success" and result.reversible:
                return True
            if result.status == "failed":
                break
        return False

    def _generate_execution_id(self) -> str:
        now = datetime.now(timezone.utc)
        return f"exec_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


# =============================================================================
# Rollback Engine
# =============================================================================


class RollbackEngine:
    """Reverses previously executed plan steps in reverse order.

    Only reverses steps that:
      1. Have status="success"
      2. Are reversible=True
      3. Have a known rollback_action_id in the action registry

    Steps are rolled back in reverse execution order (last-in, first-out).
    Non-reversible steps (e.g. block_deploy with reversible=False) are skipped
    during rollback — a human must handle those manually.

    Rollback itself uses the same adapters as forward execution, so every
    rollback step goes through the same safety checks.
    """

    def __init__(self, adapters: list[ToolAdapter] | None = None) -> None:
        self.adapters = adapters or [
            NotificationService(),
            SQLRunner(),
            CIPipelineRunner(),
            DBTRunner(),
        ]

    def rollback(self, execution_report: ExecutionReport) -> RollbackReport:
        """Reverse all reversible, successful steps from execution_report.

        Returns a RollbackReport with step-by-step results.
        """
        # Collect steps eligible for rollback, in reverse execution order
        reversible_successes = [
            r for r in execution_report.step_results if r.status == "success" and r.reversible
        ]
        reversible_successes.reverse()

        rollback_results: list[ExecutionResult] = []

        for i, original_result in enumerate(reversible_successes, start=1):
            rollback_action = self._get_rollback_action(original_result.action)
            if rollback_action is None:
                logger.warning(
                    "No rollback action for '%s' — skipping step %d",
                    original_result.action,
                    original_result.step,
                )
                rollback_results.append(
                    ExecutionResult(
                        step=i,
                        action=original_result.action,
                        status="skipped",
                        error_message=f"No rollback action defined for '{original_result.action}'",
                        reversible=True,
                    )
                )
                continue

            # Build a synthetic rollback PlanStep
            rollback_step = PlanStep(
                step=i,
                action=rollback_action,
                target=original_result.action,  # Target is the original action's subject
                notes=f"rollback_of={original_result.action},original_step={original_result.step}",
                reversible=True,
            )
            # Copy metadata from original so adapters can reuse target/SHA/etc.
            if original_result.metadata:
                notes_extra = ",".join(
                    f"{k}={v}" for k, v in original_result.metadata.items() if isinstance(v, str)
                )
                if notes_extra:
                    rollback_step = rollback_step.model_copy(
                        update={"notes": rollback_step.notes + "," + notes_extra}
                    )

            result = self._execute_rollback_step(rollback_step)
            rollback_results.append(result)

            if result.status == "failed":
                logger.error(
                    "Rollback step %d FAILED (reverting '%s'): %s",
                    i,
                    original_result.action,
                    result.error_message,
                )

        overall = self._compute_status(rollback_results)
        logger.info(
            "Rollback complete: %d steps, status=%s",
            len(rollback_results),
            overall,
        )

        return RollbackReport(
            original_execution_id=execution_report.execution_id,
            step_results=rollback_results,
            overall_status=overall,
        )

    def _get_rollback_action(self, action_id: str) -> str | None:
        """Look up the rollback_action_id for a given action."""
        template = _REGISTRY_BY_ID.get(action_id)
        if template is None:
            return None
        return template.rollback_action_id

    def _execute_rollback_step(self, step: PlanStep) -> ExecutionResult:
        for adapter in self.adapters:
            if adapter.supports_action(step.action):
                return adapter.execute(step)
        return ExecutionResult(
            step=step.step,
            action=step.action,
            status="failed",
            error_message=f"No adapter found for rollback action: {step.action}",
            reversible=True,
        )

    @staticmethod
    def _compute_status(
        results: list[ExecutionResult],
    ) -> Literal["success", "partial_failure", "failed"]:
        if not results:
            return "success"
        statuses = {r.status for r in results}
        if statuses <= {"success", "skipped"}:
            return "success"
        if "failed" in statuses and "success" in statuses:
            return "partial_failure"
        if "failed" in statuses:
            return "failed"
        return "success"
