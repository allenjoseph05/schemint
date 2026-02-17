"""Tests for desired state model and migration gap — Task 2.

Verifies MigrationGap model, diff_against_desired(), desired_state source,
and is_desired_state flag.
"""

from __future__ import annotations

from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ColumnSnapshot,
    MigrationGap,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.drift.snapshot import SnapshotService

# =============================================================================
# SchemaSnapshot desired state fields
# =============================================================================


class TestDesiredStateFields:
    """Tests for desired_state source and is_desired_state flag."""

    def test_desired_state_source(self):
        snap = SchemaSnapshot(
            snapshot_id="desired_1",
            source="desired_state",
            is_desired_state=True,
        )
        assert snap.source == "desired_state"
        assert snap.is_desired_state is True

    def test_default_is_not_desired_state(self):
        snap = SchemaSnapshot(snapshot_id="test_1", source="ddl")
        assert snap.is_desired_state is False

    def test_desired_state_serializes(self):
        snap = SchemaSnapshot(
            snapshot_id="desired_1",
            source="desired_state",
            is_desired_state=True,
        )
        data = snap.model_dump()
        assert data["source"] == "desired_state"
        assert data["is_desired_state"] is True

    def test_desired_state_round_trip(self):
        snap = SchemaSnapshot(
            snapshot_id="desired_1",
            source="desired_state",
            is_desired_state=True,
            environment="production",
        )
        restored = SchemaSnapshot(**snap.model_dump())
        assert restored.source == "desired_state"
        assert restored.is_desired_state is True
        assert restored.environment == "production"


# =============================================================================
# MigrationGap model
# =============================================================================


class TestMigrationGap:
    """Tests for MigrationGap model."""

    def test_migration_gap_creation(self):
        change = SchemaChangeEvent(change_type="column_added", table="users", column="phone")
        gap = MigrationGap(
            current_snapshot_id="snap_current",
            desired_snapshot_id="snap_desired",
            environment="staging",
            changes=[change],
        )
        assert gap.current_snapshot_id == "snap_current"
        assert gap.desired_snapshot_id == "snap_desired"
        assert gap.environment == "staging"
        assert len(gap.changes) == 1
        assert gap.detected_at is not None

    def test_migration_gap_default_environment(self):
        gap = MigrationGap(
            current_snapshot_id="a",
            desired_snapshot_id="b",
        )
        assert gap.environment == "default"
        assert gap.changes == []

    def test_migration_gap_serializes(self):
        change = SchemaChangeEvent(change_type="table_added", table="orders")
        gap = MigrationGap(
            current_snapshot_id="snap_1",
            desired_snapshot_id="snap_2",
            changes=[change],
        )
        data = gap.model_dump()
        assert data["current_snapshot_id"] == "snap_1"
        assert len(data["changes"]) == 1


class TestDiffAgainstDesired:
    """Tests for diff_against_desired() method."""

    def _make_snapshot(
        self, snapshot_id: str, tables: dict[str, TableSnapshot] | None = None
    ) -> SchemaSnapshot:
        return SchemaSnapshot(
            snapshot_id=snapshot_id,
            source="ddl",
            tables=tables or {},
        )

    def test_no_diff_identical_snapshots(self):
        table = TableSnapshot(
            name="users",
            columns={"id": ColumnSnapshot(name="id", type="integer")},
        )
        current = self._make_snapshot("current", {"users": table})
        desired = self._make_snapshot("desired", {"users": table})

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired)

        assert isinstance(gap, MigrationGap)
        assert gap.current_snapshot_id == "current"
        assert gap.desired_snapshot_id == "desired"
        assert len(gap.changes) == 0

    def test_diff_detects_missing_table(self):
        """Current has no table, desired has one — should produce table_added."""
        desired_table = TableSnapshot(
            name="orders",
            columns={"id": ColumnSnapshot(name="id", type="integer")},
        )
        current = self._make_snapshot("current", {})
        desired = self._make_snapshot("desired", {"orders": desired_table})

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired, environment="staging")

        assert gap.environment == "staging"
        assert len(gap.changes) == 1
        assert gap.changes[0].change_type == "table_added"
        assert gap.changes[0].table == "orders"

    def test_diff_detects_extra_table(self):
        """Current has a table that desired doesn't — table_dropped."""
        current_table = TableSnapshot(
            name="legacy",
            columns={"id": ColumnSnapshot(name="id", type="integer")},
        )
        current = self._make_snapshot("current", {"legacy": current_table})
        desired = self._make_snapshot("desired", {})

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired)

        assert len(gap.changes) == 1
        assert gap.changes[0].change_type == "table_dropped"

    def test_diff_detects_column_changes(self):
        """Column added in desired state."""
        current_table = TableSnapshot(
            name="users",
            columns={"id": ColumnSnapshot(name="id", type="integer")},
        )
        desired_table = TableSnapshot(
            name="users",
            columns={
                "id": ColumnSnapshot(name="id", type="integer"),
                "email": ColumnSnapshot(name="email", type="varchar(255)"),
            },
        )
        current = self._make_snapshot("current", {"users": current_table})
        desired = self._make_snapshot("desired", {"users": desired_table})

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired)

        assert len(gap.changes) == 1
        assert gap.changes[0].change_type == "column_added"
        assert gap.changes[0].column == "email"

    def test_diff_multiple_changes(self):
        """Multiple gaps detected between current and desired."""
        current_table = TableSnapshot(
            name="users",
            columns={
                "id": ColumnSnapshot(name="id", type="integer"),
                "name": ColumnSnapshot(name="name", type="varchar(100)"),
            },
        )
        desired_table = TableSnapshot(
            name="users",
            columns={
                "id": ColumnSnapshot(name="id", type="bigint"),
                "name": ColumnSnapshot(name="name", type="varchar(100)"),
                "email": ColumnSnapshot(name="email", type="text"),
            },
        )
        current = self._make_snapshot("current", {"users": current_table})
        desired = self._make_snapshot("desired", {"users": desired_table})

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired)

        types = {c.change_type for c in gap.changes}
        assert "column_type_change" in types
        assert "column_added" in types

    def test_diff_against_desired_with_ddl(self):
        """Integration test: capture DDL and diff against desired state."""
        service = SnapshotService()
        current = service.capture_from_ddl("CREATE TABLE users (id INT);")
        desired = service.capture_from_ddl("CREATE TABLE users (id INT, email VARCHAR(255));")
        desired.source = "desired_state"
        desired.is_desired_state = True

        differ = SchemaDiffer()
        gap = differ.diff_against_desired(current, desired, environment="prod")

        assert gap.environment == "prod"
        assert any(c.change_type == "column_added" for c in gap.changes)
