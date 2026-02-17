"""DDL extraction for non-table objects: sequences, enums, functions, matviews, extensions.

Strategy:
    1. sqlglot parses the full DDL into statements (strips comments, handles quoting).
    2. For statement types sqlglot supports (sequences, matviews): walk the AST directly.
    3. For types sqlglot falls back to Command (enums, functions, extensions):
       apply regex to the individual statement text — NOT the raw DDL.

This avoids false positives from DDL inside comments, dollar-quoted function
bodies, or string literals, while still handling syntax sqlglot doesn't model.
"""

from __future__ import annotations

import contextlib
import re
from typing import Literal

from schemint.drift.models import (
    EnumSnapshot,
    ExtensionSnapshot,
    FunctionSnapshot,
    MaterializedViewSnapshot,
    SequenceSnapshot,
)


def extract_sequences_from_ddl(sql: str) -> dict[str, SequenceSnapshot]:
    """Extract CREATE SEQUENCE statements from DDL using sqlglot AST.

    Note: sqlglot loses the NO from 'NO CYCLE' (outputs just 'CYCLE'),
    so we check the original SQL for CYCLE/NO CYCLE disambiguation.
    """
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
    except ImportError:
        return _extract_sequences_regex(sql)

    sequences: dict[str, SequenceSnapshot] = {}

    try:
        statements = sqlglot.parse(sql)
    except Exception:
        return _extract_sequences_regex(sql)

    for stmt in statements:
        if stmt is None:
            continue
        if not isinstance(stmt, sqlglot_exp.Create):
            if isinstance(stmt, sqlglot_exp.Command):
                cmd_text = stmt.sql()
                # Skip Commands that are function definitions (may contain
                # inner DDL that should not be extracted as top-level objects)
                if not re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", cmd_text, re.IGNORECASE):
                    for k, v in _extract_sequences_regex(cmd_text + ";").items():
                        sequences.setdefault(k, v)
            continue

        kind = stmt.args.get("kind")
        if not kind or str(kind).upper() != "SEQUENCE":
            continue

        table_expr = stmt.this
        if not table_expr or not hasattr(table_expr, "name"):
            continue
        name = table_expr.name.lower()

        seq = SequenceSnapshot(name=name)

        # AS data_type is in stmt.args["expression"] as a DataType node
        expr = stmt.args.get("expression")
        if expr is not None and type(expr).__name__ == "DataType":
            dtype = str(expr).lower().strip()
            if dtype in ("smallint", "integer", "bigint"):
                seq.data_type = dtype

        # Extract properties from SequenceProperties node
        props = stmt.args.get("properties")
        if props:
            for prop in props.expressions:
                if isinstance(prop, sqlglot_exp.SequenceProperties):
                    # Use the ORIGINAL sql for CYCLE detection (sqlglot loses NO)
                    _apply_sequence_properties(seq, prop, sql)

        sequences[name] = seq

    return sequences


def _apply_sequence_properties(
    seq: SequenceSnapshot, prop: object, stmt_sql: str
) -> None:
    """Apply parsed SequenceProperties to a SequenceSnapshot."""
    args = getattr(prop, "args", {})

    increment = args.get("increment")
    if increment is not None:
        with contextlib.suppress(ValueError, TypeError):
            seq.increment_by = int(str(increment))

    start = args.get("start")
    if start is not None:
        with contextlib.suppress(ValueError, TypeError):
            seq.start_value = int(str(start))

    minvalue = args.get("minvalue")
    if minvalue is not None:
        with contextlib.suppress(ValueError, TypeError):
            seq.min_value = int(str(minvalue))

    maxvalue = args.get("maxvalue")
    if maxvalue is not None:
        with contextlib.suppress(ValueError, TypeError):
            seq.max_value = int(str(maxvalue))

    cache = args.get("cache")
    if cache is not None:
        with contextlib.suppress(ValueError, TypeError):
            seq.cache_size = int(str(cache))

    # sqlglot may produce Var("CYCLE") for both CYCLE and NO CYCLE.
    # Check the original SQL to disambiguate.
    stmt_upper = stmt_sql.upper()
    if re.search(r"\bNO\s+CYCLE\b", stmt_upper):
        seq.cycle = False
    elif re.search(r"\bCYCLE\b", stmt_upper):
        seq.cycle = True


