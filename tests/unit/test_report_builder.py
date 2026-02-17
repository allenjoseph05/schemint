"""Unit tests for CIReportBuilder."""

from uuid import uuid4

from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    CIReportScore,
    DecisionStatus,
    FindingLocation,
)
from schemint.ci.report_builder import CIReportBuilder
from schemint.models.analysis import AnalysisResult, AnalysisScore


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


class TestBuildSummary:
    """Tests for build_summary."""

    def test_build_summary_pass(self):
        """No findings produces a PASS summary with grade."""
        builder = CIReportBuilder()

        decision = AnalysisDecision(
            project_id="test:repo",
            ref="abc123",
            status=DecisionStatus.PASS,
            findings=[],
            critical_count=0,
            warning_count=0,
            suggestion_count=0,
            duration_ms=45,
            report_score=CIReportScore(
                total=100,
                grade="A",
                label="Excellent",
                structural=100,
                performance=100,
                naming=100,
                best_practices=100,
            ),
        )

        summary = builder.build_summary(decision, [])
        assert "PASS" in summary
        assert "Grade: A" in summary
        assert "100/100" in summary
        assert "Excellent" in summary
        assert "45ms" in summary

    def test_build_summary_with_findings(self):
        """Mixed findings produce sections by severity."""
        builder = CIReportBuilder()

        findings = [
            AnalysisFinding(
                type="security_risk",
                severity="critical",
                title="Plaintext password",
                description="password column stores plaintext",
                location=FindingLocation(
                    file="schema.sql", table="users", column="password"
                ),
            ),
            AnalysisFinding(
                type="missing_timestamps",
                severity="warning",
                title="Missing timestamps",
                description="No created_at column",
                location=FindingLocation(file="schema.sql", table="users"),
            ),
            AnalysisFinding(
                type="no_soft_delete",
                severity="suggestion",
                title="No soft delete",
                description="No deleted_at column",
                location=FindingLocation(file="schema.sql", table="users"),
            ),
        ]

        decision = AnalysisDecision(
            project_id="test:repo",
            ref="abc123",
            status=DecisionStatus.FAIL,
            findings=findings,
            critical_count=1,
            warning_count=1,
            suggestion_count=1,
            duration_ms=50,
            report_score=CIReportScore(
                total=60,
                grade="D",
                label="Needs Work",
                structural=100,
                performance=100,
                naming=100,
                best_practices=50,
            ),
        )

        summary = builder.build_summary(decision, [])
        assert "FAIL" in summary
        assert "### Critical Issues" in summary
        assert "### Warnings" in summary
        assert "### Suggestions" in summary
        assert "Plaintext password" in summary


class TestBuildAnnotations:
    """Tests for build_annotations."""

    def test_finding_with_file_becomes_annotation(self):
        builder = CIReportBuilder()

        findings = [
            AnalysisFinding(
                type="missing_primary_key",
                severity="critical",
                title="Missing PK on users",
                description="Table users has no primary key",
                location=FindingLocation(
                    file="migrations/001.sql", table="users"
                ),
            ),
        ]

        annotations = builder.build_annotations(findings)
        assert len(annotations) == 1
        assert annotations[0].file == "migrations/001.sql"
        assert annotations[0].severity == "critical"
        assert annotations[0].title == "Missing PK on users"
        assert annotations[0].category == "missing_primary_key"

    def test_suppressed_findings_excluded(self):
        builder = CIReportBuilder()

        findings = [
            AnalysisFinding(
                type="missing_timestamps",
                severity="warning",
                title="Missing timestamps",
                description="No created_at",
                location=FindingLocation(file="schema.sql", table="logs"),
                suppressed_by_memory=True,
                memory_context="Accepted by admin",
            ),
        ]

        annotations = builder.build_annotations(findings)
        assert len(annotations) == 0

    def test_findings_without_file_excluded(self):
        builder = CIReportBuilder()

        findings = [
            AnalysisFinding(
                type="missing_timestamps",
                severity="warning",
                title="Missing timestamps",
                description="No created_at",
                location=FindingLocation(table="logs"),
            ),
        ]

        annotations = builder.build_annotations(findings)
        assert len(annotations) == 0


class TestBuildScore:
    """Tests for build_score."""

    def test_score_averages_results(self):
        """Score aggregation averages across all results."""
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
        assert score.total == 70
        assert score.structural == 80
        assert score.performance == 60

    def test_empty_results_perfect_score(self):
        """No results gives perfect score."""
        builder = CIReportBuilder()

        score = builder.build_score([])
        assert score.total == 100
        assert score.grade == "A"

    def test_single_result_score(self):
        """Single result uses its scores directly."""
        builder = CIReportBuilder()

        results = [
            _make_analysis_result(
                total=75, structural=80, performance=70,
                naming=75, best_practices=65,
            ),
        ]

        score = builder.build_score(results)
        assert score.total == 75
        assert score.grade in ("C", "B")
