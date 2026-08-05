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
import re
from typing import cast

import sqlglot
from sqlglot import exp as sqlglot_exp

from schemint.drift.alter_parser import AlterParser
from schemint.drift.models import (
    ColumnSnapshot,
    ForeignKeySnapshot,
    IndexSnapshot,
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
            self._apply_statement(
                cast(sqlglot_exp.Expression, stmt),  # type: ignore[redundant-cast]
                result,
                migration_sql,
            )

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
        elif isinstance(stmt, sqlglot_exp.Command):
            statement_sql = stmt.sql()
            if re.match(r"ALTER\s+TABLE\b", statement_sql, re.IGNORECASE):
                self._apply_alter_sql(statement_sql, result)
        else:
            logger.debug("Skipping unsupported statement type: %s", type(stmt).__name__)

    def _apply_create(self, stmt: sqlglot_exp.Create, result: SchemaSnapshot) -> None:
        """Handle CREATE TABLE by parsing DDL and merging into snapshot."""
        kind = stmt.args.get("kind")
        if kind and str(kind).upper() == "INDEX":
            self._apply_create_index(stmt, result)
            return
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
        if kind and str(kind).upper() == "INDEX":
            index_expr = stmt.args.get("this")
            index_name = getattr(index_expr, "name", "").lower()
            for table in result.tables.values():
                table.indexes = [
                    index
                    for index in table.indexes
                    if self._field(index, "name") != index_name
                ]
            return
        if not kind or str(kind).upper() != "TABLE":
            return

        table_expr = stmt.args.get("this")
        if table_expr and hasattr(table_expr, "name"):
            table_name = str(table_expr.name).lower()
            result.tables.pop(table_name, None)

    def _apply_alter(self, stmt: sqlglot_exp.Alter, result: SchemaSnapshot) -> None:
        """Handle ALTER TABLE by parsing events and applying mutations."""
        table_expr = stmt.args.get("this")
        table_name = getattr(table_expr, "name", "").lower()
        table = result.tables.get(table_name)
        actions = stmt.args.get("actions") or []

        if table is not None and len(actions) == 1 and isinstance(
            actions[0], sqlglot_exp.RenameColumn
        ):
            self._apply_rename_column(actions[0], table_name, result)
            return

        captured_columns: set[str] = set()
        if table is not None:
            for action in actions:
                if not isinstance(action, sqlglot_exp.ColumnDef):
                    continue
                try:
                    captured = self._ddl_capture.capture(
                        f"CREATE TABLE __migration ({action.sql()});"
                    )
                    column = next(iter(captured.tables["__migration"].columns.values()))
                    table.columns[column.name] = column
                    captured_columns.add(column.name)
                except Exception as exc:
                    logger.debug("Could not fully capture added column: %s", exc)

        has_fk_sql = "FOREIGN KEY" in stmt.sql().upper()
        if has_fk_sql or "DROP CONSTRAINT" in stmt.sql().upper():
            self._apply_alter_sql(stmt.sql(), result)

        events = self._alter_parser.parse(stmt.sql())

        for event in events:
            table_name = event.table
            table = result.tables.get(table_name)

            if event.change_type == "column_added" and event.column in captured_columns:
                continue
            if event.change_type in {"fk_added", "fk_dropped"} and (
                has_fk_sql or "DROP CONSTRAINT" in stmt.sql().upper()
            ):
                continue

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

    def _apply_create_index(
        self, stmt: sqlglot_exp.Create, result: SchemaSnapshot
    ) -> None:
        index_expr = stmt.args.get("this")
        table_expr = index_expr.args.get("table") if index_expr is not None else None
        table_name = getattr(table_expr, "name", "").lower()
        table = result.tables.get(table_name)
        if table is None or index_expr is None:
            return

        params = index_expr.args.get("params")
        columns = []
        for expression in params.args.get("columns", []) if params is not None else []:
            value = expression.args.get("this", expression)
            columns.append(value.sql().strip('"').lower())
        name = getattr(index_expr, "name", "").lower()
        table.indexes = [index for index in table.indexes if self._field(index, "name") != name]
        table.indexes.append(
            IndexSnapshot(
                name=name,
                columns=columns,
                is_unique=bool(stmt.args.get("unique")),
                definition=stmt.sql(),
            )
        )

    def _apply_rename_column(
        self,
        action: sqlglot_exp.RenameColumn,
        table_name: str,
        result: SchemaSnapshot,
    ) -> None:
        old_expr = action.args.get("this")
        new_expr = action.args.get("to")
        old_name = getattr(old_expr, "name", "").lower()
        new_name = getattr(new_expr, "name", "").lower()
        table = result.tables[table_name]
        column = table.columns.pop(old_name, None)
        if column is None or not new_name:
            return
        column.name = new_name
        table.columns[new_name] = column
        table.primary_key = [new_name if name == old_name else name for name in table.primary_key]
        for index in table.indexes:
            columns_value = self._field(index, "columns", [])
            columns = columns_value if isinstance(columns_value, list) else []
            renamed = [new_name if name == old_name else name for name in columns]
            if isinstance(index, dict):
                index["columns"] = renamed
            elif isinstance(index, IndexSnapshot):
                index.columns = renamed
        for candidate in result.tables.values():
            for fk in candidate.foreign_keys:
                if candidate is table and self._field(fk, "column") == old_name:
                    if isinstance(fk, dict):
                        fk["column"] = new_name
                    else:
                        fk.column = new_name
                if (
                    self._field(fk, "references_table") == table_name
                    and self._field(fk, "references_column") == old_name
                ):
                    if isinstance(fk, dict):
                        fk["references_column"] = new_name
                    else:
                        fk.references_column = new_name

    def _apply_alter_sql(self, sql: str, result: SchemaSnapshot) -> None:
        """Apply FK mutations from ALTER forms sqlglot represents as Command."""
        table_match = re.search(
            r"ALTER\s+TABLE\s+(?:\"?\w+\"?\.)?\"?(\w+)\"?", sql, re.IGNORECASE
        )
        if not table_match:
            return
        table = result.tables.get(table_match.group(1).lower())
        if table is None:
            return

        for constraint_name in re.findall(
            r"DROP\s+CONSTRAINT(?:\s+IF\s+EXISTS)?\s+\"?(\w+)\"?",
            sql,
            re.IGNORECASE,
        ):
            table.foreign_keys = [
                fk
                for fk in table.foreign_keys
                if self._field(fk, "name") != constraint_name.lower()
            ]

        fk_pattern = re.compile(
            r"(?:ADD\s+CONSTRAINT\s+\"?(\w+)\"?\s+)?FOREIGN\s+KEY\s*"
            r"\(\s*\"?(\w+)\"?\s*\)\s+REFERENCES\s+"
            r"(?:\"?\w+\"?\.)?\"?(\w+)\"?\s*\(\s*\"?(\w+)\"?\s*\)"
            r"(.*?)(?=,\s*(?:ADD|DROP|ALTER|RENAME)\b|$)",
            re.IGNORECASE | re.DOTALL,
        )
        for match in fk_pattern.finditer(sql):
            name, column, ref_table, ref_column, options = match.groups()
            delete = re.search(
                r"ON\s+DELETE\s+(NO\s+ACTION|RESTRICT|CASCADE|SET\s+NULL|SET\s+DEFAULT)",
                options,
                re.IGNORECASE,
            )
            update = re.search(
                r"ON\s+UPDATE\s+(NO\s+ACTION|RESTRICT|CASCADE|SET\s+NULL|SET\s+DEFAULT)",
                options,
                re.IGNORECASE,
            )
            fk_name = (name or f"{table.name}_{column}_fkey").lower()
            table.foreign_keys = [
                fk for fk in table.foreign_keys if self._field(fk, "name") != fk_name
            ]
            table.foreign_keys.append(
                ForeignKeySnapshot(
                    name=fk_name,
                    column=column.lower(),
                    references_table=ref_table.lower(),
                    references_column=ref_column.lower(),
                    on_delete=delete.group(1).upper() if delete else None,
                    on_update=update.group(1).upper() if update else None,
                )
            )

    @staticmethod
    def _field(item: object, name: str, default: object = "") -> object:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

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
