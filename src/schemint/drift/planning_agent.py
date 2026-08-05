"""Phase 4: PlanningAgent — constrained plan generation.

Receives an AgentDecision (Phase 3 output) and produces an ExecutionPlan
using only actions from the scoped registry (filtered by the decision's
recommended categories).

Short-circuits to notification-only plan when human review is required.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from schemint.config import get_settings
from schemint.drift.action_templates import (
    get_templates_for_categories,
)
from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    ExecutionPlan,
    PlanStep,
)

# Try to import anthropic SDK
try:
    import anthropic

    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Backward-compatibility structural actions — forbidden when context is insufficient
_BACKWARD_COMPAT_STRUCTURAL = {
    "add_column_alias",
    "add_default_value",
    "create_migration_view",
}

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

PLANNING_AGENT_SYSTEM_PROMPT = """\
You are a PLANNING AGENT for database schema drift remediation. Given a \
severity decision and a set of ALLOWED actions, produce an execution plan.

You will receive:
1. The AgentDecision (severity, categories, rationale)
2. The ContextPackage (what changed, impact metrics)
3. The ALLOWED action templates (you may ONLY use these action IDs)

Respond with a JSON object:
{{
    "plan": [
        {{
            "step": 1,
            "action": "<action_id from allowed templates>",
            "target": "<specific table/column/model affected>",
            "notes": "<brief explanation>",
            "reversible": true/false
        }}
    ]
}}

RULES:
- You may ONLY use action IDs from the allowed templates list.
- Keep plans concise — typically 2-5 steps.
- Order steps logically (notifications first, then structural, then enforcement last).
- Set reversible=false only for blocking/enforcement actions.
- Do NOT invent new action IDs.\
"""

_CRITIQUE_SYSTEM_PROMPT = """\
You are a PLAN REVIEWER for database schema drift remediation. \
Review an execution plan for correctness and completeness.

Given the severity decision and the plan, identify any problems. \
Return a JSON array of problem strings (max 3). Return [] if the plan is correct.

Example of a good response: []
Example of a problem response: ["block_deploy step is missing for critical severity", \
"notify_table_owner should come before structural changes"]

