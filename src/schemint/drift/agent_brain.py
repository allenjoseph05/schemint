"""Phase 3: DriftAgent — safety-focused severity judgment.

The agent computes a deterministic severity floor BEFORE calling Claude,
then applies post-AI invariants that the LLM cannot weaken.

Core principle: the LLM may escalate but NEVER undercut deterministic
blast-radius signals.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from schemint.config import get_settings
from schemint.drift.models import AgentDecision, ContextPackage

# Try to import anthropic SDK
try:
    import anthropic

    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Severity levels ordered low → critical
_SEVERITY_ORDER = ["low", "medium", "high", "critical"]

# Priority order for category truncation (highest first)
_CATEGORY_PRIORITY = [
    "block_deploy",
    "notify_owner",
    "backward_compatibility",
    "downstream_updates",
    "monitor_only",
]

# ---------------------------------------------------------------------------
# System Prompt — goal ownership
# ---------------------------------------------------------------------------

DRIFT_AGENT_SYSTEM_PROMPT = """\
You are a SAFETY AGENT for database schema drift detection. Your job is to \
judge the severity and risk of a schema change given its deterministic context.

YOUR OBJECTIVES (in priority order):
1. Prevent downstream breakage — flag changes that could break consumers
2. Minimize false positives — do not escalate without evidence
3. Escalate under uncertainty — when context is incomplete, err on the side of caution
4. Preserve deployment stability — avoid unnecessary deploy blocks

You are evaluated on RISK MITIGATION QUALITY, not verbosity.

You will receive a ContextPackage containing:
- The schema change event (what changed)
- Impact metrics (downstream blast radius)
- Dependency coverage (how much lineage is known)
- Context quality (complete / partial / insufficient)

Respond with a JSON object matching this schema:
{
    "severity": "low" | "medium" | "high" | "critical",
    "confidence_in_decision": 0.0-1.0,
    "requires_human_review": true/false,
    "rationale": ["reason1", "reason2"],
    "recommended_action_categories": ["category1", "category2"],
    "context_quality": "complete" | "partial" | "insufficient"
}

Valid categories: backward_compatibility, downstream_updates, monitor_only, \
block_deploy, notify_owner (max 3).

IMPORTANT:
- Your severity judgment may be OVERRIDDEN upward by deterministic invariants.
- You may escalate severity above the deterministic floor, never below it.
- If context quality is insufficient, always recommend human review.
- If you are unsure, escalate — false negatives are worse than false positives.

## Examples

Example 1 — Safe: column_dropped, no consumers, tiny table
Context: column_dropped on "audit_log.raw_payload", 0 downstream tables, \
100 rows, coverage 95%, context_quality=complete
Correct output:
{"severity": "low", "confidence_in_decision": 0.9, "requires_human_review": false,
 "rationale": ["No downstream consumers detected", "Table is small (100 rows)", \
"Coverage is high so absence of edges is trustworthy"],
 "recommended_action_categories": ["monitor_only"], "context_quality": "complete"}

Example 2 — Breaking: column type change with active consumers
Context: column_type_changed (integer→varchar) on "orders.status", \
5 downstream views, 2 FK constraints, coverage 80%, context_quality=complete
Correct output:
{"severity": "high", "confidence_in_decision": 0.85, "requires_human_review": true,
 "rationale": ["Type change from integer to varchar is a breaking change for typed consumers", \
"5 downstream views reference this column", "Implicit casts will fail in strict-mode clients"],
 "recommended_action_categories": ["block_deploy", "notify_owner"], "context_quality": "complete"}

Example 3 — Ambiguous: table_dropped but marked deprecated
Context: table_dropped on "legacy_sessions", 0 FK references, \
last_vacuum 6 months ago, coverage 60% (partial), context_quality=partial
Correct output:
{"severity": "medium", "confidence_in_decision": 0.65, "requires_human_review": false,
 "rationale": ["Table appears to be legacy / unused", \
"Coverage is partial — some consumers may be untracked", \
"Dropping even unused tables carries medium risk without full lineage"],
 "recommended_action_categories": ["notify_owner", "monitor_only"], "context_quality": "partial"}

