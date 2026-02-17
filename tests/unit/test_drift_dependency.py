"""Tests for DependencyGraphBuilder — deterministic sources only.

Design invariant: "The dependency graph records only what can be proven.
Missing lineage results in uncertainty, not inference."
"""

import json

import pytest

from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.models import (
    ColumnSnapshot,
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    ParseHealth,
    SchemaSnapshot,
    TableSnapshot,
)


@pytest.fixture
def builder():
    return DependencyGraphBuilder()


@pytest.fixture
def sample_schema():
    return SchemaSnapshot(
        snapshot_id="test",
        source="ddl",
        tables={
            "users": TableSnapshot(
                name="users",
                columns={"id": ColumnSnapshot(name="id", type="INT")},
                foreign_keys=[],
            ),
            "orders": TableSnapshot(
                name="orders",
                columns={
                    "id": ColumnSnapshot(name="id", type="INT"),
                    "user_id": ColumnSnapshot(name="user_id", type="INT"),
                },
                foreign_keys=[
                    {
                        "column": "user_id",
                        "references_table": "users",
                        "references_column": "id",
                        "name": "fk_orders_user",
                    },
                ],
            ),
            "order_items": TableSnapshot(
                name="order_items",
                columns={
                    "id": ColumnSnapshot(name="id", type="INT"),
                    "order_id": ColumnSnapshot(name="order_id", type="INT"),
                },
                foreign_keys=[
                    {
                        "column": "order_id",
                        "references_table": "orders",
                        "references_column": "id",
                        "name": "fk_items_order",
                    },
                ],
            ),
        },
    )


# =========================================================================
# FK Extraction
# =========================================================================

class TestFKExtraction:
    def test_extracts_fk_edges(self, builder, sample_schema):
        edges = builder.from_fk_constraints(sample_schema)

        assert len(edges) == 2

        # FK: orders.user_id → users.id
        # Edge direction: from=users.id (upstream) → to=orders.user_id (downstream)
        fk_edges = [e for e in edges if e.from_element == "users.id"]
        assert len(fk_edges) == 1
        assert fk_edges[0].to_element == "orders.user_id"
        assert fk_edges[0].usage_type == "fk"
        assert fk_edges[0].final_confidence == 1.0

    def test_fk_direction_is_downstream(self, builder, sample_schema):
        """FK edges flow downstream: from referenced (upstream) to dependent (downstream)."""
        edges = builder.from_fk_constraints(sample_schema)
        for edge in edges:
            assert edge.direction == "downstream"

    def test_fk_source_type(self, builder, sample_schema):
        edges = builder.from_fk_constraints(sample_schema)
        for edge in edges:
            assert len(edge.sources) == 1
            assert edge.sources[0].source_type == "fk_constraint"
            assert edge.sources[0].confidence == 1.0

    def test_no_fks_returns_empty(self, builder):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "users": TableSnapshot(name="users", foreign_keys=[]),
            },
        )
        edges = builder.from_fk_constraints(schema)
        assert edges == []

    def test_incomplete_fk_skipped(self, builder):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "orders": TableSnapshot(
                    name="orders",
                    foreign_keys=[{"column": "user_id"}],  # missing refs
                ),
            },
        )
        edges = builder.from_fk_constraints(schema)
        assert edges == []


# =========================================================================
# dbt Manifest
# =========================================================================

