"""Normalization shared by the deterministic and co-pilot sandbox adapters."""

from __future__ import annotations

from typing import cast

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

    return EvalAnalysis(
        risk=risk,
        # CopilotResult exposes only downstream counts, not dependency identities.
        # An empty set records that product limitation instead of fabricating keys.
        blast_radius=[],
        blocked=risk == "breaking",
        safety_score=result.safety_score,
        rationale="\n".join(rationale),
        rollback_sql=result.rollback.rollback_sql if result.rollback else None,
        alternative_sqls=[alternative.safe_sql for alternative in result.alternatives],
        predicted_snapshot=predicted.model_dump(mode="json"),
    )
