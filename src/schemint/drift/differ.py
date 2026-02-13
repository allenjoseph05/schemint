"""Schema differ — pure deterministic set comparison.

No heuristics. No inference. Pure set operations on table and column names.

Rename detection is intentionally absent:
    If a table disappears and a new one appears with identical columns,
    the differ reports table_dropped + table_added — NOT table_renamed.
    Rename detection requires reasoning about intent (was it a rename or
    a drop+recreate?). That is an AI task (Phase 3+), not a diff task.
    The "table_renamed" change_type exists in the model for AI to emit
    after reasoning, but the differ itself will never produce it.

Enhancements over original:
    - FK action changes (ON DELETE, ON UPDATE) are detected as fk_action_change events.
    - Column constraint changes (CHECK, UNIQUE added/removed) are detected.
    - Every change event is risk-classified by the ChangeClassifier.
"""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.change_classifier import classify_change
from schemint.drift.models import (
    MultiSchemaSnapshot,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
)


class SchemaDiffer:
    """Diffs two schema snapshots to produce deterministic change events."""

    def diff(self, old: SchemaSnapshot, new: SchemaSnapshot) -> SchemaDiffResult:
        """Diff two snapshots and return all detected changes.

        Pure set comparison — no inference, no heuristics.
        Every change event is risk-classified automatically.
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

            # FK changes (structural + action changes)
            changes.extend(self._diff_foreign_keys(old_table, new_table, now))

        # View changes
        changes.extend(self._diff_views(old, new, now))

        # Trigger changes
        changes.extend(self._diff_triggers(old, new, now))

        # Classify risk for every change
        for change in changes:
            change.change_risk = classify_change(change)

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

        # Shared columns — check for type, nullable, default, constraint changes
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

            # Constraint changes (CHECK, UNIQUE, etc.)
            old_constraints = set(old_col.constraints)
            new_constraints = set(new_col.constraints)
            if old_constraints != new_constraints:
                changes.append(SchemaChangeEvent(
                    change_type="column_constraint_change",
                    table=table_name,
                    column=col_name,
                    old_value=",".join(sorted(old_constraints)),
                    new_value=",".join(sorted(new_constraints)),
                    detected_at=now,
                ))

        return changes

    def _diff_indexes(self, old_table, new_table, now: datetime) -> list[SchemaChangeEvent]:
        """Compare indexes between two table snapshots."""
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def index_key(idx) -> str:
            """Create a comparable key for an index (handles both typed and dict)."""
            if hasattr(idx, "columns"):
                cols = idx.columns if isinstance(idx.columns, list) else []
                name = idx.name if hasattr(idx, "name") else ""
            else:
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
        """Compare foreign keys between two table snapshots.

        Detects:
            - FK added/dropped (structural key: column→ref_table.ref_col)
            - FK action changes (ON DELETE/ON UPDATE changed on same structural FK)
        """
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def _fk_attr(fk, attr: str, default: str = "") -> str:
            """Get FK attribute, supporting both typed models and dicts."""
            if hasattr(fk, attr):
                return getattr(fk, attr) or default
            return fk.get(attr, default)

        def fk_structural_key(fk) -> str:
            """Structural identity of a FK (ignoring actions)."""
            col = _fk_attr(fk, "column")
            ref_table = _fk_attr(fk, "references_table")
            ref_col = _fk_attr(fk, "references_column")
            return f"{col}->{ref_table}.{ref_col}"

        # Build maps: structural_key → fk
        old_fk_map = {}
        for fk in old_table.foreign_keys:
            old_fk_map[fk_structural_key(fk)] = fk

        new_fk_map = {}
        for fk in new_table.foreign_keys:
            new_fk_map[fk_structural_key(fk)] = fk

        old_keys = set(old_fk_map.keys())
        new_keys = set(new_fk_map.keys())

        # FKs dropped
        for key in sorted(old_keys - new_keys):
            changes.append(SchemaChangeEvent(
                change_type="fk_dropped",
                table=table_name,
                old_value=key,
                detected_at=now,
            ))

        # FKs added
        for key in sorted(new_keys - old_keys):
            changes.append(SchemaChangeEvent(
                change_type="fk_added",
                table=table_name,
                new_value=key,
                detected_at=now,
            ))

        # Shared FKs — check for action changes
        for key in sorted(old_keys & new_keys):
            old_fk = old_fk_map[key]
            new_fk = new_fk_map[key]

            old_on_delete = _fk_attr(old_fk, "on_delete")
            new_on_delete = _fk_attr(new_fk, "on_delete")
            old_on_update = _fk_attr(old_fk, "on_update")
            new_on_update = _fk_attr(new_fk, "on_update")

            if old_on_delete != new_on_delete:
                changes.append(SchemaChangeEvent(
                    change_type="fk_action_change",
                    table=table_name,
                    column=_fk_attr(old_fk, "column"),
                    old_value=f"ON DELETE {old_on_delete or 'NO ACTION'}",
                    new_value=f"ON DELETE {new_on_delete or 'NO ACTION'}",
                    detected_at=now,
                ))

            if old_on_update != new_on_update:
                changes.append(SchemaChangeEvent(
                    change_type="fk_action_change",
                    table=table_name,
                    column=_fk_attr(old_fk, "column"),
                    old_value=f"ON UPDATE {old_on_update or 'NO ACTION'}",
                    new_value=f"ON UPDATE {new_on_update or 'NO ACTION'}",
                    detected_at=now,
                ))

        return changes

    def _diff_views(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare views between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_views = set(old.views.keys())
        new_views = set(new.views.keys())

        for view_name in sorted(old_views - new_views):
            changes.append(SchemaChangeEvent(
                change_type="view_dropped",
                table=view_name,
                detected_at=now,
            ))

        for view_name in sorted(new_views - old_views):
            changes.append(SchemaChangeEvent(
                change_type="view_added",
                table=view_name,
                detected_at=now,
            ))

        for view_name in sorted(old_views & new_views):
            old_def = old.views[view_name].definition.strip()
            new_def = new.views[view_name].definition.strip()
            if old_def != new_def:
                changes.append(SchemaChangeEvent(
                    change_type="view_definition_change",
                    table=view_name,
                    old_value=old_def,
                    new_value=new_def,
                    detected_at=now,
                ))

        return changes

    def _diff_triggers(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare triggers between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_triggers = set(old.triggers.keys())
        new_triggers = set(new.triggers.keys())

        for trigger_name in sorted(old_triggers - new_triggers):
            trig = old.triggers[trigger_name]
            changes.append(SchemaChangeEvent(
                change_type="trigger_dropped",
                table=trig.table,
                old_value=trigger_name,
                detected_at=now,
            ))

        for trigger_name in sorted(new_triggers - old_triggers):
            trig = new.triggers[trigger_name]
            changes.append(SchemaChangeEvent(
                change_type="trigger_added",
                table=trig.table,
                new_value=trigger_name,
                detected_at=now,
            ))

        for trigger_name in sorted(old_triggers & new_triggers):
            old_trig = old.triggers[trigger_name]
            new_trig = new.triggers[trigger_name]
            if (
                old_trig.event != new_trig.event
                or old_trig.timing != new_trig.timing
                or old_trig.function_name != new_trig.function_name
                or old_trig.definition != new_trig.definition
            ):
                changes.append(SchemaChangeEvent(
                    change_type="trigger_changed",
                    table=new_trig.table,
                    old_value=f"{old_trig.timing} {old_trig.event} → {old_trig.function_name}",
                    new_value=f"{new_trig.timing} {new_trig.event} → {new_trig.function_name}",
                    detected_at=now,
                ))

        return changes

    def diff_from_alter(self, sql: str) -> SchemaDiffResult:
        """Parse ALTER TABLE statements into a SchemaDiffResult.

        Wraps AlterParser output in a SchemaDiffResult for pipeline compatibility.
        Useful for migration file analysis where you have ALTER statements directly.
        """
        from schemint.drift.alter_parser import AlterParser

        parser = AlterParser()
        changes = parser.parse(sql)

        # Classify risk for every change
        for change in changes:
            change.change_risk = classify_change(change)

        now = datetime.now(timezone.utc)
        return SchemaDiffResult(
            old_snapshot_id="alter_source",
            new_snapshot_id="alter_target",
            changes=changes,
            diffed_at=now,
        )

    def diff_multi(
        self, old: MultiSchemaSnapshot, new: MultiSchemaSnapshot
    ) -> SchemaDiffResult:
        """Diff two multi-schema snapshots by flattening both first.

        Uses qualified table names (schema.table) so cross-schema changes
        are visible as table adds/drops when schemas differ.
        """
        from schemint.drift.snapshot import SnapshotService

        service = SnapshotService()
        flat_old = service.flatten_multi_schema(old)
        flat_new = service.flatten_multi_schema(new)
        return self.diff(flat_old, flat_new)
