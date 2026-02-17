"""Comprehensive tests for Phase B: new models, diff logic, classification, context.

Covers:
    - SequenceSnapshot, EnumSnapshot, FunctionSnapshot models
    - TableStatistics, IndexStatistics models
    - Sequence/Enum/Function diff detection
    - Risk classification for all new change types
    - Expanded type families (20 families, 61 types)
    - Context package with table/index statistics
    - PK diff detection (from Phase A, additional coverage)
    - Index property change detection
    - View definition normalization
"""


from schemint.drift.change_classifier import (
    _TYPE_TO_FAMILY,
    TYPE_FAMILIES,
    classify_change,
    classify_type_change,
)
from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ColumnSnapshot,
    ContextPackage,
    DependencyGraph,
    EnumSnapshot,
    FunctionSnapshot,
    IndexSnapshot,
    IndexStatistics,
    SchemaChangeEvent,
    SchemaSnapshot,
    SequenceSnapshot,
    TableSnapshot,
    TableStatistics,
    ViewSnapshot,
)

# =========================================================================
# Model Validation Tests
# =========================================================================


class TestNewModels:
    def test_sequence_snapshot_defaults(self):
        seq = SequenceSnapshot(name="users_id_seq")
        assert seq.data_type == "bigint"
        assert seq.increment_by == 1
        assert seq.cycle is False

    def test_sequence_snapshot_full(self):
        seq = SequenceSnapshot(
            name="orders_id_seq",
            data_type="integer",
            start_value=1000,
            increment_by=10,
            min_value=1,
            max_value=999999,
            cache_size=20,
            cycle=True,
            last_value=5000,
        )
        assert seq.max_value == 999999
        assert seq.last_value == 5000

    def test_enum_snapshot(self):
        enum = EnumSnapshot(name="status", values=["active", "inactive", "pending"])
        assert len(enum.values) == 3
        assert enum.values[0] == "active"

    def test_enum_snapshot_empty_values(self):
        enum = EnumSnapshot(name="empty_enum")
        assert enum.values == []

    def test_function_snapshot_defaults(self):
        fn = FunctionSnapshot(name="my_func")
        assert fn.language == "sql"
        assert fn.volatility == "volatile"

    def test_function_snapshot_full(self):
        fn = FunctionSnapshot(
            name="calculate_total",
            argument_types="integer, numeric",
            return_type="numeric",
            language="plpgsql",
            volatility="stable",
            definition="BEGIN RETURN a * b; END;",
        )
        assert fn.volatility == "stable"
        assert fn.return_type == "numeric"

    def test_table_statistics(self):
        stats = TableStatistics(
            table_name="orders",
            row_count=5_000_000,
            dead_tuples=10000,
            total_size_bytes=2_000_000_000,
            table_size_bytes=1_500_000_000,
            index_size_bytes=500_000_000,
            seq_scan_count=10,
            idx_scan_count=999999,
        )
        assert stats.row_count == 5_000_000
        assert stats.total_size_bytes == 2_000_000_000

    def test_index_statistics(self):
        stats = IndexStatistics(
            index_name="idx_orders_user_id",
            table_name="orders",
            idx_scan=50000,
            idx_tup_read=200000,
            size_bytes=100_000_000,
        )
        assert stats.idx_scan == 50000

    def test_schema_snapshot_with_new_fields(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="live_db",
            sequences={"id_seq": SequenceSnapshot(name="id_seq")},
            enums={"status": EnumSnapshot(name="status", values=["a", "b"])},
            functions={"fn": FunctionSnapshot(name="fn")},
            table_statistics={"t": TableStatistics(table_name="t", row_count=100)},
            index_statistics={"idx": IndexStatistics(index_name="idx", table_name="t")},
        )
        assert "id_seq" in schema.sequences
        assert "status" in schema.enums
        assert "fn" in schema.functions
        assert schema.table_statistics["t"].row_count == 100

    def test_context_package_with_statistics(self):
        pkg = ContextPackage(
            schema_change=SchemaChangeEvent(
                change_type="column_type_change", table="orders"
            ),
            affected_table_stats=TableStatistics(
                table_name="orders", row_count=1_000_000
            ),
            affected_index_stats=[
                IndexStatistics(index_name="idx1", table_name="orders", idx_scan=500),
            ],
        )
        assert pkg.affected_table_stats is not None
        assert pkg.affected_table_stats.row_count == 1_000_000
        assert len(pkg.affected_index_stats) == 1


