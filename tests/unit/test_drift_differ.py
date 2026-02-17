"""Tests for SchemaDiffer — pure deterministic set comparison."""


import pytest

from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ColumnSnapshot,
    SchemaSnapshot,
    TableSnapshot,
)


@pytest.fixture
def differ():
    return SchemaDiffer()


def make_snapshot(snapshot_id: str, tables: dict[str, TableSnapshot]) -> SchemaSnapshot:
    """Helper to create a snapshot."""
    return SchemaSnapshot(
        snapshot_id=snapshot_id,
        source="ddl",
        database_type="postgresql",
        tables=tables,
    )


def make_table(name: str, columns: dict[str, ColumnSnapshot] | None = None, **kwargs) -> TableSnapshot:
    """Helper to create a table snapshot."""
    return TableSnapshot(name=name, columns=columns or {}, **kwargs)


def make_col(name: str, type: str = "INT", nullable: bool = True, default: str | None = None) -> ColumnSnapshot:
    """Helper to create a column snapshot."""
    return ColumnSnapshot(name=name, type=type, nullable=nullable, default=default)


class TestTableChanges:
    def test_table_added(self, differ):
        old = make_snapshot("old", {"users": make_table("users")})
        new = make_snapshot("new", {
            "users": make_table("users"),
            "orders": make_table("orders"),
        })

        result = differ.diff(old, new)
        added = [c for c in result.changes if c.change_type == "table_added"]
        assert len(added) == 1
        assert added[0].table == "orders"

    def test_table_dropped(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users"),
            "orders": make_table("orders"),
        })
        new = make_snapshot("new", {"users": make_table("users")})

        result = differ.diff(old, new)
        dropped = [c for c in result.changes if c.change_type == "table_dropped"]
        assert len(dropped) == 1
        assert dropped[0].table == "orders"

    def test_no_rename_inference(self, differ):
        """If a table disappears and a new one appears with the same columns,
        we report table_dropped + table_added, NOT table_renamed."""
        cols = {"id": make_col("id"), "name": make_col("name", "VARCHAR")}

        old = make_snapshot("old", {
            "old_users": make_table("old_users", columns=cols.copy()),
        })
        new = make_snapshot("new", {
            "new_users": make_table("new_users", columns=cols.copy()),
        })

        result = differ.diff(old, new)
        types = [c.change_type for c in result.changes]

        assert "table_renamed" not in types
        assert "table_dropped" in types
        assert "table_added" in types

    def test_identical_snapshots_empty_diff(self, differ):
        tables = {
            "users": make_table("users", columns={
                "id": make_col("id"),
                "name": make_col("name", "VARCHAR"),
            }),
        }
        old = make_snapshot("old", tables)
        new = make_snapshot("new", tables)

        result = differ.diff(old, new)
        assert len(result.changes) == 0


class TestColumnChanges:
    def test_column_added(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "id": make_col("id"),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "id": make_col("id"),
                "email": make_col("email", "VARCHAR"),
            }),
        })

        result = differ.diff(old, new)
        added = [c for c in result.changes if c.change_type == "column_added"]
        assert len(added) == 1
        assert added[0].column == "email"
        assert added[0].table == "users"

    def test_column_dropped(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "id": make_col("id"),
                "legacy": make_col("legacy"),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "id": make_col("id"),
            }),
        })

        result = differ.diff(old, new)
        dropped = [c for c in result.changes if c.change_type == "column_dropped"]
        assert len(dropped) == 1
        assert dropped[0].column == "legacy"

    def test_column_type_change(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "age": make_col("age", "INT"),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "age": make_col("age", "BIGINT"),
            }),
        })

        result = differ.diff(old, new)
        type_changes = [c for c in result.changes if c.change_type == "column_type_change"]
        assert len(type_changes) == 1
        assert type_changes[0].old_value == "INT"
        assert type_changes[0].new_value == "BIGINT"

    def test_column_nullable_change(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "name": make_col("name", "VARCHAR", nullable=True),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "name": make_col("name", "VARCHAR", nullable=False),
            }),
        })

        result = differ.diff(old, new)
        nullable_changes = [c for c in result.changes if c.change_type == "column_nullable_change"]
        assert len(nullable_changes) == 1
        assert nullable_changes[0].old_value == "True"
        assert nullable_changes[0].new_value == "False"

    def test_column_default_change(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "status": make_col("status", "VARCHAR", default="'active'"),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "status": make_col("status", "VARCHAR", default="'inactive'"),
            }),
        })

        result = differ.diff(old, new)
        default_changes = [c for c in result.changes if c.change_type == "column_default_change"]
        assert len(default_changes) == 1
        assert default_changes[0].old_value == "'active'"
        assert default_changes[0].new_value == "'inactive'"

    def test_column_default_added(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "status": make_col("status", "VARCHAR", default=None),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "status": make_col("status", "VARCHAR", default="'active'"),
            }),
        })

        result = differ.diff(old, new)
        default_changes = [c for c in result.changes if c.change_type == "column_default_change"]
        assert len(default_changes) == 1
        assert default_changes[0].old_value is None
        assert default_changes[0].new_value == "'active'"