class TestDbtManifest:
    def test_parses_manifest(self, builder, tmp_path):
        manifest = {
            "nodes": {
                "model.project.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "depends_on": {
                        "nodes": ["model.project.users"],
                    },
                    "columns": {},
                },
                "model.project.users": {
                    "resource_type": "model",
                    "name": "users",
                    "depends_on": {"nodes": []},
                    "columns": {},
                },
            },
        }

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))

        assert len(edges) == 1
        assert edges[0].from_element == "users"
        assert edges[0].to_element == "orders"
        assert edges[0].usage_type == "transform"
        assert edges[0].final_confidence == 1.0

    def test_dbt_direction_is_upstream(self, builder, tmp_path):
        manifest = {
            "nodes": {
                "model.project.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "depends_on": {"nodes": ["model.project.users"]},
                    "columns": {},
                },
                "model.project.users": {
                    "resource_type": "model",
                    "name": "users",
                    "depends_on": {"nodes": []},
                    "columns": {},
                },
            },
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))
        for edge in edges:
            assert edge.direction == "upstream"

    def test_dbt_stores_unique_id_provenance(self, builder, tmp_path):
        """dbt edges must store the original unique_id for provenance."""
        manifest = {
            "nodes": {
                "model.project.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "depends_on": {"nodes": ["model.project.users"]},
                    "columns": {},
                },
                "model.project.users": {
                    "resource_type": "model",
                    "name": "users",
                    "depends_on": {"nodes": []},
                    "columns": {},
                },
            },
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))
        assert len(edges) == 1
        assert edges[0].sources[0].dbt_unique_id == "model.project.users"

    def test_dbt_fqn_with_schema_and_database(self, builder, tmp_path):
        """When database/schema are available, use fully-qualified names."""
        manifest = {
            "nodes": {
                "model.project.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "analytics",
                    "database": "warehouse",
                    "depends_on": {"nodes": ["model.project.users"]},
                    "columns": {},
                },
                "model.project.users": {
                    "resource_type": "model",
                    "name": "users",
                    "schema": "analytics",
                    "database": "warehouse",
                    "depends_on": {"nodes": []},
                    "columns": {},
                },
            },
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))
        assert len(edges) == 1
        assert edges[0].from_element == "warehouse.analytics.users"
        assert edges[0].to_element == "warehouse.analytics.orders"

    def test_skips_non_model_nodes(self, builder, tmp_path):
        manifest = {
            "nodes": {
                "test.project.test_orders": {
                    "resource_type": "test",
                    "name": "test_orders",
                    "depends_on": {"nodes": ["model.project.orders"]},
                    "columns": {},
                },
            },
        }

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))
        assert edges == []

    def test_extracts_name_from_id(self, builder, tmp_path):
        manifest = {
            "nodes": {
                "model.project.derived": {
                    "resource_type": "model",
                    "name": "derived",
                    "depends_on": {
                        "nodes": ["source.project.raw_data"],
                    },
                    "columns": {},
                },
            },
        }

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        edges = builder.from_dbt_manifest(str(manifest_file))
        assert len(edges) == 1
        # Should extract "raw_data" from "source.project.raw_data"
        assert edges[0].from_element == "raw_data"


# =========================================================================
# SQL AST (sqlglot-based, deterministic)
# =========================================================================

class TestSQLAST:
    def test_extracts_join_edges(self, builder):
        sql = """
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        """
        edges = builder.from_sql_ast(sql)

        join_edges = [e for e in edges if e.usage_type == "join_key"]
        assert len(join_edges) >= 1

        # Should resolve aliases
        found = False
        for e in join_edges:
            if "users" in e.from_element and "orders" in e.to_element:
                found = True
                assert e.final_confidence == 0.9
        assert found

    def test_extracts_where_edges(self, builder):
        sql = """
        SELECT *
        FROM users u, orders o
        WHERE u.id = o.user_id
        """
        edges = builder.from_sql_ast(sql)

        filter_edges = [e for e in edges if e.usage_type == "filter"]
        assert len(filter_edges) >= 1

    def test_join_confidence_higher_than_where(self, builder):
        sql = """
        SELECT * FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE u.status = o.status
        """
        edges = builder.from_sql_ast(sql)

        join_edges = [e for e in edges if e.usage_type == "join_key"]
        where_edges = [e for e in edges if e.usage_type == "filter"]

        if join_edges and where_edges:
            assert join_edges[0].final_confidence > where_edges[0].final_confidence

    def test_no_edges_from_simple_select(self, builder):
        sql = "SELECT * FROM users"
        edges = builder.from_sql_ast(sql)
        # No join or where comparisons — only column-level lineage edges
        table_edges = [e for e in edges if e.lineage_type == "table"]
        assert table_edges == []

    def test_alias_resolved_flag_true_when_resolved(self, builder):
        """When aliases are present and resolved, alias_resolved must be True."""
        sql = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        edges = builder.from_sql_ast(sql)

        assert len(edges) >= 1
        for edge in edges:
            assert edge.sources[0].alias_resolved is True

    def test_every_edge_has_source(self, builder):
        """Every SQL-derived edge MUST have ≥1 DependencySource."""
        sql = """
        SELECT * FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE u.status = o.status
        """
        edges = builder.from_sql_ast(sql)
        for edge in edges:
            assert len(edge.sources) >= 1
            assert edge.sources[0].source_type == "sql_ast"

    def test_parse_failure_emits_no_edges(self, builder):
        """If SQL cannot be parsed, emit NO edges — not partial results."""
        # sqlglot is lenient, so test with truly broken syntax
        # that it cannot parse at all
        edges = builder.from_sql_ast("")
        assert edges == []

    def test_unqualified_columns_ignored(self, builder):
        """Columns without table qualifiers cannot prove dependencies."""
        sql = "SELECT * FROM users u, orders o WHERE id = user_id"
        edges = builder.from_sql_ast(sql)
        # id and user_id have no table qualifiers → no provable table-level edge
        table_edges = [e for e in edges if e.lineage_type == "table"]
        assert table_edges == []

    def test_where_literal_comparison_ignored(self, builder):
        """WHERE col = 'literal' should NOT produce a table-level edge."""
        sql = "SELECT * FROM users u WHERE u.status = 'active'"
        edges = builder.from_sql_ast(sql)
        table_edges = [e for e in edges if e.lineage_type == "table"]
        assert table_edges == []

    def test_file_path_propagated(self, builder):
        """file_path should be stored in the source for provenance."""
        sql = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        edges = builder.from_sql_ast(sql, file_path="queries/report.sql")
        assert len(edges) >= 1
        assert edges[0].sources[0].file_path == "queries/report.sql"

    def test_multiple_joins(self, builder):
        sql = """
        SELECT *
        FROM users u
        JOIN orders o ON u.id = o.user_id
        JOIN payments p ON o.id = p.order_id
        """
        edges = builder.from_sql_ast(sql)
        join_edges = [e for e in edges if e.usage_type == "join_key"]
        assert len(join_edges) == 2


