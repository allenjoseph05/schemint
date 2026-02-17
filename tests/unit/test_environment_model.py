"""Tests for environment model — Task 1.

Verifies that the environment field propagates correctly through
SchemaSnapshot, ContextPackage, snapshot capture, and store operations.
"""

from __future__ import annotations

from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.models import (
    ColumnSnapshot,
    ContextPackage,
    DependencyGraph,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.drift.snapshot import SnapshotService

# =============================================================================
# SchemaSnapshot environment field
# =============================================================================


class TestSchemaSnapshotEnvironment:
    """Tests for SchemaSnapshot.environment field."""

    def test_default_environment(self):
        snap = SchemaSnapshot(snapshot_id="test_1", source="ddl")
        assert snap.environment == "default"

    def test_custom_environment(self):
        snap = SchemaSnapshot(
            snapshot_id="test_1", source="ddl", environment="production"
        )
        assert snap.environment == "production"

    def test_environment_serializes(self):
        snap = SchemaSnapshot(
            snapshot_id="test_1", source="ddl", environment="staging"
        )
        data = snap.model_dump()
        assert data["environment"] == "staging"

    def test_environment_round_trip(self):
        snap = SchemaSnapshot(
            snapshot_id="test_1", source="ddl", environment="dev"
        )
        restored = SchemaSnapshot(**snap.model_dump())
        assert restored.environment == "dev"

    def test_backward_compatibility_no_environment(self):
        """Existing code that doesn't pass environment should work."""
        data = {
            "snapshot_id": "test_1",
            "source": "ddl",
            "database_type": "postgresql",
        }
        snap = SchemaSnapshot(**data)
        assert snap.environment == "default"


# =============================================================================
# ContextPackage environment field
# =============================================================================


class TestContextPackageEnvironment:
    """Tests for ContextPackage.environment field."""

    def test_default_environment(self):
        change = SchemaChangeEvent(change_type="table_added", table="users")
        ctx = ContextPackage(schema_change=change)
        assert ctx.environment == "default"

    def test_custom_environment(self):
        change = SchemaChangeEvent(change_type="table_added", table="users")
        ctx = ContextPackage(schema_change=change, environment="prod")
        assert ctx.environment == "prod"

    def test_environment_serializes(self):
        change = SchemaChangeEvent(change_type="table_added", table="users")
        ctx = ContextPackage(schema_change=change, environment="staging")
        data = ctx.model_dump()
        assert data["environment"] == "staging"


# =============================================================================
# SnapshotService environment propagation
# =============================================================================


class TestSnapshotServiceEnvironment:
    """Tests for environment propagation through SnapshotService."""

    def test_capture_from_ddl_default_environment(self):
        service = SnapshotService()
        snap = service.capture_from_ddl("CREATE TABLE users (id INT);")
        assert snap.environment == "default"

    def test_capture_from_ddl_custom_environment(self):
        service = SnapshotService()
        snap = service.capture_from_ddl(
            "CREATE TABLE users (id INT);", environment="production"
        )
        assert snap.environment == "production"

    def test_capture_from_ddl_staging(self):
        service = SnapshotService()
        snap = service.capture_from_ddl(
            "CREATE TABLE orders (id INT);", environment="staging"
        )
        assert snap.environment == "staging"
        assert "orders" in snap.tables


# =============================================================================
# Context assembler environment wiring
# =============================================================================


class TestContextAssemblerEnvironment:
    """Tests that ContextAssembler wires schema.environment to ContextPackage."""

    def _make_schema(self, environment: str = "default") -> SchemaSnapshot:
        return SchemaSnapshot(
            snapshot_id="test_snap",
            source="ddl",
            environment=environment,
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )

    def test_assembler_propagates_default_environment(self):
        schema = self._make_schema("default")
        change = SchemaChangeEvent(change_type="table_added", table="users")
        graph = DependencyGraph()
        assembler = ContextAssembler()
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.environment == "default"

    def test_assembler_propagates_custom_environment(self):
        schema = self._make_schema("production")
        change = SchemaChangeEvent(change_type="table_added", table="users")
        graph = DependencyGraph()
        assembler = ContextAssembler()
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.environment == "production"

    def test_assembler_propagates_staging_environment(self):
        schema = self._make_schema("staging")
        change = SchemaChangeEvent(
            change_type="column_added", table="users", column="id"
        )
        graph = DependencyGraph()
        assembler = ContextAssembler()
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.environment == "staging"
