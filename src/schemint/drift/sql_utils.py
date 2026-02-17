"""Shared SQL parsing utilities for schema drift detection.

Deduplicates SQL table extraction logic used by both snapshot.py
and dependency_graph.py. Uses sqlglot for deterministic AST parsing.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
from sqlglot import exp as sqlglot_exp

logger = logging.getLogger(__name__)


def extract_tables_from_sql(sql: str, context: str = "unknown") -> list[str]:
    """Extract table names referenced in SQL using sqlglot AST.

    Returns deduplicated, lowercased, sorted table names.
    If parsing fails, returns empty list (no guessing).

    Args:
        sql: SQL string to parse.
        context: Description of the source (for logging on failure).
    """
    try:
        statements = sqlglot.parse(sql)
    except (sqlglot.errors.ParseError, Exception) as e:
        logger.warning("Failed to extract tables from %s: %s", context, e)
        return []

    tables: set[str] = set()
    for statement in statements:
        if statement is None:
            continue
        for table in statement.find_all(sqlglot_exp.Table):
            if table.name:
                tables.add(table.name.lower())

    return sorted(tables)


def extract_aliases_from_ast(statement: Any) -> dict[str, str]:
    """Extract table alias -> real table name from a sqlglot AST node.

    Returns a dict mapping lowercase alias -> lowercase table name.
    Only includes aliases that can be deterministically resolved.
    Tables used without aliases map to themselves.
    """
    aliases: dict[str, str] = {}
    for table in statement.find_all(sqlglot_exp.Table):
        table_name = table.name
        alias = table.alias
        if alias and table_name:
            aliases[alias.lower()] = table_name.lower()
        elif table_name:
            aliases[table_name.lower()] = table_name.lower()
    return aliases


def resolve_column_ref(
    col: sqlglot_exp.Column, aliases: dict[str, str]
) -> tuple[str, bool]:
    """Resolve a column reference to table.column using an alias map.

    Returns (resolved_ref, alias_was_resolved).
    - No table qualifier: returns ("column_name", False).
    - Qualifier in alias map: returns ("real_table.column", True).
    - Qualifier not in map: returns ("qualifier.column", False).
    """
    col_name = col.name.lower() if col.name else ""
    table_ref = col.table.lower() if col.table else ""

    if not table_ref:
        return col_name, False

    if table_ref in aliases:
        return f"{aliases[table_ref]}.{col_name}", True

    return f"{table_ref}.{col_name}", False
