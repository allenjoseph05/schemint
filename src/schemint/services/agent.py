"""Multi-turn agentic schema analyzer using Anthropic tool-use loop.

The agent gets tools to inspect tables, analyze relationships, and check
statistics. It *decides* what to investigate and how deep to go, then
submits structured findings.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from schemint.config import get_settings
from schemint.core.analyzer.pre_analysis import (
    SchemaPreAnalysis,
    run_pre_analysis,
    serialize_pre_analysis,
)
from schemint.services.claude import ANALYSIS_TOOL, select_model

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent System Prompt
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
You are Schemint, the SOLE schema analysis engine. You act as a senior database
architect performing a comprehensive schema review. There is NO deterministic
rule engine — you are the only analyzer. Your findings, scores, and
recommendations are the final output.

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

7. CONVENTIONS: When project context is provided, proactively enforce project-
   specific conventions (naming patterns, required columns, forbidden names,
   preferred data types, soft delete requirements). Flag any schema that
   violates the project's stated conventions.

8. SCHEMA DRIFT: When project context includes deprecated/renamed columns or
   migration history, detect usage of deprecated elements and suggest using
   the renamed alternatives. Flag potential schema drift from the project's
   expected state.

SEVERITY LEVELS:
- critical: Will cause data loss, security breach, or system failure
- warning: Performance degradation, data integrity risk, or maintenance burden
- suggestion: Improvement that would make the schema better but isn't urgent

SCORING RUBRIC:
Score each dimension 0-100 (start at 100, deduct):

Structural: -20 missing PK, -15 missing FK where clear, -10 orphaned FK, -5 missing NOT NULL
Performance: -15 missing index on FK, -15 FLOAT for money, -10 no indexes on 5+ col table
Naming: -10 reserved word, -8 inconsistent convention, -5 heavy abbreviation
Best Practices: -10 missing timestamps, -5 missing ON DELETE, -5 no soft delete
Security deductions from best_practices: -25 plaintext password, -15 PII unencrypted

Total = structural*0.30 + performance*0.25 + naming*0.15 + best_practices*0.30

MEMORY RULES:
When the input includes a "memory" section with accepted_findings, DO NOT
report findings that match an accepted pattern. Instead, note them in a
"suppressed" list. If the scope is "pattern", suppress similar findings
across all tables. If the scope is "rule", suppress all findings of that type.

WORKFLOW:
1. Call get_schema_overview to understand the schema structure, topology, and risk signals
2. Call inspect_table on tables with risks or that need deeper investigation
3. Analyze cross-table relationships, domain patterns, and security implications
4. If project context is provided, check convention compliance and schema drift
5. Call submit_analysis with your complete findings, scores, and summary

IMPORTANT:
- You are the ONLY analysis engine. Be thorough — there is no fallback.
- Do NOT call inspect_table on every table. Focus on tables with risks or interesting patterns.
- For simple schemas (1-3 tables), you may call get_schema_overview and submit_analysis directly.
- For complex schemas (10+), focus on the highest-risk tables.
- Always check the schema overview first before inspecting individual tables.
- All final output MUST go through submit_analysis.\
"""


# ---------------------------------------------------------------------------
# Agent Tool Definitions
# ---------------------------------------------------------------------------

GET_SCHEMA_OVERVIEW_TOOL = {
    "name": "get_schema_overview",
    "description": (
        "Get a structural overview of the entire schema: table topology "
        "(hub/leaf/orphan), detected column patterns (money columns, missing "
        "FKs, PII), statistics (FK coverage, index coverage), and risk signals. "
        "Call this first."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

INSPECT_TABLE_TOOL = {
    "name": "inspect_table",
    "description": (
        "Get detailed information about a specific table: all columns with "
        "types and constraints, foreign keys, indexes, detected patterns "
        "(money columns, PII, security risks), and which tables reference "
        "this one. Use this to investigate tables that look risky."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Name of the table to inspect",
            },
        },
        "required": ["table_name"],
    },
}

# Terminal tool — same schema as ANALYSIS_TOOL from claude.py
SUBMIT_ANALYSIS_TOOL = ANALYSIS_TOOL


# ---------------------------------------------------------------------------
# AgentAnalyzer
# ---------------------------------------------------------------------------