class TestIndexChanges:
    def test_index_added(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", indexes=[]),
        })
        new = make_snapshot("new", {
            "users": make_table("users", indexes=[
                {"name": "idx_email", "columns": ["email"], "is_unique": True},
            ]),
        })

        result = differ.diff(old, new)
        idx_added = [c for c in result.changes if c.change_type == "index_added"]
        assert len(idx_added) == 1

    def test_index_dropped(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", indexes=[
                {"name": "idx_email", "columns": ["email"], "is_unique": True},
            ]),
        })
        new = make_snapshot("new", {
            "users": make_table("users", indexes=[]),
        })

        result = differ.diff(old, new)
        idx_dropped = [c for c in result.changes if c.change_type == "index_dropped"]
        assert len(idx_dropped) == 1


class TestForeignKeyChanges:
    def test_fk_added(self, differ):
        old = make_snapshot("old", {
            "orders": make_table("orders", foreign_keys=[]),
        })
        new = make_snapshot("new", {
            "orders": make_table("orders", foreign_keys=[
                {"column": "user_id", "references_table": "users", "references_column": "id"},
            ]),
        })

        result = differ.diff(old, new)
        fk_added = [c for c in result.changes if c.change_type == "fk_added"]
        assert len(fk_added) == 1

    def test_fk_dropped(self, differ):
        old = make_snapshot("old", {
            "orders": make_table("orders", foreign_keys=[
                {"column": "user_id", "references_table": "users", "references_column": "id"},
            ]),
        })
        new = make_snapshot("new", {
            "orders": make_table("orders", foreign_keys=[]),
        })

        result = differ.diff(old, new)
        fk_dropped = [c for c in result.changes if c.change_type == "fk_dropped"]
        assert len(fk_dropped) == 1


class TestDiffMetadata:
    def test_diff_result_has_snapshot_ids(self, differ):
        old = make_snapshot("snap_v1", {})
        new = make_snapshot("snap_v2", {})

        result = differ.diff(old, new)
        assert result.old_snapshot_id == "snap_v1"
        assert result.new_snapshot_id == "snap_v2"

    def test_diff_result_has_timestamp(self, differ):
        old = make_snapshot("old", {})
        new = make_snapshot("new", {})

        result = differ.diff(old, new)
        assert result.diffed_at is not None

    def test_multiple_changes_detected(self, differ):
        old = make_snapshot("old", {
            "users": make_table("users", columns={
                "id": make_col("id"),
                "old_col": make_col("old_col"),
            }),
        })
        new = make_snapshot("new", {
            "users": make_table("users", columns={
                "id": make_col("id", "BIGINT"),  # type change
                "new_col": make_col("new_col"),  # added
                # old_col dropped
            }),
        })

        result = differ.diff(old, new)
        types = [c.change_type for c in result.changes]
        assert "column_dropped" in types
        assert "column_added" in types
        assert "column_type_change" in types


# =========================================================================
# Enhanced differ tests (merged from test_enhanced_differ.py)
# =========================================================================


def _make_snapshot(snapshot_id: str, tables: dict[str, TableSnapshot]) -> SchemaSnapshot:
    return SchemaSnapshot(
        snapshot_id=snapshot_id,
        source="ddl",
        tables=tables,
    )


def _make_table(
    name: str,
    columns: dict[str, ColumnSnapshot] | None = None,
    foreign_keys: list[dict] | None = None,
    indexes: list[dict] | None = None,
) -> TableSnapshot:
    return TableSnapshot(
        name=name,
        columns=columns or {},
        foreign_keys=foreign_keys or [],
        indexes=indexes or [],
    )


class TestFKActionChanges:
    """FK action change detection (ON DELETE, ON UPDATE)."""

    def test_on_delete_change_detected(self):
        old = _make_snapshot("old", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": "CASCADE",
                "on_update": None,
            }]),
        })
        new = _make_snapshot("new", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": "RESTRICT",
                "on_update": None,
            }]),
        })

        result = SchemaDiffer().diff(old, new)
        fk_changes = [c for c in result.changes if c.change_type == "fk_action_change"]
        assert len(fk_changes) == 1
        assert "CASCADE" in fk_changes[0].old_value
        assert "RESTRICT" in fk_changes[0].new_value

    def test_on_update_change_detected(self):
        old = _make_snapshot("old", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": None,
                "on_update": "CASCADE",
            }]),
        })
        new = _make_snapshot("new", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": None,
                "on_update": "SET NULL",
            }]),
        })

        result = SchemaDiffer().diff(old, new)
        fk_changes = [c for c in result.changes if c.change_type == "fk_action_change"]
        assert len(fk_changes) == 1
        assert "CASCADE" in fk_changes[0].old_value
        assert "SET NULL" in fk_changes[0].new_value

    def test_no_action_change_no_event(self):
        """Same FK with same actions should produce no changes."""
        fk = {
            "column": "user_id",
            "references_table": "users",
            "references_column": "id",
            "on_delete": "CASCADE",
            "on_update": None,
        }
        old = _make_snapshot("old", {"orders": _make_table("orders", foreign_keys=[fk])})
        new = _make_snapshot("new", {"orders": _make_table("orders", foreign_keys=[fk])})

        result = SchemaDiffer().diff(old, new)
        assert len(result.changes) == 0

    def test_both_actions_changed(self):
        old = _make_snapshot("old", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": "CASCADE",
                "on_update": "CASCADE",
            }]),
        })
        new = _make_snapshot("new", {
            "orders": _make_table("orders", foreign_keys=[{
                "column": "user_id",
                "references_table": "users",
                "references_column": "id",
                "on_delete": "RESTRICT",
                "on_update": "SET NULL",
            }]),
        })

        result = SchemaDiffer().diff(old, new)
        fk_changes = [c for c in result.changes if c.change_type == "fk_action_change"]
        assert len(fk_changes) == 2