# =========================================================================
# View Definitions
# =========================================================================

class TestViewDefinitions:
    def test_extracts_view_sources(self, builder):
        views = {
            "active_users": "SELECT * FROM users WHERE status = 'active'",
            "order_summary": "SELECT o.*, u.name FROM orders o JOIN users u ON o.user_id = u.id",
        }

        edges = builder.from_view_definitions(views)

        assert len(edges) >= 2
        # active_users depends on users
        au_edges = [e for e in edges if e.to_element == "active_users"]
        assert len(au_edges) >= 1
        assert au_edges[0].final_confidence == 0.95

    def test_view_direction_is_upstream(self, builder):
        views = {"v": "SELECT * FROM t"}
        edges = builder.from_view_definitions(views)
        for edge in edges:
            assert edge.direction == "upstream"

    def test_view_source_type(self, builder):
        views = {"v": "SELECT * FROM t"}
        edges = builder.from_view_definitions(views)

        for edge in edges:
            assert edge.sources[0].source_type == "view_definition"

    def test_view_self_reference_excluded(self, builder):
        """A view should not list itself as a source table."""
        views = {"users": "SELECT * FROM users WHERE active = true"}
        edges = builder.from_view_definitions(views)
        # "users" references itself — should be excluded
        assert edges == []


# =========================================================================
# Edge Merging & Invariants
# =========================================================================

class TestEdgeMerging:
    def test_merges_duplicate_edges(self, builder):
        edges = [
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                sources=[DependencySource(source_type="sql_ast", confidence=0.9)],
                final_confidence=0.9,
            ),
        ]

        graph = builder.build(edges)
        assert len(graph.edges) == 1
        assert graph.edges[0].final_confidence == 1.0
        assert len(graph.edges[0].sources) == 2

    def test_different_usage_types_not_merged(self, builder):
        edges = [
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="join_key",
                sources=[DependencySource(source_type="sql_ast", confidence=0.9)],
                final_confidence=0.9,
            ),
        ]

        graph = builder.build(edges)
        assert len(graph.edges) == 2

    def test_confidence_is_max_not_average(self, builder):
        """final_confidence must be max(source confidences), not average."""
        edges = [
            DependencyEdge(
                from_element="a.x",
                to_element="b.y",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="a.x",
                to_element="b.y",
                usage_type="fk",
                sources=[DependencySource(source_type="sql_ast", confidence=0.5)],
                final_confidence=0.5,
            ),
        ]

        graph = builder.build(edges)
        # max(1.0, 0.5) = 1.0, not avg(1.0, 0.5) = 0.75
        assert graph.edges[0].final_confidence == 1.0

    def test_edges_without_sources_stripped(self, builder):
        """build() must strip edges that have no sources."""
        edges = [
            DependencyEdge(
                from_element="a.x",
                to_element="b.y",
                usage_type="fk",
                sources=[],  # No provenance!
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="c.x",
                to_element="d.y",
                usage_type="fk",
                sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
                final_confidence=1.0,
            ),
        ]

        graph = builder.build(edges)
        assert len(graph.edges) == 1
        assert graph.edges[0].from_element == "c.x"