Example 4 — Critical: column_dropped, 50M-row table, 15 consumers, poor coverage
Context: column_dropped on "users.email", 15 downstream tables, 50M rows, \
coverage 30%, context_quality=insufficient
Correct output:
{"severity": "critical", "confidence_in_decision": 0.5, "requires_human_review": true,
 "rationale": ["Dropping a column from a 50M-row table with 15 consumers is very high risk", \
"Coverage is only 30% — many consumers may be untracked", \
"Insufficient context prevents confident safe assessment"],
 "recommended_action_categories": ["block_deploy", "notify_owner"], \
"context_quality": "insufficient"}\
"""


# ---------------------------------------------------------------------------
# DriftAgent
# ---------------------------------------------------------------------------


class DriftAgent:
    """Safety-focused severity judgment agent (Phase 3).

    Flow:
    1. Compute deterministic severity floor from ImpactMetrics.criticality
    2. Call Claude for nuanced judgment
    3. Apply post-AI invariants (cannot be weakened)
    4. Return AgentDecision
    """

    def __init__(self) -> None:
        settings = get_settings()

        if not CLAUDE_AVAILABLE:
            raise RuntimeError("anthropic not installed. Install with: pip install anthropic")
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY not set.")

        self.client = anthropic.Anthropic(api_key=settings.claude_api_key)
        self.model = settings.claude_model

    def judge(self, context: ContextPackage) -> AgentDecision:
        """Judge a schema change and return a safety decision.

        Deterministic fallback if Claude is unavailable.
        """
        severity_floor = self._compute_severity_floor(context)

        try:
            decision = self._call_claude(context)
        except Exception as e:
            logger.error("DriftAgent Claude call failed: %s", e)
            return self._fallback_decision(context)

        return self._enforce_invariants(decision, context, severity_floor)

    # ----- deterministic severity floor -----

    def _compute_severity_floor(self, context: ContextPackage) -> str:
        """Deterministic severity floor from impact metrics."""
        return context.impact_metrics.criticality

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

    # ----- Claude call -----

    def _call_claude(self, context: ContextPackage) -> AgentDecision:
        """Call Claude for nuanced severity judgment."""
        memory_section = self._build_memory_section(context)
        user_message = (
            "Analyze this schema change context and provide your severity judgment.\n\n"
            "Think step by step before writing your JSON:\n"
            "1. What type of change is this, and what is its baseline risk level?\n"
            "2. How many downstream consumers are affected, and how critical are they?\n"
            "3. Is the dependency coverage sufficient to trust the absence of consumers?\n"
            "4. Does the context quality allow a confident decision, or are there gaps?\n"
            "5. Given the above, what is the severity and does it require human review?\n\n"
            "After your reasoning, output the JSON object.\n\n"
            f"CONTEXT PACKAGE:\n{context.model_dump_json(indent=2)}"
            f"{memory_section}"
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": DRIFT_AGENT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract JSON from response
        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        parsed = self._parse_response(text)
        return AgentDecision(**parsed)

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Extract JSON from Claude's response text."""
        # Try direct parse first
        text = text.strip()
        if text.startswith("{"):
            return json.loads(text)  # type: ignore[no-any-return]
        # Try to find JSON block in markdown
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())  # type: ignore[no-any-return]
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            return json.loads(text[start:end].strip())  # type: ignore[no-any-return]
        # Last resort: find first { ... }
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])  # type: ignore[no-any-return]

    # ----- invariant enforcement -----

    def _enforce_invariants(
        self,
        decision: AgentDecision,
        context: ContextPackage,
        severity_floor: str,
    ) -> AgentDecision:
        """Apply post-AI invariants. These CANNOT be weakened by the LLM."""
        severity: str = decision.severity
        confidence = decision.confidence_in_decision
        requires_human = decision.requires_human_review
        categories: list[str] = list(decision.recommended_action_categories)
        context_quality = decision.context_quality

        # 1. LLM cannot undercut severity floor
        if _sev_index(severity) < _sev_index(severity_floor):
            severity = severity_floor

        # 2. Coverage < 50% → cap confidence at 0.6
        if context.dependency_coverage.coverage_pct < 50.0:
            confidence = min(confidence, 0.6)

        # 3. Critical criticality → severity ≥ high
        if context.impact_metrics.criticality == "critical" and _sev_index(severity) < _sev_index(
            "high"
        ):
            severity = "high"

        # 4. Insufficient context → human review, severity ≥ medium, no backward_compat
        if context_quality == "insufficient":
            requires_human = True
            if _sev_index(severity) < _sev_index("medium"):
                severity = "medium"
            if "backward_compatibility" in categories:
                categories.remove("backward_compatibility")

        # 5. Low severity → no block_deploy
        if severity == "low" and "block_deploy" in categories:
            categories.remove("block_deploy")

        # 6. Human review → add notify_owner if missing
        if requires_human and "notify_owner" not in categories:
            categories.append("notify_owner")

        # 7. Confidence < 0.5 → requires human review
        if confidence < 0.5:
            requires_human = True
            # Re-check notify_owner after setting human review
            if "notify_owner" not in categories:
                categories.append("notify_owner")

        # 8. Critical severity → add block_deploy if missing
        if severity == "critical" and "block_deploy" not in categories:
            categories.append("block_deploy")

        # 9. monitor_only + block_deploy → remove monitor_only (incompatible)
        if "monitor_only" in categories and "block_deploy" in categories:
            categories.remove("monitor_only")

        # 10. Cap categories at 3 (truncate by priority)
        if len(categories) > 3:
            categories = _truncate_categories(categories, 3)

        # 11. Confidence re-scoring: count how many invariants changed the decision.
        #     If Claude was significantly corrected, its stated confidence is too high.
        corrections = _count_corrections(decision, severity, confidence, requires_human, categories)
        if corrections >= 3:
            # Heavily corrected — Claude was significantly wrong; reduce confidence
            penalty = min(0.3, (corrections - 2) * 0.05)
            confidence = round(max(0.0, confidence - penalty), 3)
            logger.debug(
                "Confidence re-scored: %d invariants fired, penalty=%.2f → confidence=%.3f",
                corrections,
                penalty,
                confidence,
            )

        return AgentDecision(
            severity=severity,  # type: ignore[arg-type]
            confidence_in_decision=confidence,
            requires_human_review=requires_human,
            rationale=decision.rationale,
            recommended_action_categories=categories,  # type: ignore[arg-type]
            context_quality=context_quality,
        )

    # ----- fallback -----

    def _fallback_decision(self, context: ContextPackage) -> AgentDecision:
        """Deterministic fallback when Claude is unavailable."""
        return AgentDecision(
            severity=context.impact_metrics.criticality,
            confidence_in_decision=0.0,
            requires_human_review=True,
            rationale=["AI service unavailable — using deterministic fallback"],
            recommended_action_categories=["notify_owner"],
            context_quality=context.context_quality,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sev_index(severity: str) -> int:
    """Return numeric index for severity comparison."""
    try:
        return _SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0


def _count_corrections(
    original: AgentDecision,
    final_severity: str,
    final_confidence: float,
    final_human_review: bool,
    final_categories: list[str],
) -> int:
    """Count how many post-AI invariants materially changed Claude's output.

    Each dimension that changed counts as one correction.
    Used for confidence re-scoring — high correction count → lower confidence.
    """
    corrections = 0
    if original.severity != final_severity:
        corrections += 1
    if abs(original.confidence_in_decision - final_confidence) > 0.05:
        corrections += 1
    if original.requires_human_review != final_human_review:
        corrections += 1
    original_cats = set(original.recommended_action_categories)
    final_cats = set(final_categories)
    if original_cats != final_cats:
        corrections += 1
    return corrections


def _truncate_categories(categories: list[str], max_count: int) -> list[str]:
    """Keep only the highest-priority categories."""
    # Sort by priority (lower index = higher priority)
    sorted_cats = sorted(
        categories,
        key=lambda c: (
            _CATEGORY_PRIORITY.index(c) if c in _CATEGORY_PRIORITY else len(_CATEGORY_PRIORITY)
        ),
    )
    return sorted_cats[:max_count]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_drift_agent() -> DriftAgent | None:
    """Get DriftAgent if available and configured."""
    settings = get_settings()
    if not settings.ai_enabled:
        return None
    if not CLAUDE_AVAILABLE:
        return None
    try:
        return DriftAgent()
    except Exception:
        return None