class TestConstraintChanges:
    """Column constraint change detection."""

    def test_constraint_added(self):
        old = _make_snapshot("old", {
            "users": _make_table("users", columns={
                "age": ColumnSnapshot(name="age", type="integer", constraints=[]),
            }),
        })
        new = _make_snapshot("new", {
            "users": _make_table("users", columns={
                "age": ColumnSnapshot(name="age", type="integer", constraints=["CHECK(age > 0)"]),
            }),
        })

        result = SchemaDiffer().diff(old, new)
        constraint_changes = [c for c in result.changes if c.change_type == "column_constraint_change"]
        assert len(constraint_changes) == 1
        assert constraint_changes[0].column == "age"
        assert constraint_changes[0].old_value == ""
        assert "CHECK" in constraint_changes[0].new_value

    def test_constraint_removed(self):
        old = _make_snapshot("old", {
            "users": _make_table("users", columns={
                "age": ColumnSnapshot(name="age", type="integer", constraints=["CHECK(age > 0)"]),
            }),
        })
        new = _make_snapshot("new", {
            "users": _make_table("users", columns={
                "age": ColumnSnapshot(name="age", type="integer", constraints=[]),
            }),
        })

        result = SchemaDiffer().diff(old, new)
        constraint_changes = [c for c in result.changes if c.change_type == "column_constraint_change"]
        assert len(constraint_changes) == 1

    def test_same_constraints_no_event(self):
        col = ColumnSnapshot(name="age", type="integer", constraints=["NOT NULL"])
        old = _make_snapshot("old", {"users": _make_table("users", columns={"age": col})})
        new = _make_snapshot("new", {"users": _make_table("users", columns={"age": col.model_copy()})})

        result = SchemaDiffer().diff(old, new)
        assert len(result.changes) == 0


class TestRiskClassification:
    """Every change event should have change_risk populated."""

    def test_table_drop_is_breaking(self):
        old = _make_snapshot("old", {"users": _make_table("users")})
        new = _make_snapshot("new", {})

        result = SchemaDiffer().diff(old, new)
        assert len(result.changes) == 1
        assert result.changes[0].change_risk == "breaking"

    def test_table_add_is_safe(self):
        old = _make_snapshot("old", {})
        new = _make_snapshot("new", {"users": _make_table("users")})

        result = SchemaDiffer().diff(old, new)
        assert len(result.changes) == 1
        assert result.changes[0].change_risk == "safe"

    def test_column_type_widening_is_safe(self):
        old = _make_snapshot("old", {
            "users": _make_table("users", columns={
                "id": ColumnSnapshot(name="id", type="integer"),
            }),
        })
        new = _make_snapshot("new", {
            "users": _make_table("users", columns={
                "id": ColumnSnapshot(name="id", type="bigint"),
            }),
        })

        result = SchemaDiffer().diff(old, new)
        type_changes = [c for c in result.changes if c.change_type == "column_type_change"]
        assert len(type_changes) == 1
        assert type_changes[0].change_risk == "safe"

    def test_column_type_narrowing_is_breaking(self):
        old = _make_snapshot("old", {
            "users": _make_table("users", columns={
                "id": ColumnSnapshot(name="id", type="bigint"),
            }),
        })
        new = _make_snapshot("new", {
            "users": _make_table("users", columns={
                "id": ColumnSnapshot(name="id", type="integer"),
            }),
        })

        result = SchemaDiffer().diff(old, new)
        type_changes = [c for c in result.changes if c.change_type == "column_type_change"]
        assert len(type_changes) == 1
        assert type_changes[0].change_risk == "potentially_breaking"

    def test_all_changes_have_risk(self):
        """Every change event should have change_risk populated (not None)."""
        old = _make_snapshot("old", {
            "users": _make_table("users", columns={
                "name": ColumnSnapshot(name="name", type="varchar(50)"),
                "old_col": ColumnSnapshot(name="old_col", type="text"),
            }),
        })
        new = _make_snapshot("new", {
            "users": _make_table("users", columns={
                "name": ColumnSnapshot(name="name", type="varchar(255)"),
                "new_col": ColumnSnapshot(name="new_col", type="integer"),
            }),
        })

        result = SchemaDiffer().diff(old, new)
        assert len(result.changes) > 0
        for change in result.changes:
            assert change.change_risk is not None, f"change_risk is None for {change.change_type}"
