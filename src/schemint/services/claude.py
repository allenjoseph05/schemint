"""Claude AI service for enhanced schema analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemint.config import get_settings

if TYPE_CHECKING:
    from schemint.memory.models import AcceptedFinding, BusinessRule, SchemaSemantics
    from schemint.models.schema import ParsedSchema


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


def compress_schema(schema: ParsedSchema) -> dict[str, Any]:
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


def select_model(schema: ParsedSchema) -> str:
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
    accepted_findings: list[AcceptedFinding],
    business_rules: list[BusinessRule],
    schema_semantics: list[SchemaSemantics],
) -> dict[str, Any] | None:
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
