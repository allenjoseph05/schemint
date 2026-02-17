"""Unit tests for memory-enriched analysis pipeline (Part 2).

Tests cover:
- build_memory_context formatting
- Suppressed findings filtering
- AI score override
- Graceful fallback when DB is unavailable
- project_id resolution (UUID vs external_id)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from schemint.models.schema import Column, DataType, ParsedSchema, Table
from schemint.services.claude import build_memory_context

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_schema(num_tables: int = 2, cols_per_table: int = 5) -> ParsedSchema:
    """Create a ParsedSchema with N tables, each with M columns."""
    tables = []
    for i in range(num_tables):
        cols = [
            Column(
                name=f"col_{j}",
                data_type=DataType.VARCHAR,
                raw_type="VARCHAR(100)",
            )
            for j in range(cols_per_table)
        ]
        cols[0] = Column(
            name="id",
            data_type=DataType.INT,
            raw_type="INT",
            is_primary_key=True,
            nullable=False,
        )
        tables.append(Table(name=f"table_{i}", columns=cols, primary_key=["id"]))
    return ParsedSchema(tables=tables, database_type="mysql")


def _make_accepted_finding(**overrides):
    """Create a mock AcceptedFinding."""
    from schemint.memory.models import AcceptedFinding, FeedbackScope

    defaults = {
        "id": uuid4(),
        "project_id": uuid4(),
        "finding_type": "wrong_data_type_float",
        "pattern_hash": "abc123",
        "scope": FeedbackScope.PATTERN,
        "reason": "Sensor data, not financial",
        "accepted_by": "test@example.com",
        "context": {"table": "metrics", "column": "value"},
    }
    defaults.update(overrides)
    return AcceptedFinding(**defaults)


def _make_business_rule(**overrides):
    """Create a mock BusinessRule."""
    from schemint.memory.models import BusinessRule, FindingSeverity

    defaults = {
        "id": uuid4(),
        "project_id": uuid4(),
        "rule_type": "require_tenant_id",
        "rule_config": {},
        "severity": FindingSeverity.CRITICAL,
        "applies_to": {"tables": ["*"], "except": ["migrations"]},
        "rationale": "Multi-tenant architecture",
        "created_by": "test@example.com",
    }
    defaults.update(overrides)
    return BusinessRule(**defaults)


def _make_schema_semantics(**overrides):
    """Create a mock SchemaSemantics."""
    from schemint.memory.models import ElementType, SchemaSemantics

    defaults = {
        "id": uuid4(),
        "project_id": uuid4(),
        "element_type": ElementType.COLUMN,
        "element_path": "orders.total",
        "semantic_tags": ["money", "usd"],
        "description": "Total order amount in USD",
        "constraints": {},
    }
    defaults.update(overrides)
    return SchemaSemantics(**defaults)


def _mock_tool_use_response(tool_input: dict) -> MagicMock:
    """Create a mock Anthropic message with a tool_use content block."""
    tool_block = SimpleNamespace(
        type="tool_use",
        name="submit_analysis",
        input=tool_input,
    )
    message = MagicMock()
    message.content = [tool_block]
    return message


# ---------------------------------------------------------------------------
# Tests -- build_memory_context
# ---------------------------------------------------------------------------


class TestBuildMemoryContext:
    """Test build_memory_context() formatting."""

    def test_with_all_data(self):
        af = _make_accepted_finding()
        br = _make_business_rule()
        ss = _make_schema_semantics()

        result = build_memory_context([af], [br], [ss])

        assert result is not None
        assert "accepted_findings" in result
        assert len(result["accepted_findings"]) == 1
        assert result["accepted_findings"][0]["type"] == "wrong_data_type_float"
        assert result["accepted_findings"][0]["reason"] == "Sensor data, not financial"
        assert result["accepted_findings"][0]["scope"] == "pattern"

        assert "business_rules" in result
        assert len(result["business_rules"]) == 1
        assert result["business_rules"][0]["rule"] == "require_tenant_id"
        assert result["business_rules"][0]["severity"] == "critical"

        assert "semantics" in result
        assert len(result["semantics"]) == 1
        assert result["semantics"][0]["path"] == "orders.total"
        assert result["semantics"][0]["tags"] == ["money", "usd"]

    def test_empty_returns_none(self):
        result = build_memory_context([], [], [])
        assert result is None

    def test_partial_data(self):
        af = _make_accepted_finding()
        result = build_memory_context([af], [], [])

        assert result is not None
        assert "accepted_findings" in result
        assert "business_rules" not in result
        assert "semantics" not in result


# ---------------------------------------------------------------------------
# Tests: Suppressed findings filtering
# ---------------------------------------------------------------------------


class TestSuppressedFindings:
    """Test that suppressed findings from AI response are filtered."""

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_suppressed_findings_filtered(self, mock_get_agent, mock_analyzer_settings):
        mock_analyzer_settings.return_value = MagicMock(ai_enabled=True)

        agent_result = {
            "findings": [
                {
                    "severity": "warning",
                    "category": "performance",
                    "title": "FLOAT used for value column",
                    "description": "FLOAT may cause precision issues",
                    "impact": "Data precision risk",
                    "reasoning": "FLOAT is imprecise for financial data",
                },
                {
                    "severity": "warning",
                    "category": "structural",
                    "title": "Missing FK on orders.user_id",
                    "description": "No FK constraint",
                    "impact": "No referential integrity",
                    "reasoning": "Column name suggests relationship",
                },
            ],
            "issues": [
                {
                    "severity": "warning",
                    "category": "performance",
                    "title": "FLOAT used for value column",
                    "description": "FLOAT may cause precision issues",
                    "impact": "Data precision risk",
                    "reasoning": "FLOAT is imprecise for financial data",
                },
                {
                    "severity": "warning",
                    "category": "structural",
                    "title": "Missing FK on orders.user_id",
                    "description": "No FK constraint",
                    "impact": "No referential integrity",
                    "reasoning": "Column name suggests relationship",
                },
            ],
            "suppressed": [
                {
                    "type": "missing_index",
                    "table": "metrics",
                    "reason": "Previously accepted: sensor data table",
                },
            ],
            "score": {
                "total": 80,
                "structural": 70,
                "performance": 85,
                "naming": 95,
                "best_practices": 80,
            },
            "good_practices": ["Primary keys present"],
            "summary": "Schema looks good.",
        }

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = agent_result
        mock_get_agent.return_value = mock_agent

        from schemint.core.analyzer.analyzer import analyze_schema

        schema = _make_schema()
        result = analyze_schema(schema)

        # The "FLOAT used for value column" finding has AI category "performance"
        # which maps to IssueCategory.MISSING_INDEX (value "missing_index").
        # The suppressed type is "missing_index", so the FLOAT finding is suppressed.
        # Only "Missing FK on orders.user_id" (structural -> missing_constraint) survives.
        ai_titles = [
            i.title
            for i in result.issues
            if i.title in ["FLOAT used for value column", "Missing FK on orders.user_id"]
        ]
        assert len(ai_titles) == 1
        assert ai_titles[0] == "Missing FK on orders.user_id"

        # Summary should mention suppressed count
        assert "1 finding(s) suppressed" in (result.ai_summary or "")


# ---------------------------------------------------------------------------
# Tests: AI scores override
# ---------------------------------------------------------------------------


class TestAIScoreOverride:
    """Test that AI-computed scores override deterministic scores."""

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_ai_scores_used_when_present(self, mock_get_agent, mock_analyzer_settings):
        mock_analyzer_settings.return_value = MagicMock(ai_enabled=True)

        agent_result = {
            "findings": [],
            "issues": [],
            "score": {
                "total": 42,
                "structural": 55,
                "performance": 60,
                "naming": 80,
                "best_practices": 35,
            },
            "good_practices": [],
            "summary": "Test.",
        }

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = agent_result
        mock_get_agent.return_value = mock_agent

        from schemint.core.analyzer.analyzer import analyze_schema

        schema = _make_schema()
        result = analyze_schema(schema)

        # AI scores should be used directly
        assert result.score.total == 42
        assert result.score.structural == 55
        assert result.score.performance == 60
        assert result.score.naming == 80
        assert result.score.best_practices == 35


# ---------------------------------------------------------------------------
# Tests: Graceful fallback
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    """Test that memory retrieval fails gracefully."""

    def test_retrieve_memory_no_database(self):
        """Memory retrieval should return None when DB is not configured."""
        from schemint.core.analyzer.analyzer import _retrieve_memory

        with patch(
            "schemint.core.analyzer.analyzer._resolve_project_id",
            side_effect=Exception("No DB"),
        ):
            result = _retrieve_memory("some-project-id")

        assert result is None

    def test_resolve_project_id_uuid(self):
        """UUID strings should be resolved directly."""
        from schemint.core.analyzer.analyzer import _resolve_project_id

        test_uuid = uuid4()
        result = _resolve_project_id(str(test_uuid))
        assert result == test_uuid

    def test_resolve_project_id_external_id(self):
        """External IDs should be resolved via store lookup."""
        from schemint.core.analyzer.analyzer import _resolve_project_id

        mock_project = MagicMock()
        mock_project.id = uuid4()

        mock_store = MagicMock()
        mock_store.get_project_by_external_id.return_value = mock_project

        with patch(
            "schemint.memory.store.get_memory_store",
            return_value=mock_store,
        ):
            result = _resolve_project_id("github:org/repo")

        assert result == mock_project.id

    def test_resolve_project_id_not_found(self):
        """Unknown external IDs should return None."""
        from schemint.core.analyzer.analyzer import _resolve_project_id

        mock_store = MagicMock()
        mock_store.get_project_by_external_id.return_value = None

        with patch(
            "schemint.memory.store.get_memory_store",
            return_value=mock_store,
        ):
            result = _resolve_project_id("github:unknown/repo")

        assert result is None

    def test_resolve_project_id_db_error(self):
        """DB errors during external_id lookup should return None."""
        from schemint.core.analyzer.analyzer import _resolve_project_id

        with patch(
            "schemint.memory.store.get_memory_store",
            side_effect=Exception("Connection refused"),
        ):
            result = _resolve_project_id("github:org/repo")

        assert result is None
