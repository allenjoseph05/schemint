"""Tests for DriftAgent (Phase 3) — safety-focused severity judgment.

All Claude calls are mocked. Tests verify:
- System prompt includes goal ownership language
- All 10 invariants individually
- Deterministic fallback
- Factory behavior
"""

import json
from unittest.mock import MagicMock, patch

from schemint.drift.agent_brain import (
    DRIFT_AGENT_SYSTEM_PROMPT,
    DriftAgent,
    _sev_index,
    _truncate_categories,
    get_drift_agent,
)
from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DependencyCoverage,
    ImpactMetrics,
    SchemaChangeEvent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_context(
    criticality: str = "low",
    coverage_pct: float = 80.0,
    context_quality: str = "complete",
    change_type: str = "column_added",
    table: str = "users",
) -> ContextPackage:
    return ContextPackage(
        schema_change=SchemaChangeEvent(
            change_type=change_type,
            table=table,
            column="email",
        ),
        impact_metrics=ImpactMetrics(
            downstream_tables=2,
            downstream_columns=4,
            max_depth=1,
            criticality=criticality,
        ),
        dependency_coverage=DependencyCoverage(
            tables_total=10,
            tables_with_lineage=8,
            coverage_pct=coverage_pct,
        ),
        context_quality=context_quality,
    )


def _make_decision(
    severity: str = "medium",
    confidence: float = 0.8,
    requires_human: bool = False,
    categories: list[str] | None = None,
    context_quality: str = "complete",
) -> AgentDecision:
    if categories is None:
        categories = ["monitor_only"]
    return AgentDecision(
        severity=severity,
        confidence_in_decision=confidence,
        requires_human_review=requires_human,
        rationale=["test rationale"],
        recommended_action_categories=categories,
        context_quality=context_quality,
    )


