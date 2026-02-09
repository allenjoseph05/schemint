"""Unit tests for the upgraded ClaudeAnalyzer (agent analyzer).

All tests use mocks — no real API calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from schemint.models.issue import IssueCategory
from schemint.models.schema import Column, DataType, ParsedSchema, Table
from schemint.services.claude import (
    ANALYSIS_TOOL,
    SYSTEM_PROMPT,
    compress_schema,
    select_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schema(num_tables: int, cols_per_table: int = 5) -> ParsedSchema:
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
        # First column is always the PK
        cols[0] = Column(
            name="id",
            data_type=DataType.INT,
            raw_type="INT",
            is_primary_key=True,
            nullable=False,
        )
        tables.append(
            Table(
                name=f"table_{i}",
                columns=cols,
                primary_key=["id"],
            )
        )
    return ParsedSchema(tables=tables, database_type="mysql")


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
# Tests
# ---------------------------------------------------------------------------


class TestCompressSchema:
    """Test compress_schema reduces token count and preserves semantics."""

    def test_compress_basic(self):
        schema = _make_schema(2, 3)
        compressed = compress_schema(schema)

        assert "tables" in compressed
        assert len(compressed["tables"]) == 2
        assert compressed["db"] == "mysql"

        # First column should have pk and nn flags
        first_col = compressed["tables"][0]["cols"][0]
        assert first_col["name"] == "id"
        assert first_col["pk"] is True
        assert first_col["nn"] is True

    def test_compress_strips_defaults(self):
        """Nullable columns should NOT have nn key."""
        schema = _make_schema(1, 2)
        compressed = compress_schema(schema)

        # col_1 is nullable by default — should NOT have "nn"
        nullable_col = compressed["tables"][0]["cols"][1]
        assert "nn" not in nullable_col

    def test_compress_shortens_keys(self):
        """Key names should be short: type, pk, nn, auto, uniq."""
        schema = ParsedSchema(
            tables=[
                Table(
                    name="t",
                    columns=[
                        Column(
                            name="id",
                            data_type=DataType.INT,
                            raw_type="INT",
                            is_primary_key=True,
                            nullable=False,
                            is_auto_increment=True,
                            is_unique=True,
                        )
                    ],
                    primary_key=["id"],
                )
            ],
            database_type="postgresql",
        )
        compressed = compress_schema(schema)
        col = compressed["tables"][0]["cols"][0]

        assert "type" in col
        assert "pk" in col
        assert "nn" in col
        assert "auto" in col
        assert "uniq" in col
        # Should NOT have verbose keys
        assert "data_type" not in col
        assert "is_primary_key" not in col
        assert "nullable" not in col

    def test_compress_fewer_tokens(self):
        """Compressed JSON should be shorter than full model_dump JSON."""
        schema = _make_schema(5, 8)
        compressed_json = len(str(compress_schema(schema)))
        full_json = len(schema.model_dump_json())
        assert compressed_json < full_json


class TestSelectModel:
    """Test tiered model selection."""

    @patch("schemint.services.claude.get_settings")
    def test_simple_schema_uses_haiku(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
        )
        schema = _make_schema(2, 5)  # 2 tables, 10 cols total
        assert select_model(schema) == "claude-haiku-4-5-20251001"

    @patch("schemint.services.claude.get_settings")
    def test_medium_schema_uses_default(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
        )
        schema = _make_schema(8, 6)  # 8 tables, 48 cols
        assert select_model(schema) == "claude-sonnet-4-20250514"

    @patch("schemint.services.claude.get_settings")
    def test_complex_schema_uses_complex_model(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
        )
        schema = _make_schema(20, 10)  # 20 tables
        assert select_model(schema) == "claude-sonnet-4-5-20250929"

    @patch("schemint.services.claude.get_settings")
    def test_simple_but_many_cols_uses_default(self, mock_settings):
        """3 tables but >20 cols should use default (medium)."""
        mock_settings.return_value = MagicMock(
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
        )
        schema = _make_schema(3, 8)  # 3 tables, 24 cols → exceeds 20
        assert select_model(schema) == "claude-sonnet-4-20250514"


class TestToolUseResponseParsing:
    """Test extraction of structured results from tool_use blocks."""

    @patch("schemint.services.claude.get_settings")
    def test_parse_tool_use_response(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_api_key="test-key",
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
            ai_enabled=True,
        )
        tool_input = {
            "findings": [
                {
                    "severity": "warning",
                    "category": "structural",
                    "title": "Missing FK on orders.user_id",
                    "description": "orders.user_id should reference users.id",
                    "table_name": "orders",
                    "column_name": "user_id",
                    "impact": "No referential integrity",
                    "fix_description": "Add FK constraint",
                    "fix_script": "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id);",
                    "reasoning": "Column name suggests a relationship",
                }
            ],
            "score": {
                "total": 72,
                "structural": 60,
                "performance": 85,
                "naming": 90,
                "best_practices": 70,
            },
            "good_practices": ["Primary keys on all tables"],
            "recommendations": ["Add foreign key constraints"],
            "summary": "Schema has structural gaps.",
        }

        mock_message = _mock_tool_use_response(tool_input)

        with patch("schemint.services.claude.anthropic") as mock_anthropic, \
             patch("schemint.services.claude.CLAUDE_AVAILABLE", True):
            from schemint.services.claude import ClaudeAnalyzer

            analyzer = ClaudeAnalyzer()
            result = analyzer._extract_tool_result(mock_message)

        assert result["summary"] == "Schema has structural gaps."
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "warning"
        assert result["score"]["total"] == 72
        assert "issues" in result  # backward compat alias


class TestSystemPrompt:
    """Test that the system prompt contains all 6 analysis dimensions."""

    def test_contains_all_dimensions(self):
        dimensions = [
            "STRUCTURAL",
            "PERFORMANCE",
            "SECURITY",
            "NAMING",
            "BEST PRACTICES",
            "DOMAIN",
        ]
        for dim in dimensions:
            assert dim in SYSTEM_PROMPT, f"Missing dimension: {dim}"

    def test_contains_severity_definitions(self):
        assert "critical" in SYSTEM_PROMPT
        assert "warning" in SYSTEM_PROMPT
        assert "suggestion" in SYSTEM_PROMPT

    def test_contains_memory_rules(self):
        assert "MEMORY RULES" in SYSTEM_PROMPT
        assert "suppressed" in SYSTEM_PROMPT


class TestGracefulFallbackOnError:
    """Test that API errors don't crash, return error dict."""

    @patch("schemint.services.claude.get_settings")
    def test_api_error_returns_error_dict(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_api_key="test-key",
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
            ai_enabled=True,
        )

        with patch("schemint.services.claude.anthropic") as mock_anthropic, \
             patch("schemint.services.claude.CLAUDE_AVAILABLE", True):
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = Exception("API rate limit")
            mock_anthropic.Anthropic.return_value = mock_client

            from schemint.services.claude import ClaudeAnalyzer

            analyzer = ClaudeAnalyzer()
            schema = _make_schema(2)
            result = analyzer.analyze_sync(schema)

        assert "error" in result
        assert "API rate limit" in result["error"]
        assert result["findings"] == []
        assert result["good_practices"] == []


