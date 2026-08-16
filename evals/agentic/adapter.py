"""Real production AgentAnalyzer adapter with evaluation-only fault injection."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from evals.agentic.models import (
    AgentEvalAnalysis,
    AgentFinding,
    AgentRunConfig,
    AgentTraceEvent,
    ToolFault,
)
from evals.agentic.suites import AgentSuiteDefinition
from schemint.config import get_settings
from schemint.core.context.models import ProjectContext
from schemint.core.parser.sql_parser import parse_sql
from schemint.services.agent import (
    AGENT_SYSTEM_PROMPT,
    GET_SCHEMA_OVERVIEW_TOOL,
    INSPECT_TABLE_TOOL,
    SUBMIT_ANALYSIS_TOOL,
    AgentAnalyzer,
)
from schemint.services.claude import select_model


class FaultInjectingAgentAnalyzer(AgentAnalyzer):
    """Production agent with deterministic task-declared tool-result mutation.

    This subclass lives only in the evaluation package. Production tools remain
    unchanged; adversarial tasks can nevertheless test what happens when a tool
    result is poisoned, unavailable, or contains attacker-controlled text.
    """

    def __init__(self, faults: list[ToolFault], trace_observer: Any) -> None:
        super().__init__(trace_observer=trace_observer)
        self._faults = faults
        self._tool_occurrences: dict[str, int] = defaultdict(int)

    def _execute_tool(self, block: Any, schema: Any, pre_analysis: Any) -> str:
        result = super()._execute_tool(block, schema, pre_analysis)
        self._tool_occurrences[block.name] += 1
        occurrence = self._tool_occurrences[block.name]
        for fault in self._faults:
            if fault.tool_name != block.name or fault.occurrence != occurrence:
                continue
            if fault.mode == "replace":
                return fault.text
            if fault.mode == "error":
                return f"EVALUATION_TOOL_ERROR: {fault.text}"
            return f"{result}\n\n{fault.text}"
        return result


class AgentAnalyzerEvalAdapter:
    """Execute one schema-review suite through the genuine multi-turn loop."""

    name = "agent_analyzer"
    version = "1"

    def analyze(self, suite: AgentSuiteDefinition) -> AgentEvalAnalysis:
        task = suite.record.task
        schema = parse_sql(suite.schema_sql(), database_type=task.database_type)
        project_context = (
            ProjectContext.model_validate(task.project_context) if task.project_context else None
        )
        trace: list[AgentTraceEvent] = []

        def observe(payload: dict[str, Any]) -> None:
            trace.append(AgentTraceEvent.model_validate(payload))

        agent = FaultInjectingAgentAnalyzer(task.tool_faults, observe)
        result = agent.analyze(
            schema,
            app_type=task.app_type,
            project_context=project_context,
            memory_context=task.memory_context,
        )
        findings = [AgentFinding.model_validate(item) for item in result.get("findings", [])]
        completed_events = [event for event in trace if event.event == "run_completed"]
        turns = max((event.turn or 0 for event in trace), default=0)
        return AgentEvalAnalysis(
            findings=findings,
            suppressed=list(result.get("suppressed", [])),
            score=result.get("score") if isinstance(result.get("score"), dict) else None,
            summary=str(result.get("summary", "")),
            recommendations=list(result.get("recommendations", [])),
            trace=trace,
            completed=bool(completed_events) and not result.get("error"),
            terminal_tool="submit_analysis" if completed_events else None,
            turns=turns,
            error=str(result["error"]) if result.get("error") else None,
        )

    def config(self, suite: AgentSuiteDefinition, trial: int) -> AgentRunConfig:
        task = suite.record.task
        schema = parse_sql(suite.schema_sql(), database_type=task.database_type)
        settings = get_settings()
        return AgentRunConfig(
            adapter=self.name,
            adapter_version=self.version,
            model_id=select_model(schema),
            prompt_version=_agent_contract_hash(),
            temperature=settings.claude_temperature,
            max_turns=settings.claude_max_agent_turns,
            trial=trial,
            truth_version=suite.record.truth.truth_version,
        )


def _agent_contract_hash() -> str:
    """Hash the system prompt and all tool schemas that constrain behavior."""
    digest = hashlib.sha256()
    digest.update(AGENT_SYSTEM_PROMPT.encode())
    for tool in (GET_SCHEMA_OVERVIEW_TOOL, INSPECT_TABLE_TOOL, SUBMIT_ANALYSIS_TOOL):
        digest.update(b"\0")
        digest.update(json.dumps(tool, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()[:12]