def _mock_claude_response(decision_dict: dict) -> MagicMock:
    """Create a mock Anthropic response containing a JSON decision."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps(decision_dict)
    response = MagicMock()
    response.content = [text_block]
    return response


# ---------------------------------------------------------------------------
# System Prompt Tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_prompt_includes_safety_agent(self):
        assert "SAFETY AGENT" in DRIFT_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_prevent_downstream(self):
        assert "Prevent downstream breakage" in DRIFT_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_minimize_false_positives(self):
        assert "Minimize false positives" in DRIFT_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_escalate_under_uncertainty(self):
        assert "Escalate under uncertainty" in DRIFT_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_preserve_stability(self):
        assert "Preserve deployment stability" in DRIFT_AGENT_SYSTEM_PROMPT

    def test_prompt_includes_risk_mitigation(self):
        assert "RISK MITIGATION QUALITY" in DRIFT_AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Invariant Tests
# ---------------------------------------------------------------------------


class TestInvariantEnforcement:
    """Test all 10 post-AI invariants individually."""

    def _enforce(self, decision: AgentDecision, context: ContextPackage) -> AgentDecision:
        """Helper to call _enforce_invariants directly."""
        agent = object.__new__(DriftAgent)
        severity_floor = agent._compute_severity_floor(context)
        return agent._enforce_invariants(decision, context, severity_floor)

    def test_severity_floor_cannot_be_undercut(self):
        """LLM returns 'low' but criticality is 'medium' → override to 'medium'."""
        context = _make_context(criticality="medium")
        decision = _make_decision(severity="low")
        result = self._enforce(decision, context)
        assert result.severity == "medium"

    def test_severity_floor_allows_escalation(self):
        """LLM returns 'high' with criticality 'low' → keep 'high'."""
        context = _make_context(criticality="low")
        decision = _make_decision(severity="high")
        result = self._enforce(decision, context)
        assert result.severity == "high"

    def test_low_coverage_caps_confidence(self):
        """Coverage < 50% → confidence capped at 0.6."""
        context = _make_context(coverage_pct=30.0)
        decision = _make_decision(confidence=0.9)
        result = self._enforce(decision, context)
        assert result.confidence_in_decision == 0.6

    def test_low_coverage_keeps_lower_confidence(self):
        """Coverage < 50% but confidence already 0.4 → stays at 0.4."""
        context = _make_context(coverage_pct=30.0)
        decision = _make_decision(confidence=0.4)
        result = self._enforce(decision, context)
        assert result.confidence_in_decision == 0.4

    def test_critical_criticality_floor_high(self):
        """Criticality 'critical' → severity ≥ 'high'."""
        context = _make_context(criticality="critical")
        decision = _make_decision(severity="medium")
        result = self._enforce(decision, context)
        assert result.severity in ("high", "critical")

    def test_insufficient_context_enforcements(self):
        """Insufficient context → human review, severity ≥ medium, no backward_compat."""
        context = _make_context(context_quality="insufficient")
        decision = _make_decision(
            severity="low",
            requires_human=False,
            categories=["backward_compatibility"],
            context_quality="insufficient",
        )
        result = self._enforce(decision, context)
        assert result.requires_human_review is True
        assert _sev_index(result.severity) >= _sev_index("medium")
        assert "backward_compatibility" not in result.recommended_action_categories

    def test_low_severity_removes_block_deploy(self):
        """Low severity → no block_deploy."""
        context = _make_context(criticality="low")
        decision = _make_decision(severity="low", categories=["block_deploy"])
        result = self._enforce(decision, context)
        assert "block_deploy" not in result.recommended_action_categories

    def test_human_review_adds_notify_owner(self):
        """requires_human_review → notify_owner added if missing."""
        context = _make_context()
        decision = _make_decision(
            requires_human=True,
            categories=["monitor_only"],
        )
        result = self._enforce(decision, context)
        assert "notify_owner" in result.recommended_action_categories

    def test_low_confidence_triggers_human_review(self):
        """Confidence < 0.5 → requires_human_review = True."""
        context = _make_context()
        decision = _make_decision(confidence=0.3, requires_human=False)
        result = self._enforce(decision, context)
        assert result.requires_human_review is True
        assert "notify_owner" in result.recommended_action_categories

    def test_critical_severity_adds_block_deploy(self):
        """Critical severity → block_deploy added if missing."""
        context = _make_context(criticality="critical")
        decision = _make_decision(severity="critical", categories=["notify_owner"])
        result = self._enforce(decision, context)
        assert "block_deploy" in result.recommended_action_categories

    def test_monitor_only_block_deploy_incompatible(self):
        """monitor_only + block_deploy → monitor_only removed."""
        context = _make_context(criticality="critical")
        decision = _make_decision(
            severity="critical",
            categories=["monitor_only", "block_deploy"],
        )
        result = self._enforce(decision, context)
        assert "block_deploy" in result.recommended_action_categories
        assert "monitor_only" not in result.recommended_action_categories

    def test_categories_capped_at_3(self):
        """Categories truncated to 3 by priority."""
        context = _make_context(criticality="critical")
        # Start with critical → block_deploy will be added, notify_owner from human_review
        decision = _make_decision(
            severity="critical",
            confidence=0.3,  # triggers human review → notify_owner
            categories=["backward_compatibility", "downstream_updates", "monitor_only"],
            requires_human=False,
        )
        result = self._enforce(decision, context)
        assert len(result.recommended_action_categories) <= 3


# ---------------------------------------------------------------------------
# Severity Helpers
# ---------------------------------------------------------------------------


class TestSeverityHelpers:
    def test_sev_index_ordering(self):
        assert _sev_index("low") < _sev_index("medium")
        assert _sev_index("medium") < _sev_index("high")
        assert _sev_index("high") < _sev_index("critical")

    def test_sev_index_unknown(self):
        assert _sev_index("unknown") == 0

    def test_truncate_categories_by_priority(self):
        cats = ["monitor_only", "block_deploy", "notify_owner", "backward_compatibility"]
        result = _truncate_categories(cats, 3)
        assert len(result) == 3
        # block_deploy is highest priority, should be first
        assert result[0] == "block_deploy"


# ---------------------------------------------------------------------------
# Fallback Tests
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_uses_criticality(self):
        agent = object.__new__(DriftAgent)
        context = _make_context(criticality="high")
        result = agent._fallback_decision(context)
        assert result.severity == "high"
        assert result.confidence_in_decision == 0.0
        assert result.requires_human_review is True
        assert result.recommended_action_categories == ["notify_owner"]


# ---------------------------------------------------------------------------
# Full Judge Flow (Claude mocked)
# ---------------------------------------------------------------------------


class TestJudgeFlow:
    @patch("schemint.drift.agent_brain.anthropic")
    @patch("schemint.drift.agent_brain.CLAUDE_AVAILABLE", True)
    def test_judge_calls_claude_and_enforces(self, mock_anthropic):
        """Full flow: Claude returns a decision, invariants applied."""
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        ai_response = _mock_claude_response({
            "severity": "medium",
            "confidence_in_decision": 0.8,
            "requires_human_review": False,
            "rationale": ["Moderate downstream impact"],
            "recommended_action_categories": ["monitor_only"],
            "context_quality": "complete",
        })
        mock_client.messages.create.return_value = ai_response

        with patch("schemint.drift.agent_brain.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                claude_api_key="test-key",
                claude_model="test-model",
                ai_enabled=True,
            )
            agent = DriftAgent()

        context = _make_context(criticality="medium")
        result = agent.judge(context)

        assert isinstance(result, AgentDecision)
        assert result.severity == "medium"
        assert mock_client.messages.create.called

    @patch("schemint.drift.agent_brain.anthropic")
    @patch("schemint.drift.agent_brain.CLAUDE_AVAILABLE", True)
    def test_judge_fallback_on_api_error(self, mock_anthropic):
        """Claude raises → deterministic fallback."""
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("schemint.drift.agent_brain.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                claude_api_key="test-key",
                claude_model="test-model",
                ai_enabled=True,
            )
            agent = DriftAgent()

        context = _make_context(criticality="high")
        result = agent.judge(context)

        assert result.severity == "high"
        assert result.confidence_in_decision == 0.0
        assert result.requires_human_review is True


# ---------------------------------------------------------------------------
# Factory Tests
# ---------------------------------------------------------------------------


class TestFactory:
    @patch("schemint.drift.agent_brain.CLAUDE_AVAILABLE", False)
    def test_factory_returns_none_without_sdk(self):
        with patch("schemint.drift.agent_brain.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ai_enabled=True)
            assert get_drift_agent() is None

    def test_factory_returns_none_without_api_key(self):
        with patch("schemint.drift.agent_brain.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ai_enabled=False)
            assert get_drift_agent() is None