class AgentAnalyzer:
    """Multi-turn agentic schema analyzer using Anthropic tool-use loop."""

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
        self.max_turns = settings.claude_max_agent_turns

    def analyze(
        self,
        schema: ParsedSchema,
        app_type: str | None = None,
        project_context: ProjectContext | None = None,
        *,
        memory_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Multi-turn agentic analysis.

        1. Pre-compute structural data for tools
        2. Send lightweight overview to Claude
        3. Loop: Claude calls tools → we execute → repeat
        4. Terminate when submit_analysis is called or max turns reached

        Args:
            schema: Parsed schema to analyze
            app_type: Optional application type for context
            project_context: Optional project context
            memory_context: Optional memory context from build_memory_context()

        Returns:
            Dict with structured AI analysis results
        """
        # Pre-compute data for tools
        pre_analysis = run_pre_analysis(schema, app_type)
        model = select_model(schema)

        # Build initial message (lightweight overview, NOT full schema)
        initial_message = self._build_initial_message(
            schema, app_type, project_context, memory_context,
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": initial_message},
        ]
        tools = [GET_SCHEMA_OVERVIEW_TOOL, INSPECT_TABLE_TOOL, SUBMIT_ANALYSIS_TOOL]

        try:
            for _turn in range(self.max_turns):
                response = self.client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=[{
                        "type": "text",
                        "text": AGENT_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,  # type: ignore[arg-type]
                )

                # Check for terminal tool (submit_analysis)
                for block in response.content:
                    if (
                        block.type == "tool_use"
                        and block.name == "submit_analysis"
                    ):
                        return self._normalize_result(block.input)

                # Process non-terminal tool calls
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(
                            block, schema, pre_analysis,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                if not tool_results:
                    # No tool calls — shouldn't happen, but break to avoid
                    # infinite loop
                    break

                messages.append({
                    "role": "assistant",
                    "content": response.content,
                })
                messages.append({"role": "user", "content": tool_results})

        except Exception as e:
            logger.error("Agent analysis failed: %s", e)
            return {
                "summary": f"AI agent analysis failed: {e}",
                "findings": [],
                "issues": [],
                "good_practices": [],
                "recommendations": [],
                "score": None,
                "error": str(e),
            }

        # Max turns reached
        return {
            "summary": "Agent reached max turns without submitting analysis",
            "findings": [],
            "issues": [],
            "good_practices": [],
            "recommendations": [],
            "score": None,
            "error": "max_turns_reached",
        }

    def _build_initial_message(
        self,
        schema: ParsedSchema,
        app_type: str | None,
        project_context: ProjectContext | None,
        memory_context: dict[str, Any] | None,
    ) -> str:
        """Build a lightweight initial message with table overview.

        Only includes table names, column counts, FK counts, and index counts.
        Full details come via inspect_table tool calls.
        """
        parts: list[str] = ["Analyze this database schema.\n"]

        # Lightweight table overview
        parts.append("TABLES (overview):")
        for table in schema.tables:
            fk_count = len(table.foreign_keys)
            idx_count = len(table.indexes)
            col_count = len(table.columns)
            parts.append(
                f"  {table.name} ({col_count} columns, "
                f"{fk_count} FKs, {idx_count} indexes)"
            )
        parts.append("")

        parts.append(f"DATABASE: {schema.database_type}")
        parts.append(f"APPLICATION TYPE: {app_type or 'general'}")

        # Project context (detailed for convention enforcement)
        if project_context:
            parts.append(f"\nPROJECT: {project_context.project_name}")
            if project_context.description:
                parts.append(f"DESCRIPTION: {project_context.description}")

            # Conventions for the agent to enforce
            if project_context.conventions:
                conv = project_context.conventions
                parts.append("\nPROJECT CONVENTIONS (enforce these):")
                if conv.naming_conventions:
                    for key, value in conv.naming_conventions.items():
                        parts.append(f"  Naming: {key} = {value}")
                if conv.required_columns:
                    parts.append(f"  Required columns: {', '.join(conv.required_columns)}")
                if conv.forbidden_column_names:
                    parts.append(f"  Forbidden names: {', '.join(conv.forbidden_column_names)}")
                if conv.preferred_types:
                    for purpose, dtype in conv.preferred_types.items():
                        parts.append(f"  Preferred type for {purpose}: {dtype}")
                if conv.require_soft_delete:
                    parts.append(f"  Soft delete required: column '{conv.soft_delete_column}'")

            # Deprecated/renamed columns for drift detection
            if project_context.schema_metadata:
                deprecated_items = []
                for meta_table in project_context.schema_metadata.tables:
                    for col in meta_table.columns:
                        if col.deprecated:
                            msg = f"  {meta_table.name}.{col.name} DEPRECATED"
                            if col.deprecated_reason:
                                msg += f": {col.deprecated_reason}"
                            if col.renamed_to:
                                msg += f" (use {col.renamed_to} instead)"
                            deprecated_items.append(msg)
                if deprecated_items:
                    parts.append("\nDEPRECATED COLUMNS (flag if used):")
                    parts.extend(deprecated_items)

        # Memory context
        if memory_context:
            parts.append(
                f"\nMEMORY (previously accepted findings):\n"
                f"{json.dumps(memory_context, separators=(',', ':'))}"
            )

        parts.append(
            "\nUse get_schema_overview to understand the structure, then "
            "inspect_table on tables that need investigation. Submit your "
            "findings when done."
        )

        return "\n".join(parts)

    def _execute_tool(
        self,
        block: Any,
        schema: ParsedSchema,
        pre_analysis: SchemaPreAnalysis,
    ) -> str:
        """Execute a non-terminal tool and return the result string."""
        if block.name == "get_schema_overview":
            return serialize_pre_analysis(pre_analysis)

        if block.name == "inspect_table":
            table_name = block.input.get("table_name", "")
            return self._inspect_table(schema, pre_analysis, table_name)

        return f"Unknown tool: {block.name}"

    def _inspect_table(
        self,
        schema: ParsedSchema,
        pre_analysis: SchemaPreAnalysis,
        table_name: str,
    ) -> str:
        """Build detailed table inspection output."""
        table = schema.get_table(table_name)
        if table is None:
            return f"Table '{table_name}' not found in schema."

        lines: list[str] = []
        lines.append(f"TABLE: {table.name} ({len(table.columns)} columns)")
        lines.append("")

        # Columns
        lines.append("COLUMNS:")
        for col in table.columns:
            flags = []
            if col.is_primary_key:
                flags.append("PK")
            if not col.nullable:
                flags.append("NOT NULL")
            if col.is_auto_increment:
                flags.append("AUTO_INCREMENT")
            if col.is_unique:
                flags.append("UNIQUE")
            if col.default is not None:
                flags.append(f"DEFAULT {col.default}")
            flag_str = f"  {', '.join(flags)}" if flags else ""
            lines.append(f"  {col.name:<20} {col.raw_type:<15}{flag_str}")
        lines.append("")

        # Foreign keys
        if table.foreign_keys:
            lines.append("FOREIGN KEYS:")
            for fk in table.foreign_keys:
                on_del = f" ON DELETE {fk.on_delete}" if fk.on_delete else " (no ON DELETE specified)"
                lines.append(
                    f"  {fk.column} -> {fk.references_table}.{fk.references_column}{on_del}"
                )
            lines.append("")

        # Indexes
        if table.indexes:
            lines.append("INDEXES:")
            for idx in table.indexes:
                unique = " (UNIQUE)" if idx.is_unique else ""
                name = f"{idx.name}: " if idx.name else ""
                lines.append(f"  {name}{', '.join(idx.columns)}{unique}")
            lines.append("")
        else:
            lines.append("INDEXES: (none)")
            lines.append("")

        # Incoming references from topology
        for topo in pre_analysis.topology:
            if topo.name.lower() == table.name.lower() and topo.referenced_by:
                lines.append("INCOMING REFERENCES:")
                for ref_table in topo.referenced_by:
                    # Find the FK details
                    ref_t = schema.get_table(ref_table)
                    if ref_t:
                        for fk in ref_t.foreign_keys:
                            if fk.references_table.lower() == table.name.lower():
                                lines.append(
                                    f"  {ref_table}.{fk.column} -> "
                                    f"{table.name}.{fk.references_column}"
                                )
                lines.append("")
                break

        # Detected patterns for this table
        table_patterns = [
            p for p in pre_analysis.column_patterns
            if p.table.lower() == table.name.lower()
        ]
        if table_patterns:
            lines.append("DETECTED PATTERNS:")
            for p in table_patterns:
                lines.append(f"  - {p.column}: {p.detail}")
            lines.append("")

        # Risk signals for this table
        table_risks = [
            r for r in pre_analysis.risk_signals
            if r.table.lower() == table.name.lower()
        ]
        if table_risks:
            lines.append("RISKS:")
            for r in table_risks:
                lines.append(f"  - [{r.severity.upper()}] {r.signal}: {r.detail}")
            lines.append("")

        return "\n".join(lines)

    def _normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Normalize agent output for backward compatibility."""
        # Map 'findings' to 'issues' key
        if "findings" in result and "issues" not in result:
            result["issues"] = result["findings"]
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_agent_analyzer() -> AgentAnalyzer | None:
    """Get agent analyzer if available and configured."""
    settings = get_settings()

    if not settings.ai_enabled:
        return None

    if not CLAUDE_AVAILABLE:
        return None

    try:
        return AgentAnalyzer()
    except Exception:
        return None
