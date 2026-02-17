"""DDL snapshot capture — extracted from SnapshotService.

Handles parsing DDL SQL strings into SchemaSnapshot models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from schemint.core.parser.sql_parser import parse_sql
from schemint.drift.models import (
    ColumnSnapshot,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.drift.snapshot_pkg.check_constraints import extract_check_constraints
from schemint.drift.snapshot_pkg.ddl_object_capture import (
    extract_enums_from_ddl,
    extract_extensions_from_ddl,
    extract_functions_from_ddl,
    extract_materialized_views_from_ddl,
    extract_sequences_from_ddl,
)
from schemint.drift.snapshot_pkg.view_capture import extract_views_from_ddl
from schemint.drift.types import canonicalize_type
from schemint.models.schema import ParsedSchema


class DDLSnapshotCapture:
    """Captures schema snapshots from DDL SQL strings."""

    def capture(
        self,
        sql: str,
        database_type: str = "postgresql",
        schema_name: str = "public",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from DDL SQL strings.

        Uses the existing parse_sql() to parse DDL, then converts to
        the drift snapshot model with canonical type normalization.
        Also extracts CHECK constraints and CREATE VIEW definitions.
        """
        parsed = parse_sql(sql, database_type=database_type)
        check_constraints = extract_check_constraints(sql)
        snapshot = self._parsed_schema_to_snapshot(
            parsed,
            source="ddl",
            schema_name=schema_name,
            check_constraints=check_constraints,
        )
        snapshot.views = extract_views_from_ddl(sql)
        snapshot.sequences = extract_sequences_from_ddl(sql)
        snapshot.enums = extract_enums_from_ddl(sql)
        snapshot.functions = extract_functions_from_ddl(sql)
        snapshot.materialized_views = extract_materialized_views_from_ddl(sql)
        snapshot.extensions = extract_extensions_from_ddl(sql)
        return snapshot

    def _parsed_schema_to_snapshot(
        self,
        schema: ParsedSchema,
        source: Literal["ddl", "live_db", "composed", "desired_state"],
        schema_name: str = "public",
        check_constraints: dict[str, list[str]] | None = None,
    ) -> SchemaSnapshot:
        """Convert a ParsedSchema to a SchemaSnapshot."""
        now = datetime.now(timezone.utc)
        tables: dict[str, TableSnapshot] = {}

        for table in schema.tables:
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

                if check_constraints:
                    table_checks = check_constraints.get(table.name.lower(), [])
                    for check_expr in table_checks:
                        if col.name.lower() in check_expr.lower():
                            constraints.append(f"CHECK({check_expr})")

                columns[col.name] = ColumnSnapshot(
                    name=col.name,
                    type=canonicalize_type(col.raw_type),
                    nullable=col.nullable,
                    default=col.default,
                    constraints=sorted(constraints),
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
                indexes=list(indexes),
                foreign_keys=list(foreign_keys),
            )

        return SchemaSnapshot(
            snapshot_id=f"ddl_{schema_name}_{now.strftime('%Y%m%d_%H%M%S')}",
            captured_at=now,
            source=source,
            database_type=schema.database_type,
            schema_name=schema_name,
            tables=tables,
        )
