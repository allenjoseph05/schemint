"""Claude AI service for enhanced schema analysis."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from schemint.config import get_settings

if TYPE_CHECKING:
    from schemint.core.context.models import ProjectContext
    from schemint.memory.models import AcceptedFinding, BusinessRule, SchemaSemantics
    from schemint.models.schema import ParsedSchema

# Try to import anthropic SDK
try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# System prompt — replaces hundreds of lines of Python rule-checking code.
# Cached via Anthropic's cache_control for 90% cost discount on repeated calls.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are Schemint, an expert database schema analyzer. You analyze SQL schemas
and produce structured findings about issues, risks, and improvements.

You have deep knowledge of:
- Relational database design (MySQL, PostgreSQL, SQLite)
- Data type selection (when to use DECIMAL vs FLOAT, VARCHAR vs TEXT, etc.)
- Security best practices (hashing, encryption, PII handling)
- Performance optimization (indexing strategies, query patterns)
- Naming conventions across different ecosystems
- Migration safety (locking, backward compatibility)
- Domain-specific patterns (e-commerce, SaaS, fintech, healthcare)
- Regulatory considerations (GDPR, HIPAA, PCI-DSS)

ANALYSIS DIMENSIONS:

1. STRUCTURAL: Primary keys, foreign keys, constraints, referential integrity,
   normalization. Missing relationships between tables that should be related.

2. PERFORMANCE: Indexing (especially on FK columns and common query patterns),
   data type efficiency, potential N+1 query risks from schema design.

3. SECURITY: Plaintext sensitive data, PII without encryption markers,
   SSRF-prone URL columns, columns that could leak data in logs.

4. NAMING: Consistency, readability, reserved word conflicts, abbreviation
   quality, semantic clarity.

5. BEST PRACTICES: Timestamps, soft deletes, audit columns, cascade actions,
   proper NULL handling for required fields.

6. DOMAIN: Business logic alignment, missing domain-expected columns,
   constraint gaps that allow invalid business states.

SEVERITY LEVELS:
- critical: Will cause data loss, security breach, or system failure
- warning: Performance degradation, data integrity risk, or maintenance burden
- suggestion: Improvement that would make the schema better but isn't urgent

MEMORY RULES:
When the input includes a "memory" section with accepted_findings, DO NOT
report findings that match an accepted pattern. Instead, note them in a
"suppressed" list. If the scope is "pattern", suppress similar findings
across all tables. If the scope is "rule", suppress all findings of that type.

OUTPUT:
Use the submit_analysis tool to return your structured analysis. Do not return
free-form text — all output must go through the tool.\
"""


# ---------------------------------------------------------------------------
# Tool definition — forces structured output via Anthropic tool use.
# Eliminates all JSON parsing issues; response is guaranteed to match schema.
# ---------------------------------------------------------------------------

ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": "Submit the schema analysis results",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "suggestion"],
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "structural",
                                "performance",
                                "security",
                                "naming",
                                "best_practices",
                                "domain",
                            ],
                        },
                        "title": {"type": "string", "maxLength": 120},
                        "description": {"type": "string"},
                        "table_name": {"type": ["string", "null"]},
                        "column_name": {"type": ["string", "null"]},
                        "impact": {"type": "string"},
                        "fix_description": {"type": "string"},
                        "fix_script": {"type": ["string", "null"]},
                        "reasoning": {"type": "string"},
                    },
                    "required": [
                        "severity",
                        "category",
                        "title",
                        "description",
                        "impact",
                        "reasoning",
                    ],
                },
            },
            "suppressed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "table": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["type", "table", "reason"],
                },
            },
            "score": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer", "minimum": 0, "maximum": 100},
                    "structural": {"type": "integer", "minimum": 0, "maximum": 100},
                    "performance": {"type": "integer", "minimum": 0, "maximum": 100},
                    "naming": {"type": "integer", "minimum": 0, "maximum": 100},
                    "best_practices": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": [
                    "total",
                    "structural",
                    "performance",
                    "naming",
                    "best_practices",
                ],
            },
            "good_practices": {"type": "array", "items": {"type": "string"}},
            "recommendations": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["findings", "score", "good_practices", "summary"],
    },
}


