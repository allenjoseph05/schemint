"""Tests for PlanningAgent (Phase 4) — constrained plan generation.

All Claude calls are mocked. Tests verify:
- Human review → notification-only, no Claude call
- Scoped registry (only allowed categories)
- Low severity → no block_deploy/require_migration_review
- Insufficient context → no backward_compat structural actions
- Critical → block_deploy injected
- block_deploy → requires_execution_approval
- Unknown actions filtered, steps renumbered
- Fallback tested
"""

import json
from unittest.mock import MagicMock, patch

from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DependencyCoverage,
    ExecutionPlan,
    ImpactMetrics,
    SchemaChangeEvent,
)
from schemint.drift.planning_agent import PlanningAgent, get_planning_agent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_context(
    criticality: str = "low",
    context_quality: str = "complete",
    table: str = "users",
) -> ContextPackage:
    return ContextPackage(
        schema_change=SchemaChangeEvent(
            change_type="column_added",
            table=table,
            column="email",
        ),
        impact_metrics=ImpactMetrics(criticality=criticality),
        dependency_coverage=DependencyCoverage(coverage_pct=80.0),
        context_quality=context_quality,
    )


def _make_decision(
    severity: str = "medium",
    requires_human: bool = False,
    categories: list[str] | None = None,
    context_quality: str = "complete",
) -> AgentDecision:
    if categories is None:
        categories = ["monitor_only", "notify_owner"]
    return AgentDecision(
        severity=severity,
        confidence_in_decision=0.8,
        requires_human_review=requires_human,
        rationale=["test rationale"],
        recommended_action_categories=categories,
        context_quality=context_quality,
    )


def _mock_claude_plan_response(steps: list[dict]) -> MagicMock:
    """Create a mock Anthropic response with a plan."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps({"plan": steps})
    response = MagicMock()
    response.content = [text_block]
    return response


def _make_agent() -> PlanningAgent:
    """Create a PlanningAgent with mocked settings."""
    with (
        patch("schemint.drift.planning_agent.anthropic"),
        patch("schemint.drift.planning_agent.CLAUDE_AVAILABLE", True),
        patch("schemint.drift.planning_agent.get_settings") as mock_settings,
    ):
        mock_settings.return_value = MagicMock(
            claude_api_key="test-key",
            claude_model="test-model",
            ai_enabled=True,
        )
        return PlanningAgent()


# ---------------------------------------------------------------------------
# Short-Circuit Tests
# ---------------------------------------------------------------------------


class TestShortCircuit:
    def test_human_review_skips_claude(self):
        """requires_human_review → notification-only, no Claude call."""
        agent = _make_agent()
        decision = _make_decision(requires_human=True)
        context = _make_context()

        result = agent.plan(decision, context)

        assert isinstance(result, ExecutionPlan)
        assert len(result.plan) == 1
        assert result.plan[0].action == "notify_table_owner"
        assert result.source_requires_human_review is True
        # Verify Claude was NOT called
        assert not agent.client.messages.create.called

    def test_human_review_plan_targets_correct_table(self):
        agent = _make_agent()
        decision = _make_decision(requires_human=True)
        context = _make_context(table="orders")

        result = agent.plan(decision, context)

        assert result.plan[0].target == "orders"


# ---------------------------------------------------------------------------
# Scoped Registry Tests
# ---------------------------------------------------------------------------


class TestScopedRegistry:
    def test_only_allowed_categories_used(self):
        """Claude can only use actions from the decision's categories."""
        agent = _make_agent()
        decision = _make_decision(categories=["monitor_only"])
        context = _make_context()

        # Claude returns an action from monitor_only AND one not in scope
        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "add_monitoring_alert", "target": "users", "notes": "ok"},
                {"step": 2, "action": "block_deploy", "target": "users", "notes": "bad"},
            ]
        )

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "add_monitoring_alert" in action_ids
        assert "block_deploy" not in action_ids  # filtered out — not in scope


# ---------------------------------------------------------------------------
# Category-to-Action Restriction Tests
# ---------------------------------------------------------------------------


