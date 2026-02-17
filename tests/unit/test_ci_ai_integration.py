"""Unit tests for CI pipeline AI integration.

Tests cover:
- _analyze_diff() passes project_id to analyze_sql()
- Report builder AI badge and AI summary section
- Report builder AI score averaging
- Graceful AI failure in CI pipeline
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    CIEventType,
    CIIngestRequest,
    CIReportScore,
    DecisionStatus,
    GitProvider,
)
from schemint.ci.report_builder import CIReportBuilder
from schemint.models.analysis import AnalysisResult, AnalysisScore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analysis_result(
    ai_summary: str | None = None,
    total: int = 85,
    structural: int = 90,
    performance: int = 80,
    naming: int = 85,
    best_practices: int = 75,
    issues: list | None = None,
    table_count: int = 1,
) -> AnalysisResult:
    """Create a minimal AnalysisResult for testing."""
    return AnalysisResult(
        id=f"ana_{uuid4().hex[:12]}",
        score=AnalysisScore(
            total=total,
            structural=structural,
            performance=performance,
            naming=naming,
            best_practices=best_practices,
        ),
        table_count=table_count,
        issues=issues or [],
        ai_summary=ai_summary,
    )


def _make_decision(
    findings: list[AnalysisFinding] | None = None,
    report_score: CIReportScore | None = None,
) -> AnalysisDecision:
    """Create a minimal AnalysisDecision for testing."""
    findings = findings or []
    return AnalysisDecision(
        project_id="github:test/repo",
        ref="abc123",
        status=DecisionStatus.PASS,
        findings=findings,
        duration_ms=100,
        report_score=report_score or CIReportScore(
            total=85, grade="B", label="Good",
            structural=90, performance=80, naming=85, best_practices=75,
        ),
    )


# ---------------------------------------------------------------------------
# Tests: CIIngestRequest no use_ai field
# ---------------------------------------------------------------------------


class TestCIIngestRequestNoUseAi:
    """Test that CIIngestRequest no longer has use_ai field."""

    def test_no_use_ai_field(self):
        """CIIngestRequest should not have use_ai field."""
        request = CIIngestRequest(
            project_id="github:acme/repo",
            event_type=CIEventType.PUSH,
            ref="abc123",
            base_ref="main",
            provider=GitProvider.GENERIC,
        )
        assert not hasattr(request, "use_ai") or "use_ai" not in request.model_fields


# ---------------------------------------------------------------------------
# Tests: _analyze_diff passes project_id
# ---------------------------------------------------------------------------


class TestAnalyzeDiffPassesProjectId:
    """Test that _analyze_diff() passes project_id to analyze_sql()."""

    @pytest.mark.asyncio
    async def test_analyze_diff_passes_project_id(self):
        """analyze_sql should be called with project_id."""
        from schemint.ci.ingest import CIIngestHandler
        from schemint.ci.models import SchemaDiff, SQLChange

        handler = CIIngestHandler()
        project_id = uuid4()

        diff = SchemaDiff(
            ref="abc123",
            base_ref="main",
            sql_files=["schema/users.sql"],
            sql_changes=[
                SQLChange(
                    file_path="schema/users.sql",
                    change_type="added",
                    content="CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));",
                ),
            ],
        )

        mock_store = MagicMock()
        mock_store.check_finding_accepted.return_value = None

        with patch("schemint.ci.ingest.analyze_sql") as mock_analyze:
            mock_result = _make_analysis_result(ai_summary="Analysis done.")
            mock_analyze.return_value = mock_result

            _findings, _results = await handler._analyze_diff(
                diff, project_id, mock_store
            )

            mock_analyze.assert_called_once()
            call_kwargs = mock_analyze.call_args
            # Check project_id is passed as string
            called_project_id = call_kwargs.kwargs.get("project_id") or call_kwargs[1].get("project_id")
            assert called_project_id == str(project_id)

    @pytest.mark.asyncio
    async def test_analyze_diff_no_use_ai_param(self):
        """analyze_sql should NOT be called with use_ai param."""
        from schemint.ci.ingest import CIIngestHandler
        from schemint.ci.models import SchemaDiff, SQLChange

        handler = CIIngestHandler()
        project_id = uuid4()

        diff = SchemaDiff(
            ref="abc123",
            base_ref="main",
            sql_files=["schema/users.sql"],
            sql_changes=[
                SQLChange(
                    file_path="schema/users.sql",
                    change_type="added",
                    content="CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));",
                ),
            ],
        )

        mock_store = MagicMock()
        mock_store.check_finding_accepted.return_value = None

        with patch("schemint.ci.ingest.analyze_sql") as mock_analyze:
            mock_result = _make_analysis_result(ai_summary="Analysis done.")
            mock_analyze.return_value = mock_result

            _findings, _results = await handler._analyze_diff(
                diff, project_id, mock_store
            )

            call_kwargs = mock_analyze.call_args
            # use_ai should not be in the call
            assert "use_ai" not in (call_kwargs.kwargs or {})


# ---------------------------------------------------------------------------
# Tests: Report builder AI badge
# ---------------------------------------------------------------------------


class TestReportAIBadge:
    """Test AI badge in report summary."""

    def test_report_ai_badge_when_ai_used(self):
        """Summary should include 'AI-Enhanced' when AI results present."""
        builder = CIReportBuilder()
        decision = _make_decision()
        results = [_make_analysis_result(ai_summary="Schema looks good overall.")]

        summary = builder.build_summary(decision, results)

        assert "AI-Enhanced" in summary

    def test_report_no_badge_without_ai(self):
        """Summary should NOT include 'AI-Enhanced' when no AI used."""
        builder = CIReportBuilder()
        decision = _make_decision()
        results = [_make_analysis_result(ai_summary=None)]

        summary = builder.build_summary(decision, results)

        assert "AI-Enhanced" not in summary


# ---------------------------------------------------------------------------
# Tests: Report builder AI summary section
# ---------------------------------------------------------------------------


class TestReportAISummarySection:
    """Test AI summary section in report."""

    def test_report_ai_summary_section(self):
        """Summary should include AI Analysis section with AI summary text."""
        builder = CIReportBuilder()
        decision = _make_decision()
        ai_text = "This schema has good structure but could use indexes."
        results = [_make_analysis_result(ai_summary=ai_text)]

        summary = builder.build_summary(decision, results)

        assert "### AI Analysis" in summary
        assert ai_text in summary

    def test_report_no_ai_section_without_ai(self):
        """Summary should NOT include AI Analysis section when no AI."""
        builder = CIReportBuilder()
        decision = _make_decision()
        results = [_make_analysis_result(ai_summary=None)]

        summary = builder.build_summary(decision, results)

        assert "### AI Analysis" not in summary


# ---------------------------------------------------------------------------
# Tests: Report builder scoring
# ---------------------------------------------------------------------------


class TestReportScoring:
    """Test score calculation in report builder."""

    def test_report_score_averages_results(self):
        """build_score should average scores across results."""
        builder = CIReportBuilder()
        results = [
            _make_analysis_result(
                ai_summary="Analysis 1",
                total=80, structural=90, performance=70,
                naming=80, best_practices=60,
            ),
            _make_analysis_result(
                ai_summary="Analysis 2",
                total=60, structural=70, performance=50,
                naming=60, best_practices=40,
            ),
        ]

        score = builder.build_score(results)

        # Average: (80+60)/2=70, (90+70)/2=80, (70+50)/2=60, etc.
        assert score.total == 70
        assert score.structural == 80
        assert score.performance == 60
        assert score.naming == 70
        assert score.best_practices == 50

    def test_report_score_empty_results(self):
        """Empty results should return perfect score."""
        builder = CIReportBuilder()
        score = builder.build_score([])

        assert score.total == 100
        assert score.grade == "A"


# ---------------------------------------------------------------------------
# Tests: Graceful AI failure in CI
# ---------------------------------------------------------------------------


class TestGracefulAIFailureInCI:
    """Test that AI failure doesn't crash CI pipeline."""

    @pytest.mark.asyncio
    async def test_graceful_ai_failure_in_ci(self):
        """When analyze_sql raises with AI, it should be caught and return parse_error finding."""
        from schemint.ci.ingest import CIIngestHandler
        from schemint.ci.models import SchemaDiff, SQLChange

        handler = CIIngestHandler()
        project_id = uuid4()

        diff = SchemaDiff(
            ref="abc123",
            base_ref="main",
            sql_files=["schema/users.sql"],
            sql_changes=[
                SQLChange(
                    file_path="schema/users.sql",
                    change_type="added",
                    content="CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));",
                ),
            ],
        )

        mock_store = MagicMock()
        mock_store.check_finding_accepted.return_value = None

        with patch("schemint.ci.ingest.analyze_sql") as mock_analyze:
            mock_analyze.side_effect = Exception("AI service unavailable")

            findings, results = await handler._analyze_diff(
                diff, project_id, mock_store
            )

            # Should not crash — returns a parse_error finding instead
            assert len(findings) >= 1
            error_findings = [f for f in findings if f.type == "parse_error"]
            assert len(error_findings) == 1
            assert "AI service unavailable" in error_findings[0].description
            # No analysis results should be returned for the failed file
            assert len(results) == 0