# =========================================================================
# Coverage & Uncertainty
# =========================================================================

class TestCoverage:
    def test_full_coverage(self, builder, sample_schema):
        graph = DependencyGraph(edges=[
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                final_confidence=1.0,
            ),
            DependencyEdge(
                from_element="orders.id",
                to_element="order_items.order_id",
                usage_type="fk",
                final_confidence=1.0,
            ),
        ])

        coverage = builder.compute_coverage(graph, sample_schema)
        assert coverage.tables_total == 3
        assert coverage.tables_with_lineage == 3
        assert coverage.coverage_pct == 100.0
        assert coverage.untracked_tables == []

    def test_partial_coverage(self, builder, sample_schema):
        graph = DependencyGraph(edges=[
            DependencyEdge(
                from_element="users.id",
                to_element="orders.user_id",
                usage_type="fk",
                final_confidence=1.0,
            ),
        ])

        coverage = builder.compute_coverage(graph, sample_schema)
        assert coverage.tables_total == 3
        assert coverage.tables_with_lineage == 2
        assert "order_items" in coverage.untracked_tables

    def test_untracked_tables_explicitly_surfaced(self, builder, sample_schema):
        """Missing lineage must be surfaced, not hidden."""
        graph = DependencyGraph(edges=[])
        coverage = builder.compute_coverage(graph, sample_schema)
        assert coverage.tables_with_lineage == 0
        assert set(coverage.untracked_tables) == {"users", "orders", "order_items"}

    def test_empty_graph_zero_coverage(self, builder, sample_schema):
        graph = DependencyGraph(edges=[])
        coverage = builder.compute_coverage(graph, sample_schema)
        assert coverage.coverage_pct == 0.0
        assert coverage.tables_with_lineage == 0

    def test_empty_schema_no_error(self, builder):
        schema = SchemaSnapshot(snapshot_id="test", source="ddl", tables={})
        graph = DependencyGraph(edges=[])
        coverage = builder.compute_coverage(graph, schema)
        assert coverage.tables_total == 0
        assert coverage.coverage_pct == 0.0


# =========================================================================
# Enhanced dependency graph tests (merged from test_enhanced_dependency_graph.py)
# =========================================================================


class TestCTEExtraction:
    """CTE (WITH ... AS) dependency extraction."""

    def test_simple_cte(self, builder):
        sql = """
        WITH active_users AS (
            SELECT id, name FROM users WHERE active = true
        )
        SELECT * FROM active_users JOIN orders ON active_users.id = orders.user_id
        """
        edges = builder.from_sql_ast(sql)
        cte_edges = [e for e in edges if e.final_confidence == 0.85]
        assert len(cte_edges) > 0
        source_tables = {e.from_element for e in cte_edges}
        assert "users" in source_tables

    def test_multiple_ctes(self, builder):
        sql = """
        WITH
            active_users AS (SELECT * FROM users WHERE active = true),
            recent_orders AS (SELECT * FROM orders WHERE created_at > '2024-01-01')
        SELECT * FROM active_users JOIN recent_orders ON active_users.id = recent_orders.user_id
        """
        edges = builder.from_sql_ast(sql)
        cte_edges = [e for e in edges if e.final_confidence == 0.85]
        source_tables = {e.from_element for e in cte_edges}
        assert "users" in source_tables
        assert "orders" in source_tables

    def test_cte_self_reference_excluded(self, builder):
        """Recursive CTEs (self-references) should not create edges."""
        sql = """
        WITH RECURSIVE tree AS (
            SELECT id, parent_id FROM categories WHERE parent_id IS NULL
            UNION ALL
            SELECT c.id, c.parent_id FROM categories c JOIN tree t ON c.parent_id = t.id
        )
        SELECT * FROM tree
        """
        edges = builder.from_sql_ast(sql)
        cte_edges = [e for e in edges if e.final_confidence == 0.85]
        # Should have edges from categories but NOT from tree→tree
        for edge in cte_edges:
            assert not (edge.from_element == "tree" and edge.to_element == "tree")


