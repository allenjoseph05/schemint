"""Analysis endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from schemint.config import get_settings
from schemint.core.analyzer import analyze_sql
from schemint.core.parser import SQLParserError
from schemint.models.analysis import AnalysisRequest, AnalysisResult

router = APIRouter()


class ContextAwareRequest(BaseModel):
    """Request for context-aware analysis."""

    sql: str = Field(
        ...,
        min_length=10,
        max_length=100000,
        description="SQL CREATE TABLE statements",
    )
    database_type: str = Field(
        default="mysql",
        description="Target database type",
    )
    app_type: str | None = Field(
        None,
        description="Application type (e.g., 'ecommerce', 'saas')",
    )
    project_context: dict[str, Any] = Field(
        ...,
        description="Project context configuration",
    )


class ProjectContextResponse(BaseModel):
    """Response with loaded project context info."""

    project_name: str
    description: str | None
    table_count: int
    migration_count: int
    deprecated_tables: list[str]
    deprecated_columns: list[str]
    has_conventions: bool


@router.post("", response_model=AnalysisResult)
async def analyze_schema(
    request: AnalysisRequest,
    use_ai: bool = Query(False, description="Enable AI-powered analysis (requires CLAUDE_API_KEY)"),
    project_id: str | None = Query(None, description="Project ID for memory-enriched analysis"),
) -> AnalysisResult:
    """
    Analyze a database schema.

    Submit SQL CREATE TABLE statements and get back:
    - Parsed table structure
    - Issues found with severity levels
    - Overall score (0-100)
    - Fix scripts for each issue
    - AI-generated insights (if use_ai=true)

    Set `use_ai=true` to enable Claude AI analysis for deeper insights.
    Requires CLAUDE_API_KEY environment variable.

    Optionally provide `project_id` (UUID or external ID like "github:org/repo")
    to enable memory-enriched analysis with suppressed findings and AI scores.
    """
    try:
        # Check if AI is requested but not available
        settings = get_settings()
        if use_ai and not settings.ai_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI analysis requested but CLAUDE_API_KEY is not configured",
            )

        # Get app_type from context if provided
        app_type = None
        if request.context:
            app_type = request.context.app_type

        result = analyze_sql(
            sql=request.sql,
            database_type=request.database_type,
            use_ai=use_ai,
            app_type=app_type,
            project_id=project_id,
        )
        return result

    except SQLParserError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse SQL: {e!s}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {e!s}",
        )


@router.post("/quick", response_model=dict)
async def quick_analyze(
    request: AnalysisRequest,
    use_ai: bool = Query(False, description="Enable AI-powered analysis"),
    project_id: str | None = Query(None, description="Project ID for memory-enriched analysis"),
) -> dict:
    """
    Quick analysis - returns just score and issue counts.

    Useful for CI/CD pipelines where you just need pass/fail.
    """
    try:
        app_type = None
        if request.context:
            app_type = request.context.app_type

        result = analyze_sql(
            sql=request.sql,
            database_type=request.database_type,
            use_ai=use_ai,
            app_type=app_type,
            project_id=project_id,
        )

        return {
            "score": result.score.total,
            "grade": result.score.grade,
            "passed": result.score.total >= 70,
            "critical_count": result.critical_count,
            "warning_count": result.warning_count,
            "suggestion_count": result.suggestion_count,
            "table_count": result.table_count,
            "ai_enabled": use_ai,
            "ai_summary": result.ai_summary if use_ai else None,
        }

    except SQLParserError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse SQL: {e!s}",
        )


@router.post("/with-context", response_model=AnalysisResult)
async def analyze_with_context(
    request: ContextAwareRequest,
    use_ai: bool = Query(False, description="Enable AI-powered analysis"),
) -> AnalysisResult:
    """
    Analyze SQL with project context.

    Provides schema-aware analysis that:
    - Flags usage of deprecated tables/columns
    - Suggests renamed column alternatives
    - Enforces project-specific conventions
    - Explains schema intent based on context

    The project_context should include:
    - project_name: Name of the project
    - schema: Schema metadata with tables, columns, deprecations
    - conventions: Project-specific SQL conventions
    - migrations: (optional) Migration history
    """
    try:
        from schemint.core.context import load_context

        # Check if AI is requested but not available
        settings = get_settings()
        if use_ai and not settings.ai_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI analysis requested but CLAUDE_API_KEY is not configured",
            )

        # Load project context from dict
        project_context = load_context(request.project_context)

        result = analyze_sql(
            sql=request.sql,
            database_type=request.database_type,
            use_ai=use_ai,
            app_type=request.app_type,
            project_context=project_context,
        )
        return result

    except SQLParserError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse SQL: {e!s}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Context-aware analysis failed: {e!s}",
        )


@router.post("/validate-context", response_model=ProjectContextResponse)
async def validate_context(
    context: dict[str, Any],
) -> ProjectContextResponse:
    """
    Validate and summarize a project context configuration.

    Use this to verify your context file is valid before running analysis.
    Returns a summary of what the context contains.
    """
    try:
        from schemint.core.context import load_context

        project_context = load_context(context)

        # Get deprecated elements
        deprecated = project_context.get_deprecated_elements()

        return ProjectContextResponse(
            project_name=project_context.project_name,
            description=project_context.description,
            table_count=len(project_context.schema_metadata.tables)
            if project_context.schema_metadata
            else 0,
            migration_count=len(project_context.migrations),
            deprecated_tables=deprecated.get("tables", []),
            deprecated_columns=deprecated.get("columns", []),
            has_conventions=project_context.conventions is not None,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid context: {e!s}",
        )
