"""Validated contracts for schema-review agent evaluation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentTaskCategory = Literal[
    "structural",
    "performance",
    "security",
    "convention",
    "memory",
    "adversarial",
    "clean",
    "scale",
]
FindingCategory = Literal[
    "structural",
    "performance",
    "security",
    "naming",
    "best_practices",
    "domain",
]
FindingSeverity = Literal["suggestion", "warning", "critical"]
InjectionRole = Literal["control", "attack"]
ToolFaultMode = Literal["append", "replace", "error"]

SEVERITY_ORDER: tuple[FindingSeverity, ...] = ("suggestion", "warning", "critical")


def severity_index(value: str) -> int:
    try:
        return SEVERITY_ORDER.index(value)  # type: ignore[arg-type]
    except ValueError:
        return -1


class ToolFault(BaseModel):
    """Evaluation-only mutation applied to one production tool result."""

    tool_name: Literal["get_schema_overview", "inspect_table"]
    mode: ToolFaultMode
    text: str
    occurrence: int = Field(default=1, ge=1)


class AgentTask(BaseModel):
    """One hand-authored schema-review task."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    category: AgentTaskCategory
    notes: str
    schema_file: str
    database_type: Literal["mysql", "postgresql", "sqlite"] = "postgresql"
    app_type: str | None = None
    project_context: dict[str, Any] | None = None
    memory_context: dict[str, Any] | None = None
    injection_pair: str | None = None
    injection_role: InjectionRole | None = None
    tool_faults: list[ToolFault] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pair(self) -> AgentTask:
        if (self.injection_pair is None) != (self.injection_role is None):
            raise ValueError("injection_pair and injection_role must be set together")
        return self


class ExpectedFinding(BaseModel):
    """A semantic finding contract, matched without requiring exact prose."""

    id: str
    category: FindingCategory
    table_name: str | None = None
    column_name: str | None = None
    allowed_severities: list[FindingSeverity] = Field(min_length=1)
    # At least one phrase must occur in title/description/impact/fix/reasoning.
    match_any: list[str] = Field(min_length=1)
    # Evidence requirements are scored after the finding itself is matched.
    evidence_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_terms(self) -> ExpectedFinding:
        self.match_any = [term.strip().lower() for term in self.match_any if term.strip()]
        if not self.match_any:
            raise ValueError("match_any must contain a non-empty phrase")
        return self


class ForbiddenFinding(BaseModel):
    """Finding pattern whose presence is an explicit hallucination/safety failure."""

    id: str
    category: FindingCategory | None = None
    table_name: str | None = None
    column_name: str | None = None
    match_any: list[str] = Field(default_factory=list)


class AgentTruth(BaseModel):
    """Versioned human-reviewed truth for one agent task."""

    task_id: str
    truth_version: str = "agent-v1"
    input_hash: str = ""
    required_findings: list[ExpectedFinding] = Field(default_factory=list)
    optional_findings: list[ExpectedFinding] = Field(default_factory=list)
    forbidden_findings: list[ForbiddenFinding] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=lambda: ["get_schema_overview"])
    required_inspections: list[str] = Field(default_factory=list)
    forbidden_inspections: list[str] = Field(default_factory=list)
    max_nonterminal_tool_calls: int = Field(default=8, ge=0)
    max_turns: int = Field(default=10, ge=1)
    must_complete: bool = True
    required_suppressions: list[str] = Field(default_factory=list)
    closed_world: bool = False


class AgentSuiteRecord(BaseModel):
    """Task and truth kept together in the reviewable corpus catalog."""

    task: AgentTask
    truth: AgentTruth

    @model_validator(mode="after")
    def validate_ids(self) -> AgentSuiteRecord:
        if self.task.id != self.truth.task_id:
            raise ValueError("task.id must equal truth.task_id")
        return self


class AgentFinding(BaseModel):
    """Normalized finding produced through ``submit_analysis``."""

    model_config = ConfigDict(extra="allow")

    severity: str = ""
    category: str = ""
    title: str = ""
    description: str = ""
    table_name: str | None = None
    column_name: str | None = None
    impact: str = ""
    fix_description: str = ""
    fix_script: str | None = None
    reasoning: str = ""

    def searchable_text(self) -> str:
        return " ".join(
            (
                self.title,
                self.description,
                self.impact,
                self.fix_description,
                self.reasoning,
            )
        ).lower()


class AgentTraceEvent(BaseModel):
    """One observable event in an AgentAnalyzer trajectory."""

    model_config = ConfigDict(extra="allow")

    event: str
    turn: int | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    terminal: bool | None = None
    content: str | None = None
    is_error: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class AgentEvalAnalysis(BaseModel):
    """Measured final answer, complete trajectory, and operational usage."""

    findings: list[AgentFinding] = Field(default_factory=list)
    suppressed: list[dict[str, Any]] = Field(default_factory=list)
    score: dict[str, Any] | None = None
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    completed: bool = False
    terminal_tool: str | None = None
    turns: int = 0
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def tool_calls(self, *, include_terminal: bool = True) -> list[AgentTraceEvent]:
        calls = [event for event in self.trace if event.event == "tool_call"]
        if include_terminal:
            return calls
        return [event for event in calls if not event.terminal]


class AgentRunConfig(BaseModel):
    """Identity of every setting expected to affect an agent trajectory."""

    adapter: str = "agent_analyzer"
    adapter_version: str = "1"
    model_id: str
    prompt_version: str
    temperature: float | None
    max_turns: int
    trial: int
    truth_version: str

    def config_hash(self) -> str:
        payload = self.model_dump(exclude={"trial"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class AgentScoreRow(BaseModel):
    """Separate final-answer, trajectory, grounding, and safety scores."""

    task_id: str
    category: AgentTaskCategory
    config_hash: str
    trial: int
    injection_pair: str | None = None
    injection_role: InjectionRole | None = None

    required_findings: int = 0
    matched_required_findings: int = 0
    finding_recall: float = 1.0
    finding_precision: float | None = None
    severity_accuracy: float | None = None
    forbidden_findings_triggered: list[str] = Field(default_factory=list)
    suppression_recall: float | None = None

    required_tool_recall: float = 1.0
    inspection_recall: float = 1.0
    inspection_precision: float | None = None
    evidence_grounding: float | None = None
    invalid_tool_calls: int = 0
    duplicate_tool_calls: int = 0
    nonterminal_tool_calls: int = 0
    overview_first: bool = False
    terminal_compliance: bool = False
    completed: bool = False
    within_turn_budget: bool = False
    within_tool_budget: bool = False
    turns: int = 0

    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    errored: bool = False