Be concise. Only flag real problems, not stylistic preferences.\
"""


# ---------------------------------------------------------------------------
# PlanningAgent
# ---------------------------------------------------------------------------


class PlanningAgent:
    """Constrained plan generator (Phase 4).

    Flow:
    1. Short-circuit if human review required → notification-only plan
    2. Scope registry to allowed categories
    3. Call Claude with scoped templates
    4. Apply post-generation invariants
    5. Return ExecutionPlan
    """

    def __init__(self) -> None:
        settings = get_settings()

        if not CLAUDE_AVAILABLE:
            raise RuntimeError("anthropic not installed. Install with: pip install anthropic")
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY not set.")

        self.client = anthropic.Anthropic(api_key=settings.claude_api_key)
        self.model = settings.claude_model
        self.critique_model = settings.claude_model_simple  # Haiku for critique
        self.enable_critique = settings.enable_plan_critique
        self.temperature = settings.claude_temperature

    def plan(
        self,
        decision: AgentDecision,
        context: ContextPackage,
    ) -> ExecutionPlan:
        """Generate an execution plan from a Phase 3 decision.

        Short-circuits to notification-only when human review is required.
        """
        # Short-circuit: human review → notification-only, no Claude call
        if decision.requires_human_review:
            return self._notification_only_plan(decision, context)

        # Scope registry to allowed categories
        cats: list[str] = list(decision.recommended_action_categories)
        scoped_templates = get_templates_for_categories(cats)
        if not scoped_templates:
            return self._notification_only_plan(decision, context)

        try:
            steps = self._call_claude(decision, context, scoped_templates)
        except Exception as e:
            logger.error("PlanningAgent Claude call failed: %s", e)
            return self._fallback_plan(decision, context)

        execution_plan = self._post_process(steps, decision, context, scoped_templates)

        # Optional plan critique (gated by SCHEMINT_ENABLE_PLAN_CRITIQUE)
        if self.enable_critique:
            warnings = self._critique_plan(execution_plan, decision)
            if warnings:
                execution_plan = execution_plan.model_copy(update={"warnings": warnings})

        return execution_plan

    # ----- short-circuit plan -----

    def _notification_only_plan(
        self,
        decision: AgentDecision,
        context: ContextPackage,
    ) -> ExecutionPlan:
        """Notification-only plan when human review is required."""
        steps = [
            PlanStep(
                step=1,
                action="notify_table_owner",
                target=context.schema_change.table,
                notes="Human review required — notification-only plan",
                reversible=True,
            ),
        ]
        return ExecutionPlan(
            plan=steps,
            requires_execution_approval=True,
            source_severity=decision.severity,
            source_requires_human_review=decision.requires_human_review,
        )

    # ----- Claude call -----

    def _call_claude(
        self,
        decision: AgentDecision,
        context: ContextPackage,
        scoped_templates: list[Any],
    ) -> list[PlanStep]:
        """Call Claude with scoped action templates."""
        templates_desc = "\n".join(
            f"  - {t.action_id}: {t.description} (category={t.category}, reversible={t.reversible})"
            for t in scoped_templates
        )

        memory_section = self._build_memory_section(context)
        user_message = (
            "Generate an execution plan for this schema drift.\n\n"
            f"DECISION:\n{decision.model_dump_json(indent=2)}\n\n"
            f"CONTEXT:\n{context.model_dump_json(indent=2)}\n\n"
            f"ALLOWED ACTIONS (use ONLY these action_ids):\n{templates_desc}"
            f"{memory_section}"
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=self.temperature,
            system=[
                {
                    "type": "text",
                    "text": PLANNING_AGENT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        parsed = self._parse_response(text)
        return [PlanStep(**s) for s in parsed.get("plan", [])]

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Extract JSON from Claude's response text."""
        text = text.strip()
        if text.startswith("{"):
            return json.loads(text)  # type: ignore[no-any-return]
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())  # type: ignore[no-any-return]
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            return json.loads(text[start:end].strip())  # type: ignore[no-any-return]
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])  # type: ignore[no-any-return]

    # ----- memory section -----

    @staticmethod
    def _build_memory_section(context: ContextPackage) -> str:
        """Format memory context into a text section for the Claude prompt.

        Returns empty string if no memory context is available.
        """
        mem = context.memory_context
        if mem is None:
            return ""

        parts: list[str] = ["\n\nAGENT MEMORY (from previous runs):"]

        if mem.accepted_findings:
            parts.append("\nPreviously accepted findings (do not re-flag):")
            for finding in mem.accepted_findings:
                parts.append(f"  - {finding}")

        if mem.business_rules:
            parts.append("\nBusiness rules to respect:")
            for rule in mem.business_rules:
                parts.append(f"  - {rule}")

        if mem.schema_semantics:
            parts.append("\nSchema semantics:")
            for key, value in mem.schema_semantics.items():
                parts.append(f"  - {key}: {value}")

        if mem.table_change_frequency:
            parts.append("\nTable change frequency (last 90 days):")
            for table, count in mem.table_change_frequency.items():
                parts.append(f"  - {table}: {count} changes")

        return "\n".join(parts) if len(parts) > 1 else ""

    # ----- post-processing -----

    def _post_process(
        self,
        steps: list[PlanStep],
        decision: AgentDecision,
        context: ContextPackage,
        scoped_templates: list[Any],
    ) -> ExecutionPlan:
        """Apply deterministic invariants after Claude generates the plan."""
        scoped_ids = {t.action_id for t in scoped_templates}

        # 1. Filter unknown actions (not in scoped registry)
        steps = [s for s in steps if s.action in scoped_ids]

        # 2. Low severity → no block_deploy or require_migration_review
        if decision.severity == "low":
            steps = [
                s for s in steps if s.action not in ("block_deploy", "require_migration_review")
            ]

        # 3. Insufficient context → no backward_compat structural actions
        if decision.context_quality == "insufficient":
            steps = [s for s in steps if s.action not in _BACKWARD_COMPAT_STRUCTURAL]

        # 4. Critical severity → must include block_deploy
        if decision.severity == "critical":
            has_block = any(s.action == "block_deploy" for s in steps)
            if not has_block:
                steps.append(
                    PlanStep(
                        step=999,  # will be renumbered
                        action="block_deploy",
                        target=context.schema_change.table,
                        notes="Injected: critical severity requires deploy block",
                        reversible=False,
                    )
                )

        # 5. Renumber steps sequentially
        for i, step in enumerate(steps, 1):
            step.step = i

        # 6. Determine requires_execution_approval
        requires_approval = any(
            s.action in ("block_deploy", "require_migration_review") for s in steps
        )

        return ExecutionPlan(
            plan=steps,
            requires_execution_approval=requires_approval,
            source_severity=decision.severity,
            source_requires_human_review=decision.requires_human_review,
        )

    # ----- plan critique -----

    def _critique_plan(
        self,
        plan: ExecutionPlan,
        decision: AgentDecision,
    ) -> list[str]:
        """Run a lightweight Haiku critique pass on the generated plan.

        Returns a list of problem strings (empty list = plan is fine).
        Failures are silently swallowed — critique is best-effort.
        """
        try:
            user_message = (
                f"Review this execution plan for severity={decision.severity}:\n\n"
                f"PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
                f"DECISION:\n{decision.model_dump_json(indent=2)}\n\n"
                "Return a JSON array of problems. Return [] if the plan is correct."
            )
            response = self.client.messages.create(
                model=self.critique_model,
                max_tokens=256,
                temperature=self.temperature,
                system=_CRITIQUE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            text = text.strip()
            # Extract JSON array
            if "[" in text:
                start = text.index("[")
                end = text.rindex("]") + 1
                problems = json.loads(text[start:end])
                if isinstance(problems, list):
                    return [str(p) for p in problems[:3]]
        except Exception as exc:
            logger.debug("Plan critique failed (non-fatal): %s", exc)
        return []

    # ----- fallback -----

    def _fallback_plan(
        self,
        decision: AgentDecision,
        context: ContextPackage,
    ) -> ExecutionPlan:
        """Deterministic fallback when Claude is unavailable."""
        steps = [
            PlanStep(
                step=1,
                action="notify_table_owner",
                target=context.schema_change.table,
                notes="AI service unavailable — fallback notification plan",
                reversible=True,
            ),
        ]

        # Critical → inject block_deploy
        if decision.severity == "critical":
            steps.append(
                PlanStep(
                    step=2,
                    action="block_deploy",
                    target=context.schema_change.table,
                    notes="Critical severity — deploy block required",
                    reversible=False,
                )
            )

        return ExecutionPlan(
            plan=steps,
            requires_execution_approval=decision.severity == "critical",
            source_severity=decision.severity,
            source_requires_human_review=decision.requires_human_review,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_planning_agent() -> PlanningAgent | None:
    """Get PlanningAgent if available and configured."""
    settings = get_settings()
    if not settings.ai_enabled:
        return None
    if not CLAUDE_AVAILABLE:
        return None
    try:
        return PlanningAgent()
    except Exception:
        return None
