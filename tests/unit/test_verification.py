"""Tests for VerificationEngine (Phase 6) — deterministic outcome validation.

Tests verify:
- Schema validation (match/mismatch)
- Dependency revalidation (lost edges)
- Test validation (CI pass/fail)
- Downstream breakage detection
- Rollback triggers
- Human escalation rules
- Goal satisfaction (all-or-nothing)
"""

from schemint.drift.models import (
    ColumnSnapshot,
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    ExecutionReport,
    ExecutionResult,
    SchemaSnapshot,
    TableSnapshot,
)
from schemint.drift.verification import CITestResults, VerificationEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_execution_report(
    status: str = "success",
    execution_id: str = "exec_test_001",
) -> ExecutionReport:
    return ExecutionReport(
        execution_id=execution_id,
        overall_status=status,
        step_results=[
            ExecutionResult(
                step=1,
                action="notify_table_owner",
                status="success" if status == "success" else "failed",
                reversible=True,
            )
        ],
        requires_rollback=False,
    )


def _make_snapshot(
    tables: dict[str, dict[str, str]] | None = None,
    snapshot_id: str = "test_snap",
) -> SchemaSnapshot:
    """Build a snapshot. tables = {"users": {"id": "integer", "name": "varchar"}}"""
    if tables is None:
        tables = {"users": {"id": "integer", "name": "varchar"}}
    snap_tables = {}
    for tname, cols in tables.items():
        snap_cols = {cname: ColumnSnapshot(name=cname, type=ctype) for cname, ctype in cols.items()}
        snap_tables[tname] = TableSnapshot(name=tname, columns=snap_cols)
    return SchemaSnapshot(
        snapshot_id=snapshot_id,
        source="ddl",
        tables=snap_tables,
    )


def _make_graph(edges: list[tuple[str, str, str]] | None = None) -> DependencyGraph:
    """Build a graph. edges = [("users.id", "orders.user_id", "fk")]"""
    if edges is None:
        edges = []
    dep_edges = [
        DependencyEdge(
            from_element=f,
            to_element=t,
            usage_type=u,
            sources=[DependencySource(source_type="fk_constraint", confidence=1.0)],
            final_confidence=1.0,
        )
        for f, t, u in edges
    ]
    return DependencyGraph(edges=dep_edges)


# ---------------------------------------------------------------------------
# Schema Validation Tests
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_matching_schemas_valid(self):
        engine = VerificationEngine()
        snap = _make_snapshot()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
        )
        assert report.schema_valid is True

    def test_missing_table_invalid(self):
        engine = VerificationEngine()
        expected = _make_snapshot({"users": {"id": "integer"}, "orders": {"id": "integer"}})
        actual = _make_snapshot({"users": {"id": "integer"}})
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=expected,
            actual_snapshot=actual,
        )
        assert report.schema_valid is False

    def test_missing_column_invalid(self):
        engine = VerificationEngine()
        expected = _make_snapshot({"users": {"id": "integer", "email": "varchar"}})
        actual = _make_snapshot({"users": {"id": "integer"}})
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=expected,
            actual_snapshot=actual,
        )
        assert report.schema_valid is False

    def test_type_mismatch_invalid(self):
        engine = VerificationEngine()
        expected = _make_snapshot({"users": {"id": "integer"}})
        actual = _make_snapshot({"users": {"id": "varchar"}})
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=expected,
            actual_snapshot=actual,
        )
        assert report.schema_valid is False

    def test_both_none_is_valid(self):
        """No snapshots provided → treat as valid (both absent)."""
        engine = VerificationEngine()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=None,
            actual_snapshot=None,
        )
        assert report.schema_valid is True

    def test_one_none_is_invalid(self):
        """Only one snapshot → invalid (precautionary)."""
        engine = VerificationEngine()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=_make_snapshot(),
            actual_snapshot=None,
        )
        assert report.schema_valid is False


# ---------------------------------------------------------------------------
# Dependency Validation Tests
# ---------------------------------------------------------------------------


