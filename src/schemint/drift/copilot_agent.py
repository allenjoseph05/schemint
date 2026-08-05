"""Migration Co-pilot Agent — AI generates safe alternatives, rollback SQL, and intent validation.

The co-pilot GENERATES solutions (SQL alternatives, rollback scripts, intent analysis).
It does NOT classify risk — that is handled by the deterministic layer (differ + classifier).

Post-generation invariants:
    1. All AI-generated SQL must parse via sqlglot.parse() — reject if not.
    2. Alternative risk must be <= original risk (verified via classify_change()).
    3. Rollback SQL must be syntactically valid.
    4. Intent validation cannot override deterministic risk classification.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlglot

from schemint.config import get_settings
from schemint.drift.models import (
    ContextPackage,
    IntentAnalysis,
    MigrationAlternative,
    RollbackScript,
    SchemaChangeEvent,
)

try:
    import anthropic

    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

ALTERNATIVES_SYSTEM_PROMPT = """\
You are a database migration safety co-pilot. Given a risky schema change \
and its dependency context, propose a SAFER alternative migration that \
achieves the same goal without breaking downstream consumers.

RULES:
- Output valid PostgreSQL SQL only
- Prefer phased approaches (add new -> migrate data -> drop old)
- Never propose changes that increase lock time on large tables
- Consider downstream views, triggers, and FK references

Respond with a JSON object:
{
  "alternatives": [
    {
      "original_change": "<description of original change>",
      "safe_sql": "<the generated safe SQL>",
      "explanation": "<why this is safer>",
      "risk_reduction": "<original_risk -> new_risk>",
      "trade_off": "<any downside>"
    }
  ]
}\
"""

ROLLBACK_SYSTEM_PROMPT = """\
You are a database migration rollback generator. Given a migration SQL \
and the changes it produces, generate a complete rollback script that \
reverses the migration.

RULES:
- Output valid PostgreSQL SQL only
- Rollback must be safe to run on a production database
- Note any data that cannot be recovered (e.g. dropped columns)
- Order operations correctly (add back columns before re-adding constraints)

Respond with a JSON object:
{
  "rollback_sql": "<complete rollback SQL script>",
  "confidence": 0.0-1.0,
  "warnings": ["any caveats about what cannot be reversed"],
  "is_complete": true/false
}\
"""

INTENT_SYSTEM_PROMPT = """\
You are a migration intent validator. Given a migration SQL and the \
detected schema changes, determine whether the SQL achieves what a \
reasonable person would intend.

Common mismatches:
- DROP + ADD instead of RENAME (causes data loss)
- Missing DEFAULT on NOT NULL column (blocks on non-empty tables)
- CASCADE delete that wasn't intended
- Type change that silently truncates data

