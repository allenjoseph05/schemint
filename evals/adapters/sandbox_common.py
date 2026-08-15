"""Normalization shared by the deterministic and co-pilot sandbox adapters."""

from __future__ import annotations

from typing import cast

from evals.core.keys import make_key, normalize_name
from evals.core.models import EvalAnalysis, RiskLevel
from evals.core.suites import SuiteDefinition
from schemint.drift.alter_applier import AlterApplier
from schemint.drift.sandbox import MigrationSandbox
from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture


def analyze_with_sandbox(suite: SuiteDefinition, *, run_copilot: bool) -> EvalAnalysis:
    """Run the production sandbox and normalize its public result."""
    schema_sql = suite.schema_sql()
    migration_sql = suite.migration_sql()
    baseline = DDLSnapshotCapture().capture(schema_sql)
    predicted = AlterApplier().apply(baseline, migration_sql)
    result = MigrationSandbox().analyze(
        migration_sql=migration_sql,
        baseline_snapshot=baseline,
        run_copilot=run_copilot,
    )
    if result.status != "ok":
        raise RuntimeError(result.error_message or "migration sandbox returned an error")
    if run_copilot and not result.copilot_available:
        raise RuntimeError("sandbox co-pilot requires Anthropic SDK and CLAUDE_API_KEY")
    risk = cast(RiskLevel, result.overall_risk)

    rationale = [*result.action_recommendations]
    rationale.extend(warning.message for warning in result.warnings)
    if result.intent_analysis and result.intent_analysis.suggestion:
        rationale.append(result.intent_analysis.suggestion)

    blast_radius: set[str] = set()
    for change in result.predicted_changes:
        for downstream_name in change.downstream_objects:
            name = normalize_name(downstream_name)
            if name in baseline.views:
                blast_radius.add(make_key("view", name))
            elif name in baseline.materialized_views:
                blast_radius.add(make_key("matview", name))
            elif name in baseline.triggers:
                blast_radius.add(make_key("trigger", name))
            elif name in baseline.functions:
                blast_radius.add(make_key("function", name))
            elif name in baseline.tables:
                matching_fks = [
                    fk
                    for fk in baseline.tables[name].foreign_keys
                    if _fk_field(fk, "references_table") == change.table
                    and (
                        change.column is None
                        or _fk_field(fk, "references_column") == change.column
                    )
                ]
                if matching_fks:
                    blast_radius.update(
                        make_key("foreign_key", fk_name)
                        for fk in matching_fks
                        if (fk_name := _fk_field(fk, "name"))
                    )
                else:
                    blast_radius.add(make_key("table", name))

    return EvalAnalysis(
        risk=risk,
        blast_radius=sorted(blast_radius),
        blocked=risk == "breaking",
        escalated=risk == "needs_review",
        safety_score=result.safety_score,
        rationale="\n".join(rationale),
        rollback_sql=result.rollback.rollback_sql if result.rollback else None,
        alternative_sqls=[alternative.safe_sql for alternative in result.alternatives],
        predicted_snapshot=predicted.model_dump(mode="json"),
    )


def _fk_field(foreign_key: object, name: str) -> str:
    if isinstance(foreign_key, dict):
        return str(foreign_key.get(name, ""))
    return str(getattr(foreign_key, name, ""))