class TestCategoryRestrictions:
    def test_low_severity_no_block_deploy(self):
        """Low severity → block_deploy and require_migration_review removed."""
        agent = _make_agent()
        # Give it block_deploy category so the action is in scope
        decision = _make_decision(severity="low", categories=["block_deploy", "notify_owner"])
        context = _make_context()

        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "block_deploy", "target": "users"},
                {"step": 2, "action": "require_migration_review", "target": "users"},
                {"step": 3, "action": "notify_table_owner", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "block_deploy" not in action_ids
        assert "require_migration_review" not in action_ids
        assert "notify_table_owner" in action_ids

    def test_insufficient_context_no_backward_compat_structural(self):
        """Insufficient context → no add_column_alias, add_default_value, create_migration_view."""
        agent = _make_agent()
        decision = _make_decision(
            categories=["backward_compatibility", "notify_owner"],
            context_quality="insufficient",
        )
        context = _make_context(context_quality="insufficient")

        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "add_column_alias", "target": "users"},
                {"step": 2, "action": "add_default_value", "target": "users"},
                {"step": 3, "action": "create_migration_view", "target": "users"},
                {"step": 4, "action": "notify_table_owner", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "add_column_alias" not in action_ids
        assert "add_default_value" not in action_ids
        assert "create_migration_view" not in action_ids
        assert "notify_table_owner" in action_ids

    def test_critical_injects_block_deploy(self):
        """Critical severity → block_deploy injected even if LLM omits it."""
        agent = _make_agent()
        decision = _make_decision(
            severity="critical",
            categories=["block_deploy", "notify_owner"],
        )
        context = _make_context(criticality="critical")

        # LLM returns plan WITHOUT block_deploy
        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "notify_table_owner", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "block_deploy" in action_ids

    def test_block_deploy_sets_requires_approval(self):
        """Plan with block_deploy → requires_execution_approval = True."""
        agent = _make_agent()
        decision = _make_decision(
            severity="high",
            categories=["block_deploy", "notify_owner"],
        )
        context = _make_context()

        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "block_deploy", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        assert result.requires_execution_approval is True


# ---------------------------------------------------------------------------
# Post-Processing Tests
# ---------------------------------------------------------------------------


class TestPostProcessing:
    def test_unknown_actions_filtered(self):
        """Actions not in scoped registry are removed."""
        agent = _make_agent()
        decision = _make_decision(categories=["notify_owner"])
        context = _make_context()

        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "notify_table_owner", "target": "users"},
                {"step": 2, "action": "invented_action", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "invented_action" not in action_ids
        assert "notify_table_owner" in action_ids

    def test_steps_renumbered_after_filtering(self):
        """After filtering, steps are renumbered sequentially starting at 1."""
        agent = _make_agent()
        decision = _make_decision(categories=["notify_owner"])
        context = _make_context()

        agent.client.messages.create.return_value = _mock_claude_plan_response(
            [
                {"step": 1, "action": "invented_action", "target": "users"},
                {"step": 2, "action": "notify_table_owner", "target": "users"},
                {"step": 3, "action": "notify_downstream_teams", "target": "users"},
            ]
        )

        result = agent.plan(decision, context)

        for i, step in enumerate(result.plan, 1):
            assert step.step == i


# ---------------------------------------------------------------------------
# Fallback Tests
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_returns_notification_plan(self):
        """Claude failure → fallback notification plan."""
        agent = _make_agent()
        decision = _make_decision(severity="medium")
        context = _make_context()

        agent.client.messages.create.side_effect = RuntimeError("API down")

        result = agent.plan(decision, context)

        assert isinstance(result, ExecutionPlan)
        assert len(result.plan) >= 1
        assert result.plan[0].action == "notify_table_owner"

    def test_fallback_critical_includes_block_deploy(self):
        """Critical severity fallback includes block_deploy."""
        agent = _make_agent()
        decision = _make_decision(severity="critical")
        context = _make_context(criticality="critical")

        agent.client.messages.create.side_effect = RuntimeError("API down")

        result = agent.plan(decision, context)

        action_ids = [s.action for s in result.plan]
        assert "block_deploy" in action_ids
        assert result.requires_execution_approval is True


# ---------------------------------------------------------------------------
# Factory Tests
# ---------------------------------------------------------------------------


class TestFactory:
    @patch("schemint.drift.planning_agent.CLAUDE_AVAILABLE", False)
    def test_factory_returns_none_without_sdk(self):
        with patch("schemint.drift.planning_agent.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ai_enabled=True)
            assert get_planning_agent() is None

    def test_factory_returns_none_without_api_key(self):
        with patch("schemint.drift.planning_agent.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ai_enabled=False)
            assert get_planning_agent() is None