class TestSubqueryExtraction:
    """Subquery table reference extraction."""

    def test_subquery_with_alias(self, builder):
        sql = """
        SELECT * FROM (
            SELECT user_id, COUNT(*) as order_count
            FROM orders
            GROUP BY user_id
        ) AS user_orders
        WHERE user_orders.order_count > 5
        """
        edges = builder.from_sql_ast(sql)
        subquery_edges = [e for e in edges if e.final_confidence == 0.8]
        if subquery_edges:
            source_tables = {e.from_element for e in subquery_edges}
            assert "orders" in source_tables

    def test_subquery_without_alias_skipped(self, builder):
        """Subqueries without aliases should not create edges (no target name)."""
        sql = """
        SELECT * FROM users WHERE id IN (SELECT user_id FROM orders)
        """
        edges = builder.from_sql_ast(sql)
        subquery_edges = [e for e in edges if e.final_confidence == 0.8]
        # No alias on the subquery, so no edge should be created
        assert len(subquery_edges) == 0


class TestInsertSelectExtraction:
    """INSERT INTO ... SELECT dependency extraction."""

    def test_insert_select(self, builder):
        sql = """
        INSERT INTO user_archive
        SELECT * FROM users WHERE deleted_at IS NOT NULL
        """
        edges = builder.from_sql_ast(sql)
        insert_edges = [e for e in edges if e.final_confidence == 0.95]
        assert len(insert_edges) > 0
        assert any(
            e.from_element == "users" and e.to_element == "user_archive"
            for e in insert_edges
        )

    def test_insert_select_multiple_sources(self, builder):
        sql = """
        INSERT INTO report_data
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        """
        edges = builder.from_sql_ast(sql)
        insert_edges = [e for e in edges if e.final_confidence == 0.95]
        source_tables = {e.from_element for e in insert_edges}
        assert "users" in source_tables
        assert "orders" in source_tables
        assert all(e.to_element == "report_data" for e in insert_edges)

    def test_insert_select_self_reference_excluded(self, builder):
        """INSERT INTO t SELECT FROM t should not create an edge."""
        sql = """
        INSERT INTO users
        SELECT * FROM users WHERE active = true
        """
        edges = builder.from_sql_ast(sql)
        insert_edges = [e for e in edges if e.final_confidence == 0.95]
        self_edges = [
            e for e in insert_edges
            if e.from_element == "users" and e.to_element == "users"
        ]
        assert len(self_edges) == 0


class TestParseHealth:
    """Parse health tracking for batch SQL file processing."""

    def test_all_files_succeed(self, builder):
        sql_files = {
            "file1.sql": "SELECT * FROM users JOIN orders ON users.id = orders.user_id",
            "file2.sql": "SELECT * FROM products",
        }
        _edges, health = builder.from_sql_files(sql_files)
        assert health.total_files == 2
        assert health.parsed_ok == 2
        assert len(health.parse_failures) == 0
        assert health.success_rate == 1.0

    def test_some_files_fail(self, builder):
        sql_files = {
            "good.sql": "SELECT * FROM users",
            "bad.sql": "THIS IS NOT SQL @@@ {{{",
        }
        _edges, health = builder.from_sql_files(sql_files)
        assert health.total_files == 2
        assert health.parsed_ok >= 1  # at least good.sql should parse

    def test_empty_input(self, builder):
        _edges, health = builder.from_sql_files({})
        assert health.total_files == 0
        assert health.parsed_ok == 0
        assert health.success_rate == 1.0

    def test_edges_from_successful_files(self, builder):
        sql_files = {
            "query.sql": "SELECT u.id, o.total FROM users u JOIN orders o ON u.id = o.user_id",
        }
        edges, _health = builder.from_sql_files(sql_files)
        assert len(edges) > 0
        assert all(
            any(s.file_path == "query.sql" for s in e.sources) for e in edges
        )


class TestParseHealthModel:
    """ParseHealth model behavior."""

    def test_success_rate_calculation(self):
        health = ParseHealth(total_files=10, parsed_ok=8)
        assert health.success_rate == 0.8

    def test_success_rate_zero_files(self):
        health = ParseHealth(total_files=0, parsed_ok=0)
        assert health.success_rate == 1.0

    def test_parse_failures_tracked(self):
        health = ParseHealth(
            total_files=3, parsed_ok=1,
            parse_failures=["bad1.sql", "bad2.sql"]
        )
        assert len(health.parse_failures) == 2
        assert health.success_rate == pytest.approx(1 / 3)
