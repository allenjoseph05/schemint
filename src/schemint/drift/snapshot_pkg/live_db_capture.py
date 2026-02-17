"""Live database snapshot capture — extracted from SnapshotService.

Handles introspecting a live PostgreSQL database via information_schema
and pg_catalog to build SchemaSnapshot models.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from schemint.drift.models import (
    ColumnSnapshot,
    ColumnStatistics,
    EnumSnapshot,
    ExtensionSnapshot,
    FunctionSnapshot,
    IndexStatistics,
    MaterializedViewSnapshot,
    PartitionInfo,
    PermissionSnapshot,
    PolicySnapshot,
    SchemaSnapshot,
    SequenceSnapshot,
    TableSnapshot,
    TableStatistics,
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
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """,
                    (schema_name,),
                )
                table_names = [row[0] for row in cur.fetchall()]

                for table_name in table_names:
                    columns = self._fetch_columns(cur, schema_name, table_name)
                    pk = self._fetch_primary_key(cur, schema_name, table_name)
                    indexes = self._fetch_indexes(cur, schema_name, table_name)
                    fks = self._fetch_foreign_keys(cur, schema_name, table_name)

                    check_constraints = self._fetch_check_constraints(cur, schema_name, table_name)
                    for check_expr in check_constraints:
                        for col_name, col_snap in columns.items():
                            if col_name.lower() in check_expr.lower():
                                col_snap.constraints.append(f"CHECK({check_expr})")
                                col_snap.constraints.sort()

                    tables[table_name] = TableSnapshot(
                        name=table_name,
                        columns=columns,
                        primary_key=pk,
                        indexes=list(indexes),
                        foreign_keys=list(fks),
                    )

                views = self._fetch_views(cur, schema_name)
                triggers = self._fetch_triggers(cur, schema_name)
                sequences = self._fetch_sequences(cur, schema_name)
                enums = self._fetch_enums(cur, schema_name)
                functions = self._fetch_functions(cur, schema_name)
                table_stats = self._fetch_table_statistics(cur, schema_name)
                index_stats = self._fetch_index_statistics(cur, schema_name)
                extensions = self._fetch_extensions(cur)
                permissions = self._fetch_permissions(cur, schema_name)
                policies = self._fetch_policies(cur, schema_name)
                partitions = self._fetch_partitions(cur, schema_name, list(tables.keys()))
                matviews = self._fetch_materialized_views(cur, schema_name)
                col_stats = self._fetch_column_statistics(cur, schema_name, list(tables.keys()))
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
            sequences=sequences,
            enums=enums,
            functions=functions,
            table_statistics=table_stats,
            index_statistics=index_stats,
            extensions=extensions,
            permissions=permissions,
            policies=policies,
            partitions=partitions,
            materialized_views=matviews,
            column_statistics=col_stats,
        )

    def _fetch_columns(
        self, cur: Any, schema_name: str, table_name: str
    ) -> dict[str, ColumnSnapshot]:
        """Fetch columns from information_schema, ordered by ordinal_position."""
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """,
            (schema_name, table_name),
        )

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

    def _fetch_primary_key(self, cur: Any, schema_name: str, table_name: str) -> list[str]:
        """Fetch primary key columns, ordered by ordinal_position."""
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
        """,
            (schema_name, table_name),
        )
        return [row[0] for row in cur.fetchall()]

    def _fetch_indexes(self, cur: Any, schema_name: str, table_name: str) -> list[dict[str, Any]]:
        """Fetch indexes from pg_indexes."""
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = %s AND tablename = %s
            ORDER BY indexname
        """,
            (schema_name, table_name),
        )

        indexes = []
        for row in cur.fetchall():
            idx_name, idx_def = row
            is_unique = "UNIQUE" in idx_def.upper()
            is_primary = "pkey" in idx_name.lower()
            indexes.append(
                {
                    "name": idx_name,
                    "definition": idx_def,
                    "is_unique": is_unique,
                    "is_primary": is_primary,
                }
            )
        return indexes

    def _fetch_foreign_keys(
        self, cur: Any, schema_name: str, table_name: str
    ) -> list[dict[str, Any]]:
        """Fetch foreign keys including ON DELETE/UPDATE actions."""
        cur.execute(
            """
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
        """,
            (schema_name, table_name),
        )

        fks = []
        for row in cur.fetchall():
            constraint_name, column, ref_table, ref_column, delete_rule, update_rule = row
            fks.append(
                {
                    "name": constraint_name,
                    "column": column,
                    "references_table": ref_table,
                    "references_column": ref_column,
                    "on_delete": delete_rule,
                    "on_update": update_rule,
                }
            )
        return fks

    def _fetch_check_constraints(self, cur: Any, schema_name: str, table_name: str) -> list[str]:
        """Fetch CHECK constraint expressions from pg_constraint."""
        cur.execute(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE n.nspname = %s
              AND t.relname = %s
              AND c.contype = 'c'
            ORDER BY c.conname
        """,
            (schema_name, table_name),
        )

        constraints = []
        for row in cur.fetchall():
            expr = row[0]
            check_match = re.match(r"CHECK\s*\(\(?(.*?)\)?\)\s*$", expr, re.IGNORECASE)
            if check_match:
                constraints.append(check_match.group(1))
            else:
                constraints.append(expr)
        return constraints

    def _fetch_views(self, cur: Any, schema_name: str) -> dict[str, ViewSnapshot]:
        """Fetch view definitions from pg_views."""
        cur.execute(
            """
            SELECT viewname, definition
            FROM pg_views
            WHERE schemaname = %s
            ORDER BY viewname
        """,
            (schema_name,),
        )

        views: dict[str, ViewSnapshot] = {}
        for row in cur.fetchall():
            view_name, definition = row
            source_tables = extract_tables_from_sql(definition, context=f"view {view_name}")
            views[view_name] = ViewSnapshot(
                name=view_name,
                definition=definition.strip() if definition else "",
                source_tables=source_tables,
            )
        return views

    def _fetch_triggers(self, cur: Any, schema_name: str) -> dict[str, TriggerSnapshot]:
        """Fetch trigger definitions from information_schema.triggers."""
        cur.execute(
            """
            SELECT trigger_name, event_object_table,
                   event_manipulation, action_timing,
                   action_statement
            FROM information_schema.triggers
            WHERE trigger_schema = %s
            ORDER BY trigger_name
        """,
            (schema_name,),
        )

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

    def _fetch_sequences(self, cur: Any, schema_name: str) -> dict[str, SequenceSnapshot]:
        """Fetch sequence definitions from pg_sequences."""
        cur.execute(
            """
            SELECT sequencename, data_type,
                   start_value, increment_by,
                   min_value, max_value,
                   cache_size, cycle
            FROM pg_sequences
            WHERE schemaname = %s
            ORDER BY sequencename
        """,
            (schema_name,),
        )

        sequences: dict[str, SequenceSnapshot] = {}
        for row in cur.fetchall():
            name, data_type, start_val, inc, min_val, max_val, cache, cycle = row
            sequences[name] = SequenceSnapshot(
                name=name,
                data_type=data_type or "bigint",
                start_value=start_val or 1,
                increment_by=inc or 1,
                min_value=min_val or 1,
                max_value=max_val,
                cache_size=cache or 1,
                cycle=bool(cycle),
            )
        return sequences

    def _fetch_enums(self, cur: Any, schema_name: str) -> dict[str, EnumSnapshot]:
        """Fetch enum type definitions from pg_type + pg_enum."""
        cur.execute(
            """
            SELECT t.typname,
                   array_agg(e.enumlabel ORDER BY e.enumsortorder) as values
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            JOIN pg_namespace n ON t.typnamespace = n.oid
            WHERE n.nspname = %s
            GROUP BY t.typname
            ORDER BY t.typname
        """,
            (schema_name,),
        )

        enums: dict[str, EnumSnapshot] = {}
        for row in cur.fetchall():
            name, values = row
            enums[name] = EnumSnapshot(
                name=name,
                values=list(values) if values else [],
            )
        return enums

    def _fetch_functions(self, cur: Any, schema_name: str) -> dict[str, FunctionSnapshot]:
        """Fetch function/procedure definitions from pg_proc."""
        cur.execute(
            """
            SELECT p.proname,
                   pg_get_function_arguments(p.oid) as arguments,
                   pg_get_function_result(p.oid) as return_type,
                   l.lanname as language,
                   p.provolatile,
                   pg_get_functiondef(p.oid) as definition
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            JOIN pg_language l ON p.prolang = l.oid
            WHERE n.nspname = %s
              AND p.prokind IN ('f', 'p')
            ORDER BY p.proname
        """,
            (schema_name,),
        )

        _volatility_map: dict[str, Literal["volatile", "stable", "immutable"]] = {
            "v": "volatile",
            "s": "stable",
            "i": "immutable",
        }

        functions: dict[str, FunctionSnapshot] = {}
        for row in cur.fetchall():
            name, args, ret_type, lang, volatility, definition = row
            functions[name] = FunctionSnapshot(
                name=name,
                argument_types=args or "",
                return_type=ret_type or "",
                language=lang or "sql",
                volatility=_volatility_map.get(volatility, "volatile"),
                definition=definition,
            )
        return functions

    def _fetch_table_statistics(self, cur: Any, schema_name: str) -> dict[str, TableStatistics]:
        """Fetch table runtime statistics from pg_stat_user_tables."""
        cur.execute(
            """
            SELECT relname,
                   n_live_tup, n_dead_tup,
                   seq_scan, idx_scan,
                   last_vacuum, last_analyze,
                   pg_total_relation_size(relid) as total_size,
                   pg_relation_size(relid) as table_size,
                   pg_indexes_size(relid) as index_size
            FROM pg_stat_user_tables
            WHERE schemaname = %s
            ORDER BY relname
        """,
            (schema_name,),
        )

        stats: dict[str, TableStatistics] = {}
        for row in cur.fetchall():
            (
                name,
                live_tup,
                dead_tup,
                seq_scan,
                idx_scan,
                last_vacuum,
                last_analyze,
                total_size,
                table_size,
                index_size,
            ) = row
            stats[name] = TableStatistics(
                table_name=name,
                row_count=live_tup or 0,
                dead_tuples=dead_tup or 0,
                total_size_bytes=total_size or 0,
                table_size_bytes=table_size or 0,
                index_size_bytes=index_size or 0,
                seq_scan_count=seq_scan or 0,
                idx_scan_count=idx_scan or 0,
                last_vacuum=last_vacuum,
                last_analyze=last_analyze,
            )
        return stats

    def _fetch_index_statistics(self, cur: Any, schema_name: str) -> dict[str, IndexStatistics]:
        """Fetch index runtime statistics from pg_stat_user_indexes."""
        cur.execute(
            """
            SELECT indexrelname, relname,
                   idx_scan, idx_tup_read, idx_tup_fetch,
                   pg_relation_size(indexrelid) as index_size
            FROM pg_stat_user_indexes
            WHERE schemaname = %s
            ORDER BY indexrelname
        """,
            (schema_name,),
        )

        stats: dict[str, IndexStatistics] = {}
        for row in cur.fetchall():
            idx_name, table_name, scans, tup_read, tup_fetch, size = row
            stats[idx_name] = IndexStatistics(
                index_name=idx_name,
                table_name=table_name,
                idx_scan=scans or 0,
                idx_tup_read=tup_read or 0,
                idx_tup_fetch=tup_fetch or 0,
                size_bytes=size or 0,
            )
        return stats

    def _fetch_extensions(self, cur: Any) -> dict[str, ExtensionSnapshot]:
        """Fetch installed extensions from pg_extension."""
        cur.execute("""
            SELECT e.extname, e.extversion, n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON e.extnamespace = n.oid
            ORDER BY e.extname
        """)

        extensions: dict[str, ExtensionSnapshot] = {}
        for row in cur.fetchall():
            name, version, schema = row
            extensions[name] = ExtensionSnapshot(
                name=name,
                version=version or "",
                installed_schema=schema or "public",
            )
        return extensions

    def _fetch_permissions(self, cur: Any, schema_name: str) -> list[PermissionSnapshot]:
        """Fetch table-level permissions from information_schema.table_privileges."""
        cur.execute(
            """
            SELECT table_name, grantee, privilege_type, is_grantable
            FROM information_schema.table_privileges
            WHERE table_schema = %s
            ORDER BY table_name, grantee, privilege_type
        """,
            (schema_name,),
        )

        permissions: list[PermissionSnapshot] = []
        for row in cur.fetchall():
            table_name, grantee, privilege_type, is_grantable = row
            permissions.append(
                PermissionSnapshot(
                    table_name=table_name,
                    grantee=grantee,
                    privilege_type=privilege_type,
                    is_grantable=is_grantable == "YES",
                )
            )
        return permissions

    def _fetch_policies(self, cur: Any, schema_name: str) -> dict[str, PolicySnapshot]:
        """Fetch row-level security policies from pg_policy."""
        cur.execute(
            """
            SELECT pol.polname,
                   cls.relname AS table_name,
                   CASE pol.polcmd
                       WHEN 'r' THEN 'SELECT'
                       WHEN 'a' THEN 'INSERT'
                       WHEN 'w' THEN 'UPDATE'
                       WHEN 'd' THEN 'DELETE'
                       ELSE 'ALL'
                   END AS command,
                   pol.polpermissive,
                   pg_get_expr(pol.polqual, pol.polrelid) AS qual,
                   pg_get_expr(pol.polwithcheck, pol.polrelid) AS with_check,
                   ARRAY(
                       SELECT rolname FROM pg_roles
                       WHERE oid = ANY(pol.polroles)
                   ) AS roles
            FROM pg_policy pol
            JOIN pg_class cls ON pol.polrelid = cls.oid
            JOIN pg_namespace n ON cls.relnamespace = n.oid
            WHERE n.nspname = %s
            ORDER BY pol.polname
        """,
            (schema_name,),
        )

        policies: dict[str, PolicySnapshot] = {}
        for row in cur.fetchall():
            name, table, command, permissive, qual, with_check, roles = row
            policies[name] = PolicySnapshot(
                name=name,
                table=table,
                command=command,
                permissive=bool(permissive),
                roles=list(roles) if roles else [],
                qual_expression=qual,
                with_check_expression=with_check,
            )
        return policies

    def _fetch_partitions(
        self, cur: Any, schema_name: str, table_names: list[str]
    ) -> dict[str, list[PartitionInfo]]:
        """Fetch partition information from pg_inherits + pg_class.

        Returns a dict mapping parent_table → list of PartitionInfo.
        """
        if not table_names:
            return {}

        cur.execute(
            """
            SELECT child.relname AS partition_name,
                   parent.relname AS parent_table,
                   pg_get_expr(child.relpartbound, child.oid) AS partition_bound
            FROM pg_inherits inh
            JOIN pg_class child ON inh.inhrelid = child.oid
            JOIN pg_class parent ON inh.inhparent = parent.oid
            JOIN pg_namespace n ON parent.relnamespace = n.oid
            WHERE n.nspname = %s
            ORDER BY parent.relname, child.relname
        """,
            (schema_name,),
        )

        partitions: dict[str, list[PartitionInfo]] = {}
        for row in cur.fetchall():
            partition_name, parent_table, bound = row
            part = PartitionInfo(
                partition_name=partition_name,
                parent_table=parent_table,
                partition_bound=bound or "",
            )
            partitions.setdefault(parent_table, []).append(part)
        return partitions

    def _fetch_materialized_views(
        self, cur: Any, schema_name: str
    ) -> dict[str, MaterializedViewSnapshot]:
        """Fetch materialized view definitions from pg_matviews."""
        cur.execute(
            """
            SELECT matviewname, definition, ispopulated,
                   tablespace
            FROM pg_matviews
            WHERE schemaname = %s
            ORDER BY matviewname
        """,
            (schema_name,),
        )

        matviews: dict[str, MaterializedViewSnapshot] = {}
        for row in cur.fetchall():
            name, definition, is_populated, tablespace = row
            source_tables = extract_tables_from_sql(definition, context=f"matview {name}")
            matviews[name] = MaterializedViewSnapshot(
                name=name,
                definition=definition.strip() if definition else "",
                is_populated=bool(is_populated),
                tablespace=tablespace,
                source_tables=source_tables,
            )
        return matviews

    def _fetch_column_statistics(
        self, cur: Any, schema_name: str, table_names: list[str]
    ) -> dict[str, list[ColumnStatistics]]:
        """Fetch column-level statistics from pg_stats.

        Returns a dict mapping table_name → list of ColumnStatistics.
        """
        if not table_names:
            return {}

        cur.execute(
            """
            SELECT attname, tablename,
                   null_frac, n_distinct,
                   avg_width, correlation
            FROM pg_stats
            WHERE schemaname = %s
            ORDER BY tablename, attname
        """,
            (schema_name,),
        )

        stats: dict[str, list[ColumnStatistics]] = {}
        for row in cur.fetchall():
            col_name, table_name, null_frac, n_distinct, avg_width, correlation = row
            col_stat = ColumnStatistics(
                column_name=col_name,
                table_name=table_name,
                null_frac=null_frac or 0.0,
                n_distinct=n_distinct or 0.0,
                avg_width=avg_width or 0,
                correlation=correlation or 0.0,
            )
            stats.setdefault(table_name, []).append(col_stat)
        return stats
