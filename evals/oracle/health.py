"""Catalog and executable health probes for the PostgreSQL truth oracle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import sqlparse

from evals.core.keys import make_key
from evals.core.models import HealthReport, ObjectHealth

_PROBE_NAME = re.compile(r"^\s*--\s*name\s*:\s*([a-z][a-z0-9_]*)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ProbeQuery:
    """One named application-level invariant query."""

    name: str
    sql: str


def parse_probe_queries(sql_text: str) -> list[ProbeQuery]:
    """Split ``probes.sql`` with sqlparse and preserve optional names."""
    queries: list[ProbeQuery] = []
    for index, statement in enumerate(sqlparse.split(sql_text), start=1):
        lines = statement.strip().splitlines()
        name = f"probe_{index:03d}"
        if lines:
            match = _PROBE_NAME.match(lines[0])
            if match:
                name = match.group(1).lower()
                lines = lines[1:]
        sql = "\n".join(lines).strip().rstrip(";")
        substantive_sql = sqlparse.format(sql, strip_comments=True).strip()
        if substantive_sql:
            queries.append(ProbeQuery(name=name, sql=sql))
    names = [query.name for query in queries]
    if len(names) != len(set(names)):
        raise ValueError("Probe names must be unique within a suite")
    return queries


def capture_health(connection: Any, probes: list[ProbeQuery] | None = None) -> HealthReport:
    """Probe catalog objects, application queries, and exact table row counts."""
    objects: list[ObjectHealth] = []
    row_counts = {table: _row_count(connection, table) for table in _table_names(connection)}
    objects.extend(_probe_views(connection, materialized=False))
    objects.extend(_probe_views(connection, materialized=True))
    objects.extend(_probe_constraints(connection))
    objects.extend(_probe_indexes(connection))
    objects.extend(_probe_functions(connection))
    objects.extend(_probe_triggers(connection))
    try:
        objects.extend(_probe_query(connection, query) for query in probes or [])
    finally:
        _reset_role(connection)
    return HealthReport(objects=sorted(objects, key=lambda item: item.key), row_counts=row_counts)


def _table_names(connection: Any) -> list[str]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        return [str(row[0]) for row in cur.fetchall()]


def _row_count(connection: Any, table: str) -> int:
    from psycopg2 import sql

    with connection.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table)))
        return int(cur.fetchone()[0])


def _probe_views(connection: Any, materialized: bool) -> list[ObjectHealth]:
    from psycopg2 import sql

    relation_kind = "m" if materialized else "v"
    object_type = "matview" if materialized else "view"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = %s ORDER BY c.relname",
            (relation_kind,),
        )
        names = [str(row[0]) for row in cur.fetchall()]
    results: list[ObjectHealth] = []
    for name in names:
        statement = sql.SQL("SELECT * FROM {} LIMIT 0").format(sql.Identifier("public", name))
        ok, detail = _execute(connection, statement)
        if ok and materialized:
            refresh = sql.SQL("REFRESH MATERIALIZED VIEW {}").format(sql.Identifier("public", name))
            ok, detail = _execute_rolled_back(connection, refresh)
        results.append(ObjectHealth(key=make_key(object_type, name), ok=ok, detail=detail))
    return results


def _probe_constraints(connection: Any) -> list[ObjectHealth]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.conname, c.convalidated FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.contype = 'f' ORDER BY c.conname"
        )
        return [
            ObjectHealth(
                key=make_key("foreign_key", str(name)),
                ok=bool(validated),
                detail="validated" if validated else "not validated",
            )
            for name, validated in cur.fetchall()
        ]


def _probe_indexes(connection: Any) -> list[ObjectHealth]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT i.relname, x.indisvalid AND x.indisready FROM pg_index x "
            "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public' AND NOT x.indisprimary ORDER BY i.relname"
        )
        return [
            ObjectHealth(
                key=make_key("index", str(name)),
                ok=bool(valid),
                detail="valid" if valid else "invalid",
            )
            for name, valid in cur.fetchall()
        ]


def _probe_functions(connection: Any) -> list[ObjectHealth]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT p.proname, pg_get_function_identity_arguments(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p') "
            "ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)"
        )
        return [
            ObjectHealth(key=make_key("function", str(name)), ok=True, detail=str(args or ""))
            for name, args in cur.fetchall()
        ]


def _probe_triggers(connection: Any) -> list[ObjectHealth]:
    from psycopg2 import sql

    with connection.cursor() as cur:
        cur.execute(
            "SELECT trigger_name, event_object_table, "
            "bool_or(event_manipulation = 'UPDATE') FROM information_schema.triggers "
            "WHERE trigger_schema = 'public' GROUP BY trigger_name, event_object_table "
            "ORDER BY trigger_name"
        )
        triggers = [(str(name), str(table), bool(update)) for name, table, update in cur.fetchall()]
    results: list[ObjectHealth] = []
    for name, table, handles_update in triggers:
        ok, detail = True, "catalog present"
        if handles_update and _row_count(connection, table) > 0:
            column = _first_updatable_column(connection, table)
            if column:
                statement = sql.SQL(
                    "UPDATE {table} SET {column} = {column} "
                    "WHERE ctid = (SELECT ctid FROM {table} LIMIT 1)"
                ).format(table=sql.Identifier("public", table), column=sql.Identifier(column))
                ok, detail = _execute_rolled_back(connection, statement)
        results.append(ObjectHealth(key=make_key("trigger", name), ok=ok, detail=detail))
    return results


def _first_updatable_column(connection: Any, table: str) -> str | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND is_generated = 'NEVER' "
            "ORDER BY ordinal_position LIMIT 1",
            (table,),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def _probe_query(connection: Any, query: ProbeQuery) -> ObjectHealth:
    try:
        with connection.cursor() as cur:
            cur.execute(query.sql)
            rows = cur.fetchall() if cur.description else []
            columns = [item.name for item in cur.description] if cur.description else []
        detail = json.dumps(
            {"columns": columns, "rows": rows},
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ObjectHealth(key=make_key("query", query.name), ok=True, detail=detail)
    except Exception as exc:
        _rollback_if_needed(connection)
        return ObjectHealth(key=make_key("query", query.name), ok=False, detail=str(exc))


def _execute(connection: Any, statement: Any) -> tuple[bool, str]:
    try:
        with connection.cursor() as cur:
            cur.execute(statement)
        return True, "ok"
    except Exception as exc:
        _rollback_if_needed(connection)
        return False, str(exc)


def _execute_rolled_back(connection: Any, statement: Any) -> tuple[bool, str]:
    original_autocommit = bool(connection.autocommit)
    try:
        connection.autocommit = False
        with connection.cursor() as cur:
            cur.execute(statement)
        connection.rollback()
        return True, "ok"
    except Exception as exc:
        connection.rollback()
        return False, str(exc)
    finally:
        connection.autocommit = original_autocommit


def _rollback_if_needed(connection: Any) -> None:
    if not connection.autocommit:
        connection.rollback()


def _reset_role(connection: Any) -> None:
    try:
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
    except Exception:
        _rollback_if_needed(connection)
