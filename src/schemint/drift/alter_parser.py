"""ALTER TABLE parser — extracts schema change events from ALTER statements.

Uses sqlglot (NOT sqlparse) to parse ALTER TABLE statements. sqlglot provides
structured AST access to ALTER operations, which encode change intent directly
(no diffing needed).

Supported ALTER operations:
    - ADD COLUMN → column_added
    - DROP COLUMN → column_dropped
    - ALTER COLUMN TYPE → column_type_change
    - ALTER COLUMN SET/DROP NOT NULL → column_nullable_change
    - ALTER COLUMN SET/DROP DEFAULT → column_default_change
    - ADD CONSTRAINT (FK) → fk_added
    - DROP CONSTRAINT → fk_dropped / column_constraint_change
    - ADD CONSTRAINT (CHECK) → column_constraint_change
    - RENAME COLUMN → column_dropped + column_added
    - RENAME TABLE → table_renamed
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp as sqlglot_exp

from schemint.drift.models import SchemaChangeEvent

logger = logging.getLogger(__name__)


class AlterParser:
    """Parses ALTER TABLE statements into SchemaChangeEvent objects."""

    def parse(self, sql: str) -> list[SchemaChangeEvent]:
        """Parse ALTER TABLE statements into change events.

        Uses sqlglot to parse the SQL and extract structured ALTER operations.
        If parsing fails, returns an empty list (no guessing).
        """
        try:
            statements = sqlglot.parse(sql)
        except sqlglot.errors.ParseError:
            logger.warning("sqlglot parse failed for ALTER SQL; returning no events")
            return []

        events: list[SchemaChangeEvent] = []
        now = datetime.now(timezone.utc)

        for statement in statements:
            if statement is None:
                continue

            if isinstance(statement, sqlglot_exp.Alter):
                events.extend(self._parse_alter(statement, now))

        return events

    def parse_file(self, file_path: str) -> list[SchemaChangeEvent]:
        """Parse a migration file containing ALTER statements."""
        content = Path(file_path).read_text(encoding="utf-8")
        return self.parse(content)

    def _get_table_name(self, statement: sqlglot_exp.Alter) -> str:
        """Extract the table name from an ALTER statement."""
        table_expr = statement.args.get("this")
        if table_expr and hasattr(table_expr, "name"):
            return str(table_expr.name).lower()
        return ""

    def _parse_alter(
        self, statement: sqlglot_exp.Alter, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Parse an Alter statement into change events."""
        events: list[SchemaChangeEvent] = []
        table_name = self._get_table_name(statement)

        if not table_name:
            return events

        actions = statement.args.get("actions")
        if not actions:
            return events

        for action in actions:
            events.extend(self._parse_action(action, table_name, now))

        return events

    def _parse_action(
        self, action: Any, table_name: str, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Parse a single ALTER TABLE action into change events."""
        events: list[SchemaChangeEvent] = []

        # ADD COLUMN — represented as ColumnDef
        if isinstance(action, sqlglot_exp.ColumnDef):
            col_name = action.name if action.name else ""
            col_type = ""
            type_node = action.find(sqlglot_exp.DataType)
            if type_node:
                col_type = type_node.sql().lower()
            events.append(SchemaChangeEvent(
                change_type="column_added",
                table=table_name,
                column=col_name.lower(),
                new_value=col_type,
                detected_at=now,
            ))

        # DROP COLUMN or DROP CONSTRAINT
        elif isinstance(action, sqlglot_exp.Drop):
            kind = action.args.get("kind")
            target = action.args.get("this")
            if kind and str(kind).upper() == "COLUMN":
                col_name = target.name.lower() if target and hasattr(target, "name") else ""
                events.append(SchemaChangeEvent(
                    change_type="column_dropped",
                    table=table_name,
                    column=col_name,
                    detected_at=now,
                ))
            elif kind and str(kind).upper() == "CONSTRAINT":
                constraint_name = target.name if target and hasattr(target, "name") else ""
                events.append(SchemaChangeEvent(
                    change_type="fk_dropped",
                    table=table_name,
                    old_value=constraint_name,
                    detected_at=now,
                ))
            else:
                # Generic drop — try Column expression
                col_expr = action.find(sqlglot_exp.Column)
                if col_expr and col_expr.name:
                    events.append(SchemaChangeEvent(
                        change_type="column_dropped",
                        table=table_name,
                        column=col_expr.name.lower(),
                        detected_at=now,
                    ))

        # ALTER COLUMN (type change, nullable, default)
        elif isinstance(action, sqlglot_exp.AlterColumn):
            col_name = ""
            col_expr = action.args.get("this")
            if col_expr and hasattr(col_expr, "name"):
                col_name = col_expr.name.lower()

            dtype = action.args.get("dtype")
            if dtype:
                events.append(SchemaChangeEvent(
                    change_type="column_type_change",
                    table=table_name,
                    column=col_name,
                    new_value=dtype.sql().lower() if hasattr(dtype, "sql") else str(dtype).lower(),
                    detected_at=now,
                ))

            # SET NOT NULL / DROP NOT NULL
            # sqlglot uses allow_null: False = SET NOT NULL, True = DROP NOT NULL
            allow_null = action.args.get("allow_null")
            if allow_null is False:
                events.append(SchemaChangeEvent(
                    change_type="column_nullable_change",
                    table=table_name,
                    column=col_name,
                    old_value="True",
                    new_value="False",
                    detected_at=now,
                ))
            elif allow_null is True:
                events.append(SchemaChangeEvent(
                    change_type="column_nullable_change",
                    table=table_name,
                    column=col_name,
                    old_value="False",
                    new_value="True",
                    detected_at=now,
                ))

            # SET DEFAULT / DROP DEFAULT
            default = action.args.get("default")
            if default is not None:
                events.append(SchemaChangeEvent(
                    change_type="column_default_change",
                    table=table_name,
                    column=col_name,
                    new_value=default.sql() if hasattr(default, "sql") else str(default),
                    detected_at=now,
                ))

            # DROP DEFAULT (when only "drop" flag is set, no dtype or allow_null)
            if action.args.get("drop") and not dtype and allow_null is None:
                events.append(SchemaChangeEvent(
                    change_type="column_default_change",
                    table=table_name,
                    column=col_name,
                    old_value="<dropped>",
                    detected_at=now,
                ))

        # ADD CONSTRAINT
        elif isinstance(action, sqlglot_exp.AddConstraint):
            constraint = action.find(sqlglot_exp.ForeignKey)
            if constraint:
                ref = action.find(sqlglot_exp.Reference)
                ref_table = ""
                if ref:
                    ref_tbl = ref.find(sqlglot_exp.Table)
                    if ref_tbl and ref_tbl.name:
                        ref_table = ref_tbl.name.lower()
                events.append(SchemaChangeEvent(
                    change_type="fk_added",
                    table=table_name,
                    new_value=ref_table,
                    detected_at=now,
                ))
            else:
                events.append(SchemaChangeEvent(
                    change_type="column_constraint_change",
                    table=table_name,
                    new_value=action.sql(),
                    detected_at=now,
                ))

        # RENAME COLUMN
        elif isinstance(action, sqlglot_exp.RenameColumn):
            old_col = action.args.get("this")
            new_col = action.args.get("to")
            old_name = old_col.name.lower() if old_col and hasattr(old_col, "name") else ""
            new_name = new_col.name.lower() if new_col and hasattr(new_col, "name") else ""
            events.append(SchemaChangeEvent(
                change_type="column_dropped",
                table=table_name,
                column=old_name,
                detected_at=now,
            ))
            events.append(SchemaChangeEvent(
                change_type="column_added",
                table=table_name,
                column=new_name,
                detected_at=now,
            ))

        # RENAME TABLE (AlterRename in sqlglot)
        elif isinstance(action, sqlglot_exp.AlterRename):
            new_table = action.args.get("this")
            new_name = ""
            if new_table and hasattr(new_table, "name"):
                new_name = new_table.name.lower()
            events.append(SchemaChangeEvent(
                change_type="table_renamed",
                table=table_name,
                old_value=table_name,
                new_value=new_name,
                detected_at=now,
            ))

        return events