class TestDependencyValidation:
    def test_matching_graphs_valid(self):
        engine = VerificationEngine()
        graph = _make_graph([("users.id", "orders.user_id", "fk")])
        report = engine.verify(
            _make_execution_report(),
            expected_graph=graph,
            actual_graph=graph,
        )
        assert report.dependency_valid is True

    def test_lost_edge_invalid(self):
        engine = VerificationEngine()
        expected = _make_graph(
            [
                ("users.id", "orders.user_id", "fk"),
                ("users.id", "payments.user_id", "fk"),
            ]
        )
        actual = _make_graph([("users.id", "orders.user_id", "fk")])
        report = engine.verify(
            _make_execution_report(),
            expected_graph=expected,
            actual_graph=actual,
        )
        assert report.dependency_valid is False

    def test_new_edge_is_acceptable(self):
        """New edges (improved coverage) are OK."""
        engine = VerificationEngine()
        expected = _make_graph([("users.id", "orders.user_id", "fk")])
        actual = _make_graph(
            [
                ("users.id", "orders.user_id", "fk"),
                ("users.id", "payments.user_id", "fk"),
            ]
        )
        report = engine.verify(
            _make_execution_report(),
            expected_graph=expected,
            actual_graph=actual,
        )
        assert report.dependency_valid is True

    def test_both_none_is_valid(self):
        engine = VerificationEngine()
        report = engine.verify(
            _make_execution_report(),
            expected_graph=None,
            actual_graph=None,
        )
        assert report.dependency_valid is True


# ---------------------------------------------------------------------------
# Test Validation Tests
# ---------------------------------------------------------------------------


class TestTestValidation:
    def test_all_tests_passed(self):
        engine = VerificationEngine()
        ci = CITestResults(total_tests=10, passed=10, failed=0)
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.tests_passed is True

    def test_some_tests_failed(self):
        engine = VerificationEngine()
        ci = CITestResults(total_tests=10, passed=8, failed=2)
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.tests_passed is False

    def test_no_ci_results_passes(self):
        """No CI results → assume tests passed (no evidence of failure)."""
        engine = VerificationEngine()
        report = engine.verify(_make_execution_report(), ci_results=None)
        assert report.tests_passed is True


# ---------------------------------------------------------------------------
# Downstream Breakage Detection Tests
# ---------------------------------------------------------------------------


class TestDownstreamBreakage:
    def test_schema_related_error_triggers_breakage(self):
        engine = VerificationEngine()
        ci = CITestResults(
            total_tests=5,
            passed=4,
            failed=1,
            errors=["column 'email' does not exist"],
        )
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.downstream_breakage_detected is True

    def test_non_schema_error_no_breakage(self):
        engine = VerificationEngine()
        ci = CITestResults(
            total_tests=5,
            passed=4,
            failed=1,
            errors=["timeout waiting for response"],
        )
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.downstream_breakage_detected is False

    def test_graph_references_missing_table(self):
        engine = VerificationEngine()
        snap = _make_snapshot({"users": {"id": "integer"}})
        graph = _make_graph([("users.id", "deleted_table.user_id", "fk")])
        report = engine.verify(
            _make_execution_report(),
            actual_snapshot=snap,
            actual_graph=graph,
        )
        assert report.downstream_breakage_detected is True

    def test_no_breakage_when_all_tables_exist(self):
        engine = VerificationEngine()
        snap = _make_snapshot(
            {
                "users": {"id": "integer"},
                "orders": {"user_id": "integer"},
            }
        )
        graph = _make_graph([("users.id", "orders.user_id", "fk")])
        report = engine.verify(
            _make_execution_report(),
            actual_snapshot=snap,
            actual_graph=graph,
        )
        assert report.downstream_breakage_detected is False


# ---------------------------------------------------------------------------
# Rollback Trigger Tests
# ---------------------------------------------------------------------------


