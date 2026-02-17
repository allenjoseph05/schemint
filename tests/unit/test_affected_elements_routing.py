"""Tests for _get_affected_elements() routing logic.

Verifies that each change type family (sequence, enum, function, matview,
extension, permission, policy, partition) produces the correct BFS seed
elements for downstream/upstream traversal.
"""

from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.models import (
    ColumnSnapshot,
    MaterializedViewSnapshot,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
    ViewSnapshot,
)


def _make_schema(**kwargs) -> SchemaSnapshot:
    defaults = {"snapshot_id": "test_snap", "source": "ddl", "tables": {}}
    defaults.update(kwargs)
    return SchemaSnapshot(**defaults)


def _make_change(change_type: str, table: str, **kwargs) -> SchemaChangeEvent:
    return SchemaChangeEvent(change_type=change_type, table=table, **kwargs)


class TestAffectedElementsRouting:
    def setup_method(self):
        self.assembler = ContextAssembler()

    def test_sequence_change_finds_tables_with_nextval(self):
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={
                        "id": ColumnSnapshot(
                            name="id", type="integer", default="nextval('users_id_seq')"
                        ),
                        "name": ColumnSnapshot(name="name", type="text"),
                    },
                ),
                "orders": TableSnapshot(name="orders"),
            },
        )
        change = _make_change("sequence_changed", "users_id_seq")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "users_id_seq" in elements
        assert "users" in elements
        assert "users.id" in elements
        assert "users.name" not in elements

    def test_enum_change_finds_columns_using_type(self):
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={
                        "id": ColumnSnapshot(name="id", type="integer"),
                        "status": ColumnSnapshot(name="status", type="user_status"),
                    },
                ),
            },
        )
        change = _make_change("enum_value_removed", "user_status")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "user_status" in elements
        assert "users" in elements
        assert "users.status" in elements
        assert "users.id" not in elements

    def test_function_change_finds_triggers(self):
        schema = _make_schema(
            triggers={
                "update_ts": TriggerSnapshot(
                    name="update_ts",
                    table="users",
                    event="UPDATE",
                    timing="BEFORE",
                    function_name="set_timestamp",
                ),
            },
        )
        change = _make_change("function_changed", "set_timestamp")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "set_timestamp" in elements
        assert "users" in elements

    def test_function_change_finds_views(self):
        schema = _make_schema(
            views={
                "user_summary": ViewSnapshot(
                    name="user_summary",
                    definition="SELECT count_active_users() as cnt",
                    source_tables=["users"],
                ),
            },
        )
        change = _make_change("function_changed", "count_active_users")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "count_active_users" in elements
        assert "user_summary" in elements

    def test_matview_change_seeds_source_tables(self):
        schema = _make_schema(
            materialized_views={
                "active_users": MaterializedViewSnapshot(
                    name="active_users",
                    definition="SELECT * FROM users WHERE active",
                    source_tables=["users"],
                ),
            },
        )
        change = _make_change("matview_dropped", "active_users")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "active_users" in elements
        assert "users" in elements

    def test_extension_change_seeds_table(self):
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                )
            },
        )
        change = _make_change("extension_dropped", "users")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "users" in elements
        assert "users.id" in elements

    def test_permission_change_seeds_table(self):
        schema = _make_schema(
            tables={
                "orders": TableSnapshot(
                    name="orders",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                )
            },
        )
        change = _make_change("permission_revoked", "orders")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "orders" in elements
        assert "orders.id" in elements

    def test_policy_change_seeds_table(self):
        schema = _make_schema(
            tables={"secrets": TableSnapshot(name="secrets")},
        )
        change = _make_change("policy_added", "secrets")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "secrets" in elements

    def test_sequence_change_no_matching_tables(self):
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        change = _make_change("sequence_dropped", "orphan_seq")
        elements = self.assembler._get_affected_elements(change, schema)
        assert elements == ["orphan_seq"]

    def test_enum_change_no_matching_columns(self):
        schema = _make_schema(
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
        )
        change = _make_change("enum_dropped", "unused_enum")
        elements = self.assembler._get_affected_elements(change, schema)
        assert elements == ["unused_enum"]

    def test_partition_change_seeds_table(self):
        schema = _make_schema(
            tables={
                "events": TableSnapshot(
                    name="events",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                )
            },
        )
        change = _make_change("partition_added", "events")
        elements = self.assembler._get_affected_elements(change, schema)
        assert "events" in elements
        assert "events.id" in elements
