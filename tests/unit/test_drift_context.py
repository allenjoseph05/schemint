"""Tests for ContextAssembler — context package assembly."""

import pytest

from schemint.drift.context_assembler import ContextAssembler, CriticalityThresholds
from schemint.drift.models import (
    ColumnSnapshot,
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    ParseHealth,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
    TableSnapshot,
)


@pytest.fixture
def schema():
    return SchemaSnapshot(
        snapshot_id="test",
        source="ddl",
        tables={
            "users": TableSnapshot(
                name="users",
                columns={"id": ColumnSnapshot(name="id", type="INT")},
            ),
            "orders": TableSnapshot(
                name="orders",
                columns={
                    "id": ColumnSnapshot(name="id", type="INT"),
                    "user_id": ColumnSnapshot(name="user_id", type="INT"),
                },
            ),
            "order_items": TableSnapshot(
                name="order_items",
                columns={
                    "id": ColumnSnapshot(name="id", type="INT"),
                    "order_id": ColumnSnapshot(name="order_id", type="INT"),
                },
            ),
            "payments": TableSnapshot(
                name="payments",
                columns={
                    "id": ColumnSnapshot(name="id", type="INT"),
                    "order_id": ColumnSnapshot(name="order_id", type="INT"),
                },
            ),
        },
    )


@pytest.fixture
def graph():
    """Graph: users.id → orders.user_id → order_items.order_id, orders.id → payments.order_id"""
    return DependencyGraph(
        edges=[
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="orders.user_id",
                to_element="order_items.order_id",
                usage_type="join_key",
                sources=[DependencySource(source_type="sql_ast", confidence=0.9)],
                final_confidence=0.9,
            ),
            DependencyEdge(
                from_element="orders.id",
                to_element="payments.order_id",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
        ]
    )


@pytest.fixture
def assembler():
    return ContextAssembler(max_depth=10)


class TestContextAssembly:
    def test_assembles_context_for_column_change(self, assembler, graph, schema):
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
            old_value="INT",
            new_value="BIGINT",
        )

        ctx = assembler.assemble(change, graph, schema)

        assert ctx.schema_change == change
        assert ctx.impact_metrics.downstream_tables >= 1
        assert ctx.dependency_coverage.tables_total == 4

    def test_downstream_traversal(self, assembler, graph, schema):
        """users.id change should cascade through orders and further."""
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
        )

        ctx = assembler.assemble(change, graph, schema)
        impacted_tables = {dep.table for dep in ctx.impacted_dependencies}

        assert "orders" in impacted_tables

    def test_no_downstream_for_leaf(self, assembler, graph, schema):
        """A change to a leaf node should have no downstream impact."""
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="payments",
            column="id",
        )

        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.downstream_tables == 0

    def test_table_level_change_expands_columns(self, assembler, graph, schema):
        """Table-level change should seed BFS with all columns in that table."""
        change = SchemaChangeEvent(
            change_type="table_dropped",
            table="orders",
        )

        ctx = assembler.assemble(change, graph, schema)
        impacted_tables = {dep.table for dep in ctx.impacted_dependencies}

        # orders has edges from orders.user_id → order_items and orders.id → payments
        assert "order_items" in impacted_tables or "payments" in impacted_tables

    def test_context_quality_present(self, assembler, graph, schema):
        """ContextPackage must include context_quality."""
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.context_quality in ("complete", "partial", "insufficient")