def extract_enums_from_ddl(sql: str) -> dict[str, EnumSnapshot]:
    """Extract CREATE TYPE ... AS ENUM statements from DDL.

    sqlglot falls back to Command for CREATE TYPE, so we parse statements
    with sqlglot (to strip comments/dollar-quoting) then regex each Command.
    """
    enums: dict[str, EnumSnapshot] = {}

    for stmt_text in _safe_statement_texts(sql):
        match = re.search(
            r"CREATE\s+TYPE\s+(?:\"?(\w+)\"?\.)?\"?(\w+)\"?\s+AS\s+ENUM\s*\("
            r"(.*?)\)",
            stmt_text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue

        name = match.group(2).lower()
        values_str = match.group(3)
        values = re.findall(r"'([^']*)'", values_str)
        enums[name] = EnumSnapshot(name=name, values=values)

    return enums


def extract_functions_from_ddl(sql: str) -> dict[str, FunctionSnapshot]:
    """Extract CREATE FUNCTION/PROCEDURE statements from DDL.

    sqlglot falls back to Command for CREATE FUNCTION, so we parse statements
    with sqlglot then regex each Command. Dollar-quoting is handled by
    a dedicated regex that matches paired $tag$...$tag$ delimiters.
    """
    functions: dict[str, FunctionSnapshot] = {}

    for stmt_text in _safe_statement_texts(sql):
        stripped = stmt_text.strip()

        # Full pattern: with dollar-quoted body
        match = re.search(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
            r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?\s*\("
            r"(.*?)\)\s*"
            r"RETURNS\s+([\w\s]+?)\s+"
            r"(?:LANGUAGE\s+(\w+)\s+)?"
            r"(?:(?:IMMUTABLE|STABLE|VOLATILE)\s+)?"
            r"AS\s+(\$\w*\$)(.*?)\6",
            stripped,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            name = match.group(2).lower()
            args = match.group(3).strip()
            return_type = match.group(4).strip().lower()
            language = (match.group(5) or "sql").lower()
            body = match.group(7).strip()

            volatility = _detect_volatility(match.group(0))

            functions[name] = FunctionSnapshot(
                name=name,
                argument_types=args,
                return_type=return_type,
                language=language,
                volatility=volatility,
                definition=body,
            )
            continue

        # Simple pattern: no body (just signature + LANGUAGE)
        match = re.search(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
            r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?\s*\("
            r"(.*?)\)\s*"
            r"RETURNS\s+([\w\s]+?)\s+"
            r"LANGUAGE\s+(\w+)",
            stripped,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            name = match.group(2).lower()
            if name in functions:
                continue

            volatility = _detect_volatility(stripped)

            functions[name] = FunctionSnapshot(
                name=name,
                argument_types=match.group(3).strip(),
                return_type=match.group(4).strip().lower(),
                language=match.group(5).lower(),
                volatility=volatility,
            )

    return functions


def _detect_volatility(text: str) -> Literal["volatile", "stable", "immutable"]:
    """Detect function volatility from SQL text."""
    if re.search(r"\bIMMUTABLE\b", text, re.IGNORECASE):
        return "immutable"
    if re.search(r"\bSTABLE\b", text, re.IGNORECASE):
        return "stable"
    return "volatile"


def extract_materialized_views_from_ddl(sql: str) -> dict[str, MaterializedViewSnapshot]:
    """Extract CREATE MATERIALIZED VIEW statements from DDL.

    Uses sqlglot AST when available (matviews without WITH NO DATA).
    Falls back to regex for edge cases sqlglot doesn't handle.
    """
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
    except ImportError:
        return _extract_matviews_regex(sql)

    matviews: dict[str, MaterializedViewSnapshot] = {}

    try:
        statements = sqlglot.parse(sql)
    except Exception:
        return _extract_matviews_regex(sql)

    for stmt in statements:
        if stmt is None:
            continue

        # sqlglot parses matviews as Create(kind=VIEW) + MaterializedProperty
        if isinstance(stmt, sqlglot_exp.Create):
            kind = stmt.args.get("kind")
            if not kind or str(kind).upper() != "VIEW":
                continue

            # Check for MaterializedProperty
            props = stmt.args.get("properties")
            is_materialized = False
            if props:
                for prop in props.expressions:
                    if type(prop).__name__ == "MaterializedProperty":
                        is_materialized = True
                        break
            if not is_materialized:
                continue

            table_expr = stmt.this
            if not table_expr or not hasattr(table_expr, "name"):
                continue
            name = table_expr.name.lower()

            select_expr = stmt.args.get("expression")
            definition = select_expr.sql() if select_expr else ""

            source_tables: list[str] = []
            if select_expr:
                for tbl in select_expr.find_all(sqlglot_exp.Table):
                    if tbl.name and tbl.name.lower() != name:
                        source_tables.append(tbl.name.lower())

            stmt_sql = stmt.sql().upper()
            is_populated = "WITH NO DATA" not in stmt_sql

            matviews[name] = MaterializedViewSnapshot(
                name=name,
                definition=definition,
                is_populated=is_populated,
                source_tables=sorted(set(source_tables)),
            )
            continue

        # Fallback for Command (e.g. WITH NO DATA causes parse fallback)
        if isinstance(stmt, sqlglot_exp.Command):
            stmt_text = stmt.sql()
            for k, v in _extract_matviews_regex(stmt_text + ";").items():
                matviews.setdefault(k, v)

    return matviews


def extract_extensions_from_ddl(sql: str) -> dict[str, ExtensionSnapshot]:
    """Extract CREATE EXTENSION statements from DDL.

    sqlglot falls back to Command for CREATE EXTENSION, so we parse
    statements with sqlglot then regex each Command.
    """
    extensions: dict[str, ExtensionSnapshot] = {}

    for stmt_text in _safe_statement_texts(sql):
        match = re.search(
            r"CREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r"\"?(\w+)\"?"
            r"(.*?)$",
            stmt_text.strip().rstrip(";"),
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue

        name = match.group(1).lower()
        options = match.group(2)

        version = ""
        ver_match = re.search(r"VERSION\s+'([^']+)'", options, re.IGNORECASE)
        if ver_match:
            version = ver_match.group(1)

        schema = "public"
        schema_match = re.search(r"SCHEMA\s+(\w+)", options, re.IGNORECASE)
        if schema_match:
            schema = schema_match.group(1).lower()

        extensions[name] = ExtensionSnapshot(
            name=name,
            version=version,
            installed_schema=schema,
        )

    return extensions


# =============================================================================
# Internal helpers
# =============================================================================


def _safe_statement_texts(sql: str) -> list[str]:
    """Split SQL into individual statement texts using sqlglot.

    sqlglot strips comments and handles dollar-quoting, so regex applied
    to these individual texts won't match false positives inside comments
    or function bodies. Block comments may be preserved as /* ... */ in
    the output text, so we strip those too.
    """
    try:
        import sqlglot

        statements = sqlglot.parse(sql)
        texts = []
        for stmt in statements:
            if stmt is None:
                continue
            text = stmt.sql()
            # Strip block comments that sqlglot may preserve in Command text
            text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
            text = text.strip()
            if text:
                texts.append(text)
        return texts
    except Exception:
        # Last resort: strip comments manually, split on semicolons
        cleaned = _strip_sql_comments(sql)
        return [s.strip() for s in cleaned.split(";") if s.strip()]


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments (line and block) from raw SQL text."""
    # Remove block comments
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove line comments
    return re.sub(r"--[^\n]*", " ", sql)


def _extract_sequences_regex(sql: str) -> dict[str, SequenceSnapshot]:
    """Regex fallback for sequence extraction (used when sqlglot unavailable)."""
    sequences: dict[str, SequenceSnapshot] = {}
    pattern = re.compile(
        r"CREATE\s+SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?"
        r"(.*?);",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(sql):
        name = match.group(2).lower()
        options = match.group(3)
        seq = SequenceSnapshot(name=name)

        inc = re.search(r"INCREMENT\s+(?:BY\s+)?(\d+)", options, re.IGNORECASE)
        if inc:
            seq.increment_by = int(inc.group(1))

        start = re.search(r"START\s+(?:WITH\s+)?(\d+)", options, re.IGNORECASE)
        if start:
            seq.start_value = int(start.group(1))

        minval = re.search(r"MINVALUE\s+(\d+)", options, re.IGNORECASE)
        if minval:
            seq.min_value = int(minval.group(1))

        maxval = re.search(r"MAXVALUE\s+(\d+)", options, re.IGNORECASE)
        if maxval:
            seq.max_value = int(maxval.group(1))

        cache = re.search(r"CACHE\s+(\d+)", options, re.IGNORECASE)
        if cache:
            seq.cache_size = int(cache.group(1))

        if re.search(r"\bNO\s+CYCLE\b", options, re.IGNORECASE):
            seq.cycle = False
        elif re.search(r"\bCYCLE\b", options, re.IGNORECASE):
            seq.cycle = True

        as_type = re.search(r"\bAS\s+(smallint|integer|bigint)\b", options, re.IGNORECASE)
        if as_type:
            seq.data_type = as_type.group(1).lower()

        sequences[name] = seq

    return sequences


def _extract_matviews_regex(sql: str) -> dict[str, MaterializedViewSnapshot]:
    """Regex fallback for materialized view extraction."""
    matviews: dict[str, MaterializedViewSnapshot] = {}
    pattern = re.compile(
        r"CREATE\s+MATERIALIZED\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:\"?(\w+)\"?\.)?\"?(\w+)\"?\s+"
        r"(?:TABLESPACE\s+(\w+)\s+)?"
        r"AS\s+(.*?)(?:\s+WITH\s+(?:NO\s+)?DATA)?\s*;",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(sql):
        name = match.group(2).lower()
        tablespace = match.group(3)
        definition = match.group(4).strip()

        full_match = match.group(0)
        is_populated = "WITH NO DATA" not in full_match.upper()

        source_tables = _extract_tables_from_select(definition)

        matviews[name] = MaterializedViewSnapshot(
            name=name,
            definition=definition,
            is_populated=is_populated,
            tablespace=tablespace.lower() if tablespace else None,
            source_tables=sorted(set(source_tables)),
        )

    return matviews


def _extract_tables_from_select(sql: str) -> list[str]:
    """Extract table names from a SELECT query (FROM and JOIN clauses)."""
    tables: list[str] = []

    from_pattern = re.compile(
        r"\bFROM\s+(?:\"?(\w+)\"?\.)?\"?(\w+)\"?",
        re.IGNORECASE,
    )
    for match in from_pattern.finditer(sql):
        tables.append(match.group(2).lower())

    join_pattern = re.compile(
        r"\bJOIN\s+(?:\"?(\w+)\"?\.)?\"?(\w+)\"?",
        re.IGNORECASE,
    )
    for match in join_pattern.finditer(sql):
        tables.append(match.group(2).lower())

    return tables
