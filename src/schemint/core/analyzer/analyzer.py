"""Main analyzer - orchestrates parsing and analysis."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from schemint.config import get_settings
from schemint.core.analyzer.rule_analyzer import RuleAnalyzer
from schemint.core.parser import parse_sql
from schemint.models.analysis import AnalysisResult, AnalysisScore, TableSummary
from schemint.models.issue import Issue, IssueCategory, IssueSeverity
from schemint.models.schema import ParsedSchema

if TYPE_CHECKING:
    from schemint.core.context.models import ProjectContext


def _map_ai_category(category: str) -> IssueCategory:
    """Map AI category string to IssueCategory enum."""
    category_map = {
        "structural": IssueCategory.MISSING_CONSTRAINT,
        "performance": IssueCategory.MISSING_INDEX,
        "security": IssueCategory.SECURITY_RISK,
        "naming": IssueCategory.NAMING_CONVENTION,
        "best_practices": IssueCategory.MISSING_TIMESTAMPS,
        "scalability": IssueCategory.NO_MULTI_TENANCY,
    }
    return category_map.get(category.lower(), IssueCategory.OTHER)


def _map_ai_severity(severity: str) -> IssueSeverity:
    """Map AI severity string to IssueSeverity enum."""
    severity_map = {
        "critical": IssueSeverity.CRITICAL,
        "warning": IssueSeverity.WARNING,
        "suggestion": IssueSeverity.SUGGESTION,
    }
    return severity_map.get(severity.lower(), IssueSeverity.SUGGESTION)


def _convert_ai_issues(ai_issues: list[dict]) -> list[Issue]:
    """Convert AI response issues to Issue models."""
    issues = []
    for ai_issue in ai_issues:
        try:
            issue = Issue(
                severity=_map_ai_severity(ai_issue.get("severity", "suggestion")),
                category=_map_ai_category(ai_issue.get("category", "other")),
                title=ai_issue.get("title", "Unknown Issue"),
                description=ai_issue.get("description", ""),
                table_name=ai_issue.get("table_name"),
                column_name=ai_issue.get("column_name"),
                impact=ai_issue.get("impact"),
                fix_script=ai_issue.get("fix_script"),
            )
            issues.append(issue)
        except Exception:
            continue
    return issues


def calculate_score(
    issues: list[Issue],
    table_count: int,
) -> AnalysisScore:
    """Calculate analysis scores based on issues found."""
    # Count by severity
    critical_count = len([i for i in issues if i.severity == IssueSeverity.CRITICAL])
    warning_count = len([i for i in issues if i.severity == IssueSeverity.WARNING])
    suggestion_count = len([i for i in issues if i.severity == IssueSeverity.SUGGESTION])

    # Calculate total score (start at 100, deduct points)
    total = 100
    total -= critical_count * 15
    total -= warning_count * 5
    total -= suggestion_count * 2
    total = max(0, min(100, total))

    # Calculate category scores (simplified)
    structural = 100 - (
        len([i for i in issues if "primary" in i.category.value or "foreign" in i.category.value])
        * 20
    )
    performance = 100 - (
        len([i for i in issues if "index" in i.category.value or "type" in i.category.value]) * 15
    )
    naming = 100 - (
        len([i for i in issues if "naming" in i.category.value or "reserved" in i.category.value])
        * 10
    )
    best_practices = 100 - (
        len([i for i in issues if "timestamp" in i.category.value or "cascade" in i.category.value])
        * 10
    )

    return AnalysisScore(
        total=max(0, min(100, total)),
        structural=max(0, min(100, structural)),
        performance=max(0, min(100, performance)),
        naming=max(0, min(100, naming)),
        best_practices=max(0, min(100, best_practices)),
    )


def analyze_schema(
    schema: ParsedSchema,
    use_ai: bool = False,
    app_type: str | None = None,
    project_context: "ProjectContext | None" = None,
) -> AnalysisResult:
    """
    Analyze a parsed schema.

    Args:
        schema: Parsed schema to analyze
        use_ai: Whether to use AI for enhanced analysis
        app_type: Application type for context (e.g., 'ecommerce', 'saas')
        project_context: Optional project context for schema-aware analysis

    Returns:
        AnalysisResult with issues and scores
    """
    start_time = time.time()
    analysis_id = f"ana_{uuid.uuid4().hex[:12]}"

    # Run rule-based analysis first (always)
    rule_analyzer = RuleAnalyzer()
    rule_issues, good_practices = rule_analyzer.analyze(schema)

    # Run convention checking if project context is provided
    context_issues: list[Issue] = []
    if project_context:
        from schemint.core.context.conventions import check_conventions
        context_issues = check_conventions(schema, project_context)

        # Add good practice for having project context
        good_practices.append("Project context loaded for schema-aware analysis")

        # Add context-specific good practices
        if project_context.conventions:
            if project_context.conventions.require_soft_delete:
                for table in schema.tables:
                    if table.has_column(project_context.conventions.soft_delete_column):
                        good_practices.append(f"Table '{table.name}' has soft delete support")

    # AI analysis
    ai_summary = None
    ai_issues: list[Issue] = []
    ai_recommendations: list[str] = []

    settings = get_settings()
    if use_ai and settings.ai_enabled:
        try:
            from schemint.services.claude import get_claude_analyzer

            analyzer = get_claude_analyzer()
            if analyzer:
                # Pass project context to AI for enhanced analysis
                ai_result = analyzer.analyze_sync(schema, app_type, project_context)

                ai_summary = ai_result.get("summary")
                ai_issues = _convert_ai_issues(ai_result.get("issues", []))
                ai_recommendations = ai_result.get("recommendations", [])

                # Add AI-found good practices
                ai_good = ai_result.get("good_practices", [])
                good_practices.extend([f"{p}" for p in ai_good])

        except Exception as e:
            ai_summary = f"AI analysis failed: {e}"

    # Merge issues (rule-based + context + AI, deduplicated by title)
    all_issues = rule_issues.copy()
    seen_titles = {i.title.lower() for i in rule_issues}

    # Add context issues
    for ctx_issue in context_issues:
        if ctx_issue.title.lower() not in seen_titles:
            all_issues.append(ctx_issue)
            seen_titles.add(ctx_issue.title.lower())

    # Add AI issues
    for ai_issue in ai_issues:
        if ai_issue.title.lower() not in seen_titles:
            all_issues.append(ai_issue)
            seen_titles.add(ai_issue.title.lower())

    # Calculate scores
    score = calculate_score(all_issues, schema.table_count)

    # Count issues by severity
    critical_count = len([i for i in all_issues if i.severity == IssueSeverity.CRITICAL])
    warning_count = len([i for i in all_issues if i.severity == IssueSeverity.WARNING])
    suggestion_count = len([i for i in all_issues if i.severity == IssueSeverity.SUGGESTION])

    # Create table summaries
    tables = [TableSummary.from_table(t) for t in schema.tables]

    # Calculate duration
    duration_ms = int((time.time() - start_time) * 1000)

    # Build AI summary with recommendations
    final_summary = ai_summary
    if ai_recommendations:
        rec_text = "\n".join(f"- {r}" for r in ai_recommendations)
        if final_summary:
            final_summary = f"{final_summary}\n\nRecommendations:\n{rec_text}"
        else:
            final_summary = f"Recommendations:\n{rec_text}"

    # Add project context info to summary
    if project_context and final_summary:
        final_summary = f"[Project: {project_context.project_name}]\n\n{final_summary}"
    elif project_context:
        final_summary = f"[Project: {project_context.project_name}] Analysis complete."

    return AnalysisResult(
        id=analysis_id,
        created_at=datetime.utcnow(),
        duration_ms=duration_ms,
        score=score,
        tables=tables,
        table_count=schema.table_count,
        issues=all_issues,
        critical_count=critical_count,
        warning_count=warning_count,
        suggestion_count=suggestion_count,
        good_practices=good_practices,
        ai_summary=final_summary,
    )


def analyze_sql(
    sql: str,
    database_type: str = "mysql",
    use_ai: bool = False,
    app_type: str | None = None,
    project_context: "ProjectContext | None" = None,
) -> AnalysisResult:
    """
    Analyze SQL schema string.

    Args:
        sql: SQL CREATE TABLE statements
        database_type: Database type (mysql, postgresql, sqlite)
        use_ai: Whether to use AI for enhanced analysis
        app_type: Application type for context
        project_context: Optional project context for schema-aware analysis

    Returns:
        AnalysisResult with issues and scores
    """
    schema = parse_sql(sql, database_type)
    return analyze_schema(
        schema,
        use_ai=use_ai,
        app_type=app_type,
        project_context=project_context,
    )


def analyze_sql_with_context(
    sql: str,
    context_source: str | dict,
    database_type: str = "mysql",
    use_ai: bool = False,
    app_type: str | None = None,
) -> AnalysisResult:
    """
    Analyze SQL with project context loaded from a source.

    Args:
        sql: SQL CREATE TABLE statements
        context_source: Path to context file/directory or context dict
        database_type: Database type
        use_ai: Whether to use AI
        app_type: Application type

    Returns:
        AnalysisResult with context-aware analysis
    """
    from schemint.core.context import load_context

    project_context = load_context(context_source)
    return analyze_sql(
        sql,
        database_type=database_type,
        use_ai=use_ai,
        app_type=app_type,
        project_context=project_context,
    )


# Alias for convenience
analyze = analyze_sql
