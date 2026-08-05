"""Full snapshot, dependency, judgment, and planning pipeline adapter."""

from __future__ import annotations

from evals.adapters.base import EvalAdapter, prompt_hash
from evals.core.keys import make_key, normalize_name
from evals.core.models import EvalAnalysis, RunConfig, Truth, max_risk, severity_index
from evals.core.suites import SuiteDefinition
from schemint.config import get_settings
from schemint.drift.agent_brain import DRIFT_AGENT_SYSTEM_PROMPT, get_drift_agent
from schemint.drift.alter_applier import AlterApplier
from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import ContextPackage, SchemaSnapshot
from schemint.drift.planning_agent import PLANNING_AGENT_SYSTEM_PROMPT, get_planning_agent
from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture


class DriftPipelineAdapter(EvalAdapter):
    """Run the richer production drift pipeline over a migration prediction."""

    name = "drift_pipeline"

    def analyze(self, suite: SuiteDefinition) -> EvalAnalysis:
        schema_sql = suite.schema_sql()
        baseline = DDLSnapshotCapture().capture(schema_sql)
        predicted = AlterApplier().apply(baseline, suite.migration_sql())
        diff = SchemaDiffer().diff(baseline, predicted)

        builder = DependencyGraphBuilder()
        edges = builder.from_fk_constraints(baseline)
        edges.extend(builder.from_schema_views(baseline))
        edges.extend(builder.from_trigger_definitions(baseline))
        graph = builder.build(edges)
        contexts = ContextAssembler().assemble_all(diff, graph, baseline)

        agent = get_drift_agent()
        planner = get_planning_agent()
        if agent is None or planner is None:
            raise RuntimeError("drift pipeline requires Anthropic SDK and CLAUDE_API_KEY")

        decisions = [agent.judge(context) for context in contexts]
        plans = [
            planner.plan(decision, context)
            for decision, context in zip(decisions, contexts, strict=True)
        ]
        severities = [decision.severity for decision in decisions]
        severity = max(severities, key=severity_index) if severities else None
        blocked = any(
            "block_deploy" in decision.recommended_action_categories
            or any(step.action == "block_deploy" for step in plan.plan)
            for decision, plan in zip(decisions, plans, strict=True)
        )

        return EvalAnalysis(
            risk=max_risk([change.change_risk or "safe" for change in diff.changes]),
            severity=severity,
            blast_radius=_blast_radius(contexts, baseline),
            blocked=blocked,
            rationale="\n".join(reason for decision in decisions for reason in decision.rationale),
            predicted_snapshot=predicted.model_dump(mode="json"),
        )

    def config(self, truth: Truth, trial: int) -> RunConfig:
        settings = get_settings()
        return RunConfig(
            adapter=self.name,
            adapter_version=self.version,
            model_id=settings.claude_model,
            prompt_version=prompt_hash(
                DRIFT_AGENT_SYSTEM_PROMPT,
                PLANNING_AGENT_SYSTEM_PROMPT,
            ),
            temperature=settings.claude_temperature,
            trial=trial,
            generator_version=truth.generator_version,
        )


def _blast_radius(contexts: list[ContextPackage], schema: SchemaSnapshot) -> list[str]:
    names = {
        normalize_name(item.table) for context in contexts for item in context.impacted_dependencies
    }
    keys: set[str] = set()
    for name in names:
        if name in schema.views:
            keys.add(make_key("view", name))
        elif name in schema.materialized_views:
            keys.add(make_key("matview", name))
        elif name in schema.triggers:
            keys.add(make_key("trigger", name))
        elif name in schema.functions:
            keys.add(make_key("function", name))
        else:
            keys.add(make_key("table", name))
    return sorted(keys)
