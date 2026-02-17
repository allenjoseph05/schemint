"""Tests for view snapshot capture + diff + dependency wiring."""


from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    SchemaSnapshot,
    TableSnapshot,
    ViewSnapshot,
)
from schemint.drift.snapshot import SnapshotService


class TestViewDDLCapture:
    """Test view extraction from DDL via sqlglot."""

    def test_simple_view(self):
        sql = """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255),
            active BOOLEAN
        );
        CREATE VIEW active_users AS SELECT id, name FROM users WHERE active = true;
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)

        assert "active_users" in snapshot.views
        view = snapshot.views["active_users"]
        assert view.name == "active_users"
        assert "users" in view.source_tables

    def test_view_with_join(self):
        sql = """
        CREATE TABLE orders (id INTEGER, user_id INTEGER, total DECIMAL(10,2));
        CREATE TABLE users (id INTEGER, name VARCHAR(255));
        CREATE VIEW order_summary AS
            SELECT u.name, o.total
            FROM orders o
            JOIN users u ON o.user_id = u.id;
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)

        assert "order_summary" in snapshot.views
        view = snapshot.views["order_summary"]
        assert "orders" in view.source_tables
        assert "users" in view.source_tables

    def test_no_views_in_table_only_ddl(self):
        sql = """
        CREATE TABLE simple (id INTEGER PRIMARY KEY, name VARCHAR(255));
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)
        assert snapshot.views == {}

    def test_multiple_views(self):
        sql = """
        CREATE TABLE products (id INTEGER, name VARCHAR(255), price DECIMAL(10,2));
        CREATE VIEW cheap AS SELECT * FROM products WHERE price < 10;
        CREATE VIEW expensive AS SELECT * FROM products WHERE price > 100;
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)
        assert "cheap" in snapshot.views
        assert "expensive" in snapshot.views


class TestViewDiff:
    """Test view diffing between snapshots."""

    def _make_snapshot(self, sid, views=None, tables=None):
        return SchemaSnapshot(
            snapshot_id=sid,
            source="ddl",
            tables=tables or {},
            views=views or {},
        )

    def test_view_added(self):
        old = self._make_snapshot("old")
        new = self._make_snapshot("new", views={
            "v1": ViewSnapshot(name="v1", definition="SELECT 1"),
        })
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        added = [c for c in result.changes if c.change_type == "view_added"]
        assert len(added) == 1
        assert added[0].table == "v1"

    def test_view_dropped(self):
        old = self._make_snapshot("old", views={
            "v1": ViewSnapshot(name="v1", definition="SELECT 1"),
        })
        new = self._make_snapshot("new")
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        dropped = [c for c in result.changes if c.change_type == "view_dropped"]
        assert len(dropped) == 1
        assert dropped[0].table == "v1"
        assert dropped[0].change_risk == "breaking"

    def test_view_definition_change(self):
        old = self._make_snapshot("old", views={
            "v1": ViewSnapshot(name="v1", definition="SELECT id FROM users"),
        })
        new = self._make_snapshot("new", views={
            "v1": ViewSnapshot(name="v1", definition="SELECT id, name FROM users"),
        })
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        changed = [c for c in result.changes if c.change_type == "view_definition_change"]
        assert len(changed) == 1
        assert changed[0].old_value == "SELECT id FROM users"

    def test_identical_views_no_changes(self):
        views = {"v1": ViewSnapshot(name="v1", definition="SELECT 1")}
        old = self._make_snapshot("old", views=views)
        new = self._make_snapshot("new", views=views)
        differ = SchemaDiffer()
        result = differ.diff(old, new)
        view_changes = [c for c in result.changes if "view" in c.change_type]
        assert len(view_changes) == 0


class TestViewDependencyWiring:
    """Test that views integrate with the dependency graph."""

    def test_from_schema_views(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "users": TableSnapshot(name="users"),
                "orders": TableSnapshot(name="orders"),
            },
            views={
                "user_orders": ViewSnapshot(
                    name="user_orders",
                    definition="SELECT u.id, o.total FROM users u JOIN orders o ON u.id = o.user_id",
                    source_tables=["users", "orders"],
                ),
            },
        )
        builder = DependencyGraphBuilder()
        edges = builder.from_schema_views(schema)
        assert len(edges) >= 1

        # Should have edges from users and orders to user_orders
        from_tables = {e.from_element for e in edges}
        assert "users" in from_tables or "orders" in from_tables

    def test_empty_views_no_edges(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={"t1": TableSnapshot(name="t1")},
        )
        builder = DependencyGraphBuilder()
        edges = builder.from_schema_views(schema)
        assert edges == []