def compress_schema(schema: "ParsedSchema") -> dict:
    """Compress a ParsedSchema into a compact dict for the prompt.

    Strips null/false/default fields, shortens keys, and omits redundant data.
    Results in ~60% fewer input tokens vs reconstructed SQL.
    """
    tables = []
    for table in schema.tables:
        cols = []
        for col in table.columns:
            c: dict[str, Any] = {"name": col.name, "type": col.raw_type}
            if col.is_primary_key:
                c["pk"] = True
            if not col.nullable:
                c["nn"] = True
            if col.is_auto_increment:
                c["auto"] = True
            if col.is_unique:
                c["uniq"] = True
            if col.default is not None:
                c["default"] = col.default
            cols.append(c)

        t: dict[str, Any] = {"name": table.name, "cols": cols}

        if len(table.primary_key) > 1:
            t["pk"] = table.primary_key

        if table.foreign_keys:
            fks = []
            for fk in table.foreign_keys:
                fk_entry: dict[str, Any] = {
                    "col": fk.column,
                    "ref": f"{fk.references_table}.{fk.references_column}",
                }
                if fk.on_delete:
                    fk_entry["on_del"] = fk.on_delete
                if fk.on_update:
                    fk_entry["on_upd"] = fk.on_update
                fks.append(fk_entry)
            t["fks"] = fks

        if table.indexes:
            idxs = []
            for idx in table.indexes:
                idx_entry: dict[str, Any] = {"cols": idx.columns}
                if idx.is_unique:
                    idx_entry["uniq"] = True
                idxs.append(idx_entry)
            t["idxs"] = idxs

        tables.append(t)

    return {"tables": tables, "db": schema.database_type}


def select_model(schema: "ParsedSchema") -> str:
    """Select Claude model tier based on schema complexity.

    - Simple  (1-3 tables, <=20 cols): Haiku  (fast, cheap)
    - Medium  (4-15 tables):           Sonnet (default)
    - Complex (16+ tables):            Sonnet complex variant
    """
    settings = get_settings()
    table_count = schema.table_count
    total_cols = sum(len(t.columns) for t in schema.tables)

    if table_count <= 3 and total_cols <= 20:
        return settings.claude_model_simple
    if table_count >= 16:
        return settings.claude_model_complex
    return settings.claude_model


def build_memory_context(
    accepted_findings: list["AcceptedFinding"],
    business_rules: list["BusinessRule"],
    schema_semantics: list["SchemaSemantics"],
) -> dict | None:
    """Build memory context dict for injection into the Claude prompt.

    Returns a dict with accepted_findings, business_rules, and semantics
    formatted for the LLM, or None if all three lists are empty.
    """
    if not accepted_findings and not business_rules and not schema_semantics:
        return None

    context: dict[str, Any] = {}

    if accepted_findings:
        context["accepted_findings"] = [
            {
                "type": af.finding_type,
                "table": af.context.get("table"),
                "column": af.context.get("column"),
                "reason": af.reason,
                "scope": af.scope.value,
            }
            for af in accepted_findings
        ]

    if business_rules:
        context["business_rules"] = [
            {
                "rule": br.rule_type,
                "severity": br.severity.value,
                "applies_to": br.applies_to,
                "rationale": br.rationale,
            }
            for br in business_rules
        ]

    if schema_semantics:
        context["semantics"] = [
            {
                "path": ss.element_path,
                "tags": ss.semantic_tags,
                "description": ss.description,
            }
            for ss in schema_semantics
        ]

    return context


