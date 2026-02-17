"""Unit tests for the pre-analysis engine."""

from __future__ import annotations

from schemint.core.analyzer.pre_analysis import (
    SchemaDomain,
    TableRole,
    build_topology,
    compute_statistics,
    detect_column_patterns,
    detect_risk_signals,
    resolve_domain,
    run_pre_analysis,
    serialize_pre_analysis,
)
from schemint.models.schema import (
    Column,
    DataType,
    ForeignKey,
    Index,
    ParsedSchema,
    Table,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table(
    name: str,
    columns: list[Column] | None = None,
    primary_key: list[str] | None = None,
    foreign_keys: list[ForeignKey] | None = None,
    indexes: list[Index] | None = None,
) -> Table:
    """Helper to create a Table with sensible defaults."""
    if columns is None:
        columns = [
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="name", data_type=DataType.VARCHAR, raw_type="VARCHAR(100)"),
        ]
    return Table(
        name=name,
        columns=columns,
        primary_key=primary_key or ["id"],
        foreign_keys=foreign_keys or [],
        indexes=indexes or [],
    )


def _ecommerce_schema() -> ParsedSchema:
    """An e-commerce schema for integration tests."""
    users = _make_table(
        "users",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="email", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            Column(name="password", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            Column(name="created_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
            Column(name="updated_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
        ],
    )

    products = _make_table(
        "products",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="name", data_type=DataType.VARCHAR, raw_type="VARCHAR(100)"),
            Column(name="price", data_type=DataType.FLOAT, raw_type="FLOAT"),
            Column(name="category_id", data_type=DataType.INT, raw_type="INT"),
            Column(name="created_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
            Column(name="updated_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
        ],
        foreign_keys=[
            ForeignKey(column="category_id", references_table="categories", references_column="id"),
        ],
    )

    orders = _make_table(
        "orders",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="user_id", data_type=DataType.INT, raw_type="INT"),
            Column(name="total", data_type=DataType.FLOAT, raw_type="FLOAT"),
            Column(name="status", data_type=DataType.VARCHAR, raw_type="VARCHAR(20)"),
            Column(name="created_at", data_type=DataType.TIMESTAMP, raw_type="TIMESTAMP"),
        ],
        foreign_keys=[
            ForeignKey(column="user_id", references_table="users", references_column="id"),
        ],
    )

    order_items = _make_table(
        "order_items",
        columns=[
            Column(
                name="id",
                data_type=DataType.INT,
                raw_type="INT",
                is_primary_key=True,
                nullable=False,
            ),
            Column(name="order_id", data_type=DataType.INT, raw_type="INT"),
            Column(name="product_id", data_type=DataType.INT, raw_type="INT"),
            Column(name="quantity", data_type=DataType.INT, raw_type="INT"),
        ],
        foreign_keys=[
            ForeignKey(column="order_id", references_table="orders", references_column="id"),
            ForeignKey(column="product_id", references_table="products", references_column="id"),
        ],
    )

    return ParsedSchema(
        tables=[users, products, orders, order_items],
        database_type="mysql",
    )


# ---------------------------------------------------------------------------
# Domain Resolution
# ---------------------------------------------------------------------------


class TestResolveDomain:
    def test_resolve_domain_ecommerce(self):
        assert resolve_domain("ecommerce") == SchemaDomain.ECOMMERCE

    def test_resolve_domain_saas(self):
        assert resolve_domain("saas") == SchemaDomain.SAAS

    def test_resolve_domain_none(self):
        assert resolve_domain(None) == SchemaDomain.GENERAL

    def test_resolve_domain_unknown(self):
        assert resolve_domain("xyz") == SchemaDomain.GENERAL

    def test_resolve_domain_case_insensitive(self):
        assert resolve_domain("ECOMMERCE") == SchemaDomain.ECOMMERCE
        assert resolve_domain("SaaS") == SchemaDomain.SAAS


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