class TestDependencyAggregation:
    """Dependencies are aggregated per table, not one-per-edge."""

    def test_multiple_edges_to_same_table_aggregated(self, assembler, schema):
        """Two edges targeting the same table should produce one ImpactAssessment."""
        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="users.id",
                    to_element="orders.user_id",
                    usage_type="fk",
                    final_confidence=1.0,
                ),
                DependencyEdge(
                    from_element="users.id",
                    to_element="orders.created_by",
                    usage_type="join_key",
                    final_confidence=0.9,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)

        # Should be aggregated into one entry for "orders"
        orders_impacts = [d for d in ctx.impacted_dependencies if d.table == "orders"]
        assert len(orders_impacts) == 1
        assert orders_impacts[0].dependency_count == 2
        assert orders_impacts[0].confidence == 1.0  # max of (1.0, 0.9)

    def test_aggregated_usages_joined(self, assembler, schema):
        """Aggregated impact should list all usage types."""
        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="users.id",
                    to_element="orders.user_id",
                    usage_type="fk",
                    final_confidence=1.0,
                ),
                DependencyEdge(
                    from_element="users.id",
                    to_element="orders.created_by",
                    usage_type="join_key",
                    final_confidence=0.9,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        orders_impact = next(d for d in ctx.impacted_dependencies if d.table == "orders")
        # Both usage types should be listed (sorted, comma-separated)
        assert "fk" in orders_impact.usage
        assert "join_key" in orders_impact.usage


class TestCriticalityComputation:
    def test_low_criticality(self, assembler, schema):
        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="users.id",
                    to_element="orders.user_id",
                    usage_type="fk",
                    final_confidence=1.0,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.criticality == "low"

    def test_medium_criticality(self, assembler):
        """More than 2 downstream tables → medium."""
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={f"t{i}": TableSnapshot(name=f"t{i}") for i in range(5)},
        )

        edges = [
            DependencyEdge(
                from_element="t0.id",
                to_element=f"t{i}.ref",
                usage_type="fk",
                final_confidence=1.0,
            )
            for i in range(1, 5)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.criticality == "medium"

    def test_high_criticality(self, assembler):
        """More than 5 downstream tables → high."""
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={f"t{i}": TableSnapshot(name=f"t{i}") for i in range(8)},
        )

        edges = [
            DependencyEdge(
                from_element="t0.id",
                to_element=f"t{i}.ref",
                usage_type="fk",
                final_confidence=1.0,
            )
            for i in range(1, 8)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.criticality == "high"

    def test_critical_criticality(self, assembler):
        """More than 10 downstream tables → critical."""
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={f"t{i}": TableSnapshot(name=f"t{i}") for i in range(13)},
        )

        edges = [
            DependencyEdge(
                from_element="t0.id",
                to_element=f"t{i}.ref",
                usage_type="fk",
                final_confidence=1.0,
            )
            for i in range(1, 13)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.criticality == "critical"


class TestBFSDepthLimiting:
    def test_respects_max_depth(self):
        """BFS should stop at max_depth."""
        assembler = ContextAssembler(max_depth=2)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={f"t{i}": TableSnapshot(name=f"t{i}") for i in range(5)},
        )

        # Chain: t0.id → t1.id → t2.id → t3.id → t4.id
        edges = [
            DependencyEdge(
                from_element=f"t{i}.id",
                to_element=f"t{i + 1}.id",
                usage_type="fk",
                final_confidence=1.0,
            )
            for i in range(4)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)

        # With max_depth=2, should only reach t1 and t2 (depths 1 and 2)
        assert ctx.impact_metrics.max_depth <= 2
        assert ctx.impact_metrics.downstream_tables <= 2

    def test_depth_truncation_downgrades_quality(self):
        """When BFS is truncated by max_depth, context_quality should degrade."""
        assembler = ContextAssembler(max_depth=2)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                f"t{i}": TableSnapshot(
                    name=f"t{i}",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                )
                for i in range(5)
            },
        )

        # Chain: t0.id → t1.id → t2.id → t3.id → t4.id
        edges = [
            DependencyEdge(
                from_element=f"t{i}.id",
                to_element=f"t{i + 1}.id",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            )
            for i in range(4)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)

        # Traversal was truncated — more edges exist beyond depth 2
        assert ctx.context_quality == "partial"

    def test_unlimited_depth(self):
        """With high max_depth, traversal should find all downstream."""
        assembler = ContextAssembler(max_depth=100)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={f"t{i}": TableSnapshot(name=f"t{i}") for i in range(5)},
        )

        edges = [
            DependencyEdge(
                from_element=f"t{i}.id",
                to_element=f"t{i + 1}.id",
                usage_type="fk",
                final_confidence=1.0,
            )
            for i in range(4)
        ]
        graph = DependencyGraph(edges=edges)

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.impact_metrics.downstream_tables == 4


