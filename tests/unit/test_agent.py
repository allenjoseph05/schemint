"""Unit tests for the multi-turn agentic analyzer.

All tests use mocks — no real API calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from schemint.core.analyzer.pre_analysis import run_pre_analysis
from schemint.models.schema import (
    Column,
    DataType,
    ForeignKey,
    ParsedSchema,
    Table,
)
from schemint.services.agent import (
    GET_SCHEMA_OVERVIEW_TOOL,
    INSPECT_TABLE_TOOL,
    SUBMIT_ANALYSIS_TOOL,
    AgentAnalyzer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schema() -> ParsedSchema:
    """Create a small test schema with 3 tables."""
    users = Table(
        name="users",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="email", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            Column(name="password", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            Column(name="created_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
            Column(name="updated_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
        ],
        primary_key=["id"],
    )
    orders = Table(
        name="orders",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="user_id", data_type=DataType.INT, raw_type="INT"),
            Column(name="total", data_type=DataType.FLOAT, raw_type="FLOAT"),
            Column(name="status", data_type=DataType.VARCHAR, raw_type="VARCHAR(20)"),
            Column(name="created_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(column="user_id", references_table="users", references_column="id"),
        ],
    )
    products = Table(
        name="products",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="name", data_type=DataType.VARCHAR, raw_type="VARCHAR(100)"),
            Column(name="price", data_type=DataType.FLOAT, raw_type="FLOAT"),
        ],
        primary_key=["id"],
    )
    return ParsedSchema(tables=[users, orders, products], database_type="mysql")


def _mock_settings():
    """Create mock settings for agent tests."""
    return MagicMock(
        claude_api_key="test-key",
        claude_model="claude-sonnet-4-20250514",
        claude_model_simple="claude-haiku-4-5-20251001",
        claude_model_complex="claude-sonnet-4-5-20250929",
        claude_max_agent_turns=10,
        ai_enabled=True,
    )


def _submit_tool_block(tool_input: dict) -> SimpleNamespace:
    """Create a mock submit_analysis tool_use block."""
    return SimpleNamespace(
        type="tool_use",
        id="tool_123",
        name="submit_analysis",
        input=tool_input,
    )


def _overview_tool_block() -> SimpleNamespace:
    """Create a mock get_schema_overview tool_use block."""
    return SimpleNamespace(
        type="tool_use",
        id="tool_overview",
        name="get_schema_overview",
        input={},
    )


def _inspect_tool_block(table_name: str) -> SimpleNamespace:
    """Create a mock inspect_table tool_use block."""
    return SimpleNamespace(
        type="tool_use",
        id=f"tool_inspect_{table_name}",
        name="inspect_table",
        input={"table_name": table_name},
    )


SAMPLE_ANALYSIS = {
    "findings": [
        {
            "severity": "critical",
            "category": "performance",
            "title": "FLOAT used for money column 'total'",
            "description": "orders.total uses FLOAT",
            "table_name": "orders",
            "column_name": "total",
            "impact": "Rounding errors",
            "fix_description": "Use DECIMAL(10,2)",
            "reasoning": "FLOAT arithmetic is imprecise for money",
        }
    ],
    "score": {
        "total": 68,
        "structural": 75,
        "performance": 55,
        "naming": 90,
        "best_practices": 60,
    },
    "good_practices": ["Primary keys on all tables"],
    "recommendations": ["Use DECIMAL for money columns"],
    "summary": "Schema has performance and security issues.",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentInitialMessage:
    @patch("schemint.services.agent.get_settings")
    def test_initial_message_is_lightweight(self, mock_settings_fn):
        """Initial message should only have table names/counts, NOT full schema."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            schema = _make_schema()
            msg = agent._build_initial_message(schema, "ecommerce", None, None)

            # Should contain table names
            assert "users" in msg
            assert "orders" in msg
            assert "products" in msg

            # Should contain column counts
            assert "5 columns" in msg
            assert "3 columns" in msg

            # Should NOT contain full column definitions
            assert "VARCHAR(255)" not in msg
            assert "FLOAT" not in msg


