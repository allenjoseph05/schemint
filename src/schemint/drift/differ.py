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
from typing import Any

from schemint.drift.change_classifier import classify_change
from schemint.drift.models import (
    MigrationGap,
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
            changes.append(
                SchemaChangeEvent(
                    change_type="table_dropped",
                    table=table_name,
                    detected_at=now,
                )
            )

        # Tables added
        for table_name in sorted(new_tables - old_tables):
            changes.append(
                SchemaChangeEvent(
                    change_type="table_added",
                    table=table_name,
                    detected_at=now,
                )
            )

        # Shared tables — compare columns, PKs, indexes, FKs
        for table_name in sorted(old_tables & new_tables):
            old_table = old.tables[table_name]
            new_table = new.tables[table_name]

            # Column changes
            changes.extend(self._diff_columns(old_table, new_table, now))

            # Primary key changes
            changes.extend(self._diff_primary_keys(old_table, new_table, now))

            # Index changes
            changes.extend(self._diff_indexes(old_table, new_table, now))

            # FK changes (structural + action changes)
            changes.extend(self._diff_foreign_keys(old_table, new_table, now))

        # View changes
        changes.extend(self._diff_views(old, new, now))

        # Trigger changes
        changes.extend(self._diff_triggers(old, new, now))

        # Sequence changes
        changes.extend(self._diff_sequences(old, new, now))

        # Enum changes
        changes.extend(self._diff_enums(old, new, now))

        # Function changes
        changes.extend(self._diff_functions(old, new, now))

        # Extension changes
        changes.extend(self._diff_extensions(old, new, now))

        # Permission changes
        changes.extend(self._diff_permissions(old, new, now))

        # RLS Policy changes
        changes.extend(self._diff_policies(old, new, now))

        # Partition changes
        changes.extend(self._diff_partitions(old, new, now))

        # Materialized view changes
        changes.extend(self._diff_materialized_views(old, new, now))

        # Classify risk for every change
        for change in changes:
            change.change_risk = classify_change(change)

        return SchemaDiffResult(
            old_snapshot_id=old.snapshot_id,
            new_snapshot_id=new.snapshot_id,
            changes=changes,
            diffed_at=now,
        )

    def _diff_columns(
        self, old_table: Any, new_table: Any, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare columns between two table snapshots."""
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        old_cols = set(old_table.columns.keys())
        new_cols = set(new_table.columns.keys())

        # Columns dropped
        for col_name in sorted(old_cols - new_cols):
            changes.append(
                SchemaChangeEvent(
                    change_type="column_dropped",
                    table=table_name,
                    column=col_name,
                    detected_at=now,
                )
            )

        # Columns added
        for col_name in sorted(new_cols - old_cols):
            col = new_table.columns[col_name]
            # Encode key properties so risk classifier can distinguish
            # NOT NULL without default (breaking) from nullable (safe).
            new_val_parts: list[str] = [col.type]
            if not col.nullable:
                new_val_parts.append("NOT NULL")
            if col.default is not None:
                new_val_parts.append(f"DEFAULT {col.default}")
            changes.append(
                SchemaChangeEvent(
                    change_type="column_added",
                    table=table_name,
                    column=col_name,
                    new_value=" ".join(new_val_parts),
                    detected_at=now,
                )
            )

        # Shared columns — check for type, nullable, default, constraint changes
        for col_name in sorted(old_cols & new_cols):
            old_col = old_table.columns[col_name]
            new_col = new_table.columns[col_name]

            # Type change
            if old_col.type != new_col.type:
                changes.append(
                    SchemaChangeEvent(
                        change_type="column_type_change",
                        table=table_name,
                        column=col_name,
                        old_value=old_col.type,
                        new_value=new_col.type,
                        detected_at=now,
                    )
                )

            # Nullable change
            if old_col.nullable != new_col.nullable:
                changes.append(
                    SchemaChangeEvent(
                        change_type="column_nullable_change",
                        table=table_name,
                        column=col_name,
                        old_value=str(old_col.nullable),
                        new_value=str(new_col.nullable),
                        detected_at=now,
                    )
                )

            # Default change
            if old_col.default != new_col.default:
                changes.append(
                    SchemaChangeEvent(
                        change_type="column_default_change",
                        table=table_name,
                        column=col_name,
                        old_value=old_col.default,
                        new_value=new_col.default,
                        detected_at=now,
                    )
                )

            # Constraint changes (CHECK, UNIQUE, etc.)
            old_constraints = set(old_col.constraints)
            new_constraints = set(new_col.constraints)
            if old_constraints != new_constraints:
                changes.append(
                    SchemaChangeEvent(
                        change_type="column_constraint_change",
                        table=table_name,
                        column=col_name,
                        old_value=",".join(sorted(old_constraints)),
                        new_value=",".join(sorted(new_constraints)),
                        detected_at=now,
                    )
                )

        return changes

    def _diff_primary_keys(
        self, old_table: Any, new_table: Any, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare primary keys between two table snapshots.

        Detects:
            - pk_added: PK was absent, now present
            - pk_dropped: PK was present, now absent
            - pk_changed: PK columns changed (e.g. composite key reordered/expanded)
        """
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        old_pk = sorted(old_table.primary_key)
        new_pk = sorted(new_table.primary_key)

        if old_pk == new_pk:
            return changes

        if not old_pk and new_pk:
            changes.append(
                SchemaChangeEvent(
                    change_type="pk_added",
                    table=table_name,
                    new_value=",".join(new_pk),
                    detected_at=now,
                )
            )
        elif old_pk and not new_pk:
            changes.append(
                SchemaChangeEvent(
                    change_type="pk_dropped",
                    table=table_name,
                    old_value=",".join(old_pk),
                    detected_at=now,
                )
            )
        else:
            changes.append(
                SchemaChangeEvent(
                    change_type="pk_changed",
                    table=table_name,
                    old_value=",".join(old_pk),
                    new_value=",".join(new_pk),
                    detected_at=now,
                )
            )

        return changes

    def _diff_indexes(
        self, old_table: Any, new_table: Any, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare indexes between two table snapshots.

        Detects:
            - index_added/dropped: structural identity (name + columns) changed
            - index_changed: same structural identity but properties differ
              (e.g. uniqueness toggled, primary flag changed)
        """
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def _idx_attr(idx: Any, attr: str, default: Any = None) -> Any:
            """Get index attribute, supporting both typed models and dicts."""
            if hasattr(idx, attr):
                return getattr(idx, attr)
            return idx.get(attr, default)

        def structural_key(idx: Any) -> str:
            """Structural identity: name + sorted columns."""
            name = _idx_attr(idx, "name", "")
            cols = _idx_attr(idx, "columns", [])
            if not isinstance(cols, list):
                cols = []
            return f"{name}:{','.join(sorted(cols))}"

        def property_summary(idx: Any) -> str:
            """Property summary for detecting property changes."""
            is_unique = _idx_attr(idx, "is_unique", False)
            is_primary = _idx_attr(idx, "is_primary", False)
            return f"unique={is_unique},primary={is_primary}"

        # Build maps: structural_key → (idx, property_summary)
        old_idx_map: dict[str, tuple[object, str]] = {}
        for idx in old_table.indexes:
            key = structural_key(idx)
            old_idx_map[key] = (idx, property_summary(idx))

        new_idx_map: dict[str, tuple[object, str]] = {}
        for idx in new_table.indexes:
            key = structural_key(idx)
            new_idx_map[key] = (idx, property_summary(idx))

        old_keys = set(old_idx_map.keys())
        new_keys = set(new_idx_map.keys())

        for key in sorted(old_keys - new_keys):
            changes.append(
                SchemaChangeEvent(
                    change_type="index_dropped",
                    table=table_name,
                    old_value=key,
                    detected_at=now,
                )
            )

        for key in sorted(new_keys - old_keys):
            changes.append(
                SchemaChangeEvent(
                    change_type="index_added",
                    table=table_name,
                    new_value=key,
                    detected_at=now,
                )
            )

        # Shared indexes — check for property changes
        for key in sorted(old_keys & new_keys):
            _, old_props = old_idx_map[key]
            _, new_props = new_idx_map[key]
            if old_props != new_props:
                changes.append(
                    SchemaChangeEvent(
                        change_type="index_changed",
                        table=table_name,
                        old_value=f"{key} ({old_props})",
                        new_value=f"{key} ({new_props})",
                        detected_at=now,
                    )
                )

        return changes

    def _diff_foreign_keys(
        self, old_table: Any, new_table: Any, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare foreign keys between two table snapshots.

        Detects:
            - FK added/dropped (structural key: column→ref_table.ref_col)
            - FK action changes (ON DELETE/ON UPDATE changed on same structural FK)
        """
        changes: list[SchemaChangeEvent] = []
        table_name = old_table.name

        def _fk_attr(fk: Any, attr: str, default: str = "") -> str:
            """Get FK attribute, supporting both typed models and dicts."""
            if hasattr(fk, attr):
                return getattr(fk, attr) or default
            return fk.get(attr, default)  # type: ignore[no-any-return]

        def fk_structural_key(fk: Any) -> str:
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
            changes.append(
                SchemaChangeEvent(
                    change_type="fk_dropped",
                    table=table_name,
                    old_value=key,
                    detected_at=now,
                )
            )

        # FKs added
        for key in sorted(new_keys - old_keys):
            changes.append(
                SchemaChangeEvent(
                    change_type="fk_added",
                    table=table_name,
                    new_value=key,
                    detected_at=now,
                )
            )

        # Shared FKs — check for action changes
        for key in sorted(old_keys & new_keys):
            old_fk = old_fk_map[key]
            new_fk = new_fk_map[key]

            old_on_delete = _fk_attr(old_fk, "on_delete")
            new_on_delete = _fk_attr(new_fk, "on_delete")
            old_on_update = _fk_attr(old_fk, "on_update")
            new_on_update = _fk_attr(new_fk, "on_update")

            if old_on_delete != new_on_delete:
                changes.append(
                    SchemaChangeEvent(
                        change_type="fk_action_change",
                        table=table_name,
                        column=_fk_attr(old_fk, "column"),
                        old_value=f"ON DELETE {old_on_delete or 'NO ACTION'}",
                        new_value=f"ON DELETE {new_on_delete or 'NO ACTION'}",
                        detected_at=now,
                    )
                )

            if old_on_update != new_on_update:
                changes.append(
                    SchemaChangeEvent(
                        change_type="fk_action_change",
                        table=table_name,
                        column=_fk_attr(old_fk, "column"),
                        old_value=f"ON UPDATE {old_on_update or 'NO ACTION'}",
                        new_value=f"ON UPDATE {new_on_update or 'NO ACTION'}",
                        detected_at=now,
                    )
                )

        return changes

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        """Normalize SQL for comparison: collapse whitespace, strip, lowercase.

        Avoids false-positive view_definition_change events caused by
        insignificant whitespace or casing differences between DDL and
        live DB representations.
        """
        import re

        return re.sub(r"\s+", " ", sql.strip()).lower()

    def _diff_views(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare views between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_views = set(old.views.keys())
        new_views = set(new.views.keys())

        for view_name in sorted(old_views - new_views):
            changes.append(
                SchemaChangeEvent(
                    change_type="view_dropped",
                    table=view_name,
                    detected_at=now,
                )
            )

        for view_name in sorted(new_views - old_views):
            changes.append(
                SchemaChangeEvent(
                    change_type="view_added",
                    table=view_name,
                    detected_at=now,
                )
            )

        for view_name in sorted(old_views & new_views):
            old_raw = old.views[view_name].definition
            new_raw = new.views[view_name].definition
            if self._normalize_sql(old_raw) != self._normalize_sql(new_raw):
                changes.append(
                    SchemaChangeEvent(
                        change_type="view_definition_change",
                        table=view_name,
                        old_value=old_raw.strip(),
                        new_value=new_raw.strip(),
                        detected_at=now,
                    )
                )

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
            changes.append(
                SchemaChangeEvent(
                    change_type="trigger_dropped",
                    table=trig.table,
                    old_value=trigger_name,
                    detected_at=now,
                )
            )

        for trigger_name in sorted(new_triggers - old_triggers):
            trig = new.triggers[trigger_name]
            changes.append(
                SchemaChangeEvent(
                    change_type="trigger_added",
                    table=trig.table,
                    new_value=trigger_name,
                    detected_at=now,
                )
            )

        for trigger_name in sorted(old_triggers & new_triggers):
            old_trig = old.triggers[trigger_name]
            new_trig = new.triggers[trigger_name]
            if (
                old_trig.event != new_trig.event
                or old_trig.timing != new_trig.timing
                or old_trig.function_name != new_trig.function_name
                or old_trig.definition != new_trig.definition
            ):
                changes.append(
                    SchemaChangeEvent(
                        change_type="trigger_changed",
                        table=new_trig.table,
                        old_value=f"{old_trig.timing} {old_trig.event} → {old_trig.function_name}",
                        new_value=f"{new_trig.timing} {new_trig.event} → {new_trig.function_name}",
                        detected_at=now,
                    )
                )

        return changes

    def _diff_sequences(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare sequences between two schema snapshots.

        Detects:
            - sequence_added/dropped: structural presence change
            - sequence_changed: properties differ (increment, bounds, cycle, etc.)
        """
        changes: list[SchemaChangeEvent] = []

        old_seqs = set(old.sequences.keys())
        new_seqs = set(new.sequences.keys())

        for seq_name in sorted(old_seqs - new_seqs):
            changes.append(
                SchemaChangeEvent(
                    change_type="sequence_dropped",
                    table=seq_name,
                    old_value=seq_name,
                    detected_at=now,
                )
            )

        for seq_name in sorted(new_seqs - old_seqs):
            changes.append(
                SchemaChangeEvent(
                    change_type="sequence_added",
                    table=seq_name,
                    new_value=seq_name,
                    detected_at=now,
                )
            )

        for seq_name in sorted(old_seqs & new_seqs):
            old_seq = old.sequences[seq_name]
            new_seq = new.sequences[seq_name]
            diffs: list[str] = []
            for attr in (
                "data_type",
                "increment_by",
                "min_value",
                "max_value",
                "cache_size",
                "cycle",
            ):
                old_val = getattr(old_seq, attr)
                new_val = getattr(new_seq, attr)
                if old_val != new_val:
                    diffs.append(f"{attr}: {old_val} → {new_val}")
            if diffs:
                changes.append(
                    SchemaChangeEvent(
                        change_type="sequence_changed",
                        table=seq_name,
                        old_value="; ".join(diffs),
                        new_value=seq_name,
                        detected_at=now,
                    )
                )

        return changes

    def _diff_enums(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare enum types between two schema snapshots.

        Detects:
            - enum_added/dropped: type presence change
            - enum_value_added: new value(s) appended (generally safe)
            - enum_value_removed: value(s) removed (always breaking — existing data uses them)
        """
        changes: list[SchemaChangeEvent] = []

        old_enums = set(old.enums.keys())
        new_enums = set(new.enums.keys())

        for enum_name in sorted(old_enums - new_enums):
            changes.append(
                SchemaChangeEvent(
                    change_type="enum_dropped",
                    table=enum_name,
                    old_value=",".join(old.enums[enum_name].values),
                    detected_at=now,
                )
            )

        for enum_name in sorted(new_enums - old_enums):
            changes.append(
                SchemaChangeEvent(
                    change_type="enum_added",
                    table=enum_name,
                    new_value=",".join(new.enums[enum_name].values),
                    detected_at=now,
                )
            )

        for enum_name in sorted(old_enums & new_enums):
            old_vals = old.enums[enum_name].values
            new_vals = new.enums[enum_name].values
            old_set = set(old_vals)
            new_set = set(new_vals)

            added_vals = sorted(new_set - old_set)
            removed_vals = sorted(old_set - new_set)

            if added_vals:
                changes.append(
                    SchemaChangeEvent(
                        change_type="enum_value_added",
                        table=enum_name,
                        new_value=",".join(added_vals),
                        detected_at=now,
                    )
                )

            if removed_vals:
                changes.append(
                    SchemaChangeEvent(
                        change_type="enum_value_removed",
                        table=enum_name,
                        old_value=",".join(removed_vals),
                        detected_at=now,
                    )
                )

        return changes

    def _diff_functions(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare functions between two schema snapshots.

        Uses function name as the identity key. Detects:
            - function_added/dropped: presence change
            - function_changed: signature or body changed
        """
        changes: list[SchemaChangeEvent] = []

        old_funcs = set(old.functions.keys())
        new_funcs = set(new.functions.keys())

        for func_name in sorted(old_funcs - new_funcs):
            changes.append(
                SchemaChangeEvent(
                    change_type="function_dropped",
                    table=func_name,
                    old_value=func_name,
                    detected_at=now,
                )
            )

        for func_name in sorted(new_funcs - old_funcs):
            changes.append(
                SchemaChangeEvent(
                    change_type="function_added",
                    table=func_name,
                    new_value=func_name,
                    detected_at=now,
                )
            )

        for func_name in sorted(old_funcs & new_funcs):
            old_fn = old.functions[func_name]
            new_fn = new.functions[func_name]
            if (
                old_fn.argument_types != new_fn.argument_types
                or old_fn.return_type != new_fn.return_type
                or old_fn.definition != new_fn.definition
                or old_fn.volatility != new_fn.volatility
            ):
                changes.append(
                    SchemaChangeEvent(
                        change_type="function_changed",
                        table=func_name,
                        old_value=f"{old_fn.return_type}({old_fn.argument_types}) [{old_fn.volatility}]",
                        new_value=f"{new_fn.return_type}({new_fn.argument_types}) [{new_fn.volatility}]",
                        detected_at=now,
                    )
                )

        return changes

    def _diff_extensions(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare installed extensions between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_exts = set(old.extensions.keys())
        new_exts = set(new.extensions.keys())

        for ext_name in sorted(old_exts - new_exts):
            changes.append(
                SchemaChangeEvent(
                    change_type="extension_dropped",
                    table=ext_name,
                    old_value=old.extensions[ext_name].version,
                    detected_at=now,
                )
            )

        for ext_name in sorted(new_exts - old_exts):
            changes.append(
                SchemaChangeEvent(
                    change_type="extension_added",
                    table=ext_name,
                    new_value=new.extensions[ext_name].version,
                    detected_at=now,
                )
            )

        for ext_name in sorted(old_exts & new_exts):
            old_ver = old.extensions[ext_name].version
            new_ver = new.extensions[ext_name].version
            if old_ver != new_ver:
                changes.append(
                    SchemaChangeEvent(
                        change_type="extension_version_changed",
                        table=ext_name,
                        old_value=old_ver,
                        new_value=new_ver,
                        detected_at=now,
                    )
                )

        return changes

    def _diff_permissions(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare table-level permissions between two schema snapshots.

        Uses (table, grantee, privilege) as the identity key.
        """
        changes: list[SchemaChangeEvent] = []

        def perm_key(p: Any) -> str:
            return f"{p.table_name}:{p.grantee}:{p.privilege_type}"

        old_perms = {perm_key(p) for p in old.permissions}
        new_perms = {perm_key(p) for p in new.permissions}

        for key in sorted(old_perms - new_perms):
            table_name = key.split(":")[0]
            changes.append(
                SchemaChangeEvent(
                    change_type="permission_revoked",
                    table=table_name,
                    old_value=key,
                    detected_at=now,
                )
            )

        for key in sorted(new_perms - old_perms):
            table_name = key.split(":")[0]
            changes.append(
                SchemaChangeEvent(
                    change_type="permission_granted",
                    table=table_name,
                    new_value=key,
                    detected_at=now,
                )
            )

        return changes

    def _diff_policies(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare RLS policies between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_pols = set(old.policies.keys())
        new_pols = set(new.policies.keys())

        for pol_name in sorted(old_pols - new_pols):
            pol = old.policies[pol_name]
            changes.append(
                SchemaChangeEvent(
                    change_type="policy_dropped",
                    table=pol.table,
                    old_value=pol_name,
                    detected_at=now,
                )
            )

        for pol_name in sorted(new_pols - old_pols):
            pol = new.policies[pol_name]
            changes.append(
                SchemaChangeEvent(
                    change_type="policy_added",
                    table=pol.table,
                    new_value=pol_name,
                    detected_at=now,
                )
            )

        for pol_name in sorted(old_pols & new_pols):
            old_pol = old.policies[pol_name]
            new_pol = new.policies[pol_name]
            if (
                old_pol.command != new_pol.command
                or old_pol.permissive != new_pol.permissive
                or old_pol.qual_expression != new_pol.qual_expression
                or old_pol.with_check_expression != new_pol.with_check_expression
                or old_pol.roles != new_pol.roles
            ):
                changes.append(
                    SchemaChangeEvent(
                        change_type="policy_changed",
                        table=new_pol.table,
                        old_value=f"{old_pol.command} permissive={old_pol.permissive}",
                        new_value=f"{new_pol.command} permissive={new_pol.permissive}",
                        detected_at=now,
                    )
                )

        return changes

    def _diff_partitions(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare partition hierarchies between two schema snapshots.

        Compares partition lists per parent table.
        """
        changes: list[SchemaChangeEvent] = []

        all_tables = set(old.partitions.keys()) | set(new.partitions.keys())

        for parent_table in sorted(all_tables):
            old_parts = {p.partition_name for p in old.partitions.get(parent_table, [])}
            new_parts = {p.partition_name for p in new.partitions.get(parent_table, [])}

            for part_name in sorted(old_parts - new_parts):
                changes.append(
                    SchemaChangeEvent(
                        change_type="partition_dropped",
                        table=parent_table,
                        old_value=part_name,
                        detected_at=now,
                    )
                )

            for part_name in sorted(new_parts - old_parts):
                changes.append(
                    SchemaChangeEvent(
                        change_type="partition_added",
                        table=parent_table,
                        new_value=part_name,
                        detected_at=now,
                    )
                )

        return changes

    def _diff_materialized_views(
        self, old: SchemaSnapshot, new: SchemaSnapshot, now: datetime
    ) -> list[SchemaChangeEvent]:
        """Compare materialized views between two schema snapshots."""
        changes: list[SchemaChangeEvent] = []

        old_mvs = set(old.materialized_views.keys())
        new_mvs = set(new.materialized_views.keys())

        for mv_name in sorted(old_mvs - new_mvs):
            changes.append(
                SchemaChangeEvent(
                    change_type="matview_dropped",
                    table=mv_name,
                    detected_at=now,
                )
            )

        for mv_name in sorted(new_mvs - old_mvs):
            changes.append(
                SchemaChangeEvent(
                    change_type="matview_added",
                    table=mv_name,
                    detected_at=now,
                )
            )

        for mv_name in sorted(old_mvs & new_mvs):
            old_def = old.materialized_views[mv_name].definition
            new_def = new.materialized_views[mv_name].definition
            if self._normalize_sql(old_def) != self._normalize_sql(new_def):
                changes.append(
                    SchemaChangeEvent(
                        change_type="matview_definition_changed",
                        table=mv_name,
                        old_value=old_def.strip(),
                        new_value=new_def.strip(),
                        detected_at=now,
                    )
                )

        return changes

    def diff_against_desired(
        self,
        current: SchemaSnapshot,
        desired: SchemaSnapshot,
        environment: str = "default",
    ) -> MigrationGap:
        """Diff current state against desired state, returning a MigrationGap.

        The changes list describes what migrations are needed to move
        from current to desired. Internally delegates to self.diff().
        """
        diff_result = self.diff(current, desired)
        return MigrationGap(
            current_snapshot_id=current.snapshot_id,
            desired_snapshot_id=desired.snapshot_id,
            environment=environment,
            changes=diff_result.changes,
        )

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

    def diff_multi(self, old: MultiSchemaSnapshot, new: MultiSchemaSnapshot) -> SchemaDiffResult:
        """Diff two multi-schema snapshots by flattening both first.

        Uses qualified table names (schema.table) so cross-schema changes
        are visible as table adds/drops when schemas differ.
        """
        from schemint.drift.snapshot import SnapshotService

        service = SnapshotService()
        flat_old = service.flatten_multi_schema(old)
        flat_new = service.flatten_multi_schema(new)
        return self.diff(flat_old, flat_new)