class TestMemoryContextInAnalyzeSync:
    """Test that memory_context is passed through analyze_sync to the user message."""

    @patch("schemint.services.claude.get_settings")
    def test_memory_context_included_in_api_call(self, mock_settings):
        mock_settings.return_value = MagicMock(
            claude_api_key="test-key",
            claude_model="claude-sonnet-4-20250514",
            claude_model_simple="claude-haiku-4-5-20251001",
            claude_model_complex="claude-sonnet-4-5-20250929",
            ai_enabled=True,
        )

        tool_input = {
            "findings": [],
            "score": {"total": 90, "structural": 90, "performance": 90, "naming": 90, "best_practices": 90},
            "good_practices": [],
            "summary": "Clean.",
        }
        mock_message = _mock_tool_use_response(tool_input)

        with patch("schemint.services.claude.anthropic") as mock_anthropic, \
             patch("schemint.services.claude.CLAUDE_AVAILABLE", True):
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_message
            mock_anthropic.Anthropic.return_value = mock_client

            from schemint.services.claude import ClaudeAnalyzer

            analyzer = ClaudeAnalyzer()
            schema = _make_schema(2)

            memory = {"accepted_findings": [{"type": "test", "table": "t", "reason": "ok", "scope": "once"}]}
            analyzer.analyze_sync(schema, memory_context=memory)

            # Verify the user message sent to Claude contains memory
            call_args = mock_client.messages.create.call_args
            user_msg = call_args.kwargs["messages"][0]["content"]
            assert "MEMORY (previously accepted findings for this project):" in user_msg
            assert "test" in user_msg


class TestDomainCategoryMapping:
    """Test that 'domain' maps to IssueCategory.DOMAIN."""

    def test_domain_mapping(self):
        from schemint.core.analyzer.analyzer import _map_ai_category

        assert _map_ai_category("domain") == IssueCategory.DOMAIN
        assert _map_ai_category("DOMAIN") == IssueCategory.DOMAIN

    def test_all_categories_mapped(self):
        """Every category the tool schema allows should map to an IssueCategory."""
        from schemint.core.analyzer.analyzer import _map_ai_category

        tool_categories = (
            ANALYSIS_TOOL["input_schema"]["properties"]["findings"]["items"][
                "properties"
            ]["category"]["enum"]
        )
        for cat in tool_categories:
            result = _map_ai_category(cat)
            assert isinstance(result, IssueCategory), f"{cat} not mapped"
            assert result != IssueCategory.OTHER, f"{cat} fell through to OTHER"
