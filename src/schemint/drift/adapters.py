"""Real tool adapters for the drift execution engine.

Replaces mock/logging-only adapters with real webhook and GitHub
integrations. Falls back to console logging when no config is provided.

Design constraints:
    - Notification failure is NEVER fatal — a webhook timeout must not
      block the pipeline.
    - All adapters extend the existing ToolAdapter ABC from execution_engine.py.
    - build_adapters() returns real adapters when config is present,
      original mock adapters when absent (backward compatible).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, ClassVar

from schemint.drift.execution_engine import ToolAdapter
from schemint.drift.models import ExecutionResult, NotificationConfig, PlanStep

logger = logging.getLogger(__name__)

# Try to import httpx for webhook calls
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False


# =============================================================================
# Webhook Notification Adapter
# =============================================================================


class WebhookNotificationAdapter(ToolAdapter):
    """Posts JSON notifications to a configured webhook URL.

    Falls back to console logging if no URL is configured.
    Notification failure is non-fatal — always returns success.
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
    }

    def __init__(self, config: NotificationConfig | None = None) -> None:
        self.config = config or NotificationConfig()

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a notification action via webhook or console fallback."""
        payload = {
            "action": step.action,
            "target": step.target,
            "notes": step.notes,
            "step": step.step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self.config.webhook_url and HTTPX_AVAILABLE:
            try:
                self._post_webhook(payload)
            except Exception as e:
                # Non-fatal — log and continue
                logger.warning(
                    "Webhook notification failed (non-fatal): %s",
                    e,
                )
        else:
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

    def _post_webhook(self, payload: dict[str, Any]) -> None:
        """POST JSON to the configured webhook URL."""
        headers = {"Content-Type": "application/json"}
        headers.update(self.config.webhook_headers)

        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                self.config.webhook_url,  # type: ignore[arg-type]
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            logger.info("Webhook POST to %s: %d", self.config.webhook_url, response.status_code)


# =============================================================================
# GitHub CI Adapter
# =============================================================================


class GitHubCIAdapter(ToolAdapter):
    """Creates GitHub check runs and PR comments for CI enforcement.

    Falls back to console logging if no GitHub config is provided.
    Notification failure is non-fatal.
    """

    _SUPPORTED_ACTIONS: ClassVar[set[str]] = {
        "block_deploy",
        "require_migration_review",
    }

    def __init__(self, config: NotificationConfig | None = None) -> None:
        self.config = config or NotificationConfig()

    def supports_action(self, action_id: str) -> bool:
        return action_id in self._SUPPORTED_ACTIONS

    def execute(self, step: PlanStep) -> ExecutionResult:
        """Execute a CI enforcement action via GitHub API or console fallback."""
        if self._has_github_config() and HTTPX_AVAILABLE:
            try:
                self._execute_github_action(step)
            except Exception as e:
                logger.warning(
                    "GitHub CI action failed (non-fatal): %s",
                    e,
                )
        else:
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

    def _has_github_config(self) -> bool:
        """Check if GitHub configuration is available."""
        return bool(
            self.config.github_token and self.config.github_repo and self.config.github_commit_sha
        )

    def _execute_github_action(self, step: PlanStep) -> None:
        """Execute a GitHub CI action (check run or PR comment)."""
        if step.action == "block_deploy":
            self._create_check_run(
                name="schemint/drift-block",
                conclusion="failure",
                title="Deploy Blocked — Schema Drift Detected",
                summary=f"Table: {step.target}\n{step.notes}",
            )
        elif step.action == "require_migration_review":
            self._create_check_run(
                name="schemint/migration-review",
                conclusion="action_required",
                title="Migration Review Required",
                summary=f"Table: {step.target}\n{step.notes}",
            )

        # Post PR comment if PR number is available
        if self.config.github_pr_number:
            self._post_pr_comment(step)

    def _create_check_run(
        self,
        name: str,
        conclusion: str,
        title: str,
        summary: str,
    ) -> None:
        """Create a GitHub check run via the API."""
        url = f"https://api.github.com/repos/{self.config.github_repo}/check-runs"
        headers = {
            "Authorization": f"token {self.config.github_token}",
            "Accept": "application/vnd.github+json",
        }
        payload = {
            "name": name,
            "head_sha": self.config.github_commit_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": title,
                "summary": summary,
            },
        }

        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info("GitHub check run created: %s (%s)", name, conclusion)

    def _post_pr_comment(self, step: PlanStep) -> None:
        """Post a comment on the associated PR."""
        url = (
            f"https://api.github.com/repos/{self.config.github_repo}"
            f"/issues/{self.config.github_pr_number}/comments"
        )
        headers = {
            "Authorization": f"token {self.config.github_token}",
            "Accept": "application/vnd.github+json",
        }
        body = (
            f"**Schemint Drift Alert** — `{step.action}`\n\n"
            f"**Target:** `{step.target}`\n"
            f"**Details:** {step.notes}"
        )

        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json={"body": body}, headers=headers)
            response.raise_for_status()
            logger.info("GitHub PR comment posted on PR #%d", self.config.github_pr_number)


# =============================================================================
# Factory
# =============================================================================


def build_adapters(config: NotificationConfig | None = None) -> list[ToolAdapter]:
    """Build tool adapters with real integrations when config is present.

    Returns:
        Real webhook + GitHub adapters if config is provided.
        Original mock adapters (from execution_engine) if no config.
    """
    if config and (config.webhook_url or config.github_token):
        from schemint.drift.execution_engine import DBTRunner, SQLRunner

        return [
            WebhookNotificationAdapter(config),
            SQLRunner(),
            GitHubCIAdapter(config),
            DBTRunner(),
        ]

    # Backward compatible — return original mock adapters
    from schemint.drift.execution_engine import (
        CIPipelineRunner,
        DBTRunner,
        NotificationService,
        SQLRunner,
    )

    return [
        NotificationService(),
        SQLRunner(),
        CIPipelineRunner(),
        DBTRunner(),
    ]