class TestContextQuality:
    def test_complete_quality(self):
        """Full coverage, high confidence, no truncation → complete."""
        assembler = ContextAssembler(max_depth=100)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "a": TableSnapshot(
                    name="a",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
                "b": TableSnapshot(
                    name="b",
                    columns={"a_id": ColumnSnapshot(name="a_id", type="integer")},
                ),
            },
        )

        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="a.id",
                    to_element="b.a_id",
                    usage_type="fk",
                    sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                    final_confidence=1.0,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="a",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.context_quality == "complete"

    def test_partial_quality_low_confidence(self):
        """Low confidence edges → partial."""
        assembler = ContextAssembler(max_depth=100)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "a": TableSnapshot(
                    name="a",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
                "b": TableSnapshot(
                    name="b",
                    columns={"a_id": ColumnSnapshot(name="a_id", type="integer")},
                ),
            },
        )

        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="a.id",
                    to_element="b.a_id",
                    usage_type="join_key",
                    sources=[DependencySource(source_type="sql_ast", confidence=0.5)],
                    final_confidence=0.5,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="a",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.context_quality == "partial"

    def test_insufficient_quality_low_coverage(self):
        """Coverage below 50% → insufficient."""
        assembler = ContextAssembler(max_depth=100)

        # 5 tables but only 2 have lineage
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                f"t{i}": TableSnapshot(
                    name=f"t{i}",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                )
                for i in range(5)
            },
        )

        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="t0.id",
                    to_element="t1.id",
                    usage_type="fk",
                    sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                    final_confidence=1.0,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="t0",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        # 2 out of 5 tables = 40% coverage → insufficient
        assert ctx.context_quality == "insufficient"

    def test_insufficient_quality_all_low_confidence(self):
        """All edges below 0.5 confidence → insufficient."""
        assembler = ContextAssembler(max_depth=100)

        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "a": TableSnapshot(
                    name="a",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
                "b": TableSnapshot(
                    name="b",
                    columns={"a_id": ColumnSnapshot(name="a_id", type="integer")},
                ),
            },
        )

        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="a.id",
                    to_element="b.a_id",
                    usage_type="filter",
                    sources=[DependencySource(source_type="sql_ast", confidence=0.4)],
                    final_confidence=0.4,
                ),
            ]
        )

        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="a",
            column="id",
        )
        ctx = assembler.assemble(change, graph, schema)
        assert ctx.context_quality == "insufficient"


class TestAssembleAll:
    def test_produces_one_package_per_change(self, assembler, graph, schema):
        diff = SchemaDiffResult(
            old_snapshot_id="old",
            new_snapshot_id="new",
            changes=[
                SchemaChangeEvent(change_type="column_added", table="users", column="phone"),
                SchemaChangeEvent(change_type="column_dropped", table="orders", column="legacy"),
                SchemaChangeEvent(change_type="table_added", table="logs"),
            ],
        )

        packages = assembler.assemble_all(diff, graph, schema)
        assert len(packages) == 3

    def test_empty_diff_produces_empty_list(self, assembler, graph, schema):
        diff = SchemaDiffResult(
            old_snapshot_id="old",
            new_snapshot_id="new",
            changes=[],
        )
        packages = assembler.assemble_all(diff, graph, schema)
        assert packages == []


# =========================================================================
# Enhanced context assembler tests (merged from test_enhanced_context_assembler.py)
# =========================================================================


def _make_schema(*table_names: str) -> SchemaSnapshot:
    tables = {}
    for name in table_names:
        tables[name] = TableSnapshot(
            name=name,
            columns={f"{name}_id": ColumnSnapshot(name=f"{name}_id", type="integer")},
        )
    return SchemaSnapshot(snapshot_id="test", source="ddl", tables=tables)


def _make_edge(from_el: str, to_el: str, confidence: float = 0.9) -> DependencyEdge:
    return DependencyEdge(
        from_element=from_el,
        to_element=to_el,
        usage_type="join_key",
        sources=[DependencySource(source_type="sql_ast", confidence=confidence)],
        final_confidence=confidence,
    )


