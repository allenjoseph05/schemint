"""Tests for multi-schema composition — cross-schema snapshots and diffing."""

from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ColumnSnapshot,
    MultiSchemaSnapshot,
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
    ViewSnapshot,
)
from schemint.drift.snapshot import SnapshotService


class TestMultiSchemaSnapshot:
    """Test MultiSchemaSnapshot model and composition."""

    def test_create_multi_schema(self):
        public = SchemaSnapshot(
            snapshot_id="public_snap",
            source="ddl",
            schema_name="public",
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        analytics = SchemaSnapshot(
            snapshot_id="analytics_snap",
            source="ddl",
            schema_name="analytics",
            tables={
                "events": TableSnapshot(
                    name="events",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        multi = MultiSchemaSnapshot(
            snapshot_id="multi_test",
            source="composed",
            schemas={"public": public, "analytics": analytics},
        )
        assert len(multi.schemas) == 2
        assert "public" in multi.schemas
        assert "analytics" in multi.schemas


class TestFlattenMultiSchema:
    """Test flattening multi-schema to single schema with qualified names."""

    def test_flatten_tables(self):
        public = SchemaSnapshot(
            snapshot_id="pub",
            source="ddl",
            schema_name="public",
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        analytics = SchemaSnapshot(
            snapshot_id="ana",
            source="ddl",
            schema_name="analytics",
            tables={
                "events": TableSnapshot(
                    name="events",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        multi = MultiSchemaSnapshot(
            snapshot_id="multi",
            source="composed",
            schemas={"public": public, "analytics": analytics},
        )

        service = SnapshotService()
        flat = service.flatten_multi_schema(multi)

        assert "public.users" in flat.tables
        assert "analytics.events" in flat.tables
        assert flat.schema_name == "__multi__"
        assert flat.source == "composed"

    def test_flatten_fk_references_qualified(self):
        public = SchemaSnapshot(
            snapshot_id="pub",
            source="ddl",
            schema_name="public",
            tables={
                "users": TableSnapshot(name="users"),
                "orders": TableSnapshot(
                    name="orders",
                    foreign_keys=[
                        {
                            "name": "fk_user",
                            "column": "user_id",
                            "references_table": "users",
                            "references_column": "id",
                        }
                    ],
                ),
            },
        )
        multi = MultiSchemaSnapshot(
            snapshot_id="multi",
            source="composed",
            schemas={"public": public},
        )

        service = SnapshotService()
        flat = service.flatten_multi_schema(multi)

        orders_fks = flat.tables["public.orders"].foreign_keys
        assert len(orders_fks) == 1
        assert orders_fks[0].references_table == "public.users"

    def test_flatten_views(self):
        public = SchemaSnapshot(
            snapshot_id="pub",
            source="ddl",
            schema_name="public",
            tables={"users": TableSnapshot(name="users")},
            views={
                "active_users": ViewSnapshot(
                    name="active_users",
                    definition="SELECT * FROM users WHERE active",
                    source_tables=["users"],
                ),
            },
        )
        multi = MultiSchemaSnapshot(
            snapshot_id="multi",
            source="composed",
            schemas={"public": public},
        )

        service = SnapshotService()
        flat = service.flatten_multi_schema(multi)

        assert "public.active_users" in flat.views
        view = flat.views["public.active_users"]
        assert "public.users" in view.source_tables

    def test_flatten_triggers(self):
        public = SchemaSnapshot(
            snapshot_id="pub",
            source="ddl",
            schema_name="public",
            tables={"orders": TableSnapshot(name="orders")},
            triggers={
                "trg_audit": TriggerSnapshot(
                    name="trg_audit",
                    table="orders",
                    event="INSERT",
                    timing="AFTER",
                    function_name="audit_fn",
                ),
            },
        )
        multi = MultiSchemaSnapshot(
            snapshot_id="multi",
            source="composed",
            schemas={"public": public},
        )

        service = SnapshotService()
        flat = service.flatten_multi_schema(multi)

        assert "public.trg_audit" in flat.triggers
        assert flat.triggers["public.trg_audit"].table == "public.orders"


class TestDiffMulti:
    """Test cross-schema diffing via diff_multi."""

    def test_diff_multi_detects_table_add(self):
        old = MultiSchemaSnapshot(
            snapshot_id="old",
            source="composed",
            schemas={
                "public": SchemaSnapshot(
                    snapshot_id="pub_old",
                    source="ddl",
                    schema_name="public",
                    tables={"users": TableSnapshot(name="users")},
                ),
            },
        )
        new = MultiSchemaSnapshot(
            snapshot_id="new",
            source="composed",
            schemas={
                "public": SchemaSnapshot(
                    snapshot_id="pub_new",
                    source="ddl",
                    schema_name="public",
                    tables={
                        "users": TableSnapshot(name="users"),
                        "orders": TableSnapshot(name="orders"),
                    },
                ),
            },
        )
        differ = SchemaDiffer()
        result = differ.diff_multi(old, new)
        added = [c for c in result.changes if c.change_type == "table_added"]
        assert len(added) == 1
        assert added[0].table == "public.orders"

    def test_diff_multi_cross_schema_change(self):
        old = MultiSchemaSnapshot(
            snapshot_id="old",
            source="composed",
            schemas={
                "public": SchemaSnapshot(
                    snapshot_id="pub_old",
                    source="ddl",
                    schema_name="public",
                    tables={"users": TableSnapshot(name="users")},
                ),
            },
        )
        new = MultiSchemaSnapshot(
            snapshot_id="new",
            source="composed",
            schemas={
                "public": SchemaSnapshot(
                    snapshot_id="pub_new",
                    source="ddl",
                    schema_name="public",
                    tables={"users": TableSnapshot(name="users")},
                ),
                "analytics": SchemaSnapshot(
                    snapshot_id="ana_new",
                    source="ddl",
                    schema_name="analytics",
                    tables={"events": TableSnapshot(name="events")},
                ),
            },
        )
        differ = SchemaDiffer()
        result = differ.diff_multi(old, new)
        added = [c for c in result.changes if c.change_type == "table_added"]
        assert any(c.table == "analytics.events" for c in added)
