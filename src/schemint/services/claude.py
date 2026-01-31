"""Claude AI service for enhanced schema analysis."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from schemint.config import get_settings

if TYPE_CHECKING:
    from schemint.core.context.models import ProjectContext
    from schemint.models.schema import ParsedSchema

# Try to import anthropic SDK
try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]


class ClaudeAnalyzer:
    """Analyzes schemas using Claude AI."""

    def __init__(self) -> None:
        settings = get_settings()

        if not CLAUDE_AVAILABLE:
            raise RuntimeError(
                "anthropic not installed. "
                "Install with: pip install anthropic"
            )

        if not settings.claude_api_key:
            raise RuntimeError(
                "CLAUDE_API_KEY not set. "
                "Get your key from: https://console.anthropic.com/"
            )

        # Initialize the client
        self.client = anthropic.Anthropic(api_key=settings.claude_api_key)
        self.model_name = settings.claude_model

    async def analyze(
        self,
        schema: "ParsedSchema",
        app_type: str | None = None,
        project_context: "ProjectContext | None" = None,
    ) -> dict:
        """
        Analyze schema using Claude AI (async).

        Args:
            schema: Parsed schema to analyze
            app_type: Optional application type for context
            project_context: Optional project context for schema-aware analysis

        Returns:
            Dict with AI analysis results
        """
        # Claude's Python SDK doesn't have native async, use sync in thread
        return self.analyze_sync(schema, app_type, project_context)

    def analyze_sync(
        self,
        schema: "ParsedSchema",
        app_type: str | None = None,
        project_context: "ProjectContext | None" = None,
    ) -> dict:
        """Synchronous version of analyze."""
        prompt = self._build_prompt(schema, app_type, project_context)

        message = self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        # Extract text from response
        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        return self._parse_response(response_text)

    def _build_prompt(
        self,
        schema: "ParsedSchema",
        app_type: str | None,
        project_context: "ProjectContext | None" = None,
    ) -> str:
        """Build the analysis prompt."""
        schema_text = self._schema_to_sql(schema)

        app_context = ""
        if app_type:
            app_context = f"""
Application Type: {app_type}
Consider industry-specific best practices for {app_type} applications.
"""

        project_context_section = ""
        if project_context:
            project_context_section = self._build_project_context_section(project_context)

        return f"""You are a senior database architect with 20+ years of experience.
Analyze this database schema and provide detailed feedback.

{app_context}
{project_context_section}

Schema to analyze:
```sql
{schema_text}
```

Analyze for:
1. STRUCTURAL ISSUES: Missing primary keys, foreign keys, constraints, relationships
2. PERFORMANCE ISSUES: Missing indexes, inefficient data types, N+1 query risks
3. SECURITY ISSUES: PII exposure, sensitive data handling, encryption needs
4. NAMING ISSUES: Convention violations, reserved words, clarity
5. BEST PRACTICES: Timestamps, soft deletes, audit fields, normalization
6. SCALABILITY: Multi-tenancy readiness, sharding considerations

For each issue:
- Explain WHY it matters with real-world consequences
- Provide exact SQL fix scripts
- Rate severity as "critical", "warning", or "suggestion"

Also identify what the schema does well.