class TestRollbackTrigger:
    def test_failed_execution_triggers_rollback(self):
        engine = VerificationEngine()
        report = engine.verify(_make_execution_report(status="failed"))
        assert report.requires_rollback is True

    def test_downstream_breakage_triggers_rollback(self):
        engine = VerificationEngine()
        ci = CITestResults(
            total_tests=5,
            passed=4,
            failed=1,
            errors=["relation 'users' does not exist"],
        )
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.requires_rollback is True

    def test_invalid_schema_triggers_rollback(self):
        engine = VerificationEngine()
        expected = _make_snapshot({"users": {"id": "integer", "email": "varchar"}})
        actual = _make_snapshot({"users": {"id": "integer"}})
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=expected,
            actual_snapshot=actual,
        )
        assert report.requires_rollback is True

    def test_success_no_rollback(self):
        engine = VerificationEngine()
        snap = _make_snapshot()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
        )
        assert report.requires_rollback is False


# ---------------------------------------------------------------------------
# Human Escalation Tests
# ---------------------------------------------------------------------------


class TestHumanEscalation:
    def test_rollback_triggers_escalation(self):
        engine = VerificationEngine()
        report = engine.verify(_make_execution_report(status="failed"))
        assert report.requires_human_escalation is True

    def test_source_human_review_triggers_escalation(self):
        engine = VerificationEngine()
        snap = _make_snapshot()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
            source_requires_human_review=True,
        )
        assert report.requires_human_escalation is True

    def test_no_escalation_when_clean(self):
        engine = VerificationEngine()
        snap = _make_snapshot()
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
            source_requires_human_review=False,
        )
        assert report.requires_human_escalation is False


# ---------------------------------------------------------------------------
# Goal Satisfaction Tests
# ---------------------------------------------------------------------------


class TestGoalSatisfaction:
    def test_all_pass_goal_satisfied(self):
        """All checks pass → goal satisfied."""
        engine = VerificationEngine()
        snap = _make_snapshot()
        graph = _make_graph()
        ci = CITestResults(total_tests=5, passed=5, failed=0)
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
            expected_graph=graph,
            actual_graph=graph,
            ci_results=ci,
        )
        assert report.goal_satisfied is True

    def test_failed_execution_not_satisfied(self):
        engine = VerificationEngine()
        report = engine.verify(_make_execution_report(status="failed"))
        assert report.goal_satisfied is False

    def test_invalid_schema_not_satisfied(self):
        engine = VerificationEngine()
        expected = _make_snapshot({"users": {"id": "integer", "email": "varchar"}})
        actual = _make_snapshot({"users": {"id": "integer"}})
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=expected,
            actual_snapshot=actual,
        )
        assert report.goal_satisfied is False

    def test_test_failure_not_satisfied(self):
        engine = VerificationEngine()
        snap = _make_snapshot()
        ci = CITestResults(total_tests=5, passed=4, failed=1)
        report = engine.verify(
            _make_execution_report(),
            expected_snapshot=snap,
            actual_snapshot=snap,
            ci_results=ci,
        )
        assert report.goal_satisfied is False

    def test_downstream_breakage_not_satisfied(self):
        engine = VerificationEngine()
        ci = CITestResults(
            total_tests=5,
            passed=4,
            failed=1,
            errors=["column 'missing_col' does not exist"],
        )
        report = engine.verify(_make_execution_report(), ci_results=ci)
        assert report.goal_satisfied is False


# ---------------------------------------------------------------------------
# CI Test Results Tests
# ---------------------------------------------------------------------------


class TestCITestResults:
    def test_all_passed_true(self):
        ci = CITestResults(total_tests=10, passed=10, failed=0)
        assert ci.all_passed is True

    def test_all_passed_false_with_failures(self):
        ci = CITestResults(total_tests=10, passed=8, failed=2)
        assert ci.all_passed is False

    def test_all_passed_false_when_no_tests(self):
        ci = CITestResults(total_tests=0, passed=0, failed=0)
        assert ci.all_passed is False
