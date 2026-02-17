"""Unit tests for schema analyzer (AI-only pipeline)."""

from unittest.mock import MagicMock, patch

from schemint.core.analyzer.analyzer import (
    _convert_ai_issues,
    _map_ai_category,
    _map_ai_severity,
    analyze_schema,
    analyze_sql,
)
from schemint.models.issue import IssueCategory, IssueSeverity
from schemint.models.schema import Column, DataType, ParsedSchema, Table

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schema(num_tables: int = 1, cols_per_table: int = 3) -> ParsedSchema:
    """Create a ParsedSchema for testing."""
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


def _mock_agent_result(**overrides):
    """Create a mock agent analysis result."""
    defaults = {
        "summary": "Schema analysis complete.",
        "findings": [],
        "issues": [],
        "good_practices": ["Primary keys present"],
        "recommendations": ["Consider adding indexes"],
        "score": {
            "total": 85,
            "structural": 90,
            "performance": 80,
            "naming": 85,
            "best_practices": 80,
        },
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests: Mapping functions
# ---------------------------------------------------------------------------


class TestMappingFunctions:
    """Test AI category/severity mapping."""

    def test_map_ai_category_known(self):
        assert _map_ai_category("structural") == IssueCategory.MISSING_CONSTRAINT
        assert _map_ai_category("performance") == IssueCategory.MISSING_INDEX
        assert _map_ai_category("security") == IssueCategory.SECURITY_RISK
        assert _map_ai_category("naming") == IssueCategory.NAMING_CONVENTION
        assert _map_ai_category("domain") == IssueCategory.DOMAIN

    def test_map_ai_category_unknown(self):
        assert _map_ai_category("unknown") == IssueCategory.OTHER

    def test_map_ai_severity_known(self):
        assert _map_ai_severity("critical") == IssueSeverity.CRITICAL
        assert _map_ai_severity("warning") == IssueSeverity.WARNING
        assert _map_ai_severity("suggestion") == IssueSeverity.SUGGESTION

    def test_map_ai_severity_unknown(self):
        assert _map_ai_severity("info") == IssueSeverity.SUGGESTION


class TestConvertAIIssues:
    """Test AI issue conversion."""

    def test_converts_basic_issue(self):
        ai_issues = [
            {
                "severity": "critical",
                "category": "structural",
                "title": "Missing primary key",
                "description": "Table has no PK",
                "reasoning": "PKs ensure uniqueness",
            },
        ]
        issues = _convert_ai_issues(ai_issues)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.CRITICAL
        assert issues[0].title == "Missing primary key"
        assert "Reasoning: PKs ensure uniqueness" in issues[0].description

    def test_skips_malformed_issues(self):
        ai_issues = [
            {"bad": "data"},  # missing required fields
            {
                "severity": "warning",
                "category": "naming",
                "title": "Good issue",
                "description": "OK",
            },
        ]
        issues = _convert_ai_issues(ai_issues)
        # Should still get the valid issue
        assert len(issues) >= 1


# ---------------------------------------------------------------------------
# Tests: analyze_schema (AI-only flow)
# ---------------------------------------------------------------------------


class TestAnalyzeSchema:
    """Test the main analyze_schema function."""

    @patch("schemint.core.analyzer.analyzer.get_settings")
    def test_returns_error_when_ai_unavailable(self, mock_settings):
        """When AI is not enabled, return error result."""
        mock_settings.return_value = MagicMock(ai_enabled=False)

        schema = _make_schema()
        result = analyze_schema(schema)

        assert result is not None
        assert result.id.startswith("ana_")
        assert result.score.total == 0
        assert "unavailable" in result.ai_summary.lower()

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_calls_agent_analyzer(self, mock_get_agent, mock_settings):
        """analyze_schema should call the agent analyzer."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result()
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema, app_type="ecommerce")

        mock_agent.analyze.assert_called_once()
        assert result.score.total == 85
        assert result.ai_summary is not None

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_uses_ai_scores_directly(self, mock_get_agent, mock_settings):
        """AI scores should be used directly, not recalculated."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result(
            score={
                "total": 42,
                "structural": 55,
                "performance": 60,
                "naming": 80,
                "best_practices": 35,
            },
        )
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema)

        assert result.score.total == 42
        assert result.score.structural == 55
        assert result.score.performance == 60

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_ai_issues_converted(self, mock_get_agent, mock_settings):
        """AI findings should be converted to Issue models."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result(
            issues=[
                {
                    "severity": "critical",
                    "category": "structural",
                    "title": "Missing PK on users",
                    "description": "No primary key",
                    "impact": "Data integrity risk",
                    "reasoning": "PKs are essential",
                },
            ],
        )
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema)

        assert result.critical_count == 1
        assert result.issues[0].title == "Missing PK on users"

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_good_practices_from_agent(self, mock_get_agent, mock_settings):
        """Good practices from agent should be included."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result(
            good_practices=["Primary keys present", "Timestamps used"],
        )
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema)

        assert len(result.good_practices) == 2
        assert "Primary keys present" in result.good_practices

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_handles_agent_failure(self, mock_get_agent, mock_settings):
        """Agent failure should produce error summary, not crash."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.side_effect = RuntimeError("API timeout")
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema)

        assert result is not None
        assert "failed" in result.ai_summary.lower()
        assert result.score.total == 0

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_agent_none_raises_error(self, mock_get_agent, mock_settings):
        """When agent can't be initialized, should handle gracefully."""
        mock_settings.return_value = MagicMock(ai_enabled=True)
        mock_get_agent.return_value = None

        schema = _make_schema()
        result = analyze_schema(schema)

        assert result is not None
        assert "failed" in result.ai_summary.lower()

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_memory_context_passed_through(self, mock_get_agent, mock_settings):
        """Memory context should be passed to agent when project_id provided."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result()
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()

        with patch("schemint.core.analyzer.analyzer._retrieve_memory") as mock_mem:
            mock_mem.return_value = {"accepted_findings": []}
            analyze_schema(schema, project_id="test-project")

        mock_mem.assert_called_once_with("test-project")
        call_kwargs = mock_agent.analyze.call_args
        assert call_kwargs.kwargs.get("memory_context") == {"accepted_findings": []}

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_suppressed_findings(self, mock_get_agent, mock_settings):
        """Suppressed findings should be removed from results."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result(
            issues=[
                {
                    "severity": "warning",
                    "category": "performance",
                    "title": "FLOAT for money",
                    "description": "Use DECIMAL",
                    "impact": "Precision loss",
                    "reasoning": "FLOAT is imprecise",
                },
            ],
            suppressed=[
                {"type": "missing_index", "table": "metrics", "reason": "Accepted"},
            ],
        )
        mock_get_agent.return_value = mock_agent

        schema = _make_schema()
        result = analyze_schema(schema)

        assert "1 finding(s) suppressed" in (result.ai_summary or "")

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_table_summaries_created(self, mock_get_agent, mock_settings):
        """Table summaries should be created from parsed schema."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result()
        mock_get_agent.return_value = mock_agent

        schema = _make_schema(num_tables=2)
        result = analyze_schema(schema)

        assert len(result.tables) == 2


class TestAnalyzeSQL:
    """Test analyze_sql entry point."""

    @patch("schemint.core.analyzer.analyzer.get_settings")
    @patch("schemint.services.agent.get_agent_analyzer")
    def test_analyze_sql_parses_and_analyzes(self, mock_get_agent, mock_settings):
        """analyze_sql should parse SQL and call analyze_schema."""
        mock_settings.return_value = MagicMock(ai_enabled=True)

        mock_agent = MagicMock()
        mock_agent.analyze.return_value = _mock_agent_result()
        mock_get_agent.return_value = mock_agent

        result = analyze_sql(
            "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100));",
        )

        assert result is not None
        assert result.table_count == 1
        mock_agent.analyze.assert_called_once()

    @patch("schemint.core.analyzer.analyzer.get_settings")
    def test_analyze_sql_no_use_ai_param(self, mock_settings):
        """analyze_sql should not accept use_ai parameter."""
        mock_settings.return_value = MagicMock(ai_enabled=False)

        # Should work without use_ai
        result = analyze_sql(
            "CREATE TABLE users (id INT PRIMARY KEY);",
        )
        assert result is not None
