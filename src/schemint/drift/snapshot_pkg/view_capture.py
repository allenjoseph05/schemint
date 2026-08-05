"""View extraction from DDL — extracted from SnapshotService.

Parses CREATE VIEW statements from DDL using sqlglot.
"""

from __future__ import annotations

import re

import sqlparse

from schemint.drift.models import ViewSnapshot


def extract_views_from_ddl(sql: str) -> dict[str, ViewSnapshot]:
    """Extract CREATE VIEW definitions from DDL using sqlglot."""
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp

        views: dict[str, ViewSnapshot] = {}

        # Parse candidate statements independently.  A later unsupported
        # PostgreSQL statement (for example REFRESH MATERIALIZED VIEW) must
        # not erase valid views captured earlier in the same DDL document.
        candidates = []
        for raw_statement in sqlparse.split(sql):
            cleaned = sqlparse.format(raw_statement, strip_comments=True).strip()
            if re.match(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", cleaned, re.IGNORECASE):
                candidates.append(cleaned)

        for raw_statement in candidates:
            parsed = sqlglot.parse(raw_statement)
            statement = parsed[0] if parsed else None
            if statement is None:
                continue
            if not isinstance(statement, sqlglot_exp.Create):
                continue

            kind = statement.args.get("kind")
            if kind and str(kind).upper() != "VIEW":
                continue

            table_expr = statement.find(sqlglot_exp.Table)
            if not table_expr or not table_expr.name:
                continue
            view_name = table_expr.name.lower()

            select_expr = statement.find(sqlglot_exp.Select)
            if not select_expr:
                continue
            definition = select_expr.sql()

            source_tables: list[str] = []
            for tbl in select_expr.find_all(sqlglot_exp.Table):
                if tbl.name and tbl.name.lower() != view_name:
                    source_tables.append(tbl.name.lower())

            views[view_name] = ViewSnapshot(
                name=view_name,
                definition=definition,
                source_tables=sorted(set(source_tables)),
            )

        return views
    except Exception:
        return {}
