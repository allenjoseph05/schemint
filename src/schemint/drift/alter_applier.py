"""Apply migration SQL to a SchemaSnapshot to predict post-migration state.

Takes a baseline SchemaSnapshot and migration SQL, returns a new
SchemaSnapshot representing the predicted schema after applying
the migration. This is the deterministic foundation of the sandbox.

Supported statements:
    - CREATE TABLE -> merge new table into snapshot
    - DROP TABLE -> remove table from snapshot
    - ALTER TABLE -> apply column/FK mutations via AlterParser
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp as sqlglot_exp

from schemint.drift.alter_parser import AlterParser
from schemint.drift.models import (
    ColumnSnapshot,
    ForeignKeySnapshot,
    SchemaSnapshot,
)
from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

logger = logging.getLogger(__name__)


class AlterApplier:
    """Applies migration SQL to a SchemaSnapshot, producing a predicted result."""

    def __init__(self) -> None:
        self._alter_parser = AlterParser()
        self._ddl_capture = DDLSnapshotCapture()

    def apply(self, baseline: SchemaSnapshot, migration_sql: str) -> SchemaSnapshot:
        """Apply migration SQL to baseline and return predicted post-migration snapshot.

        Deep-copies baseline so the original is never mutated.
        Unknown statements are logged and skipped (graceful degradation).
        """
        result = baseline.model_copy(deep=True)

        try:
            statements = sqlglot.parse(migration_sql)
        except sqlglot.errors.ParseError:
            logger.warning("sqlglot failed to parse migration SQL; returning baseline unchanged")
            return result

        for stmt in statements:
            if stmt is None:
                continue
            self._apply_statement(stmt, result, migration_sql)

        return result

    def _apply_statement(
        self, stmt: sqlglot_exp.Expression, result: SchemaSnapshot, _full_sql: str
    ) -> None:
        """Apply a single parsed statement to the snapshot."""
        if isinstance(stmt, sqlglot_exp.Create):
            self._apply_create(stmt, result)
        elif isinstance(stmt, sqlglot_exp.Drop):
            self._apply_drop(stmt, result)
        elif isinstance(stmt, sqlglot_exp.Alter):
            self._apply_alter(stmt, result)
        else:
            logger.debug("Skipping unsupported statement type: %s", type(stmt).__name__)

    def _apply_create(self, stmt: sqlglot_exp.Create, result: SchemaSnapshot) -> None:
        """Handle CREATE TABLE by parsing DDL and merging into snapshot."""
        kind = stmt.args.get("kind")
        if kind and str(kind).upper() != "TABLE":
            return

        try:
            new_snapshot = self._ddl_capture.capture(stmt.sql())
            for table_name, table in new_snapshot.tables.items():
                result.tables[table_name] = table
        except Exception as e:
            logger.warning("Failed to capture CREATE TABLE: %s", e)

    def _apply_drop(self, stmt: sqlglot_exp.Drop, result: SchemaSnapshot) -> None:
        """Handle DROP TABLE by removing the table from snapshot."""
        kind = stmt.args.get("kind")
        if not kind or str(kind).upper() != "TABLE":
            return

        table_expr = stmt.args.get("this")
        if table_expr and hasattr(table_expr, "name"):
            table_name = str(table_expr.name).lower()
            result.tables.pop(table_name, None)

    def _apply_alter(self, stmt: sqlglot_exp.Alter, result: SchemaSnapshot) -> None:
        """Handle ALTER TABLE by parsing events and applying mutations."""
        events = self._alter_parser.parse(stmt.sql())

        for event in events:
            table_name = event.table
            table = result.tables.get(table_name)

            if event.change_type == "table_renamed":
                # Pop old table, insert under new name
                old_table = result.tables.pop(table_name, None)
                if old_table and event.new_value:
                    old_table.name = event.new_value
                    result.tables[event.new_value] = old_table
                continue

            if table is None:
                logger.warning("ALTER references unknown table '%s'; skipping", table_name)
                continue

            self._apply_event(event, table)

    def _apply_event(self, event: object, table: object) -> None:
        """Apply a single SchemaChangeEvent mutation to a TableSnapshot."""
        ct = event.change_type  # type: ignore[attr-defined]
        col_name = event.column  # type: ignore[attr-defined]

        if ct == "column_added":
            if col_name and col_name not in table.columns:  # type: ignore[attr-defined]
                col_type = event.new_value or ""  # type: ignore[attr-defined]
                table.columns[col_name] = ColumnSnapshot(  # type: ignore[attr-defined]
                    name=col_name,
                    type=col_type,
                )

        elif ct == "column_dropped":
            if col_name:
                table.columns.pop(col_name, None)  # type: ignore[attr-defined]

        elif ct == "column_type_change":
            if col_name and col_name in table.columns:  # type: ignore[attr-defined]
                table.columns[col_name].type = event.new_value or ""  # type: ignore[attr-defined]

        elif ct == "column_nullable_change":
            if col_name and col_name in table.columns:  # type: ignore[attr-defined]
                table.columns[col_name].nullable = (  # type: ignore[attr-defined]
                    event.new_value == "True"  # type: ignore[attr-defined]
                )

        elif ct == "column_default_change":
            if col_name and col_name in table.columns:  # type: ignore[attr-defined]
                table.columns[col_name].default = event.new_value  # type: ignore[attr-defined]

        elif ct == "fk_added":
            ref_table = event.new_value or ""  # type: ignore[attr-defined]
            table.foreign_keys.append(  # type: ignore[attr-defined]
                ForeignKeySnapshot(
                    references_table=ref_table,
                )
            )

        elif ct == "fk_dropped":
            constraint_name = event.old_value or ""  # type: ignore[attr-defined]
            if constraint_name:
                table.foreign_keys = [  # type: ignore[attr-defined]
                    fk
                    for fk in table.foreign_keys  # type: ignore[attr-defined]
                    if fk.name != constraint_name
                ]

        else:
            logger.debug("Skipping unhandled event type '%s' in alter applier", ct)