# =========================================================================
# Sequence Diff Tests
# =========================================================================


class TestSequenceDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def _make_schema(self, sequences=None, **kwargs):
        return SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            sequences=sequences or {},
            **kwargs,
        )

    def test_sequence_added(self):
        old = self._make_schema()
        new = self._make_schema(
            sequences={"id_seq": SequenceSnapshot(name="id_seq")}
        )
        result = self.differ.diff(old, new)
        seq_changes = [c for c in result.changes if c.change_type == "sequence_added"]
        assert len(seq_changes) == 1
        assert seq_changes[0].table == "id_seq"

    def test_sequence_dropped(self):
        old = self._make_schema(
            sequences={"id_seq": SequenceSnapshot(name="id_seq")}
        )
        new = self._make_schema()
        result = self.differ.diff(old, new)
        seq_changes = [c for c in result.changes if c.change_type == "sequence_dropped"]
        assert len(seq_changes) == 1

    def test_sequence_changed(self):
        old = self._make_schema(
            sequences={"id_seq": SequenceSnapshot(name="id_seq", increment_by=1)}
        )
        new = self._make_schema(
            sequences={"id_seq": SequenceSnapshot(name="id_seq", increment_by=10)}
        )
        result = self.differ.diff(old, new)
        seq_changes = [c for c in result.changes if c.change_type == "sequence_changed"]
        assert len(seq_changes) == 1
        assert "increment_by" in seq_changes[0].old_value

    def test_sequence_unchanged(self):
        seq = SequenceSnapshot(name="id_seq", increment_by=1)
        old = self._make_schema(sequences={"id_seq": seq})
        new = self._make_schema(sequences={"id_seq": seq.model_copy()})
        result = self.differ.diff(old, new)
        seq_changes = [c for c in result.changes if "sequence" in c.change_type]
        assert len(seq_changes) == 0


# =========================================================================
# Enum Diff Tests
# =========================================================================


class TestEnumDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def _make_schema(self, enums=None, **kwargs):
        return SchemaSnapshot(
            snapshot_id="test", source="ddl", enums=enums or {}, **kwargs,
        )

    def test_enum_added(self):
        old = self._make_schema()
        new = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["a", "b"])}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "enum_added"]
        assert len(changes) == 1
        assert "a,b" in changes[0].new_value

    def test_enum_dropped(self):
        old = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["a", "b"])}
        )
        new = self._make_schema()
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "enum_dropped"]
        assert len(changes) == 1

    def test_enum_value_added(self):
        old = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["active", "inactive"])}
        )
        new = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["active", "inactive", "pending"])}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "enum_value_added"]
        assert len(changes) == 1
        assert "pending" in changes[0].new_value

    def test_enum_value_removed(self):
        old = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["active", "inactive", "pending"])}
        )
        new = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["active", "inactive"])}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "enum_value_removed"]
        assert len(changes) == 1
        assert "pending" in changes[0].old_value

    def test_enum_value_added_and_removed(self):
        """Adding and removing values in the same diff should produce both events."""
        old = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["a", "b"])}
        )
        new = self._make_schema(
            enums={"status": EnumSnapshot(name="status", values=["b", "c"])}
        )
        result = self.differ.diff(old, new)
        added = [c for c in result.changes if c.change_type == "enum_value_added"]
        removed = [c for c in result.changes if c.change_type == "enum_value_removed"]
        assert len(added) == 1
        assert len(removed) == 1

    def test_enum_unchanged(self):
        enum = EnumSnapshot(name="status", values=["a", "b"])
        old = self._make_schema(enums={"status": enum})
        new = self._make_schema(enums={"status": enum.model_copy()})
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if "enum" in c.change_type]
        assert len(changes) == 0


# =========================================================================
# Function Diff Tests
# =========================================================================


class TestFunctionDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def _make_schema(self, functions=None, **kwargs):
        return SchemaSnapshot(
            snapshot_id="test", source="ddl", functions=functions or {}, **kwargs,
        )

    def test_function_added(self):
        old = self._make_schema()
        new = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc", return_type="integer")}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "function_added"]
        assert len(changes) == 1

    def test_function_dropped(self):
        old = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc")}
        )
        new = self._make_schema()
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "function_dropped"]
        assert len(changes) == 1

    def test_function_changed_body(self):
        old = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc", definition="RETURN 1;")}
        )
        new = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc", definition="RETURN 2;")}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "function_changed"]
        assert len(changes) == 1

    def test_function_changed_volatility(self):
        old = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc", volatility="volatile")}
        )
        new = self._make_schema(
            functions={"calc": FunctionSnapshot(name="calc", volatility="stable")}
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "function_changed"]
        assert len(changes) == 1
        assert "volatile" in changes[0].old_value
        assert "stable" in changes[0].new_value

    def test_function_unchanged(self):
        fn = FunctionSnapshot(name="calc", definition="RETURN 1;", volatility="stable")
        old = self._make_schema(functions={"calc": fn})
        new = self._make_schema(functions={"calc": fn.model_copy()})
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if "function" in c.change_type]
        assert len(changes) == 0