class TestExecuteTool:
    @patch("schemint.services.agent.get_settings")
    def test_execute_tool_get_schema_overview(self, mock_settings_fn):
        """get_schema_overview should return pre-analysis data."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            schema = _make_schema()
            pre = run_pre_analysis(schema, "ecommerce")

            block = _overview_tool_block()
            result = agent._execute_tool(block, schema, pre)

            assert "DOMAIN:" in result
            assert "TOPOLOGY:" in result
            assert "STATISTICS:" in result

    @patch("schemint.services.agent.get_settings")
    def test_execute_tool_inspect_table(self, mock_settings_fn):
        """inspect_table should return correct table details."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            schema = _make_schema()
            pre = run_pre_analysis(schema)

            block = _inspect_tool_block("orders")
            result = agent._execute_tool(block, schema, pre)

            assert "TABLE: orders" in result
            assert "COLUMNS:" in result
            assert "user_id" in result
            assert "FOREIGN KEYS:" in result

    @patch("schemint.services.agent.get_settings")
    def test_execute_tool_inspect_nonexistent_table(self, mock_settings_fn):
        """inspect_table for missing table should return error, not crash."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            schema = _make_schema()
            pre = run_pre_analysis(schema)

            block = _inspect_tool_block("nonexistent")
            result = agent._execute_tool(block, schema, pre)

            assert "not found" in result


class TestAgentLoop:
    @patch("schemint.services.agent.get_settings")
    def test_agent_loop_terminates_on_submit(self, mock_settings_fn):
        """Loop should exit when submit_analysis is called."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic") as mock_anthropic,
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            mock_client = MagicMock()

            # Turn 1: call get_schema_overview
            turn1_response = MagicMock()
            turn1_response.content = [_overview_tool_block()]

            # Turn 2: submit_analysis
            turn2_response = MagicMock()
            turn2_response.content = [_submit_tool_block(SAMPLE_ANALYSIS)]

            mock_client.messages.create.side_effect = [
                turn1_response,
                turn2_response,
            ]
            mock_anthropic.Anthropic.return_value = mock_client

            agent = AgentAnalyzer()
            schema = _make_schema()
            result = agent.analyze(schema, "ecommerce")

            assert result["summary"] == "Schema has performance and security issues."
            assert len(result["findings"]) == 1
            assert result["score"]["total"] == 68
            # Should have been called exactly 2 times
            assert mock_client.messages.create.call_count == 2

    @patch("schemint.services.agent.get_settings")
    def test_agent_loop_max_turns(self, mock_settings_fn):
        """Loop should exit at max_turns limit."""
        settings = _mock_settings()
        settings.claude_max_agent_turns = 2
        mock_settings_fn.return_value = settings

        with (
            patch("schemint.services.agent.anthropic") as mock_anthropic,
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            mock_client = MagicMock()

            # Every turn returns a non-terminal tool call
            overview_response = MagicMock()
            overview_response.content = [_overview_tool_block()]
            mock_client.messages.create.return_value = overview_response

            mock_anthropic.Anthropic.return_value = mock_client

            agent = AgentAnalyzer()
            schema = _make_schema()
            result = agent.analyze(schema)

            assert result["error"] == "max_turns_reached"
            assert mock_client.messages.create.call_count == 2


class TestAgentNormalization:
    @patch("schemint.services.agent.get_settings")
    def test_agent_normalizes_result(self, mock_settings_fn):
        """findings should be mapped to issues key."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            result = agent._normalize_result(
                {
                    "findings": [{"title": "test"}],
                    "score": {"total": 80},
                    "summary": "ok",
                }
            )

            assert "issues" in result
            assert result["issues"] == [{"title": "test"}]


class TestAgentMemoryContext:
    @patch("schemint.services.agent.get_settings")
    def test_agent_passes_memory_context(self, mock_settings_fn):
        """Memory context should appear in initial message."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic"),
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            agent = AgentAnalyzer()
            schema = _make_schema()

            memory = {
                "accepted_findings": [
                    {
                        "type": "missing_timestamps",
                        "table": "orders",
                        "reason": "ok",
                        "scope": "once",
                    }
                ]
            }
            msg = agent._build_initial_message(schema, None, None, memory)

            assert "MEMORY" in msg
            assert "missing_timestamps" in msg


class TestAgentToolDefinitions:
    def test_agent_tool_definitions_valid(self):
        """All tools should have required schema fields."""
        for tool in [GET_SCHEMA_OVERVIEW_TOOL, INSPECT_TABLE_TOOL, SUBMIT_ANALYSIS_TOOL]:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"


class TestAgentFallback:
    @patch("schemint.services.agent.get_settings")
    def test_api_error_returns_error_dict(self, mock_settings_fn):
        """On API failure, agent should return error dict, not crash."""
        mock_settings_fn.return_value = _mock_settings()

        with (
            patch("schemint.services.agent.anthropic") as mock_anthropic,
            patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        ):
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = Exception("API rate limit")
            mock_anthropic.Anthropic.return_value = mock_client

            agent = AgentAnalyzer()
            schema = _make_schema()
            result = agent.analyze(schema)

            assert "error" in result
            assert "API rate limit" in result["error"]
            assert result["findings"] == []
