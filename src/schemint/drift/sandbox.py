"""Migration Sandbox — orchestrates deterministic analysis + optional AI co-pilot.

The sandbox is the main entry point for migration analysis. It:
1. Resolves a baseline SchemaSnapshot (from DDL, snapshot, or stored project)
2. Runs fast danger pattern checks
3. Applies migration SQL to predict post-migration state
4. Diffs baseline vs. predicted state to detect changes + risk levels
5. Assembles blast-radius context per change
6. Computes a safety score + grade
7. (Optional) Invokes the AI co-pilot for alternatives, rollback, and intent validation

The deterministic layer (steps 1-6) always runs. The AI layer (step 7) runs
only when run_copilot=True and an API key is available.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from schemint.drift.alter_applier import AlterApplier
from schemint.drift.change_classifier import classify_change
from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.copilot_agent import get_copilot_agent
from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ContextPackage,
    CopilotResult,
    PredictedChange,
    SandboxWarning,
    SchemaSnapshot,
)
from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

logger = logging.getLogger(__name__)

# Risk level ordering for overall_risk computation
_RISK_ORDER = ["safe", "needs_review", "potentially_breaking", "breaking"]


class MigrationSandbox:
    """Orchestrates migration sandbox analysis with optional AI co-pilot."""

    def analyze(
        self,
        migration_sql: str,
        current_ddl: str | None = None,
        baseline_snapshot: SchemaSnapshot | None = None,
        project_id: str | None = None,
        run_copilot: bool = True,
    ) -> CopilotResult:
        """Run full migration sandbox analysis.

        Returns CopilotResult with deterministic analysis always populated.
        AI co-pilot fields are populated only when run_copilot=True and
        the AI service is available.
        """
        sandbox_id = (
            f"sandbox_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        now = datetime.now(timezone.utc)

        try:
            return self._run_analysis(
                sandbox_id=sandbox_id,
                migration_sql=migration_sql,
                current_ddl=current_ddl,
                baseline_snapshot=baseline_snapshot,
                project_id=project_id,
                run_copilot=run_copilot,
                now=now,
            )
        except Exception as e:
            logger.error("Sandbox analysis failed: %s", e)
            return CopilotResult(
                sandbox_id=sandbox_id,
                migration_sql=migration_sql,
                baseline_snapshot_id="error",
                status="error",
                error_message=str(e),
                analyzed_at=now,
            )

    def _run_analysis(
        self,
        sandbox_id: str,
        migration_sql: str,
        current_ddl: str | None,
        baseline_snapshot: SchemaSnapshot | None,
        project_id: str | None,
        run_copilot: bool,
        now: datetime,
    ) -> CopilotResult:
        """Internal analysis pipeline."""
        # Step 1: Resolve baseline
        baseline = self._resolve_baseline(current_ddl, baseline_snapshot, project_id)

        # Step 2: Fast danger check
        warnings = self._check_danger_patterns(migration_sql)

        # Step 3: Apply migration
        applier = AlterApplier()
        post_snapshot = applier.apply(baseline, migration_sql)

        # Step 4: Diff
        differ = SchemaDiffer()
        diff_result = differ.diff(baseline, post_snapshot)

        # Step 5: Context assembly (best-effort)
        context_packages = self._assemble_context(diff_result, baseline)

        # Step 6: Build predicted changes + score
        predicted_changes = self._build_predicted_changes(diff_result, context_packages)
        safety_score, safety_grade = self._compute_score(predicted_changes, warnings)
        overall_risk = self._compute_overall_risk(predicted_changes)
        recommendations = self._build_recommendations(predicted_changes, warnings)

        # Build result with deterministic layer
        result = CopilotResult(
            sandbox_id=sandbox_id,
            migration_sql=migration_sql,
            baseline_snapshot_id=baseline.snapshot_id,
            predicted_changes=predicted_changes,
            warnings=warnings,
            safety_score=safety_score,
            safety_grade=safety_grade,
            overall_risk=overall_risk,
            action_recommendations=recommendations,
            analyzed_at=now,
        )

        # Step 7: AI co-pilot (optional)
        if run_copilot:
            copilot = get_copilot_agent()
            if copilot is not None:
                result.copilot_available = True
                self._run_copilot(copilot, result, diff_result, context_packages)

        return result

    def _resolve_baseline(
        self,
        current_ddl: str | None,
        baseline_snapshot: SchemaSnapshot | None,
        project_id: str | None,
    ) -> SchemaSnapshot:
        """Resolve baseline snapshot from one of the three sources."""
        if baseline_snapshot is not None:
            return baseline_snapshot

        if current_ddl is not None:
            capture = DDLSnapshotCapture()
            return capture.capture(current_ddl)

        if project_id is not None:
            try:
                from schemint.drift.store import get_drift_store

                store = get_drift_store()
                snapshot = store.get_latest_snapshot(project_id)
                if snapshot is not None:
                    return snapshot
            except Exception as e:
                logger.warning("Failed to load snapshot for project '%s': %s", project_id, e)

        raise ValueError(
            "Must provide one of: current_ddl, baseline_snapshot, or project_id with stored snapshot"
        )

    def _check_danger_patterns(self, migration_sql: str) -> list[SandboxWarning]:
        """Run fast danger pattern detection."""
        try:
            from schemint.ci.sql_utils import detect_dangerous_patterns

            patterns = detect_dangerous_patterns(migration_sql)
            return [
                SandboxWarning(
                    pattern=p.pattern_type,
                    severity=p.severity,
                    message=p.description,
                    table=p.table_name,
                )
                for p in patterns
            ]
        except Exception as e:
            logger.warning("Danger pattern detection failed: %s", e)
            return []

    def _assemble_context(
        self, diff_result: object, schema: SchemaSnapshot
    ) -> list[ContextPackage]:
        """Best-effort context assembly for blast radius."""
        try:
            builder = DependencyGraphBuilder()
            edges = builder.from_fk_constraints(schema)
            graph = builder.build(edges)

            assembler = ContextAssembler()
            return assembler.assemble_all(diff_result, graph, schema)  # type: ignore[arg-type]
        except Exception as e:
            logger.debug("Context assembly skipped: %s", e)
            return []

    def _build_predicted_changes(
        self, diff_result: object, context_packages: list[ContextPackage]
    ) -> list[PredictedChange]:
        """Convert diff result changes into PredictedChange models."""
        # Build a map of table -> downstream impact count from context
        downstream_map: dict[str, int] = {}
        for ctx in context_packages:
            table = ctx.schema_change.table
            downstream_map[table] = max(
                downstream_map.get(table, 0),
                ctx.impact_metrics.downstream_tables,
            )

        changes = []
        for event in diff_result.changes:  # type: ignore[attr-defined]
            changes.append(
                PredictedChange(
                    change_type=event.change_type,
                    table=event.table,
                    column=event.column,
                    old_value=event.old_value,
                    new_value=event.new_value,
                    risk_level=event.change_risk or classify_change(event),
                    downstream_impact=downstream_map.get(event.table, 0),
                )
            )
        return changes

    @staticmethod
    def _compute_score(
        changes: list[PredictedChange], warnings: list[SandboxWarning]
    ) -> tuple[int, str]:
        """Compute safety score (0-100) and grade (A-F).

        Deductions:
            -30 per breaking change (cap 60)
            -15 per potentially_breaking change (cap 30)
            -5 per needs_review change (cap 10)
            -10 per critical warning
        """
        score = 100

        breaking_count = sum(1 for c in changes if c.risk_level == "breaking")
        pot_breaking_count = sum(1 for c in changes if c.risk_level == "potentially_breaking")
        review_count = sum(1 for c in changes if c.risk_level == "needs_review")
        critical_warnings = sum(1 for w in warnings if w.severity == "critical")

        score -= min(breaking_count * 30, 60)
        score -= min(pot_breaking_count * 15, 30)
        score -= min(review_count * 5, 10)
        score -= critical_warnings * 10

        score = max(score, 0)

        if score >= 90:
            grade = "A"
        elif score >= 75:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 45:
            grade = "D"
        else:
            grade = "F"

        return score, grade

    @staticmethod
    def _compute_overall_risk(changes: list[PredictedChange]) -> str:
        """Compute overall risk as the highest risk level among all changes."""
        if not changes:
            return "safe"

        max_idx = 0
        for c in changes:
            risk = c.risk_level or "safe"
            try:
                idx = _RISK_ORDER.index(risk)
            except ValueError:
                idx = 0
            max_idx = max(max_idx, idx)

        return _RISK_ORDER[max_idx]

    @staticmethod
    def _build_recommendations(
        changes: list[PredictedChange], warnings: list[SandboxWarning]
    ) -> list[str]:
        """Build human-readable action recommendations."""
        recommendations: list[str] = []

        breaking = [c for c in changes if c.risk_level == "breaking"]
        if breaking:
            tables = sorted({c.table for c in breaking})
            recommendations.append(
                f"BLOCKING: {len(breaking)} breaking change(s) on table(s): {', '.join(tables)}. "
                "Consider phased migration approach."
            )

        pot_breaking = [c for c in changes if c.risk_level == "potentially_breaking"]
        if pot_breaking:
            recommendations.append(
                f"WARNING: {len(pot_breaking)} potentially breaking change(s). "
                "Review downstream consumers before applying."
            )

        critical_warnings = [w for w in warnings if w.severity == "critical"]
        if critical_warnings:
            recommendations.append(
                f"DANGER: {len(critical_warnings)} dangerous pattern(s) detected. "
                "Review migration SQL carefully."
            )

        if not recommendations:
            recommendations.append("Migration looks safe to apply.")

        return recommendations

    def _run_copilot(
        self,
        copilot: object,
        result: CopilotResult,
        diff_result: object,
        context_packages: list[ContextPackage],
    ) -> None:
        """Run AI co-pilot analysis (best-effort, non-fatal)."""
        changes = diff_result.changes  # type: ignore[attr-defined]

        # Filter risky changes for alternatives generation
        risky_changes = [
            c for c in changes if c.change_risk in ("breaking", "potentially_breaking")
        ]

        # Pick the first context package for dependency info (if available)
        context = context_packages[0] if context_packages else None

        # Generate alternatives
        try:
            if risky_changes:
                result.alternatives = copilot.generate_alternatives(  # type: ignore[union-attr]
                    risky_changes, context, result.migration_sql
                )
        except Exception as e:
            logger.warning("Copilot alternatives generation failed: %s", e)

        # Generate rollback
        try:
            result.rollback = copilot.generate_rollback(  # type: ignore[union-attr]
                result.migration_sql, changes
            )
        except Exception as e:
            logger.warning("Copilot rollback generation failed: %s", e)

        # Validate intent
        try:
            result.intent_analysis = copilot.validate_intent(  # type: ignore[union-attr]
                result.migration_sql, changes
            )
        except Exception as e:
            logger.warning("Copilot intent validation failed: %s", e)