class TestBuildTopology:
    def test_hub_table_detected(self):
        """Table with 3+ incoming FKs is classified as HUB."""
        hub = _make_table("hub_table")
        ref1 = _make_table(
            "ref1",
            foreign_keys=[
                ForeignKey(column="hub_id", references_table="hub_table", references_column="id")
            ],
        )
        ref2 = _make_table(
            "ref2",
            foreign_keys=[
                ForeignKey(column="hub_id", references_table="hub_table", references_column="id")
            ],
        )
        ref3 = _make_table(
            "ref3",
            foreign_keys=[
                ForeignKey(column="hub_id", references_table="hub_table", references_column="id")
            ],
        )
        schema = ParsedSchema(tables=[hub, ref1, ref2, ref3])

        topology = build_topology(schema)
        hub_topo = next(t for t in topology if t.name == "hub_table")
        assert hub_topo.role == TableRole.HUB
        assert hub_topo.incoming_fk_count == 3

    def test_orphan_table_detected(self):
        """Table with no FK relationships is ORPHAN."""
        orphan = _make_table("orphan_table")
        schema = ParsedSchema(tables=[orphan])

        topology = build_topology(schema)
        assert topology[0].role == TableRole.ORPHAN

    def test_leaf_table_detected(self):
        """Table with outgoing FKs but no incoming is LEAF."""
        parent = _make_table("parent")
        leaf = _make_table(
            "leaf",
            foreign_keys=[
                ForeignKey(column="parent_id", references_table="parent", references_column="id")
            ],
        )
        schema = ParsedSchema(tables=[parent, leaf])

        topology = build_topology(schema)
        leaf_topo = next(t for t in topology if t.name == "leaf")
        assert leaf_topo.role == TableRole.LEAF
        assert leaf_topo.outgoing_fk_count == 1

    def test_bridge_table_detected(self):
        """Junction table with 2+ outgoing FKs and few own columns is BRIDGE."""
        t1 = _make_table("table_a")
        t2 = _make_table("table_b")
        bridge = Table(
            name="a_b",
            columns=[
                Column(
                    name="id",
                    data_type=DataType.INT,
                    raw_type="INT",
                    is_primary_key=True,
                    nullable=False,
                ),
                Column(name="a_id", data_type=DataType.INT, raw_type="INT"),
                Column(name="b_id", data_type=DataType.INT, raw_type="INT"),
            ],
            primary_key=["id"],
            foreign_keys=[
                ForeignKey(column="a_id", references_table="table_a", references_column="id"),
                ForeignKey(column="b_id", references_table="table_b", references_column="id"),
            ],
        )
        schema = ParsedSchema(tables=[t1, t2, bridge])

        topology = build_topology(schema)
        bridge_topo = next(t for t in topology if t.name == "a_b")
        assert bridge_topo.role == TableRole.BRIDGE


# ---------------------------------------------------------------------------
# Column Patterns
# ---------------------------------------------------------------------------