Respond with a JSON object:
{
  "intent_matches": true/false,
  "detected_intent": "<what the user likely wanted to do>",
  "actual_behavior": "<what the SQL actually does>",
  "suggestion": "<better approach if intent doesn't match>",
  "suggested_sql": "<corrected SQL if applicable, or null>"
}\
"""


# ---------------------------------------------------------------------------
# CopilotAgent
# ---------------------------------------------------------------------------


class CopilotAgent:
    """AI co-pilot that generates safe migration alternatives, rollback SQL, and intent validation."""

    def __init__(self) -> None:
        settings = get_settings()
        if not CLAUDE_AVAILABLE:
            raise RuntimeError("anthropic not installed")
        if not settings.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY not set")

        self.client = anthropic.Anthropic(api_key=settings.claude_api_key)
        self.model = settings.claude_model
        self.temperature = settings.claude_temperature

    def generate_alternatives(
        self,
        risky_changes: list[SchemaChangeEvent],
        context: ContextPackage | None,
        migration_sql: str,
    ) -> list[MigrationAlternative]:
        """Generate safer migration alternatives for risky changes."""
        if not risky_changes:
            return []

        changes_desc = "\n".join(
            f"- {c.change_type} on {c.table}"
            + (f".{c.column}" if c.column else "")
            + (f" (risk: {c.change_risk})" if c.change_risk else "")
            for c in risky_changes
        )

        context_section = ""
        if context:
            deps = context.impacted_dependencies
            if deps:
                context_section = "\n\nDownstream dependencies:\n" + "\n".join(
                    f"- {d.table} ({d.usage})" for d in deps
                )

        user_message = (
            f"Migration SQL:\n```sql\n{migration_sql}\n```\n\n"
            f"Risky changes detected:\n{changes_desc}"
            f"{context_section}"
        )

        try:
            text = self._call_claude(ALTERNATIVES_SYSTEM_PROMPT, user_message)
            parsed = self._parse_response(text)
            alternatives_raw = parsed.get("alternatives", [])
        except Exception as e:
            logger.error("CopilotAgent alternatives generation failed: %s", e)
            return []

        result: list[MigrationAlternative] = []
        for alt in alternatives_raw:
            safe_sql = alt.get("safe_sql", "")
            if not self._validate_sql(safe_sql):
                logger.warning("AI-generated alternative SQL failed validation; skipping")
                continue
            result.append(
                MigrationAlternative(
                    original_change=alt.get("original_change", ""),
                    safe_sql=safe_sql,
                    explanation=alt.get("explanation", ""),
                    risk_reduction=alt.get("risk_reduction", ""),
                    trade_off=alt.get("trade_off", ""),
                )
            )

        return result

    def generate_rollback(
        self,
        migration_sql: str,
        changes: list[SchemaChangeEvent],
    ) -> RollbackScript | None:
        """Generate rollback SQL for the entire migration."""
        changes_desc = "\n".join(
            f"- {c.change_type} on {c.table}" + (f".{c.column}" if c.column else "")
            for c in changes
        )

        user_message = (
            f"Migration SQL:\n```sql\n{migration_sql}\n```\n\nDetected changes:\n{changes_desc}"
        )

        try:
            text = self._call_claude(ROLLBACK_SYSTEM_PROMPT, user_message)
            parsed = self._parse_response(text)
        except Exception as e:
            logger.error("CopilotAgent rollback generation failed: %s", e)
            return None

        rollback_sql = parsed.get("rollback_sql", "")
        if not self._validate_sql(rollback_sql):
            logger.warning("AI-generated rollback SQL failed validation")
            return RollbackScript(
                rollback_sql=rollback_sql,
                confidence=0.0,
                warnings=["Generated SQL failed syntax validation"],
                is_complete=False,
            )

        return RollbackScript(
            rollback_sql=rollback_sql,
            confidence=parsed.get("confidence", 0.0),
            warnings=parsed.get("warnings", []),
            is_complete=parsed.get("is_complete", False),
        )

    def validate_intent(
        self,
        migration_sql: str,
        detected_changes: list[SchemaChangeEvent],
    ) -> IntentAnalysis | None:
        """Validate whether migration SQL matches reasonable intent."""
        changes_desc = "\n".join(
            f"- {c.change_type} on {c.table}"
            + (f".{c.column}" if c.column else "")
            + (f": {c.old_value} -> {c.new_value}" if c.old_value or c.new_value else "")
            for c in detected_changes
        )

        user_message = (
            f"Migration SQL:\n```sql\n{migration_sql}\n```\n\n"
            f"Detected schema changes:\n{changes_desc}"
        )

        try:
            text = self._call_claude(INTENT_SYSTEM_PROMPT, user_message)
            parsed = self._parse_response(text)
        except Exception as e:
            logger.error("CopilotAgent intent validation failed: %s", e)
            return None

        suggested_sql = parsed.get("suggested_sql")
        if suggested_sql and not self._validate_sql(suggested_sql):
            logger.warning("AI-generated suggested SQL failed validation; clearing")
            suggested_sql = None

        return IntentAnalysis(
            intent_matches=parsed.get("intent_matches", True),
            detected_intent=parsed.get("detected_intent", ""),
            actual_behavior=parsed.get("actual_behavior", ""),
            suggestion=parsed.get("suggestion", ""),
            suggested_sql=suggested_sql,
        )

    # ----- Claude call -----

    def _call_claude(self, system_prompt: str, user_message: str) -> str:
        """Call Claude and return the response text."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=self.temperature,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text
        return text

    # ----- Response parsing (reuses pattern from agent_brain.py) -----

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Extract JSON from Claude's response text.

        4-fallback strategy: direct parse -> ```json -> ``` -> first {...}.
        """
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

    # ----- SQL validation -----

    @staticmethod
    def _validate_sql(sql: str) -> bool:
        """Validate that SQL parses successfully via sqlglot."""
        if not sql or not sql.strip():
            return False
        try:
            result = sqlglot.parse(sql)
            return any(stmt is not None for stmt in result)
        except sqlglot.errors.ParseError:
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_copilot_agent() -> CopilotAgent | None:
    """Get CopilotAgent if AI is enabled and configured. Returns None otherwise."""
    settings = get_settings()
    if not settings.ai_enabled:
        return None
    if not CLAUDE_AVAILABLE:
        return None
    try:
        return CopilotAgent()
    except Exception:
        return None