class ClaudeAnalyzer:
    """Analyzes schemas using Claude AI with structured tool-use output."""

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

        self.client = anthropic.Anthropic(api_key=settings.claude_api_key)

    async def analyze(
        self,
        schema: "ParsedSchema",
        app_type: str | None = None,
        project_context: "ProjectContext | None" = None,
        *,
        model_override: str | None = None,
        memory_context: dict | None = None,
    ) -> dict:
        """Analyze schema using Claude AI (async wrapper).

        Args:
            schema: Parsed schema to analyze
            app_type: Optional application type for context
            project_context: Optional project context for schema-aware analysis
            model_override: Force a specific model (for enterprise/manual analysis)
            memory_context: Optional memory context from build_memory_context()

        Returns:
            Dict with structured AI analysis results
        """
        return self.analyze_sync(
            schema, app_type, project_context,
            model_override=model_override, memory_context=memory_context,
        )

    def analyze_sync(
        self,
        schema: "ParsedSchema",
        app_type: str | None = None,
        project_context: "ProjectContext | None" = None,
        *,
        model_override: str | None = None,
        memory_context: dict | None = None,
    ) -> dict:
        """Synchronous schema analysis using Claude with tool use.

        1. Compress schema to compact JSON
        2. Select model tier (or use override)
        3. Build user message with context + memory
        4. Call Claude with cached system prompt + forced tool use
        5. Extract structured result from tool_use block
        """
        # 1. Compress schema
        compressed = compress_schema(schema)

        # 2. Select model
        model = model_override or select_model(schema)

        # 3. Build user message
        user_message = self._build_user_message(
            compressed, app_type, project_context, schema.database_type,
            memory_context=memory_context,
        )

        # 4. Call Claude with system prompt (cached) + tool use (forced)
        try:
            message = self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
                tools=[ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "submit_analysis"},
            )
        except Exception as e:
            return {
                "summary": f"AI analysis failed: {e}",
                "findings": [],
                "issues": [],
                "good_practices": [],
                "recommendations": [],
                "score": None,
                "error": str(e),
            }

        # 5. Extract structured result from tool_use block
        return self._extract_tool_result(message)

    def _build_user_message(
        self,
        compressed_schema: dict,
        app_type: str | None,
        project_context: "ProjectContext | None",
        database_type: str,
        *,
        memory_context: dict | None = None,
    ) -> str:
        """Build the user message with compressed schema, context, and memory."""
        parts = ["Analyze this database schema:\n"]

        parts.append(f"SCHEMA:\n{json.dumps(compressed_schema, separators=(',', ':'))}\n")

        parts.append(f"DATABASE: {database_type}")
        parts.append(f"APPLICATION TYPE: {app_type or 'general'}")

        if project_context:
            parts.append(self._build_project_context_section(project_context))

        # Add memory section
        if memory_context:
            parts.append(
                f"MEMORY (previously accepted findings for this project):\n"
                f"{json.dumps(memory_context, separators=(',', ':'))}"
            )
        else:
            parts.append("MEMORY: No previous findings accepted.")

        return "\n\n".join(parts)

    def _build_project_context_section(
        self,
        project_context: "ProjectContext",
    ) -> str:
        """Build the project context section for the prompt."""
        sections = []

        sections.append("PROJECT CONTEXT:")
        sections.append(f"Project: {project_context.project_name}")

        if project_context.description:
            sections.append(f"Description: {project_context.description}")

        # Schema metadata
        if project_context.schema_metadata:
            meta = project_context.schema_metadata
            sections.append("\nKnown Schema:")

            for table in meta.tables:
                sections.append(f"\nTable: {table.name}")
                if table.description:
                    sections.append(f"  Purpose: {table.description}")

                deprecated_cols = [c for c in table.columns if c.deprecated]
                if deprecated_cols:
                    sections.append("  DEPRECATED COLUMNS:")
                    for col in deprecated_cols:
                        msg = f"    - {col.name}"
                        if col.deprecated_reason:
                            msg += f": {col.deprecated_reason}"
                        if col.renamed_to:
                            msg += f" (renamed to: {col.renamed_to})"
                        sections.append(msg)

                renamed_cols = [c for c in table.columns if c.renamed_from]
                if renamed_cols:
                    sections.append("  RENAMED COLUMNS:")
                    for col in renamed_cols:
                        sections.append(f"    - {col.name} (was: {col.renamed_from})")

        # Migration history
        if project_context.migrations:
            sections.append("\nRecent Schema Changes:")
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
            sections.append("\nProject Conventions:")

            if conv.naming_conventions:
                sections.append("  Naming:")
                for key, value in conv.naming_conventions.items():
                    sections.append(f"    - {key}: {value}")

            if conv.required_columns:
                sections.append(
                    f"  Required columns: {', '.join(conv.required_columns)}"
                )

            if conv.forbidden_column_names:
                sections.append(
                    f"  Forbidden names: {', '.join(conv.forbidden_column_names)}"
                )

            if conv.preferred_types:
                sections.append("  Preferred types:")
                for purpose, dtype in conv.preferred_types.items():
                    sections.append(f"    - {purpose}: {dtype}")

        sections.append("\nIMPORTANT:")
        sections.append("- Flag any queries that reference deprecated columns")
        sections.append("- Suggest using renamed columns instead of old names")
        sections.append("- Enforce project-specific conventions")

        return "\n".join(sections)

    def _extract_tool_result(self, message: Any) -> dict:
        """Extract the structured analysis from a tool_use response block."""
        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_analysis":
                result = block.input
                # Normalize: map 'findings' to 'issues' key for backward compat
                if "findings" in result and "issues" not in result:
                    result["issues"] = result["findings"]
                return result

        # Fallback: no tool_use block found (shouldn't happen with forced tool)
        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        return {
            "summary": "AI analysis returned unexpected format",
            "findings": [],
            "issues": [],
            "good_practices": [],
            "recommendations": [],
            "score": None,
            "error": "No tool_use block in response",
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
