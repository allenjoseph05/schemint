"""
CI Report Builder.

Builds markdown summaries, PR annotations, and score breakdowns
from analysis results for CI pipeline output.
"""

from __future__ import annotations

from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    CIAnnotation,
    CIReportScore,
)
from schemint.models.analysis import AnalysisResult


class CIReportBuilder:
    """Builds CI-friendly reports from analysis results."""

    def build_summary(
        self,
        decision: AnalysisDecision,
        analysis_results: list[AnalysisResult],
    ) -> str:
        """Build a markdown summary for CI logs.

        Args:
            decision: The analysis decision
            analysis_results: List of AnalysisResult from each analyzed file

        Returns:
            Markdown formatted report string
        """
        status_label = decision.status.value.upper()
        grade = decision.report_score.grade if decision.report_score else "N/A"
        score_total = decision.report_score.total if decision.report_score else 0
        score_label = decision.report_score.label if decision.report_score else "N/A"

        # Check if any result has AI analysis
        has_ai = any(r.ai_summary for r in analysis_results)
        ai_badge = " | AI-Enhanced" if has_ai else ""

        lines = [
            f"## Schemint Schema Analysis: {status_label} (Grade: {grade}){ai_badge}",
            "",
            f"Score: {score_total}/100 | {score_label}",
            "",
        ]

        # Severity counts table
        lines.extend([
            "| Severity | Count |",
            "|----------|-------|",
            f"| Critical | {decision.critical_count} |",
            f"| Warning | {decision.warning_count} |",
            f"| Suggestion | {decision.suggestion_count} |",
            "",
        ])

        # AI Analysis section
        if has_ai:
            lines.append("### AI Analysis")
            for result in analysis_results:
                if result.ai_summary:
                    lines.append(result.ai_summary)
                    lines.append("")

        # Group active findings by severity
        active = [f for f in decision.findings if not f.suppressed_by_memory]
        critical = [f for f in active if f.severity == "critical"]
        warnings = [f for f in active if f.severity == "warning"]
        suggestions = [f for f in active if f.severity == "suggestion"]

        if critical:
            lines.append("### Critical Issues")
            lines.extend(self._findings_table(critical))
            lines.append("")

        if warnings:
            lines.append("### Warnings")
            lines.extend(self._findings_table(warnings))
            lines.append("")

        if suggestions:
            lines.append("### Suggestions")
            lines.extend(self._findings_table(suggestions))
            lines.append("")

        # Footer
        duration = decision.duration_ms
        lines.extend([
            "---",
            f"_Analysis completed in {duration}ms by Schemint_",
        ])

        return "\n".join(lines)

    def build_annotations(
        self,
        findings: list[AnalysisFinding],
    ) -> list[CIAnnotation]:
        """Convert findings to CI annotations for inline PR comments.

        Only includes findings that have a file location and are not suppressed.
        """
        annotations = []
        for finding in findings:
            if finding.suppressed_by_memory:
                continue
            if not finding.location or not finding.location.file:
                continue

            location_parts = []
            if finding.location.table:
                location_parts.append(finding.location.table)
            if finding.location.column:
                location_parts.append(finding.location.column)
            location_str = ".".join(location_parts) if location_parts else ""

            message = finding.description
            if location_str:
                message = f"[{location_str}] {message}"

            annotations.append(
                CIAnnotation(
                    file=finding.location.file,
                    line=finding.location.line,
                    severity=finding.severity,
                    title=finding.title,
                    message=message,
                    category=finding.type,
                )
            )
        return annotations

    def build_score(
        self,
        analysis_results: list[AnalysisResult],
    ) -> CIReportScore:
        """Aggregate scores from AnalysisResult objects.

        Averages the AI-computed scores from each AnalysisResult.
        All analysis is AI-powered — no deterministic fallback.

        Args:
            analysis_results: List of results from analyzed files

        Returns:
            CIReportScore with aggregated scores
        """
        if not analysis_results:
            return CIReportScore(
                total=100,
                grade="A",
                label="Excellent",
                structural=100,
                performance=100,
                naming=100,
                best_practices=100,
            )

        # Average scores across all results
        n = len(analysis_results)
        total = sum(r.score.total for r in analysis_results) // n
        structural = sum(r.score.structural for r in analysis_results) // n
        performance = sum(r.score.performance for r in analysis_results) // n
        naming = sum(r.score.naming for r in analysis_results) // n
        best_practices = sum(r.score.best_practices for r in analysis_results) // n

        from schemint.models.analysis import AnalysisScore
        avg_score = AnalysisScore(
            total=max(0, min(100, total)),
            structural=max(0, min(100, structural)),
            performance=max(0, min(100, performance)),
            naming=max(0, min(100, naming)),
            best_practices=max(0, min(100, best_practices)),
        )

        return CIReportScore(
            total=avg_score.total,
            grade=avg_score.grade,
            label=avg_score.label,
            structural=avg_score.structural,
            performance=avg_score.performance,
            naming=avg_score.naming,
            best_practices=avg_score.best_practices,
        )

    def _findings_table(self, findings: list[AnalysisFinding]) -> list[str]:
        """Build a markdown table of findings."""
        lines = [
            "| Location | Issue | Description |",
            "|----------|-------|-------------|",
        ]
        for f in findings:
            loc_parts = []
            if f.location and f.location.table:
                loc_parts.append(f.location.table)
            if f.location and f.location.column:
                loc_parts.append(f.location.column)
            location = ".".join(loc_parts) if loc_parts else f.location.file if f.location else "—"
            # Escape pipes in text
            title = f.title.replace("|", "\\|")
            desc = f.description.replace("|", "\\|")[:80]
            lines.append(f"| {location} | {title} | {desc} |")
        return lines