# =========================================================================
# PK Diff Tests (additional coverage from Phase A)
# =========================================================================


class TestPKDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def _make_schema(self, pk_old=None, pk_new=None):
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            tables={"t": TableSnapshot(name="t", primary_key=pk_old or [])},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            tables={"t": TableSnapshot(name="t", primary_key=pk_new or [])},
        )
        return old, new

    def test_pk_added(self):
        old, new = self._make_schema(pk_old=[], pk_new=["id"])
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "pk_added"]
        assert len(changes) == 1
        assert changes[0].new_value == "id"

    def test_pk_dropped(self):
        old, new = self._make_schema(pk_old=["id"], pk_new=[])
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "pk_dropped"]
        assert len(changes) == 1

    def test_pk_changed(self):
        old, new = self._make_schema(pk_old=["id"], pk_new=["id", "version"])
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "pk_changed"]
        assert len(changes) == 1

    def test_pk_unchanged(self):
        old, new = self._make_schema(pk_old=["id"], pk_new=["id"])
        result = self.differ.diff(old, new)
        pk_changes = [c for c in result.changes if "pk_" in c.change_type]
        assert len(pk_changes) == 0


# =========================================================================
# Index Property Change Tests
# =========================================================================


class TestIndexPropertyDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_index_uniqueness_changed(self):
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            tables={"t": TableSnapshot(
                name="t",
                indexes=[IndexSnapshot(name="idx_email", columns=["email"], is_unique=False)],
            )},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            tables={"t": TableSnapshot(
                name="t",
                indexes=[IndexSnapshot(name="idx_email", columns=["email"], is_unique=True)],
            )},
        )
        result = self.differ.diff(old, new)
        changes = [c for c in result.changes if c.change_type == "index_changed"]
        assert len(changes) == 1
        assert "unique=False" in changes[0].old_value
        assert "unique=True" in changes[0].new_value


# =========================================================================
# View Normalization Tests
# =========================================================================


class TestViewNormalization:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_whitespace_difference_ignored(self):
        """Views with different whitespace but same semantics should not diff."""
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="SELECT   id,  name  FROM  users"
            )},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="SELECT id, name FROM users"
            )},
        )
        result = self.differ.diff(old, new)
        view_changes = [c for c in result.changes if "view" in c.change_type]
        assert len(view_changes) == 0

    def test_case_difference_ignored(self):
        """Views with different casing but same semantics should not diff."""
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="SELECT id FROM users"
            )},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="select id from users"
            )},
        )
        result = self.differ.diff(old, new)
        view_changes = [c for c in result.changes if "view" in c.change_type]
        assert len(view_changes) == 0

    def test_real_definition_change_detected(self):
        """Actual semantic changes should still be detected."""
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="SELECT id FROM users"
            )},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            views={"v": ViewSnapshot(
                name="v", definition="SELECT id, name FROM users"
            )},
        )
        result = self.differ.diff(old, new)
        view_changes = [c for c in result.changes if c.change_type == "view_definition_change"]
        assert len(view_changes) == 1


# =========================================================================
# Risk Classification Tests for New Types
# =========================================================================


