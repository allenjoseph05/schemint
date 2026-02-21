"""Tests for the real tool adapters (webhook + GitHub CI)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from schemint.drift.adapters import (
    GitHubCIAdapter,
    WebhookNotificationAdapter,
    build_adapters,
)
from schemint.drift.models import NotificationConfig, PlanStep

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def notify_step() -> PlanStep:
    return PlanStep(
        step=1,
        action="notify_table_owner",
        target="users",
        notes="Test notification",
        reversible=True,
    )


@pytest.fixture
def block_step() -> PlanStep:
    return PlanStep(
        step=1,
        action="block_deploy",
        target="users",
        notes="Deploy block",
        reversible=False,
    )


@pytest.fixture
def webhook_config() -> NotificationConfig:
    return NotificationConfig(
        webhook_url="https://hooks.example.com/drift",
        webhook_headers={"X-Api-Key": "test-key"},
    )


@pytest.fixture
def github_config() -> NotificationConfig:
    return NotificationConfig(
        github_repo="org/repo",
        github_token="ghp_test123",
        github_commit_sha="abc123",
        github_pr_number=42,
    )


# =============================================================================
# WebhookNotificationAdapter Tests
# =============================================================================


class TestWebhookNotificationAdapter:
    def test_supports_notification_actions(self) -> None:
        adapter = WebhookNotificationAdapter()
        assert adapter.supports_action("notify_table_owner")
        assert adapter.supports_action("notify_downstream_teams")
        assert adapter.supports_action("log_drift_event")
        assert not adapter.supports_action("block_deploy")

    def test_console_fallback_without_config(self, notify_step: PlanStep) -> None:
        adapter = WebhookNotificationAdapter()
        result = adapter.execute(notify_step)
        assert result.status == "success"
        assert result.action == "notify_table_owner"

    @patch("schemint.drift.adapters.HTTPX_AVAILABLE", True)
    def test_webhook_post_success(
        self, notify_step: PlanStep, webhook_config: NotificationConfig
    ) -> None:
        adapter = WebhookNotificationAdapter(config=webhook_config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("schemint.drift.adapters.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            result = adapter.execute(notify_step)

        assert result.status == "success"
        mock_client.post.assert_called_once()

    @patch("schemint.drift.adapters.HTTPX_AVAILABLE", True)
    def test_webhook_failure_non_fatal(
        self, notify_step: PlanStep, webhook_config: NotificationConfig
    ) -> None:
        adapter = WebhookNotificationAdapter(config=webhook_config)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = ConnectionError("timeout")

        with patch("schemint.drift.adapters.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            result = adapter.execute(notify_step)

        # Non-fatal — still returns success
        assert result.status == "success"


# =============================================================================
# GitHubCIAdapter Tests
# =============================================================================


class TestGitHubCIAdapter:
    def test_supports_ci_actions(self) -> None:
        adapter = GitHubCIAdapter()
        assert adapter.supports_action("block_deploy")
        assert adapter.supports_action("require_migration_review")
        assert not adapter.supports_action("notify_table_owner")

    def test_console_fallback_without_config(self, block_step: PlanStep) -> None:
        adapter = GitHubCIAdapter()
        result = adapter.execute(block_step)
        assert result.status == "success"

    @patch("schemint.drift.adapters.HTTPX_AVAILABLE", True)
    def test_github_check_run(
        self, block_step: PlanStep, github_config: NotificationConfig
    ) -> None:
        adapter = GitHubCIAdapter(config=github_config)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("schemint.drift.adapters.httpx") as mock_httpx:
            mock_httpx.Client.return_value = mock_client
            result = adapter.execute(block_step)

        assert result.status == "success"
        # Should have been called twice: check run + PR comment
        assert mock_client.post.call_count == 2


# =============================================================================
# build_adapters Factory Tests
# =============================================================================


class TestBuildAdapters:
    def test_no_config_returns_mock_adapters(self) -> None:
        adapters = build_adapters()
        adapter_types = [type(a).__name__ for a in adapters]
        assert "NotificationService" in adapter_types
        assert "CIPipelineRunner" in adapter_types

    def test_webhook_config_returns_real_adapters(self) -> None:
        config = NotificationConfig(webhook_url="https://hooks.example.com")
        adapters = build_adapters(config)
        adapter_types = [type(a).__name__ for a in adapters]
        assert "WebhookNotificationAdapter" in adapter_types
        assert "GitHubCIAdapter" in adapter_types

    def test_github_config_returns_real_adapters(self) -> None:
        config = NotificationConfig(github_token="ghp_test")
        adapters = build_adapters(config)
        adapter_types = [type(a).__name__ for a in adapters]
        assert "WebhookNotificationAdapter" in adapter_types
