"""Tests for column-level lineage extraction in dependency graph."""

from schemint.drift.dependency_graph import DependencyGraphBuilder


class TestColumnLineageBasic:
    """Test basic column-level lineage extraction from SELECT."""

    def setup_method(self):
        self.builder = DependencyGraphBuilder()

    def test_simple_column_ref(self):
        sql = "SELECT u.name, u.email FROM users u"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        assert len(col_edges) >= 1
        # Should have edges from users.name and users.email
        from_elements = {e.from_element for e in col_edges}
        assert any("users.name" in f for f in from_elements)

    def test_aliased_output(self):
        sql = "SELECT u.name AS user_name FROM users u"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        assert len(col_edges) >= 1
        # The output should reference user_name
        to_elements = {e.to_element for e in col_edges}
        assert any("user_name" in t for t in to_elements)

    def test_function_call_lower_confidence(self):
        sql = "SELECT UPPER(u.name) AS upper_name FROM users u"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        func_edges = [e for e in col_edges if e.final_confidence <= 0.75]
        assert len(func_edges) >= 1

    def test_aggregate_lowest_confidence(self):
        sql = "SELECT COUNT(o.id) AS order_count FROM orders o"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        agg_edges = [e for e in col_edges if e.final_confidence <= 0.7]
        assert len(agg_edges) >= 1

    def test_star_select(self):
        sql = "SELECT * FROM users"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        star_edges = [e for e in col_edges if ".*" in e.from_element]
        assert len(star_edges) >= 1
        assert all(e.final_confidence == 0.6 for e in star_edges)

    def test_no_lineage_for_literal_only(self):
        sql = "SELECT 1 AS one"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        assert len(col_edges) == 0


class TestCTEColumnLineage:
    """Test column lineage through CTEs."""

    def setup_method(self):
        self.builder = DependencyGraphBuilder()

    def test_cte_column_passthrough(self):
        sql = """
        WITH recent_orders AS (
            SELECT o.id, o.user_id, o.total
            FROM orders o
            WHERE o.created_at > '2024-01-01'
        )
        SELECT r.user_id, r.total FROM recent_orders r
        """
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        # Should trace orders.id → recent_orders.id etc.
        cte_edges = [e for e in col_edges if "recent_orders" in e.to_element]
        assert len(cte_edges) >= 1

    def test_cte_with_aggregation(self):
        sql = """
        WITH order_totals AS (
            SELECT o.user_id, SUM(o.total) AS total_spent
            FROM orders o
            GROUP BY o.user_id
        )
        SELECT t.user_id, t.total_spent FROM order_totals t
        """
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        assert len(col_edges) >= 1


class TestInsertSelectColumnLineage:
    """Test column lineage through INSERT INTO ... SELECT."""

    def setup_method(self):
        self.builder = DependencyGraphBuilder()

    def test_insert_select_column_mapping(self):
        sql = """
        INSERT INTO archive (user_id, total)
        SELECT o.user_id, o.total
        FROM orders o
        """
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        # Should map orders.user_id → archive.user_id
        archive_edges = [e for e in col_edges if "archive" in e.to_element]
        assert len(archive_edges) >= 1


class TestLineageTypeField:
    """Test that lineage_type field correctly distinguishes table vs column edges."""

    def setup_method(self):
        self.builder = DependencyGraphBuilder()

    def test_join_edges_are_table_level(self):
        sql = """
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        """
        edges = self.builder.from_sql_ast(sql)
        join_edges = [e for e in edges if e.usage_type == "join_key"]
        for e in join_edges:
            assert e.lineage_type == "table"

    def test_column_lineage_edges_are_column_level(self):
        sql = "SELECT u.name FROM users u"
        edges = self.builder.from_sql_ast(sql)
        col_edges = [e for e in edges if e.lineage_type == "column"]
        assert len(col_edges) >= 1
        for e in col_edges:
            assert e.lineage_type == "column"