class TestDetectColumnPatterns:
    def test_money_as_float_detected(self):
        """price FLOAT should be flagged."""
        table = _make_table(
            "products",
            columns=[
                Column(
                    name="id",
                    data_type=DataType.INT,
                    raw_type="INT",
                    is_primary_key=True,
                    nullable=False,
                ),
                Column(name="price", data_type=DataType.FLOAT, raw_type="FLOAT"),
            ],
        )
        schema = ParsedSchema(tables=[table])
        patterns = detect_column_patterns(schema)

        money_patterns = [p for p in patterns if p.pattern == "money_as_float"]
        assert len(money_patterns) == 1
        assert money_patterns[0].column == "price"

    def test_id_without_fk_detected(self):
        """user_id without FK constraint should be flagged."""
        table = _make_table(
            "orders",
            columns=[
                Column(
                    name="id",
                    data_type=DataType.INT,
                    raw_type="INT",
                    is_primary_key=True,
                    nullable=False,
                ),
                Column(name="user_id", data_type=DataType.INT, raw_type="INT"),
            ],
        )
        schema = ParsedSchema(tables=[table])
        patterns = detect_column_patterns(schema)

        id_patterns = [p for p in patterns if p.pattern == "id_without_fk"]
        assert len(id_patterns) == 1
        assert id_patterns[0].column == "user_id"

    def test_security_plaintext_detected(self):
        """password VARCHAR should be flagged."""
        table = _make_table(
            "users",
            columns=[
                Column(
                    name="id",
                    data_type=DataType.INT,
                    raw_type="INT",
                    is_primary_key=True,
                    nullable=False,
                ),
                Column(name="password", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            ],
        )
        schema = ParsedSchema(tables=[table])
        patterns = detect_column_patterns(schema)

        security_patterns = [p for p in patterns if p.pattern == "security_plaintext"]
        assert len(security_patterns) == 1
        assert security_patterns[0].column == "password"

    def test_safe_suffix_not_flagged(self):
        """password_hash should NOT be flagged."""
        table = _make_table(
            "users",
            columns=[
                Column(
                    name="id",
                    data_type=DataType.INT,
                    raw_type="INT",
                    is_primary_key=True,
                    nullable=False,
                ),
                Column(name="password_hash", data_type=DataType.VARCHAR, raw_type="VARCHAR(255)"),
            ],
        )
        schema = ParsedSchema(tables=[table])
        patterns = detect_column_patterns(schema)

        security_patterns = [p for p in patterns if p.pattern == "security_plaintext"]
        assert len(security_patterns) == 0


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class TestComputeStatistics:
    def test_basic_statistics(self):
        schema = _ecommerce_schema()
        stats = compute_statistics(schema)

        assert stats.table_count == 4
        # users=5, products=6, orders=5, order_items=4 → 20
        assert stats.total_columns == 20
        assert stats.avg_columns_per_table == 5.0
        assert stats.tables_with_pk_pct == 100.0
        # users + products + orders have timestamps, order_items doesn't
        assert stats.tables_with_timestamps_pct == 75.0

    def test_empty_schema(self):
        schema = ParsedSchema(tables=[])
        stats = compute_statistics(schema)

        assert stats.table_count == 0
        assert stats.total_columns == 0
        assert stats.avg_columns_per_table == 0.0


# ---------------------------------------------------------------------------
# Risk Signals
# ---------------------------------------------------------------------------


class TestDetectRiskSignals:
    def test_no_pk_risk(self):
        """Table without PK should generate high risk signal."""
        table = Table(
            name="bad_table",
            columns=[
                Column(name="data", data_type=DataType.VARCHAR, raw_type="VARCHAR(100)"),
            ],
            primary_key=[],
        )
        schema = ParsedSchema(tables=[table])
        risks = detect_risk_signals(schema)

        no_pk = [r for r in risks if r.signal == "no_pk"]
        assert len(no_pk) == 1
        assert no_pk[0].severity == "high"

    def test_wide_table_risk(self):
        """Table with 15+ columns should be flagged."""
        cols = [
            Column(name=f"col_{i}", data_type=DataType.VARCHAR, raw_type="VARCHAR(100)")
            for i in range(16)
        ]
        cols[0] = Column(
            name="id", data_type=DataType.INT, raw_type="INT", is_primary_key=True, nullable=False
        )
        table = Table(name="wide", columns=cols, primary_key=["id"])
        schema = ParsedSchema(tables=[table])
        risks = detect_risk_signals(schema)

        wide = [r for r in risks if r.signal == "wide_table"]
        assert len(wide) == 1


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestRunPreAnalysis:
    def test_run_pre_analysis_complete(self):
        """Integration test: returns all sections."""
        schema = _ecommerce_schema()
        result = run_pre_analysis(schema, app_type="ecommerce")

        assert result.domain == SchemaDomain.ECOMMERCE
        assert len(result.topology) == 4
        assert result.statistics.table_count == 4
        assert isinstance(result.column_patterns, list)
        assert isinstance(result.risk_signals, list)

    def test_serialize_is_compact(self):
        """Serialized output should be compact and contain key sections."""
        schema = _ecommerce_schema()
        pre = run_pre_analysis(schema, app_type="ecommerce")
        output = serialize_pre_analysis(pre)

        assert len(output) < 2000
        assert "DOMAIN:" in output
        assert "TOPOLOGY:" in output
        assert "STATISTICS:" in output
