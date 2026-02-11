"""Pre-analysis engine for schema structural analysis.

Computes structural facts (topology, patterns, statistics, risk signals)
that agent tools return. Zero LLM tokens — pure Python.
"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field

from schemint.core.analyzer.rule_analyzer import (
    DATE_WORDS,
    FK_ID_EXCEPTIONS,
    MONEY_WORDS,
    PII_INDICATORS,
    PII_ENCRYPTION_MARKERS,
    SECURITY_SAFE_SUFFIXES,
    SECURITY_SENSITIVE_NAMES,
)
from schemint.models.schema import ParsedSchema


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

class SchemaDomain(str, Enum):
    ECOMMERCE = "ecommerce"
    SAAS = "saas"
    SOCIAL = "social"
    CMS = "cms"
    FINTECH = "fintech"
    HEALTHCARE = "healthcare"
    IOT = "iot"
    GENERAL = "general"


_DOMAIN_MAP: dict[str, SchemaDomain] = {
    "ecommerce": SchemaDomain.ECOMMERCE,
    "e-commerce": SchemaDomain.ECOMMERCE,
    "shop": SchemaDomain.ECOMMERCE,
    "saas": SchemaDomain.SAAS,
    "social": SchemaDomain.SOCIAL,
    "cms": SchemaDomain.CMS,
    "fintech": SchemaDomain.FINTECH,
    "finance": SchemaDomain.FINTECH,
    "healthcare": SchemaDomain.HEALTHCARE,
    "health": SchemaDomain.HEALTHCARE,
    "iot": SchemaDomain.IOT,
}


def resolve_domain(app_type: str | None = None) -> SchemaDomain:
    """Map user-provided app_type string to SchemaDomain.

    None or unknown → GENERAL. No keyword guessing.
    """
    if app_type is None:
        return SchemaDomain.GENERAL
    return _DOMAIN_MAP.get(app_type.lower().strip(), SchemaDomain.GENERAL)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TableRole(str, Enum):
    HUB = "hub"           # 3+ incoming FKs
    BRIDGE = "bridge"     # 2+ outgoing FKs, few own columns (junction table)
    LEAF = "leaf"         # Has outgoing FKs but no incoming
    ORPHAN = "orphan"     # No FK relationships
    STANDARD = "standard"


class TableTopology(BaseModel):
    name: str
    role: TableRole
    incoming_fk_count: int = 0
    outgoing_fk_count: int = 0
    referenced_by: list[str] = Field(default_factory=list)


class ColumnPattern(BaseModel):
    table: str
    column: str
    pattern: str   # "id_without_fk", "money_as_float", "bool_as_int",
                   # "pii_unencrypted", "security_plaintext", "date_as_string"
    detail: str


class SchemaStatistics(BaseModel):
    table_count: int
    total_columns: int
    avg_columns_per_table: float
    fk_coverage_pct: float
    index_coverage_pct: float
    tables_with_pk_pct: float
    tables_with_timestamps_pct: float


class RiskSignal(BaseModel):
    table: str
    signal: str     # "no_pk", "no_indexes", "wide_table", "security_plaintext"
    severity: str   # "high", "medium", "low"
    detail: str


class SchemaPreAnalysis(BaseModel):
    domain: SchemaDomain
    topology: list[TableTopology]
    column_patterns: list[ColumnPattern]
    statistics: SchemaStatistics
    risk_signals: list[RiskSignal]


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def build_topology(schema: ParsedSchema) -> list[TableTopology]:
    """Build FK topology graph and classify table roles.

    HUB: 3+ incoming FKs (central entity)
    BRIDGE: 2+ outgoing FKs and few own non-FK columns (junction table)
    LEAF: has outgoing FKs but no incoming
    ORPHAN: no FK relationships at all
    STANDARD: everything else
    """
    # Incoming FK map: table_name → list of tables that reference it
    incoming: dict[str, list[str]] = {t.name.lower(): [] for t in schema.tables}
    # Outgoing FK count per table
    outgoing: dict[str, int] = {t.name.lower(): 0 for t in schema.tables}

    for table in schema.tables:
        for fk in table.foreign_keys:
            ref_lower = fk.references_table.lower()
            if ref_lower in incoming:
                incoming[ref_lower].append(table.name)
            outgoing[table.name.lower()] = outgoing.get(table.name.lower(), 0) + 1

    result = []
    for table in schema.tables:
        tname = table.name.lower()
        in_count = len(incoming.get(tname, []))
        out_count = outgoing.get(tname, 0)

        # Classify role
        if in_count >= 3:
            role = TableRole.HUB
        elif out_count >= 2:
            # Bridge if few own columns (junction tables typically have
            # mostly FK columns + maybe an id)
            fk_col_names = {fk.column.lower() for fk in table.foreign_keys}
            non_fk_cols = [
                c for c in table.columns
                if c.name.lower() not in fk_col_names
                and not c.is_primary_key
            ]
            if len(non_fk_cols) <= 2:
                role = TableRole.BRIDGE
            elif in_count == 0:
                role = TableRole.LEAF
            else:
                role = TableRole.STANDARD
        elif out_count > 0 and in_count == 0:
            role = TableRole.LEAF
        elif out_count == 0 and in_count == 0:
            role = TableRole.ORPHAN
        else:
            role = TableRole.STANDARD

        result.append(TableTopology(
            name=table.name,
            role=role,
            incoming_fk_count=in_count,
            outgoing_fk_count=out_count,
            referenced_by=incoming.get(tname, []),
        ))

    return result


# ---------------------------------------------------------------------------
# Column Patterns
# ---------------------------------------------------------------------------

def detect_column_patterns(schema: ParsedSchema) -> list[ColumnPattern]:
    """Detect 6 column patterns using word lists from rule_analyzer."""
    patterns: list[ColumnPattern] = []

    for table in schema.tables:
        fk_columns = {fk.column.lower() for fk in table.foreign_keys}
        pk_set = {pk.lower() for pk in table.primary_key}

        for col in table.columns:
            col_lower = col.name.lower()
            col_type = str(col.data_type).upper()
            raw_upper = col.raw_type.upper()

            # 1. id_without_fk: column ends with _id but no FK constraint
            if (
                col_lower.endswith("_id")
                and col_lower not in FK_ID_EXCEPTIONS
                and col_lower not in pk_set
                and col_lower not in fk_columns
            ):
                patterns.append(ColumnPattern(
                    table=table.name,
                    column=col.name,
                    pattern="id_without_fk",
                    detail=f"{col.name} ends with _id but has no FK constraint",
                ))

            # 2. money_as_float: FLOAT/DOUBLE for money columns
            if ("FLOAT" in col_type or "DOUBLE" in col_type
                    or "FLOAT" in raw_upper or "DOUBLE" in raw_upper):
                if any(word in col_lower for word in MONEY_WORDS):
                    patterns.append(ColumnPattern(
                        table=table.name,
                        column=col.name,
                        pattern="money_as_float",
                        detail=f"{col.name} uses {col.raw_type} for money (should be DECIMAL)",
                    ))

            # 3. bool_as_int: INT for boolean-like columns
            if col_type == "DATATYPE.INT" or raw_upper.startswith("INT"):
                if col_lower.startswith("is_") or col_lower.startswith("has_"):
                    patterns.append(ColumnPattern(
                        table=table.name,
                        column=col.name,
                        pattern="bool_as_int",
                        detail=f"{col.name} uses INT for boolean (should be BOOLEAN)",
                    ))

            # 4. pii_unencrypted: PII column without encryption marker
            if col_lower in PII_INDICATORS:
                has_marker = any(
                    col_lower.endswith(m) for m in PII_ENCRYPTION_MARKERS
                )
                if not has_marker:
                    patterns.append(ColumnPattern(
                        table=table.name,
                        column=col.name,
                        pattern="pii_unencrypted",
                        detail=f"{col.name} contains PII without encryption marker",
                    ))

            # 5. security_plaintext: sensitive column without safe suffix
            for sensitive in SECURITY_SENSITIVE_NAMES:
                if col_lower == sensitive or col_lower.endswith(f"_{sensitive}"):
                    has_safe = any(
                        col_lower.endswith(s) for s in SECURITY_SAFE_SUFFIXES
                    )
                    if not has_safe:
                        patterns.append(ColumnPattern(
                            table=table.name,
                            column=col.name,
                            pattern="security_plaintext",
                            detail=f"{col.name} stores sensitive data without hashing/encryption",
                        ))
                    break  # Only flag once per column

            # 6. date_as_string: VARCHAR/CHAR/TEXT for date columns
            if "VARCHAR" in col_type or "CHAR" in col_type or "TEXT" in col_type:
                if any(word in col_lower for word in DATE_WORDS):
                    patterns.append(ColumnPattern(
                        table=table.name,
                        column=col.name,
                        pattern="date_as_string",
                        detail=f"{col.name} uses string type for date (should be TIMESTAMP/DATE)",
                    ))

    return patterns


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_statistics(schema: ParsedSchema) -> SchemaStatistics:
    """Compute schema-wide statistics and coverage metrics."""
    table_count = schema.table_count
    if table_count == 0:
        return SchemaStatistics(
            table_count=0,
            total_columns=0,
            avg_columns_per_table=0.0,
            fk_coverage_pct=0.0,
            index_coverage_pct=0.0,
            tables_with_pk_pct=0.0,
            tables_with_timestamps_pct=0.0,
        )

    total_columns = sum(len(t.columns) for t in schema.tables)
    avg_columns = total_columns / table_count

    # FK coverage: % of _id columns that have FK constraints
    id_cols_total = 0
    id_cols_with_fk = 0
    for table in schema.tables:
        fk_columns = {fk.column.lower() for fk in table.foreign_keys}
        pk_set = {pk.lower() for pk in table.primary_key}
        for col in table.columns:
            col_lower = col.name.lower()
            if col_lower.endswith("_id") and col_lower not in FK_ID_EXCEPTIONS and col_lower not in pk_set:
                id_cols_total += 1
                if col_lower in fk_columns:
                    id_cols_with_fk += 1

    fk_coverage = (id_cols_with_fk / id_cols_total * 100) if id_cols_total > 0 else 100.0

    # Index coverage: % of tables that have at least one non-PK index
    tables_with_indexes = sum(
        1 for t in schema.tables
        if any(not idx.is_primary for idx in t.indexes)
    )
    index_coverage = tables_with_indexes / table_count * 100

    # PK coverage
    tables_with_pk = sum(1 for t in schema.tables if t.has_primary_key())
    pk_pct = tables_with_pk / table_count * 100

    # Timestamp coverage
    tables_with_ts = sum(1 for t in schema.tables if t.has_timestamps())
    ts_pct = tables_with_ts / table_count * 100

    return SchemaStatistics(
        table_count=table_count,
        total_columns=total_columns,
        avg_columns_per_table=round(avg_columns, 1),
        fk_coverage_pct=round(fk_coverage, 1),
        index_coverage_pct=round(index_coverage, 1),
        tables_with_pk_pct=round(pk_pct, 1),
        tables_with_timestamps_pct=round(ts_pct, 1),
    )


# ---------------------------------------------------------------------------
# Risk Signals
# ---------------------------------------------------------------------------

def detect_risk_signals(schema: ParsedSchema) -> list[RiskSignal]:
    """Detect high-level risk signals across the schema."""
    signals: list[RiskSignal] = []

    for table in schema.tables:
        # no_pk
        if not table.has_primary_key():
            signals.append(RiskSignal(
                table=table.name,
                signal="no_pk",
                severity="high",
                detail=f"Table '{table.name}' has no primary key",
            ))

        # no_indexes (only flag if table has 5+ columns)
        has_non_pk_index = any(not idx.is_primary for idx in table.indexes)
        if not has_non_pk_index and len(table.columns) >= 5:
            signals.append(RiskSignal(
                table=table.name,
                signal="no_indexes",
                severity="medium",
                detail=f"Table '{table.name}' has {len(table.columns)} columns but no indexes",
            ))

        # wide_table (15+ columns)
        if len(table.columns) >= 15:
            signals.append(RiskSignal(
                table=table.name,
                signal="wide_table",
                severity="low",
                detail=f"Table '{table.name}' has {len(table.columns)} columns (consider splitting)",
            ))

        # security_plaintext
        for col in table.columns:
            col_lower = col.name.lower()
            for sensitive in SECURITY_SENSITIVE_NAMES:
                if col_lower == sensitive or col_lower.endswith(f"_{sensitive}"):
                    has_safe = any(
                        col_lower.endswith(s) for s in SECURITY_SAFE_SUFFIXES
                    )
                    if not has_safe:
                        signals.append(RiskSignal(
                            table=table.name,
                            signal="security_plaintext",
                            severity="high",
                            detail=f"Column '{table.name}.{col.name}' stores sensitive data in plaintext",
                        ))
                    break

    return signals


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_pre_analysis(
    schema: ParsedSchema,
    app_type: str | None = None,
) -> SchemaPreAnalysis:
    """Run complete pre-analysis on a schema. Zero LLM tokens."""
    return SchemaPreAnalysis(
        domain=resolve_domain(app_type),
        topology=build_topology(schema),
        column_patterns=detect_column_patterns(schema),
        statistics=compute_statistics(schema),
        risk_signals=detect_risk_signals(schema),
    )


# ---------------------------------------------------------------------------
# Serialization for agent tools
# ---------------------------------------------------------------------------

def serialize_pre_analysis(pre: SchemaPreAnalysis) -> str:
    """Serialize pre-analysis to a compact string for agent tool output."""
    lines: list[str] = []

    lines.append(f"DOMAIN: {pre.domain.value}")
    lines.append("")

    # Topology
    lines.append("TOPOLOGY:")
    for t in pre.topology:
        refs = f" (referenced by: {', '.join(t.referenced_by)})" if t.referenced_by else ""
        lines.append(
            f"  {t.name}: {t.role.value} "
            f"(in={t.incoming_fk_count}, out={t.outgoing_fk_count}){refs}"
        )
    lines.append("")

    # Statistics
    s = pre.statistics
    lines.append("STATISTICS:")
    lines.append(f"  Tables: {s.table_count}")
    lines.append(f"  Total columns: {s.total_columns}")
    lines.append(f"  Avg columns/table: {s.avg_columns_per_table}")
    lines.append(f"  FK coverage: {s.fk_coverage_pct}%")
    lines.append(f"  Index coverage: {s.index_coverage_pct}%")
    lines.append(f"  Tables with PK: {s.tables_with_pk_pct}%")
    lines.append(f"  Tables with timestamps: {s.tables_with_timestamps_pct}%")
    lines.append("")

    # Column patterns
    if pre.column_patterns:
        lines.append("COLUMN PATTERNS:")
        for p in pre.column_patterns:
            lines.append(f"  [{p.pattern}] {p.table}.{p.column}: {p.detail}")
        lines.append("")

    # Risk signals
    if pre.risk_signals:
        lines.append("RISK SIGNALS:")
        for r in pre.risk_signals:
            lines.append(f"  [{r.severity.upper()}] {r.table}: {r.signal} — {r.detail}")
        lines.append("")

    return "\n".join(lines)
