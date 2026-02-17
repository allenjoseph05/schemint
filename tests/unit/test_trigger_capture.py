"""Tests for trigger snapshot capture + diff + dependency wiring."""


from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
)


class TestTriggerDiff:
    """Test trigger diffing between snapshots."""

    def _make_snapshot(self, sid, triggers=None, tables=None):
        return SchemaSnapshot(
            snapshot_id=sid,
            source="ddl",
            tables=tables or {},
            triggers=triggers or {},
        )

    def test_trigger_added(self):
        old = self._make_snapshot("old")
        new = self._make_snapshot("new", triggers={
            "trg_audit": TriggerSnapshot(
                name="trg_audit",
                table="orders",
                event="INSERT",
                timing="AFTER",
                function_name="audit_log",
            ),
        })
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        added = [c for c in result.changes if c.change_type == "trigger_added"]
        assert len(added) == 1
        assert added[0].table == "orders"
        assert added[0].new_value == "trg_audit"

    def test_trigger_dropped(self):
        old = self._make_snapshot("old", triggers={
            "trg_audit": TriggerSnapshot(
                name="trg_audit",
                table="orders",
                event="INSERT",
                timing="AFTER",
                function_name="audit_log",
            ),
        })
        new = self._make_snapshot("new")
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        dropped = [c for c in result.changes if c.change_type == "trigger_dropped"]
        assert len(dropped) == 1
        assert dropped[0].old_value == "trg_audit"

    def test_trigger_changed(self):
        old = self._make_snapshot("old", triggers={
            "trg_audit": TriggerSnapshot(
                name="trg_audit",
                table="orders",
                event="INSERT",
                timing="AFTER",
                function_name="audit_insert",
            ),
        })
        new = self._make_snapshot("new", triggers={
            "trg_audit": TriggerSnapshot(
                name="trg_audit",
                table="orders",
                event="INSERT",
                timing="BEFORE",
                function_name="audit_insert",
            ),
        })
        differ = SchemaDiffer()
        result = differ.diff(old, new)

        changed = [c for c in result.changes if c.change_type == "trigger_changed"]
        assert len(changed) == 1

    def test_identical_triggers_no_changes(self):
        triggers = {
            "trg1": TriggerSnapshot(
                name="trg1", table="t1", event="INSERT",
                timing="AFTER", function_name="fn1",
            ),
        }
        old = self._make_snapshot("old", triggers=triggers)
        new = self._make_snapshot("new", triggers=triggers)
        differ = SchemaDiffer()
        result = differ.diff(old, new)
        trig_changes = [c for c in result.changes if "trigger" in c.change_type]
        assert len(trig_changes) == 0

    def test_trigger_function_change(self):
        old = self._make_snapshot("old", triggers={
            "trg1": TriggerSnapshot(
                name="trg1", table="t1", event="UPDATE",
                timing="AFTER", function_name="old_fn",
            ),
        })
        new = self._make_snapshot("new", triggers={
            "trg1": TriggerSnapshot(
                name="trg1", table="t1", event="UPDATE",
                timing="AFTER", function_name="new_fn",
            ),
        })
        differ = SchemaDiffer()
        result = differ.diff(old, new)
        changed = [c for c in result.changes if c.change_type == "trigger_changed"]
        assert len(changed) == 1


class TestTriggerDependencyWiring:
    """Test that triggers integrate with the dependency graph."""

    def test_trigger_with_table_reference_in_body(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "orders": TableSnapshot(name="orders"),
                "audit_log": TableSnapshot(name="audit_log"),
            },
            triggers={
                "trg_audit": TriggerSnapshot(
                    name="trg_audit",
                    table="orders",
                    event="INSERT",
                    timing="AFTER",
                    function_name="audit_fn",
                    definition="INSERT INTO audit_log (table_name) VALUES ('orders')",
                ),
            },
        )
        builder = DependencyGraphBuilder()
        edges = builder.from_trigger_definitions(schema)
        # Should find edge from orders to audit_log
        assert len(edges) >= 1
        edge_pairs = [(e.from_element, e.to_element) for e in edges]
        assert ("orders", "audit_log") in edge_pairs

    def test_trigger_no_body_no_edges(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={"t1": TableSnapshot(name="t1")},
            triggers={
                "trg1": TriggerSnapshot(
                    name="trg1", table="t1", event="INSERT",
                    timing="AFTER", function_name="fn1",
                    definition=None,
                ),
            },
        )
        builder = DependencyGraphBuilder()
        edges = builder.from_trigger_definitions(schema)
        assert edges == []

    def test_empty_triggers_no_edges(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={"t1": TableSnapshot(name="t1")},
        )
        builder = DependencyGraphBuilder()
        edges = builder.from_trigger_definitions(schema)
        assert edges == []
