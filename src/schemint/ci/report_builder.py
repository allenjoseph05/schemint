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
from schemint.core.analyzer.analyzer import calculate_score
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

        lines = [
            f"## Schemint Schema Analysis: {status_label} (Grade: {grade})",
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

        # Collect all issues across results
        all_issues = []
        total_tables = 0
        for result in analysis_results:
            all_issues.extend(result.issues)
            total_tables += result.table_count

        # Calculate aggregate score
        score = calculate_score(all_issues, total_tables)

        return CIReportScore(
            total=score.total,
            grade=score.grade,
            label=score.label,
            structural=score.structural,
            performance=score.performance,
            naming=score.naming,
            best_practices=score.best_practices,
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