class TestNewChangeTypeClassification:
    def test_sequence_added_safe(self):
        e = SchemaChangeEvent(change_type="sequence_added", table="seq")
        assert classify_change(e) == "safe"

    def test_sequence_dropped_potentially_breaking(self):
        e = SchemaChangeEvent(change_type="sequence_dropped", table="seq")
        assert classify_change(e) == "potentially_breaking"

    def test_sequence_changed_needs_review(self):
        e = SchemaChangeEvent(change_type="sequence_changed", table="seq")
        assert classify_change(e) == "needs_review"

    def test_enum_added_safe(self):
        e = SchemaChangeEvent(change_type="enum_added", table="status")
        assert classify_change(e) == "safe"

    def test_enum_dropped_breaking(self):
        e = SchemaChangeEvent(change_type="enum_dropped", table="status")
        assert classify_change(e) == "breaking"

    def test_enum_value_added_safe(self):
        e = SchemaChangeEvent(change_type="enum_value_added", table="status")
        assert classify_change(e) == "safe"

    def test_enum_value_removed_breaking(self):
        e = SchemaChangeEvent(change_type="enum_value_removed", table="status")
        assert classify_change(e) == "breaking"

    def test_function_added_safe(self):
        e = SchemaChangeEvent(change_type="function_added", table="fn")
        assert classify_change(e) == "safe"

    def test_function_dropped_potentially_breaking(self):
        e = SchemaChangeEvent(change_type="function_dropped", table="fn")
        assert classify_change(e) == "potentially_breaking"

    def test_function_changed_needs_review(self):
        e = SchemaChangeEvent(change_type="function_changed", table="fn")
        assert classify_change(e) == "needs_review"

    def test_pk_added_needs_review(self):
        e = SchemaChangeEvent(change_type="pk_added", table="t")
        assert classify_change(e) == "needs_review"

    def test_pk_dropped_breaking(self):
        e = SchemaChangeEvent(change_type="pk_dropped", table="t")
        assert classify_change(e) == "breaking"

    def test_pk_changed_potentially_breaking(self):
        e = SchemaChangeEvent(change_type="pk_changed", table="t")
        assert classify_change(e) == "potentially_breaking"

    def test_index_changed_needs_review(self):
        e = SchemaChangeEvent(change_type="index_changed", table="t")
        assert classify_change(e) == "needs_review"

    def test_column_added_not_null_no_default_potentially_breaking(self):
        e = SchemaChangeEvent(
            change_type="column_added",
            table="t",
            column="email",
            new_value="varchar NOT NULL",
        )
        assert classify_change(e) == "potentially_breaking"

    def test_column_added_not_null_with_default_safe(self):
        e = SchemaChangeEvent(
            change_type="column_added",
            table="t",
            column="status",
            new_value="varchar NOT NULL DEFAULT 'active'",
        )
        assert classify_change(e) == "safe"

    def test_column_added_nullable_safe(self):
        e = SchemaChangeEvent(
            change_type="column_added",
            table="t",
            column="notes",
            new_value="text",
        )
        assert classify_change(e) == "safe"


# =========================================================================
# Expanded Type Family Tests
# =========================================================================


class TestExpandedTypeFamilies:
    def test_family_count(self):
        assert len(TYPE_FAMILIES) >= 20

    def test_total_mapped_types(self):
        assert len(_TYPE_TO_FAMILY) >= 50

    def test_real_to_double_precision_safe(self):
        assert classify_type_change("real", "double precision") == "safe"

    def test_double_to_real_breaking(self):
        assert classify_type_change("double precision", "real") == "potentially_breaking"

    def test_varchar_to_text_safe(self):
        assert classify_type_change("varchar", "text") == "safe"

    def test_text_to_varchar_breaking(self):
        assert classify_type_change("text", "varchar") == "potentially_breaking"

    def test_varchar_to_citext_safe(self):
        assert classify_type_change("varchar", "citext") == "safe"

    def test_inet_to_cidr_safe(self):
        """Both in network family, cidr is wider."""
        assert classify_type_change("inet", "cidr") == "safe"

    def test_integer_to_text_breaking(self):
        """Cross-family change is always breaking."""
        assert classify_type_change("integer", "text") == "breaking"

    def test_json_to_jsonb_safe(self):
        assert classify_type_change("json", "jsonb") == "safe"

    def test_timestamp_to_timestamptz_safe(self):
        assert classify_type_change("timestamp", "timestamptz") == "safe"

    def test_timestamptz_to_timestamp_breaking(self):
        assert classify_type_change("timestamptz", "timestamp") == "potentially_breaking"

    def test_unknown_type_needs_review(self):
        assert classify_type_change("custom_type", "text") == "needs_review"

    def test_serial_to_bigserial_safe(self):
        assert classify_type_change("serial", "bigserial") == "safe"

    def test_smallserial_in_family(self):
        assert "smallserial" in _TYPE_TO_FAMILY

    def test_bytea_in_binary_family(self):
        assert _TYPE_TO_FAMILY.get("bytea", (None,))[0] == "binary"

    def test_money_in_decimal_family(self):
        assert _TYPE_TO_FAMILY.get("money", (None,))[0] == "decimal"

    def test_interval_in_own_family(self):
        assert _TYPE_TO_FAMILY.get("interval", (None,))[0] == "interval"


