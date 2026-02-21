"""CopilotService — enriches execution plan steps with AI-generated SQL.

Bridges CopilotAgent (Phase 0-2 tool) with the Phase 4 plan.
Called after PlanningAgent generates a plan; enriches SQLRunner steps
with pre-validated SQL so the executor doesn't need to build it from templates.

Design constraints:
    - Best-effort: failures are silently logged, never re-raised.
    - Non-blocking: if CopilotAgent is unavailable, the original plan is returned.
    - Enrichment only affects SQLRunner actions (structural SQL steps).
    - The CopilotAgent validates generated SQL via sqlglot before returning.
"""

from __future__ import annotations

import logging
from typing import Literal

from schemint.drift.copilot_agent import get_copilot_agent
from schemint.drift.models import ContextPackage, ExecutionPlan, PlanStep, SchemaChangeEvent

logger = logging.getLogger(__name__)

# Actions handled by SQLRunner — only these get SQL enrichment
_SQL_ACTIONS = {
    "add_column_alias",
    "add_default_value",
    "create_migration_view",
}


class CopilotService:
    """Enriches an ExecutionPlan with AI-generated SQL from CopilotAgent.

    Usage:
        service = CopilotService()
        enriched_plan = service.enrich_plan(plan, context)
    """

    def enrich_plan(
        self,
        plan: ExecutionPlan,
        context: ContextPackage,
    ) -> ExecutionPlan:
        """Enrich plan steps with AI-generated SQL where applicable.

        Returns the original plan unchanged if CopilotAgent is unavailable
        or if no steps are SQL-eligible.
        """
        try:
            agent = get_copilot_agent()
            if agent is None:
                return plan
        except Exception as exc:
            logger.debug("CopilotService: agent unavailable (%s) — skipping enrichment", exc)
            return plan

        sql_steps = [s for s in plan.plan if s.action in _SQL_ACTIONS]
        if not sql_steps:
            return plan

        enriched_steps = list(plan.plan)
        enriched_count = 0

        for i, step in enumerate(enriched_steps):
            if step.action not in _SQL_ACTIONS:
                continue

            # Build a synthetic SchemaChangeEvent for this step
            change = _step_to_change_event(step, context)
            if change is None:
                continue

            try:
                alternatives = agent.generate_alternatives(
                    risky_changes=[change],
                    context=context,
                    migration_sql=_describe_step(step),
                )
                rollback_script = agent.generate_rollback(
                    migration_sql=_describe_step(step),
                    changes=[change],
                )
            except Exception as exc:
                logger.warning(
                    "CopilotService: enrichment failed for step %d (%s): %s",
                    step.step,
                    step.action,
                    exc,
                )
                continue

            gen_sql: str | None = None
            rollback_sql: str | None = None

            if alternatives:
                gen_sql = alternatives[0].safe_sql or None

            if rollback_script and rollback_script.is_complete:
                rollback_sql = rollback_script.rollback_sql or None

            if gen_sql or rollback_sql:
                enriched_steps[i] = step.model_copy(
                    update={
                        "generated_sql": gen_sql,
                        "rollback_sql": rollback_sql,
                    }
                )
                enriched_count += 1
                logger.info(
                    "CopilotService: enriched step %d (%s) with AI SQL",
                    step.step,
                    step.action,
                )

        if enriched_count == 0:
            return plan

        logger.info(
            "CopilotService: enriched %d/%d steps",
            enriched_count,
            len(sql_steps),
        )
        return plan.model_copy(update={"plan": enriched_steps})


_ActionChangeType = Literal["column_type_change", "column_default_change", "table_renamed"]

_ACTION_TO_CHANGE: dict[str, _ActionChangeType] = {
    "add_column_alias": "column_type_change",  # alias = exposing column under new type/name
    "add_default_value": "column_default_change",
    "create_migration_view": "table_renamed",
}


def _step_to_change_event(step: PlanStep, context: ContextPackage) -> SchemaChangeEvent | None:
    """Build a SchemaChangeEvent from a PlanStep for the copilot prompt."""
    try:
        change_type: _ActionChangeType | None = _ACTION_TO_CHANGE.get(step.action)
        if change_type is None:
            return None

        table, _, column = step.target.partition(".")
        return SchemaChangeEvent(
            change_type=change_type,
            table=table,
            column=column or None,
            change_risk=context.schema_change.change_risk if context.schema_change else None,
        )
    except Exception:
        return None


def _describe_step(step: PlanStep) -> str:
    """Produce a human-readable SQL description of the step for the copilot."""
    return f"-- Action: {step.action}\n-- Target: {step.target}\n-- Notes: {step.notes}"


def get_copilot_service() -> CopilotService | None:
    """Return a CopilotService instance if AI is available, else None."""
    try:
        if get_copilot_agent() is None:
            return None
        return CopilotService()
    except Exception:
        return None
