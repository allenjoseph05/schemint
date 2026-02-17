"""Tests for CriticalityThresholds — dependency count and table size computation.

Verifies that criticality escalates based on downstream dependency count,
row count, and byte size, and that the max of all signals is used.
"""


from schemint.drift.context_assembler import ContextAssembler, CriticalityThresholds
from schemint.drift.models import (
    DependencyGraph,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
    TableStatistics,
)


class TestCriticalityFromDeps:
    """Backward-compatible: criticality from dependency count only."""

    def test_critical(self):
        assert CriticalityThresholds().compute(15) == "critical"

    def test_high(self):
        assert CriticalityThresholds().compute(7) == "high"

    def test_medium(self):
        assert CriticalityThresholds().compute(3) == "medium"

    def test_low(self):
        assert CriticalityThresholds().compute(1) == "low"


class TestCriticalityFromSize:
    """Criticality escalation from table row count and byte size."""

    def test_large_rows_zero_deps_is_critical(self):
        stats = TableStatistics(
            table_name="huge",
            row_count=200_000_000,
            total_size_bytes=15 * 1024 * 1024 * 1024,
        )
        assert CriticalityThresholds().compute(0, stats) == "critical"

    def test_small_table_many_deps_is_critical(self):
        stats = TableStatistics(table_name="small", row_count=100)
        assert CriticalityThresholds().compute(15, stats) == "critical"

    def test_both_small_is_low(self):
        stats = TableStatistics(table_name="tiny", row_count=10)
        assert CriticalityThresholds().compute(0, stats) == "low"

    def test_medium_rows(self):
        stats = TableStatistics(table_name="mid", row_count=5_000_000)
        assert CriticalityThresholds().compute(0, stats) == "medium"

    def test_high_rows(self):
        stats = TableStatistics(table_name="big", row_count=50_000_000)
        assert CriticalityThresholds().compute(0, stats) == "high"

    def test_large_bytes_high(self):
        stats = TableStatistics(
            table_name="fat",
            row_count=100,
            total_size_bytes=2 * 1024 * 1024 * 1024,
        )
        assert CriticalityThresholds().compute(0, stats) == "high"

    def test_max_of_deps_and_size(self):
        stats = TableStatistics(table_name="mid", row_count=5_000_000)
        # medium from rows, high from deps
        assert CriticalityThresholds().compute(7, stats) == "high"


class TestCriticalityInContextPackage:
    def test_large_table_escalates_criticality(self):
        stats = TableStatistics(table_name="users", row_count=200_000_000)
        schema = SchemaSnapshot(
            snapshot_id="s",
            source="ddl",
            tables={"users": TableSnapshot(name="users")},
            table_statistics={"users": stats},
        )
        change = SchemaChangeEvent(change_type="column_added", table="users", column="email")
        ctx = ContextAssembler().assemble(change, DependencyGraph(), schema)
        assert ctx.impact_metrics.criticality == "critical"
