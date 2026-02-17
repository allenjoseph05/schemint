"""Tests for DataQualitySignals model and computation.

Verifies dead tuple ratio, sequential scan ratio, vacuum/analyze staleness,
and integration with the ContextPackage via assembly.
"""

from datetime import datetime, timedelta, timezone

from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.models import (
    DataQualitySignals,
    DependencyGraph,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
    TableStatistics,
)


class TestDataQualityModel:
    def test_defaults(self):
        dqs = DataQualitySignals()
        assert dqs.dead_tuple_ratio == 0.0
        assert dqs.is_vacuum_needed is False
        assert dqs.is_analyze_stale is False
        assert dqs.last_vacuum_age_hours is None


class TestDeadTupleRatio:
    def test_high_dead_tuples_vacuum_needed(self):
        stats = TableStatistics(table_name="t", row_count=1000, dead_tuples=200)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.is_vacuum_needed is True
        assert signals.dead_tuple_ratio > 0.1

    def test_low_dead_tuples_no_vacuum(self):
        stats = TableStatistics(table_name="t", row_count=10000, dead_tuples=50)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.is_vacuum_needed is False

    def test_zero_rows_zero_ratio(self):
        stats = TableStatistics(table_name="t", row_count=0, dead_tuples=0)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.dead_tuple_ratio == 0.0
        assert signals.is_vacuum_needed is False


class TestAnalyzeStaleness:
    def test_stale_analyze(self):
        old = datetime.now(timezone.utc) - timedelta(hours=100)
        stats = TableStatistics(table_name="t", row_count=1000, last_analyze=old)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.is_analyze_stale is True
        assert signals.last_analyze_age_hours > 72

    def test_recent_analyze_not_stale(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=10)
        stats = TableStatistics(table_name="t", row_count=1000, last_analyze=recent)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.is_analyze_stale is False

    def test_no_analyze_timestamp(self):
        stats = TableStatistics(table_name="t", row_count=1000)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.last_analyze_age_hours is None
        assert signals.is_analyze_stale is False


class TestSeqScanRatio:
    def test_high_seq_scan_ratio(self):
        stats = TableStatistics(table_name="t", row_count=1000, seq_scan_count=90, idx_scan_count=10)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.seq_scan_ratio == 0.9

    def test_zero_scans(self):
        stats = TableStatistics(table_name="t", row_count=1000)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.seq_scan_ratio == 0.0


class TestVacuumAge:
    def test_vacuum_age_hours(self):
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        stats = TableStatistics(table_name="t", row_count=1000, last_vacuum=old)
        signals = ContextAssembler._compute_data_quality_signals(stats)
        assert signals is not None
        assert signals.last_vacuum_age_hours is not None
        assert 47 < signals.last_vacuum_age_hours < 49


class TestNoStats:
    def test_none_returns_none(self):
        assert ContextAssembler._compute_data_quality_signals(None) is None


class TestDataQualityInContextPackage:
    def test_signals_included(self):
        stats = TableStatistics(table_name="users", row_count=1000, dead_tuples=200)
        schema = SchemaSnapshot(
            snapshot_id="s",
            source="ddl",
            tables={"users": TableSnapshot(name="users")},
            table_statistics={"users": stats},
        )
        change = SchemaChangeEvent(change_type="column_added", table="users", column="email")
        ctx = ContextAssembler().assemble(change, DependencyGraph(), schema)
        assert ctx.data_quality_signals is not None
        assert ctx.data_quality_signals.is_vacuum_needed is True

    def test_no_stats_no_signals(self):
        schema = SchemaSnapshot(
            snapshot_id="s",
            source="ddl",
            tables={"users": TableSnapshot(name="users")},
        )
        change = SchemaChangeEvent(change_type="table_added", table="users")
        ctx = ContextAssembler().assemble(change, DependencyGraph(), schema)
        assert ctx.data_quality_signals is None
