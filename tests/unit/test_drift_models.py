"""Tests for drift Pydantic models."""

import pytest

from schemint.drift.models import (
    ColumnSnapshot,
    ContextPackage,
    DependencyCoverage,
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    ImpactAssessment,
    ImpactMetrics,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
    TableSnapshot,
)


class TestSnapshotModels:
    def test_column_snapshot_defaults(self):
        col = ColumnSnapshot(name="id", type="INT")
        assert col.nullable is True
        assert col.default is None
        assert col.constraints == []

    def test_column_snapshot_full(self):
        col = ColumnSnapshot(
            name="email",
            type="VARCHAR(255)",
            nullable=False,
            default="''",
            constraints=["NOT NULL", "UNIQUE"],
        )
        assert col.name == "email"
        assert col.nullable is False
        assert len(col.constraints) == 2

    def test_table_snapshot(self):
        table = TableSnapshot(
            name="users",
            columns={
                "id": ColumnSnapshot(name="id", type="INT"),
                "email": ColumnSnapshot(name="email", type="VARCHAR(255)"),
            },
            primary_key=["id"],
        )
        assert table.name == "users"
        assert len(table.columns) == 2
        assert table.primary_key == ["id"]

    def test_schema_snapshot(self):
        snapshot = SchemaSnapshot(
            snapshot_id="test_001",
            source="ddl",
            database_type="postgresql",
            tables={
                "users": TableSnapshot(name="users"),
            },
        )
        assert snapshot.snapshot_id == "test_001"
        assert snapshot.source == "ddl"
        assert "users" in snapshot.tables

    def test_schema_snapshot_serialization(self):
        snapshot = SchemaSnapshot(
            snapshot_id="test_002",
            source="live_db",
            database_type="postgresql",
            tables={
                "orders": TableSnapshot(
                    name="orders",
                    columns={
                        "id": ColumnSnapshot(name="id", type="INT"),
                    },
                ),
            },
        )
        data = snapshot.model_dump()
        restored = SchemaSnapshot(**data)
        assert restored.snapshot_id == "test_002"
        assert "orders" in restored.tables
        assert "id" in restored.tables["orders"].columns


class TestDependencyModels:
    def test_dependency_source_confidence_bounds(self):
        source = DependencySource(
            source_type="fk_constraint",
            confidence=1.0,
        )
        assert source.confidence == 1.0

        source = DependencySource(
            source_type="sql_ast",
            confidence=0.0,
        )
        assert source.confidence == 0.0

    def test_dependency_source_invalid_confidence(self):
        with pytest.raises(Exception, match="confidence"):
            DependencySource(
                source_type="fk_constraint",
                confidence=1.5,
            )

        with pytest.raises(Exception, match="confidence"):
            DependencySource(
                source_type="fk_constraint",
                confidence=-0.1,
            )

    def test_dependency_edge(self):
        edge = DependencyEdge(
            from_element="users.id",
            to_element="orders.user_id",
            usage_type="fk",
            sources=[
                DependencySource(source_type="fk_constraint", confidence=1.0),
            ],
            final_confidence=1.0,
        )
        assert edge.from_element == "users.id"
        assert edge.to_element == "orders.user_id"
        assert edge.usage_type == "fk"
        assert edge.final_confidence == 1.0

    def test_dependency_graph(self):
        graph = DependencyGraph(
            edges=[
                DependencyEdge(
                    from_element="a.id",
                    to_element="b.a_id",
                    usage_type="fk",
                    final_confidence=1.0,
                ),
            ]
        )
        assert len(graph.edges) == 1

    def test_dependency_coverage(self):
        cov = DependencyCoverage(
            tables_total=10,
            tables_with_lineage=7,
            coverage_pct=70.0,
            untracked_tables=["orphan1", "orphan2", "orphan3"],
        )
        assert cov.coverage_pct == 70.0
        assert len(cov.untracked_tables) == 3

    def test_dependency_edge_serialization(self):
        edge = DependencyEdge(
            from_element="users.id",
            to_element="orders.user_id",
            usage_type="join_key",
            sources=[
                DependencySource(source_type="sql_ast", confidence=0.9),
            ],
            final_confidence=0.9,
        )
        data = edge.model_dump()
        restored = DependencyEdge(**data)
        assert restored.from_element == "users.id"
        assert restored.sources[0].confidence == 0.9


class TestDiffModels:
    def test_schema_change_event(self):
        event = SchemaChangeEvent(
            change_type="column_added",
            table="users",
            column="phone",
        )
        assert event.change_type == "column_added"
        assert event.table == "users"
        assert event.column == "phone"
        assert event.old_value is None

    def test_schema_change_event_with_values(self):
        event = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="age",
            old_value="INT",
            new_value="BIGINT",
        )
        assert event.old_value == "INT"
        assert event.new_value == "BIGINT"

    def test_schema_diff_result(self):
        diff = SchemaDiffResult(
            old_snapshot_id="snap_001",
            new_snapshot_id="snap_002",
            changes=[
                SchemaChangeEvent(
                    change_type="table_added",
                    table="logs",
                ),
            ],
        )
        assert len(diff.changes) == 1
        assert diff.old_snapshot_id == "snap_001"

    def test_diff_serialization(self):
        diff = SchemaDiffResult(
            old_snapshot_id="a",
            new_snapshot_id="b",
            changes=[
                SchemaChangeEvent(change_type="column_dropped", table="t", column="c"),
            ],
        )
        data = diff.model_dump()
        restored = SchemaDiffResult(**data)
        assert len(restored.changes) == 1
        assert restored.changes[0].change_type == "column_dropped"


class TestContextModels:
    def test_impact_assessment(self):
        ia = ImpactAssessment(
            table="orders",
            usage="fk",
            dependency_count=3,
            confidence=0.95,
        )
        assert ia.table == "orders"

    def test_impact_metrics_criticality(self):
        low = ImpactMetrics(downstream_tables=1, criticality="low")
        assert low.criticality == "low"

        critical = ImpactMetrics(downstream_tables=15, criticality="critical")
        assert critical.criticality == "critical"

    def test_context_package(self):
        pkg = ContextPackage(
            schema_change=SchemaChangeEvent(
                change_type="column_dropped",
                table="users",
                column="legacy_field",
            ),
            impacted_dependencies=[
                ImpactAssessment(table="orders", usage="fk", confidence=1.0),
            ],
            impact_metrics=ImpactMetrics(
                downstream_tables=1,
                downstream_columns=1,
                max_depth=1,
                criticality="low",
            ),
        )
        assert pkg.schema_change.change_type == "column_dropped"
        assert len(pkg.impacted_dependencies) == 1

    def test_context_package_serialization(self):
        pkg = ContextPackage(
            schema_change=SchemaChangeEvent(
                change_type="table_added",
                table="new_table",
            ),
        )
        data = pkg.model_dump()
        restored = ContextPackage(**data)
        assert restored.schema_change.table == "new_table"
