"""Tests for context assembly data wiring.

Verifies that column statistics, permissions, and functions from the schema
snapshot are correctly plumbed into the ContextPackage during assembly.
"""

from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.models import (
    ColumnSnapshot,
    ColumnStatistics,
    DependencyGraph,
    FunctionSnapshot,
    PermissionSnapshot,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
)


def _make_schema(**kwargs) -> SchemaSnapshot:
    defaults = {"snapshot_id": "test_snap", "source": "ddl", "tables": {}}
    defaults.update(kwargs)
    return SchemaSnapshot(**defaults)


def _make_change(change_type: str, table: str, **kwargs) -> SchemaChangeEvent:
    return SchemaChangeEvent(change_type=change_type, table=table, **kwargs)


def _empty_graph() -> DependencyGraph:
    return DependencyGraph()


# =========================================================================
# Column Statistics Wiring
# =========================================================================


class TestColumnStatsWiring:
    def test_column_stats_populated_for_changed_table(self):
        stats = [
            ColumnStatistics(column_name="id", table_name="users", null_frac=0.0, n_distinct=-1.0),
            ColumnStatistics(
                column_name="email", table_name="users", null_frac=0.05, n_distinct=-0.9
            ),
        ]
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users", columns={"id": ColumnSnapshot(name="id", type="integer")}
                )
            },
            column_statistics={"users": stats},
        )
        change = _make_change("column_added", "users", column="email")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert len(ctx.affected_column_stats) == 2
        assert ctx.affected_column_stats[0].column_name == "id"

    def test_column_stats_empty_for_different_table(self):
        stats = [ColumnStatistics(column_name="id", table_name="orders")]
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            column_statistics={"orders": stats},
        )
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_column_stats == []

    def test_column_stats_empty_when_no_stats(self):
        schema = _make_schema(tables={"users": TableSnapshot(name="users")})
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_column_stats == []


# =========================================================================
# Permission Wiring
# =========================================================================


class TestPermissionWiring:
    def test_permissions_populated_for_table(self):
        perms = [
            PermissionSnapshot(table_name="users", grantee="app_role", privilege_type="SELECT"),
            PermissionSnapshot(table_name="users", grantee="admin_role", privilege_type="ALL"),
            PermissionSnapshot(table_name="orders", grantee="app_role", privilege_type="SELECT"),
        ]
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            permissions=perms,
        )
        change = _make_change("column_added", "users", column="email")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert len(ctx.affected_permissions) == 2
        assert all(p.table_name == "users" for p in ctx.affected_permissions)

    def test_permissions_empty_for_different_table(self):
        perms = [
            PermissionSnapshot(table_name="orders", grantee="app_role", privilege_type="SELECT"),
        ]
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            permissions=perms,
        )
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_permissions == []

    def test_permissions_empty_when_no_permissions(self):
        schema = _make_schema(tables={"users": TableSnapshot(name="users")})
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_permissions == []


# =========================================================================
# Function Wiring
# =========================================================================


class TestFunctionWiring:
    def test_function_referencing_changed_table(self):
        fns = {
            "count_users": FunctionSnapshot(
                name="count_users",
                return_type="integer",
                definition="SELECT count(*) FROM users",
            ),
            "count_orders": FunctionSnapshot(
                name="count_orders",
                return_type="integer",
                definition="SELECT count(*) FROM orders",
            ),
        }
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            functions=fns,
        )
        change = _make_change("column_added", "users", column="email")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert len(ctx.affected_functions) == 1
        assert ctx.affected_functions[0].name == "count_users"

    def test_no_function_references_table(self):
        fns = {
            "compute_tax": FunctionSnapshot(
                name="compute_tax",
                return_type="numeric",
                definition="SELECT amount * 0.1 FROM orders",
            ),
        }
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            functions=fns,
        )
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_functions == []

    def test_function_without_definition(self):
        fns = {
            "mystery_fn": FunctionSnapshot(
                name="mystery_fn",
                return_type="void",
                definition=None,
            ),
        }
        schema = _make_schema(
            tables={"users": TableSnapshot(name="users")},
            functions=fns,
        )
        change = _make_change("table_added", "users")
        ctx = ContextAssembler().assemble(change, _empty_graph(), schema)
        assert ctx.affected_functions == []
