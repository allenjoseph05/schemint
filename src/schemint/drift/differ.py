"""Schema differ — pure deterministic set comparison.

No heuristics. No inference. Pure set operations on table and column names.

Rename detection is intentionally absent:
    If a table disappears and a new one appears with identical columns,
    the differ reports table_dropped + table_added — NOT table_renamed.
    Rename detection requires reasoning about intent (was it a rename or
    a drop+recreate?). That is an AI task (Phase 3+), not a diff task.
    The "table_renamed" change_type exists in the model for AI to emit
    after reasoning, but the differ itself will never produce it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.models import (
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
)


class SchemaDiffer:
    """Diffs two schema snapshots to produce deterministic change events."""

    def diff(self, old: SchemaSnapshot, new: SchemaSnapshot) -> SchemaDiffResult:
        """Diff two snapshots and return all detected changes.

        Pure set comparison — no inference, no heuristics.
        """
        now = datetime.now(timezone.utc)
        changes: list[SchemaChangeEvent] = []

        old_tables = set(old.tables.keys())
        new_tables = set(new.tables.keys())

        # Tables dropped
        for table_name in sorted(old_tables - new_tables):
            changes.append(SchemaChangeEvent(
                change_type="table_dropped",
                table=table_name,
                detected_at=now,
            ))

        # Tables added
        for table_name in sorted(new_tables - old_tables):
            changes.append(SchemaChangeEvent(
                change_type="table_added",
                table=table_name,
                detected_at=now,
            ))

        # Shared tables — compare columns, indexes, FKs
        for table_name in sorted(old_tables & new_tables):
            old_table = old.tables[table_name]
            new_table = new.tables[table_name]

            # Column changes
            changes.extend(self._diff_columns(old_table, new_table, now))

            # Index changes
            changes.extend(self._diff_indexes(old_table, new_table, now))

            # FK changes
            changes.extend(self._diff_foreign_keys(old_table, new_table, now))

        return SchemaDiffResult(
            old_snapshot_id=old.snapshot_id,
            new_snapshot_id=new.snapshot_id,
            changes=changes,
            diffed_at=now,
        )

    def _diff_columns(self, old_table, new_table, now: datetime) -> list[SchemaChangeEvent]:
        """Compare columns between two table snapshots."""
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        old_cols = set(old_table.columns.keys())
        new_cols = set(new_table.columns.keys())

        # Columns dropped
        for col_name in sorted(old_cols - new_cols):
            changes.append(SchemaChangeEvent(
                change_type="column_dropped",
                table=table_name,
                column=col_name,
                detected_at=now,
            ))

        # Columns added
        for col_name in sorted(new_cols - old_cols):
            changes.append(SchemaChangeEvent(
                change_type="column_added",
                table=table_name,
                column=col_name,
                detected_at=now,
            ))

        # Shared columns — check for type, nullable, default changes
        for col_name in sorted(old_cols & new_cols):
            old_col = old_table.columns[col_name]
            new_col = new_table.columns[col_name]

            # Type change
            if old_col.type != new_col.type:
                changes.append(SchemaChangeEvent(
                    change_type="column_type_change",
                    table=table_name,
                    column=col_name,
                    old_value=old_col.type,
                    new_value=new_col.type,
                    detected_at=now,
                ))

            # Nullable change
            if old_col.nullable != new_col.nullable:
                changes.append(SchemaChangeEvent(
                    change_type="column_nullable_change",
                    table=table_name,
                    column=col_name,
                    old_value=str(old_col.nullable),
                    new_value=str(new_col.nullable),
                    detected_at=now,
                ))

            # Default change
            if old_col.default != new_col.default:
                changes.append(SchemaChangeEvent(
                    change_type="column_default_change",
                    table=table_name,
                    column=col_name,
                    old_value=old_col.default,
                    new_value=new_col.default,
                    detected_at=now,
                ))

        return changes

    def _diff_indexes(self, old_table, new_table, now: datetime) -> list[SchemaChangeEvent]:
        """Compare indexes between two table snapshots."""
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def index_key(idx: dict) -> str:
            """Create a comparable key for an index."""
            cols = idx.get("columns", [])
            name = idx.get("name", "")
            return f"{name}:{','.join(sorted(cols))}"

        old_idx_keys = {index_key(idx) for idx in old_table.indexes}
        new_idx_keys = {index_key(idx) for idx in new_table.indexes}

        for key in sorted(old_idx_keys - new_idx_keys):
            changes.append(SchemaChangeEvent(
                change_type="index_dropped",
                table=table_name,
                old_value=key,
                detected_at=now,
            ))

        for key in sorted(new_idx_keys - old_idx_keys):
            changes.append(SchemaChangeEvent(
                change_type="index_added",
                table=table_name,
                new_value=key,
                detected_at=now,
            ))

        return changes

    def _diff_foreign_keys(self, old_table, new_table, now: datetime) -> list[SchemaChangeEvent]:
        """Compare foreign keys between two table snapshots."""
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def fk_key(fk: dict) -> str:
            """Create a comparable key for a foreign key."""
            col = fk.get("column", "")
            ref_table = fk.get("references_table", "")
            ref_col = fk.get("references_column", "")
            return f"{col}->{ref_table}.{ref_col}"

        old_fk_keys = {fk_key(fk) for fk in old_table.foreign_keys}
        new_fk_keys = {fk_key(fk) for fk in new_table.foreign_keys}

        for key in sorted(old_fk_keys - new_fk_keys):
            changes.append(SchemaChangeEvent(
                change_type="fk_dropped",
                table=table_name,
                old_value=key,
                detected_at=now,
            ))

        for key in sorted(new_fk_keys - old_fk_keys):
            changes.append(SchemaChangeEvent(
                change_type="fk_added",
                table=table_name,
                new_value=key,
                detected_at=now,
            ))

        return changes
