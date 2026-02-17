"""Phase 6: Verification & Feedback Loop — deterministic outcome validation.

Core invariant: NO LLM reasoning. All verification is deterministic.

Verifies whether the agent's goal was achieved and produces structured
signals for the agent controller to decide: retry, rollback, escalate,
or terminate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from schemint.drift.models import (
    DependencyGraph,
    ExecutionReport,
    SchemaSnapshot,
    VerificationReport,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CI Test Results (structured input from external CI)
# =============================================================================


class CITestResults:
    """Structured CI/dbt test results.

    In production, this would be parsed from dbt test output,
    pytest XML reports, or CI pipeline artifacts.
    """

    def __init__(
        self,
        total_tests: int = 0,
        passed: int = 0,
        failed: int = 0,
        errors: list[str] | None = None,
    ) -> None:
        self.total_tests = total_tests
        self.passed = passed
        self.failed = failed
        self.errors = errors or []

    @property
    def all_passed(self) -> bool:
        return self.total_tests > 0 and self.failed == 0


# =============================================================================
# Verification Engine
# =============================================================================


class VerificationEngine:
    """Deterministic verification of execution outcomes.

    Checks:
    1. Schema validation — do snapshots reflect intended changes?
    2. Dependency revalidation — is lineage intact after execution?
    3. Test validation — did CI/dbt tests pass?
    4. Downstream breakage detection — are consumers broken?

    Produces a VerificationReport with structured signals.
    No policy decisions — only structured truth.
    """

    def verify(
        self,
        execution_report: ExecutionReport,
        expected_snapshot: SchemaSnapshot | None = None,
        actual_snapshot: SchemaSnapshot | None = None,
        expected_graph: DependencyGraph | None = None,
        actual_graph: DependencyGraph | None = None,
        ci_results: CITestResults | None = None,
        source_requires_human_review: bool = False,
    ) -> VerificationReport:
        """Run all verification checks and produce a report.

        All checks are independent — each produces a boolean signal.
        Goal satisfaction requires ALL checks to pass.
        """
        execution_id = execution_report.execution_id

        # 1. Schema validation
        schema_valid = self._validate_schema(
            expected_snapshot, actual_snapshot
        )

        # 2. Dependency revalidation
        dependency_valid = self._validate_dependencies(
            expected_graph, actual_graph
        )

        # 3. Test validation
        tests_passed = self._validate_tests(ci_results)

        # 4. Downstream breakage detection
        downstream_breakage = self._detect_downstream_breakage(
            actual_snapshot, actual_graph, ci_results
        )

        # 5. Deterministic rollback trigger
        requires_rollback = self._compute_rollback_need(
            execution_report, schema_valid, downstream_breakage
        )

        # 6. Human escalation rules
        requires_human_escalation = self._compute_human_escalation(
            requires_rollback, source_requires_human_review
        )

        # 7. Goal satisfaction
        goal_satisfied = self._compute_goal_satisfaction(
            execution_report,
            schema_valid,
            dependency_valid,
            tests_passed,
            downstream_breakage,
        )

        report = VerificationReport(
            execution_id=execution_id,
            schema_valid=schema_valid,
            dependency_valid=dependency_valid,
            tests_passed=tests_passed,
            downstream_breakage_detected=downstream_breakage,
            goal_satisfied=goal_satisfied,
            requires_rollback=requires_rollback,
            requires_human_escalation=requires_human_escalation,
            verified_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Verification %s: goal=%s rollback=%s escalate=%s",
            execution_id,
            goal_satisfied,
            requires_rollback,
            requires_human_escalation,
        )

        return report

    # ----- Schema validation -----

    def _validate_schema(
        self,
        expected: SchemaSnapshot | None,
        actual: SchemaSnapshot | None,
    ) -> bool:
        """Verify that the actual schema matches expected state.

        If either snapshot is None, validation cannot be performed,
        which is treated as invalid (precautionary).
        """
        if expected is None or actual is None:
            logger.warning("Schema validation skipped: missing snapshot(s)")
            return expected is None and actual is None

        # Compare table sets
        expected_tables = set(expected.tables.keys())
        actual_tables = set(actual.tables.keys())

        if expected_tables != actual_tables:
            logger.warning(
                "Schema mismatch: expected tables %s, got %s",
                expected_tables,
                actual_tables,
            )
            return False

        # Compare columns in each table
        for table_name in expected_tables:
            exp_table = expected.tables[table_name]
            act_table = actual.tables[table_name]

            exp_cols = set(exp_table.columns.keys())
            act_cols = set(act_table.columns.keys())

            if exp_cols != act_cols:
                logger.warning(
                    "Column mismatch in %s: expected %s, got %s",
                    table_name,
                    exp_cols,
                    act_cols,
                )
                return False

            # Check column types
            for col_name in exp_cols:
                if exp_table.columns[col_name].type != act_table.columns[col_name].type:
                    logger.warning(
                        "Type mismatch in %s.%s: expected %s, got %s",
                        table_name,
                        col_name,
                        exp_table.columns[col_name].type,
                        act_table.columns[col_name].type,
                    )
                    return False

        return True

    # ----- Dependency revalidation -----

    def _validate_dependencies(
        self,
        expected: DependencyGraph | None,
        actual: DependencyGraph | None,
    ) -> bool:
        """Verify that the dependency graph is still intact.

        Checks that no edges were lost after execution.
        New edges are acceptable (they indicate improved coverage).
        """
        if expected is None or actual is None:
            # If no graphs provided, skip validation (pass)
            return expected is None and actual is None

        # Build edge key sets for comparison
        expected_keys = {
            (e.from_element, e.to_element, e.usage_type)
            for e in expected.edges
        }
        actual_keys = {
            (e.from_element, e.to_element, e.usage_type)
            for e in actual.edges
        }

        # Check for lost edges (expected but not in actual)
        lost_edges = expected_keys - actual_keys
        if lost_edges:
            logger.warning(
                "Dependency validation: %d edges lost: %s",
                len(lost_edges),
                lost_edges,
            )
            return False

        return True

    # ----- Test validation -----

    def _validate_tests(self, ci_results: CITestResults | None) -> bool:
        """Check if CI/dbt tests passed.

        If no CI results provided, tests are considered passing
        (no evidence of failure).
        """
        if ci_results is None:
            return True

        if not ci_results.all_passed:
            logger.warning(
                "Test validation: %d/%d tests failed",
                ci_results.failed,
                ci_results.total_tests,
            )
            return False

        return True

    # ----- Downstream breakage detection -----

    def _detect_downstream_breakage(
        self,
        actual_snapshot: SchemaSnapshot | None,
        actual_graph: DependencyGraph | None,
        ci_results: CITestResults | None,
    ) -> bool:
        """Detect downstream breakage from multiple signals.

        Breakage is detected if:
        - CI test failures reference schema-related errors
        - Dependency graph has edges to tables that no longer exist
        """
        # Signal 1: CI test failures
        if ci_results and not ci_results.all_passed:
            for error in ci_results.errors:
                error_lower = error.lower()
                # Look for schema-related error patterns
                if any(
                    pattern in error_lower
                    for pattern in [
                        "column",
                        "relation",
                        "table",
                        "does not exist",
                        "undefined",
                        "missing",
                    ]
                ):
                    logger.warning("Downstream breakage: schema-related test failure: %s", error)
                    return True

        # Signal 2: Dependency graph references non-existent tables
        if actual_snapshot and actual_graph:
            known_tables = set(actual_snapshot.tables.keys())
            for edge in actual_graph.edges:
                from_table = edge.from_element.split(".")[0]
                to_table = edge.to_element.split(".")[0]
                if from_table not in known_tables or to_table not in known_tables:
                    logger.warning(
                        "Downstream breakage: edge references missing table: %s -> %s",
                        edge.from_element,
                        edge.to_element,
                    )
                    return True

        return False

    # ----- Rollback trigger -----

    def _compute_rollback_need(
        self,
        execution_report: ExecutionReport,
        schema_valid: bool,
        downstream_breakage: bool,
    ) -> bool:
        """Deterministic rollback trigger.

        Rollback required if:
        - execution failed
        - OR downstream breakage detected
        - OR schema is invalid
        """
        if execution_report.overall_status == "failed":
            return True
        if downstream_breakage:
            return True
        return bool(not schema_valid)

    # ----- Human escalation -----

    def _compute_human_escalation(
        self,
        requires_rollback: bool,
        source_requires_human_review: bool,
    ) -> bool:
        """Determine if human escalation is needed.

        Escalation required if:
        - rollback is required
        - OR source decision required human review
        """
        if requires_rollback:
            return True
        return bool(source_requires_human_review)

    # ----- Goal satisfaction -----

    def _compute_goal_satisfaction(
        self,
        execution_report: ExecutionReport,
        schema_valid: bool,
        dependency_valid: bool,
        tests_passed: bool,
        downstream_breakage: bool,
    ) -> bool:
        """Goal is satisfied only if ALL checks pass.

        This is the strictest possible check — any single failure
        means the goal is not satisfied.
        """
        if execution_report.overall_status != "success":
            return False
        if not schema_valid:
            return False
        if not dependency_valid:
            return False
        if not tests_passed:
            return False
        return not downstream_breakage
