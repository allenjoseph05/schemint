"""Snapshot service — captures schema state from DDL or live PostgreSQL.

Design constraints:
    - Snapshots are schema-scoped (single schema, e.g. "public").
    - Multi-schema and cross-database capture is NOT supported.
    - Cross-schema foreign keys are captured as-is (the referenced table
      name is stored verbatim) but the referenced table will NOT appear
      in this snapshot's tables dict — only same-schema tables are captured.
    - Output is fully normalized: canonical lowercase type names,
      stable column ordering (insertion order from source).
    - Snapshot IDs include the schema name for disambiguation, e.g.
      "ddl_public_20240101_120000".
    - Snapshot IDs are timestamp-based, never random.
    - A snapshot represents what exists, not logical intent. If a column
      has no DEFAULT in the DDL, default is None — not "inferred absent".
    - No inference. Only records what the parser or database reports.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from schemint.core.parser.sql_parser import parse_sql
from schemint.drift.models import (
    ColumnSnapshot,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.models.schema import ParsedSchema


# Canonical type mapping: raw parser output → normalized form.
# Only types we can deterministically recognize are mapped.
# Unknown types pass through as-is (lowercased).
_CANONICAL_TYPES: dict[str, str] = {
    "int": "integer",
    "integer": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "tinyint",
    "float": "float",
    "double": "double",
    "decimal": "decimal",
    "numeric": "numeric",
    "varchar": "varchar",
    "char": "char",
    "text": "text",
    "longtext": "text",
    "date": "date",
    "time": "time",
    "datetime": "timestamp",
    "timestamp": "timestamp",
    "blob": "blob",
    "binary": "binary",
    "boolean": "boolean",
    "bool": "boolean",
    "json": "json",
    "jsonb": "jsonb",
    "uuid": "uuid",
    "enum": "enum",
    "serial": "serial",
    "bigserial": "bigserial",
}


def _canonicalize_type(raw_type: str) -> str:
    """Normalize a SQL type string to canonical lowercase form.

    Handles types with parameters: "VARCHAR(255)" → "varchar(255)".
    Handles bare types: "INT" → "integer".
    Unknown types are lowercased but not mapped.
    """
    raw_lower = raw_type.strip().lower()

    # Split base type from parameters: "varchar(255)" → ("varchar", "(255)")
    match = re.match(r"^(\w+)(.*)", raw_lower)
    if not match:
        return raw_lower

    base = match.group(1)
    params = match.group(2).strip()

    canonical_base = _CANONICAL_TYPES.get(base, base)
    if params:
        return f"{canonical_base}{params}"
    return canonical_base


class SnapshotService:
    """Captures schema snapshots from DDL strings or live databases.

    Scope: single schema per snapshot. The schema_name parameter
    controls which schema is captured (default: "public").
    """

    def capture_from_ddl(
        self,
        sql: str,
        database_type: str = "postgresql",
        schema_name: str = "public",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from DDL SQL strings.

        Uses the existing parse_sql() to parse DDL, then converts to
        the drift snapshot model with canonical type normalization.

        Args:
            sql: One or more CREATE TABLE statements.
            database_type: Target database dialect.
            schema_name: The schema these tables belong to.
        """
        parsed = parse_sql(sql, database_type=database_type)
        return self._parsed_schema_to_snapshot(
            parsed, source="ddl", schema_name=schema_name
        )

    def capture_from_live_db(
        self,
        connection_string: str,
        schema_name: str = "public",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from a live PostgreSQL database.

        Queries information_schema to build a complete snapshot.
        Only captures tables in the specified schema (default: "public").

        Args:
            connection_string: PostgreSQL connection string.
            schema_name: The single schema to capture.
        """
        import psycopg2

        conn = psycopg2.connect(connection_string)
        try:
            tables: dict[str, TableSnapshot] = {}

            with conn.cursor() as cur:
                # Get all user tables in the target schema, ordered for stability
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

                    tables[table_name] = TableSnapshot(
                        name=table_name,
                        columns=columns,
                        primary_key=pk,
                        indexes=indexes,
                        foreign_keys=fks,
                    )
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
        )

    def _parsed_schema_to_snapshot(
        self,
        schema: ParsedSchema,
        source: str,
        schema_name: str = "public",
    ) -> SchemaSnapshot:
        """Convert a ParsedSchema from the existing parser to a SchemaSnapshot.

        Applies canonical type normalization and preserves column insertion
        order from the parser (which follows DDL ordinal position).
        """
        now = datetime.now(timezone.utc)
        tables: dict[str, TableSnapshot] = {}

        for table in schema.tables:
            # Columns are inserted in parser order (DDL ordinal position).
            # dict preserves insertion order in Python 3.7+.
            columns: dict[str, ColumnSnapshot] = {}
            for col in table.columns:
                constraints: list[str] = []
                if col.is_primary_key:
                    constraints.append("PRIMARY KEY")
                if col.is_unique:
                    constraints.append("UNIQUE")
                if col.is_auto_increment:
                    constraints.append("AUTO_INCREMENT")
                if not col.nullable:
                    constraints.append("NOT NULL")

                columns[col.name] = ColumnSnapshot(
                    name=col.name,
                    type=_canonicalize_type(col.raw_type),
                    nullable=col.nullable,
                    default=col.default,
                    constraints=sorted(constraints),  # Sorted for stable comparison
                )

            indexes = [
                {
                    "name": idx.name,
                    "columns": idx.columns,
                    "is_unique": idx.is_unique,
                    "is_primary": idx.is_primary,
                }
                for idx in table.indexes
            ]

            foreign_keys = [
                {
                    "name": fk.name,
                    "column": fk.column,
                    "references_table": fk.references_table,
                    "references_column": fk.references_column,
                    "on_delete": fk.on_delete,
                    "on_update": fk.on_update,
                }
                for fk in table.foreign_keys
            ]

            tables[table.name] = TableSnapshot(
                name=table.name,
                columns=columns,
                primary_key=table.primary_key,
                indexes=indexes,
                foreign_keys=foreign_keys,
            )

        return SchemaSnapshot(
            snapshot_id=f"ddl_{schema_name}_{now.strftime('%Y%m%d_%H%M%S')}",
            captured_at=now,
            source=source,
            database_type=schema.database_type,
            schema_name=schema_name,
            tables=tables,
        )

    # =========================================================================
    # Live DB introspection helpers (PostgreSQL only)
    # =========================================================================

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

            # Build canonical type with parameters from information_schema
            canonical = _canonicalize_type(data_type)
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
        """Fetch foreign keys from information_schema."""
        cur.execute("""
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name AS ref_table,
                ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.table_schema = %s
              AND tc.table_name = %s
              AND tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.constraint_name
        """, (schema_name, table_name))

        fks = []
        for row in cur.fetchall():
            constraint_name, column, ref_table, ref_column = row
            fks.append({
                "name": constraint_name,
                "column": column,
                "references_table": ref_table,
                "references_column": ref_column,
            })
        return fks
