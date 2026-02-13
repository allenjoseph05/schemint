"""Live database snapshot capture — extracted from SnapshotService.

Handles introspecting a live PostgreSQL database via information_schema
and pg_catalog to build SchemaSnapshot models.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from schemint.drift.models import (
    ColumnSnapshot,
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
    ViewSnapshot,
)
from schemint.drift.sql_utils import extract_tables_from_sql
from schemint.drift.types import canonicalize_type


class LiveDBSnapshotCapture:
    """Captures schema snapshots from a live PostgreSQL database."""

    def capture(
        self,
        connection_string: str,
        schema_name: str = "public",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from a live PostgreSQL database."""
        import psycopg2

        conn = psycopg2.connect(connection_string)
        try:
            tables: dict[str, TableSnapshot] = {}
            views: dict[str, ViewSnapshot] = {}
            triggers: dict[str, TriggerSnapshot] = {}

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """, (schema_name,))
                table_names = [row[0] for row in cur.fetchall()]

                for table_name in table_names:
                    columns = self._fetch_columns(cur, schema_name, table_name)
                    pk = self._fetch_primary_key(cur, schema_name, table_name)
                    indexes = self._fetch_indexes(cur, schema_name, table_name)
                    fks = self._fetch_foreign_keys(cur, schema_name, table_name)

                    check_constraints = self._fetch_check_constraints(
                        cur, schema_name, table_name
                    )
                    for check_expr in check_constraints:
                        for col_name, col_snap in columns.items():
                            if col_name.lower() in check_expr.lower():
                                col_snap.constraints.append(f"CHECK({check_expr})")
                                col_snap.constraints.sort()

                    tables[table_name] = TableSnapshot(
                        name=table_name,
                        columns=columns,
                        primary_key=pk,
                        indexes=indexes,
                        foreign_keys=fks,
                    )

                views = self._fetch_views(cur, schema_name)
                triggers = self._fetch_triggers(cur, schema_name)
        finally:
            conn.close()

        now = datetime.now(timezone.utc)
        return SchemaSnapshot(
            snapshot_id=f"live_{schema_name}_{now.strftime('%Y%m%d_%H%M%S')}",
            captured_at=now,
            source="live_db",
            database_type="postgresql",
            schema_name=schema_name,
            tables=tables,
            views=views,
            triggers=triggers,
        )

    def _fetch_columns(
        self, cur, schema_name: str, table_name: str
    ) -> dict[str, ColumnSnapshot]:
        """Fetch columns from information_schema, ordered by ordinal_position."""
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """, (schema_name, table_name))

        columns: dict[str, ColumnSnapshot] = {}
        for row in cur.fetchall():
            col_name, data_type, is_nullable, default, char_len, num_prec, num_scale = row

            canonical = canonicalize_type(data_type)
            if char_len is not None and "char" in canonical:
                canonical = f"{canonical}({char_len})"
            elif num_prec is not None and canonical in ("decimal", "numeric"):
                if num_scale is not None and num_scale > 0:
                    canonical = f"{canonical}({num_prec},{num_scale})"
                else:
                    canonical = f"{canonical}({num_prec})"

            columns[col_name] = ColumnSnapshot(
                name=col_name,
                type=canonical,
                nullable=is_nullable == "YES",
                default=default,
            )
        return columns

    def _fetch_primary_key(
        self, cur, schema_name: str, table_name: str
    ) -> list[str]:
        """Fetch primary key columns, ordered by ordinal_position."""
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
        """, (schema_name, table_name))
        return [row[0] for row in cur.fetchall()]

    def _fetch_indexes(
        self, cur, schema_name: str, table_name: str
    ) -> list[dict]:
        """Fetch indexes from pg_indexes."""
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = %s AND tablename = %s
            ORDER BY indexname
        """, (schema_name, table_name))

        indexes = []
        for row in cur.fetchall():
            idx_name, idx_def = row
            is_unique = "UNIQUE" in idx_def.upper()
            is_primary = "pkey" in idx_name.lower()
            indexes.append({
                "name": idx_name,
                "definition": idx_def,
                "is_unique": is_unique,
                "is_primary": is_primary,
            })
        return indexes

    def _fetch_foreign_keys(
        self, cur, schema_name: str, table_name: str
    ) -> list[dict]:
        """Fetch foreign keys including ON DELETE/UPDATE actions."""
        cur.execute("""
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name AS ref_table,
                ccu.column_name AS ref_column,
                rc.delete_rule,
                rc.update_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.table_schema = rc.constraint_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.constraint_name
        """, (schema_name, table_name))

        fks = []
        for row in cur.fetchall():
            constraint_name, column, ref_table, ref_column, delete_rule, update_rule = row
            fks.append({
                "name": constraint_name,
                "column": column,
                "references_table": ref_table,
                "references_column": ref_column,
                "on_delete": delete_rule,
                "on_update": update_rule,
            })
        return fks

    def _fetch_check_constraints(
        self, cur, schema_name: str, table_name: str
    ) -> list[str]:
        """Fetch CHECK constraint expressions from pg_constraint."""
        cur.execute("""
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE n.nspname = %s
              AND t.relname = %s
              AND c.contype = 'c'
            ORDER BY c.conname
        """, (schema_name, table_name))

        constraints = []
        for row in cur.fetchall():
            expr = row[0]
            check_match = re.match(r"CHECK\s*\(\(?(.*?)\)?\)\s*$", expr, re.IGNORECASE)
            if check_match:
                constraints.append(check_match.group(1))
            else:
                constraints.append(expr)
        return constraints

    def _fetch_views(
        self, cur, schema_name: str
    ) -> dict[str, ViewSnapshot]:
        """Fetch view definitions from pg_views."""
        cur.execute("""
            SELECT viewname, definition
            FROM pg_views
            WHERE schemaname = %s
            ORDER BY viewname
        """, (schema_name,))

        views: dict[str, ViewSnapshot] = {}
        for row in cur.fetchall():
            view_name, definition = row
            source_tables = extract_tables_from_sql(
                definition, context=f"view {view_name}"
            )
            views[view_name] = ViewSnapshot(
                name=view_name,
                definition=definition.strip() if definition else "",
                source_tables=source_tables,
            )
        return views

    def _fetch_triggers(
        self, cur, schema_name: str
    ) -> dict[str, TriggerSnapshot]:
        """Fetch trigger definitions from information_schema.triggers."""
        cur.execute("""
            SELECT trigger_name, event_object_table,
                   event_manipulation, action_timing,
                   action_statement
            FROM information_schema.triggers
            WHERE trigger_schema = %s
            ORDER BY trigger_name
        """, (schema_name,))

        triggers: dict[str, TriggerSnapshot] = {}
        for row in cur.fetchall():
            name, table, event, timing, statement = row
            func_name = ""
            func_match = re.search(
                r"EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(\S+)\(",
                statement or "",
                re.IGNORECASE,
            )
            if func_match:
                func_name = func_match.group(1)

            triggers[name] = TriggerSnapshot(
                name=name,
                table=table,
                event=event,
                timing=timing,
                function_name=func_name,
                definition=statement,
            )
        return triggers