class TestCriticalityThresholds:
    """Configurable criticality thresholds."""

    def test_default_thresholds(self):
        t = CriticalityThresholds()
        assert t.compute(0) == "low"
        assert t.compute(2) == "low"
        assert t.compute(3) == "medium"
        assert t.compute(6) == "high"
        assert t.compute(11) == "critical"

    def test_custom_thresholds(self):
        t = CriticalityThresholds(critical=20, high=10, medium=5)
        assert t.compute(3) == "low"
        assert t.compute(6) == "medium"
        assert t.compute(11) == "high"
        assert t.compute(21) == "critical"

    def test_strict_thresholds(self):
        t = CriticalityThresholds(critical=3, high=2, medium=1)
        assert t.compute(0) == "low"
        assert t.compute(2) == "medium"
        assert t.compute(3) == "high"
        assert t.compute(4) == "critical"


class TestConfigurableAssembler:
    """Assembler uses configurable thresholds."""

    def test_custom_thresholds_affect_criticality(self):
        schema = _make_schema("users", "orders", "items", "payments")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        # Create edges so users has 3 downstream tables
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "orders.users_id"),
                _make_edge("users.users_id", "items.users_id"),
                _make_edge("users.users_id", "payments.users_id"),
            ]
        )

        # Default thresholds: 3 downstream > 2 = "medium"
        assembler_default = ContextAssembler()
        pkg_default = assembler_default.assemble(change, graph, schema)
        assert pkg_default.impact_metrics.criticality == "medium"

        # Strict thresholds: 3 downstream > 1 = "high" (since > high=2 → "high")
        assembler_strict = ContextAssembler(
            criticality_thresholds=CriticalityThresholds(critical=5, high=2, medium=1)
        )
        pkg_strict = assembler_strict.assemble(change, graph, schema)
        assert pkg_strict.impact_metrics.criticality == "high"


class TestContextGaps:
    """Context gaps reporting."""

    def test_no_gaps_when_complete(self):
        schema = _make_schema("users", "orders")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "orders.users_id"),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        assert pkg.context_gaps is not None
        # Both tables are in schema and have edges, so no untracked
        assert len(pkg.context_gaps.missing_upstream_tables) == 0
        assert len(pkg.context_gaps.missing_downstream_tables) == 0

    def test_gaps_when_edge_references_missing_table(self):
        """Tables in edges but not in schema should appear as gaps."""
        schema = _make_schema("users")  # only users, missing "external_service"
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "external_service.users_id"),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        assert pkg.context_gaps is not None
        assert pkg.context_gaps.has_gaps

    def test_low_confidence_edges_counted(self):
        schema = _make_schema("users", "orders")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "orders.users_id", confidence=0.4),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        assert pkg.context_gaps is not None
        assert pkg.context_gaps.low_confidence_edges > 0
        assert pkg.context_gaps.has_gaps

    def test_parse_health_included(self):
        schema = _make_schema("users")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        graph = DependencyGraph(edges=[])

        health = ParseHealth(total_files=5, parsed_ok=3, parse_failures=["bad1.sql", "bad2.sql"])

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema, parse_health=health)

        assert pkg.context_gaps is not None
        assert pkg.context_gaps.parse_health.total_files == 5
        assert pkg.context_gaps.parse_health.parsed_ok == 3
        assert len(pkg.context_gaps.parse_health.parse_failures) == 2
        assert pkg.context_gaps.has_gaps


class TestUpstreamAnalysis:
    """Upstream impact traversal."""

    def test_upstream_impacts_populated(self):
        schema = _make_schema("users", "orders", "payments")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="orders",
            column="orders_id",
        )
        # payments depends on orders, orders depends on users
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "orders.orders_id"),
                _make_edge("orders.orders_id", "payments.orders_id"),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        # orders should have users as upstream
        upstream_tables = {imp.table for imp in pkg.upstream_impacts}
        assert "users" in upstream_tables

    def test_downstream_impacts_still_work(self):
        schema = _make_schema("users", "orders", "payments")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="orders",
            column="orders_id",
        )
        graph = DependencyGraph(
            edges=[
                _make_edge("orders.orders_id", "payments.orders_id"),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        downstream_tables = {imp.table for imp in pkg.impacted_dependencies}
        assert "payments" in downstream_tables

    def test_no_upstream_when_root_table(self):
        schema = _make_schema("users", "orders")
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="users_id",
        )
        graph = DependencyGraph(
            edges=[
                _make_edge("users.users_id", "orders.users_id"),
            ]
        )

        assembler = ContextAssembler()
        pkg = assembler.assemble(change, graph, schema)

        assert len(pkg.upstream_impacts) == 0