Respond with this exact JSON structure (no markdown code blocks, just raw JSON):
{{
    "summary": "Brief 2-3 sentence summary of the schema quality",
    "issues": [
        {{
            "severity": "critical|warning|suggestion",
            "category": "structural|performance|security|naming|best_practices|scalability",
            "title": "Short descriptive title",
            "description": "Detailed explanation of the issue",
            "table_name": "affected_table or null",
            "column_name": "affected_column or null",
            "impact": "Real-world consequences of this issue",
            "fix_script": "SQL to fix the issue"
        }}
    ],
    "good_practices": [
        "Things the schema does well"
    ],
    "recommendations": [
        "High-level recommendations for improvement"
    ],
    "estimated_score": 0-100
}}"""

    def _build_project_context_section(
        self,
        project_context: "ProjectContext",
    ) -> str:
        """Build the project context section for the prompt."""
        sections = []

        sections.append("## PROJECT CONTEXT")
        sections.append(f"Project: {project_context.project_name}")

        if project_context.description:
            sections.append(f"Description: {project_context.description}")

        # Schema metadata
        if project_context.schema_metadata:
            meta = project_context.schema_metadata
            sections.append("\n### Known Schema:")

            for table in meta.tables:
                sections.append(f"\nTable: {table.name}")
                if table.description:
                    sections.append(f"  Purpose: {table.description}")

                # Deprecated columns
                deprecated_cols = [c for c in table.columns if c.deprecated]
                if deprecated_cols:
                    sections.append("  DEPRECATED COLUMNS (should not be used in new queries):")
                    for col in deprecated_cols:
                        msg = f"    - {col.name}"
                        if col.deprecated_reason:
                            msg += f": {col.deprecated_reason}"
                        if col.renamed_to:
                            msg += f" (renamed to: {col.renamed_to})"
                        sections.append(msg)

                # Renamed columns
                renamed_cols = [c for c in table.columns if c.renamed_from]
                if renamed_cols:
                    sections.append("  RENAMED COLUMNS:")
                    for col in renamed_cols:
                        sections.append(f"    - {col.name} (was: {col.renamed_from})")

        # Migration history highlights
        if project_context.migrations:
            sections.append("\n### Recent Schema Changes:")
            # Show last 5 migrations
            for migration in project_context.migrations[-5:]:
                sections.append(f"  - {migration.version}: {migration.description}")
                if migration.deprecated_columns:
                    for dep in migration.deprecated_columns:
                        sections.append(f"      DEPRECATED: {dep}")
                if migration.renamed_columns:
                    for old, new in migration.renamed_columns.items():
                        sections.append(f"      RENAMED: {old} -> {new}")

        # Conventions
        if project_context.conventions:
            conv = project_context.conventions
            sections.append("\n### Project Conventions:")

            if conv.naming_conventions:
                sections.append("  Naming:")
                for key, value in conv.naming_conventions.items():
                    sections.append(f"    - {key}: {value}")

            if conv.required_columns:
                sections.append(f"  Required columns for all tables: {', '.join(conv.required_columns)}")

            if conv.forbidden_column_names:
                sections.append(f"  Forbidden column names: {', '.join(conv.forbidden_column_names)}")

            if conv.preferred_types:
                sections.append("  Preferred types:")
                for purpose, dtype in conv.preferred_types.items():
                    sections.append(f"    - {purpose}: {dtype}")

        sections.append("\n### IMPORTANT:")
        sections.append("- Flag any queries that reference deprecated columns")
        sections.append("- Suggest using renamed columns instead of old names")
        sections.append("- Enforce project-specific conventions")
        sections.append("- Explain schema intent based on the context above")

        return "\n".join(sections)

    def _schema_to_sql(self, schema: "ParsedSchema") -> str:
        """Convert parsed schema back to SQL for the prompt."""
        lines = []

        for table in schema.tables:
            lines.append(f"CREATE TABLE {table.name} (")

            col_defs = []
            for col in table.columns:
                col_def = f"    {col.name} {col.raw_type}"
                if not col.nullable:
                    col_def += " NOT NULL"
                if col.default:
                    col_def += f" DEFAULT {col.default}"
                if col.is_auto_increment:
                    col_def += " AUTO_INCREMENT"
                if col.is_primary_key and len(table.primary_key) == 1:
                    col_def += " PRIMARY KEY"
                col_defs.append(col_def)

            # Composite primary key
            if len(table.primary_key) > 1:
                col_defs.append(f"    PRIMARY KEY ({', '.join(table.primary_key)})")

            # Foreign keys
            for fk in table.foreign_keys:
                fk_def = f"    FOREIGN KEY ({fk.column}) REFERENCES {fk.references_table}({fk.references_column})"
                if fk.on_delete:
                    fk_def += f" ON DELETE {fk.on_delete}"
                if fk.on_update:
                    fk_def += f" ON UPDATE {fk.on_update}"
                col_defs.append(fk_def)

            lines.append(",\n".join(col_defs))
            lines.append(");")
            lines.append("")

        return "\n".join(lines)

    def _parse_response(self, response_text: str) -> dict:
        """Parse the AI response."""
        try:
            # Clean up response if needed
            text = response_text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            return {
                "summary": "AI analysis failed to parse",
                "issues": [],
                "good_practices": [],
                "recommendations": [],
                "estimated_score": None,
                "error": str(e),
                "raw_response": response_text[:500],
            }


def get_claude_analyzer() -> ClaudeAnalyzer | None:
    """Get Claude analyzer if available and configured."""
    settings = get_settings()

    if not settings.ai_enabled:
        return None

    if not CLAUDE_AVAILABLE:
        return None

    try:
        return ClaudeAnalyzer()
    except Exception:
        return None
