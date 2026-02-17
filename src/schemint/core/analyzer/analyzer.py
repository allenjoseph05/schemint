"""Main analyzer - orchestrates AI-powered schema analysis.

The AI agent is the sole analysis path. No deterministic rule-based fallback.
Requires CLAUDE_API_KEY to be configured.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from schemint.config import get_settings
from schemint.core.parser import parse_sql
from schemint.models.analysis import AnalysisResult, AnalysisScore, TableSummary
from schemint.models.issue import Issue, IssueCategory, IssueSeverity
from schemint.models.schema import ParsedSchema

if TYPE_CHECKING:
    from schemint.core.context.models import ProjectContext

logger = logging.getLogger(__name__)


def _map_ai_category(category: str) -> IssueCategory:
    """Map AI category string to IssueCategory enum."""
    category_map = {
        "structural": IssueCategory.MISSING_CONSTRAINT,
        "performance": IssueCategory.MISSING_INDEX,
        "security": IssueCategory.SECURITY_RISK,
        "naming": IssueCategory.NAMING_CONVENTION,
        "best_practices": IssueCategory.MISSING_TIMESTAMPS,
        "scalability": IssueCategory.NO_MULTI_TENANCY,
        "domain": IssueCategory.DOMAIN,
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


def _convert_ai_issues(ai_issues: list[dict[str, Any]]) -> list[Issue]:
    """Convert AI response issues to Issue models."""
    issues = []
    for ai_issue in ai_issues:
        try:
            # Build description: append reasoning if present
            description = ai_issue.get("description", "")
            reasoning = ai_issue.get("reasoning")
            if reasoning and reasoning not in description:
                description = f"{description}\n\nReasoning: {reasoning}"

            issue = Issue(
                severity=_map_ai_severity(ai_issue.get("severity", "suggestion")),
                category=_map_ai_category(ai_issue.get("category", "other")),
                title=ai_issue.get("title", "Unknown Issue"),
                description=description,
                table_name=ai_issue.get("table_name"),
                column_name=ai_issue.get("column_name"),
                impact=ai_issue.get("impact"),
                fix_description=ai_issue.get("fix_description"),
                fix_script=ai_issue.get("fix_script"),
            )
            issues.append(issue)
        except Exception:
            continue
    return issues


def _resolve_project_id(project_id: str) -> UUID | None:
    """Resolve a project_id string to a UUID.

    Tries UUID parse first, then falls back to external_id lookup.
    Returns None if the project cannot be found or DB is unavailable.
    """
    # Try direct UUID parse
    try:
        return UUID(project_id)
    except (ValueError, AttributeError):
        pass

    # Fall back to external_id lookup
    try:
        from schemint.memory.store import get_memory_store
        store = get_memory_store()
        project = store.get_project_by_external_id(project_id)
        if project:
            return project.id
    except Exception:
        pass

    return None


def _retrieve_memory(project_id: str) -> dict[str, Any] | None:
    """Retrieve memory context for a project. Returns None on any failure."""
    try:
        resolved_id = _resolve_project_id(project_id)
        if resolved_id is None:
            logger.debug("Could not resolve project_id=%s", project_id)
            return None

        from schemint.memory.store import get_memory_store
        from schemint.services.claude import build_memory_context

        store = get_memory_store()
        accepted = store.get_accepted_findings(resolved_id)
        rules = store.get_business_rules(resolved_id)
        semantics = store.get_schema_semantics(resolved_id)

        return build_memory_context(accepted, rules, semantics)
    except Exception as exc:
        logger.debug("Memory retrieval failed for project_id=%s: %s", project_id, exc)
        return None


def analyze_schema(
    schema: ParsedSchema,
    app_type: str | None = None,
    project_context: ProjectContext | None = None,
    project_id: str | None = None,
) -> AnalysisResult:
    """
    Analyze a parsed schema using the AI agent.

    The AI agent is the sole analysis path. If CLAUDE_API_KEY is not
    configured, returns an error result.

    Args:
        schema: Parsed schema to analyze
        app_type: Application type for context (e.g., 'ecommerce', 'saas')
        project_context: Optional project context for schema-aware analysis
        project_id: Optional project ID for memory-enriched analysis

    Returns:
        AnalysisResult with issues and scores
    """
    start_time = time.time()
    analysis_id = f"ana_{uuid.uuid4().hex[:12]}"

    good_practices: list[str] = []
    ai_summary = None
    ai_issues: list[Issue] = []
    ai_recommendations: list[str] = []
    suppressed_count = 0

    # Retrieve memory context if project_id is provided
    memory_context: dict[str, Any] | None = None
    if project_id:
        memory_context = _retrieve_memory(project_id)

    # AI agent is the sole analysis path
    settings = get_settings()
    if not settings.ai_enabled:
        # No API key — return error result
        duration_ms = int((time.time() - start_time) * 1000)
        tables = [TableSummary.from_table(t) for t in schema.tables]
        return AnalysisResult(
            id=analysis_id,
            created_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            score=AnalysisScore(
                total=0, structural=0, performance=0, naming=0, best_practices=0,
            ),
            tables=tables,
            table_count=schema.table_count,
            issues=[],
            critical_count=0,
            warning_count=0,
            suggestion_count=0,
            good_practices=[],
            ai_summary="AI analysis unavailable: CLAUDE_API_KEY is not configured.",
        )

    try:
        from schemint.services.agent import get_agent_analyzer

        agent = get_agent_analyzer()
        if not agent:
            raise RuntimeError("AI agent could not be initialized")

        ai_result = agent.analyze(
            schema, app_type, project_context,
            memory_context=memory_context,
        )

        if ai_result:
            ai_summary = ai_result.get("summary")
            ai_issues = _convert_ai_issues(ai_result.get("issues", []))
            ai_recommendations = ai_result.get("recommendations", [])

            # Extract suppressed findings from AI response
            suppressed = ai_result.get("suppressed", [])
            suppressed_count = len(suppressed)
            if suppressed:
                suppressed_types = {
                    s.get("type", "").lower() for s in suppressed
                }
                ai_issues = [
                    issue for issue in ai_issues
                    if issue.category.value not in suppressed_types
                ]

            # Extract AI scores
            ai_score_data = ai_result.get("score")
            if isinstance(ai_score_data, dict) and "total" in ai_score_data:
                score = AnalysisScore(
                    total=max(0, min(100, ai_score_data["total"])),
                    structural=max(0, min(100, ai_score_data.get("structural", 100))),
                    performance=max(0, min(100, ai_score_data.get("performance", 100))),
                    naming=max(0, min(100, ai_score_data.get("naming", 100))),
                    best_practices=max(0, min(100, ai_score_data.get("best_practices", 100))),
                )
            else:
                score = AnalysisScore(
                    total=0, structural=0, performance=0, naming=0, best_practices=0,
                )

            # Add AI-found good practices
            ai_good = ai_result.get("good_practices", [])
            good_practices.extend([f"{p}" for p in ai_good])

    except Exception as e:
        ai_summary = f"AI analysis failed: {e}"
        score = AnalysisScore(
            total=0, structural=0, performance=0, naming=0, best_practices=0,
        )

    all_issues = ai_issues

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

    # Add suppressed count to summary
    if suppressed_count > 0 and final_summary:
        final_summary = f"{final_summary}\n\n[{suppressed_count} finding(s) suppressed by project memory]"
    elif suppressed_count > 0:
        final_summary = f"[{suppressed_count} finding(s) suppressed by project memory]"

    # Add project context info to summary
    if project_context and final_summary:
        final_summary = f"[Project: {project_context.project_name}]\n\n{final_summary}"
    elif project_context:
        final_summary = f"[Project: {project_context.project_name}] Analysis complete."

    return AnalysisResult(
        id=analysis_id,
        created_at=datetime.now(timezone.utc),
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
    app_type: str | None = None,
    project_context: ProjectContext | None = None,
    project_id: str | None = None,
) -> AnalysisResult:
    """
    Analyze SQL schema string.

    Args:
        sql: SQL CREATE TABLE statements
        database_type: Database type (mysql, postgresql, sqlite)
        app_type: Application type for context
        project_context: Optional project context for schema-aware analysis
        project_id: Optional project ID for memory-enriched analysis

    Returns:
        AnalysisResult with issues and scores
    """
    schema = parse_sql(sql, database_type)
    return analyze_schema(
        schema,
        app_type=app_type,
        project_context=project_context,
        project_id=project_id,
    )


def analyze_sql_with_context(
    sql: str,
    context_source: str | dict[str, Any],
    database_type: str = "mysql",
    app_type: str | None = None,
) -> AnalysisResult:
    """
    Analyze SQL with project context loaded from a source.

    Args:
        sql: SQL CREATE TABLE statements
        context_source: Path to context file/directory or context dict
        database_type: Database type
        app_type: Application type

    Returns:
        AnalysisResult with context-aware analysis
    """
    from schemint.core.context import load_context

    project_context = load_context(context_source)
    return analyze_sql(
        sql,
        database_type=database_type,
        app_type=app_type,
        project_context=project_context,
    )


# Alias for convenience
analyze = analyze_sql