# =========================================================================
# Context Assembly with Statistics
# =========================================================================


class TestContextWithStatistics:
    def test_stats_attached_to_context(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="live_db",
            tables={
                "orders": TableSnapshot(
                    name="orders",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
            table_statistics={
                "orders": TableStatistics(
                    table_name="orders",
                    row_count=5_000_000,
                    total_size_bytes=2_000_000_000,
                ),
            },
            index_statistics={
                "idx_orders_pkey": IndexStatistics(
                    index_name="idx_orders_pkey",
                    table_name="orders",
                    idx_scan=100000,
                ),
            },
        )
        graph = DependencyGraph(edges=[])
        change = SchemaChangeEvent(
            change_type="column_type_change",
            table="orders",
            column="id",
        )

        assembler = ContextAssembler(max_depth=10)
        ctx = assembler.assemble(change, graph, schema)

        assert ctx.affected_table_stats is not None
        assert ctx.affected_table_stats.row_count == 5_000_000
        assert len(ctx.affected_index_stats) == 1
        assert ctx.affected_index_stats[0].idx_scan == 100000

    def test_no_stats_when_table_not_found(self):
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="ddl",
            tables={
                "users": TableSnapshot(name="users"),
            },
        )
        graph = DependencyGraph(edges=[])
        change = SchemaChangeEvent(
            change_type="column_added",
            table="users",
            column="email",
        )

        assembler = ContextAssembler(max_depth=10)
        ctx = assembler.assemble(change, graph, schema)

        assert ctx.affected_table_stats is None
        assert ctx.affected_index_stats == []

    def test_only_relevant_index_stats_attached(self):
        """Only indexes belonging to the affected table should be attached."""
        schema = SchemaSnapshot(
            snapshot_id="test",
            source="live_db",
            tables={
                "orders": TableSnapshot(name="orders"),
                "users": TableSnapshot(name="users"),
            },
            index_statistics={
                "idx_orders_id": IndexStatistics(
                    index_name="idx_orders_id", table_name="orders", idx_scan=100
                ),
                "idx_users_email": IndexStatistics(
                    index_name="idx_users_email", table_name="users", idx_scan=200
                ),
            },
        )
        graph = DependencyGraph(edges=[])
        change = SchemaChangeEvent(
            change_type="table_dropped", table="orders"
        )

        assembler = ContextAssembler(max_depth=10)
        ctx = assembler.assemble(change, graph, schema)

        assert len(ctx.affected_index_stats) == 1
        assert ctx.affected_index_stats[0].table_name == "orders"


# =========================================================================
# Combined Diff Test (multiple object types in one diff)
# =========================================================================


class TestCombinedDiff:
    def test_all_new_types_in_single_diff(self):
        """A single diff should detect changes across all object types."""
        old = SchemaSnapshot(
            snapshot_id="old",
            source="ddl",
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={"id": ColumnSnapshot(name="id", type="integer")},
                ),
            },
            sequences={"id_seq": SequenceSnapshot(name="id_seq")},
            enums={"status": EnumSnapshot(name="status", values=["a", "b"])},
            functions={"old_fn": FunctionSnapshot(name="old_fn")},
        )
        new = SchemaSnapshot(
            snapshot_id="new",
            source="ddl",
            tables={
                "users": TableSnapshot(
                    name="users",
                    columns={
                        "id": ColumnSnapshot(name="id", type="bigint"),
                        "email": ColumnSnapshot(name="email", type="varchar"),
                    },
                ),
            },
            sequences={
                "id_seq": SequenceSnapshot(name="id_seq", increment_by=5),
                "order_seq": SequenceSnapshot(name="order_seq"),
            },
            enums={"status": EnumSnapshot(name="status", values=["a", "b", "c"])},
            functions={"new_fn": FunctionSnapshot(name="new_fn")},
        )

        differ = SchemaDiffer()
        result = differ.diff(old, new)

        change_types = {c.change_type for c in result.changes}

        assert "column_type_change" in change_types  # id: integer → bigint
        assert "column_added" in change_types  # email added
        assert "sequence_changed" in change_types  # id_seq increment
        assert "sequence_added" in change_types  # order_seq
        assert "enum_value_added" in change_types  # status: +c
        assert "function_dropped" in change_types  # old_fn
        assert "function_added" in change_types  # new_fn

        # Every change should have a risk classification
        for change in result.changes:
            assert change.change_risk is not None
