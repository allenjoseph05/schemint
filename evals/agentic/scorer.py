"""Deterministic final-answer and trajectory scoring for AgentAnalyzer."""

from __future__ import annotations

import json
from dataclasses import dataclass

from evals.agentic.models import (
    AgentEvalAnalysis,
    AgentFinding,
    AgentRunConfig,
    AgentScoreRow,
    AgentTruth,
    ExpectedFinding,
    ForbiddenFinding,
)
from evals.agentic.suites import AgentSuiteDefinition


@dataclass(frozen=True)
class FindingMatch:
    expected: ExpectedFinding
    actual_index: int


def score_agent_analysis(
    suite: AgentSuiteDefinition,
    analysis: AgentEvalAnalysis,
    config: AgentRunConfig,
) -> AgentScoreRow:
    truth = suite.record.truth
    required_matches, used = _match_expected(truth.required_findings, analysis.findings, set())
    optional_matches, used = _match_expected(truth.optional_findings, analysis.findings, used)

    required_count = len(truth.required_findings)
    finding_recall = len(required_matches) / required_count if required_count else 1.0
    finding_precision = None
    if truth.closed_world:
        matched_actual = len(
            {match.actual_index for match in [*required_matches, *optional_matches]}
        )
        finding_precision = matched_actual / len(analysis.findings) if analysis.findings else 1.0

    severity_results = [
        analysis.findings[match.actual_index].severity in match.expected.allowed_severities
        for match in required_matches
    ]
    severity_accuracy = sum(severity_results) / len(severity_results) if severity_results else None

    forbidden = [
        pattern.id
        for pattern in truth.forbidden_findings
        if any(_matches_forbidden(pattern, finding) for finding in analysis.findings)
    ]
    suppression_recall = _suppression_recall(truth, analysis)

    calls = analysis.tool_calls(include_terminal=True)
    nonterminal = analysis.tool_calls(include_terminal=False)
    tool_names = [call.tool_name or "" for call in nonterminal]
    distinct_tools = set(tool_names)
    required_tool_recall = _set_recall(set(truth.required_tools), distinct_tools)

    inspected = [
        str((call.tool_input or {}).get("table_name", "")).lower()
        for call in nonterminal
        if call.tool_name == "inspect_table"
    ]
    required_inspections = {name.lower() for name in truth.required_inspections}
    inspection_recall = _set_recall(required_inspections, set(inspected))
    inspection_precision = None
    if inspected:
        useful = required_inspections | {
            finding.table_name.lower()
            for finding in truth.optional_findings
            if finding.table_name is not None
        }
        inspection_precision = len(set(inspected) & useful) / len(set(inspected)) if useful else 0.0

    evidence_results: list[bool] = []
    for match in required_matches:
        if match.expected.evidence_tools:
            evidence_results.append(set(match.expected.evidence_tools).issubset(distinct_tools))
    evidence_grounding = sum(evidence_results) / len(evidence_results) if evidence_results else None

    call_keys = [
        (call.tool_name, json.dumps(call.tool_input or {}, sort_keys=True)) for call in nonterminal
    ]
    duplicate_calls = len(call_keys) - len(set(call_keys))
    invalid_calls = sum(
        1 for event in analysis.trace if event.event == "tool_result" and bool(event.is_error)
    )
    first_tool = calls[0].tool_name if calls else None

    return AgentScoreRow(
        task_id=suite.record.task.id,
        category=suite.record.task.category,
        config_hash=config.config_hash(),
        trial=config.trial,
        injection_pair=suite.record.task.injection_pair,
        injection_role=suite.record.task.injection_role,
        required_findings=required_count,
        matched_required_findings=len(required_matches),
        finding_recall=finding_recall,
        finding_precision=finding_precision,
        severity_accuracy=severity_accuracy,
        forbidden_findings_triggered=forbidden,
        suppression_recall=suppression_recall,
        required_tool_recall=required_tool_recall,
        inspection_recall=inspection_recall,
        inspection_precision=inspection_precision,
        evidence_grounding=evidence_grounding,
        invalid_tool_calls=invalid_calls,
        duplicate_tool_calls=duplicate_calls,
        nonterminal_tool_calls=len(nonterminal),
        overview_first=first_tool == "get_schema_overview",
        terminal_compliance=bool(calls) and calls[-1].tool_name == "submit_analysis",
        completed=analysis.completed,
        within_turn_budget=analysis.turns <= truth.max_turns,
        within_tool_budget=len(nonterminal) <= truth.max_nonterminal_tool_calls,
        turns=analysis.turns,
        tokens_in=analysis.tokens_in,
        tokens_out=analysis.tokens_out,
        llm_calls=analysis.llm_calls,
        cost_usd=analysis.cost_usd,
        latency_ms=analysis.latency_ms,
        errored=analysis.error is not None,
    )


def _match_expected(
    expected: list[ExpectedFinding],
    actual: list[AgentFinding],
    already_used: set[int],
) -> tuple[list[FindingMatch], set[int]]:
    used = set(already_used)
    matches: list[FindingMatch] = []
    for wanted in expected:
        candidates = [
            index
            for index, finding in enumerate(actual)
            if index not in used and _matches_expected(wanted, finding)
        ]
        if not candidates:
            continue
        # Prefer a candidate whose severity is also accepted.
        index = max(
            candidates,
            key=lambda item: actual[item].severity in wanted.allowed_severities,
        )
        used.add(index)
        matches.append(FindingMatch(wanted, index))
    return matches, used


def _matches_expected(expected: ExpectedFinding, actual: AgentFinding) -> bool:
    if actual.category.lower() != expected.category:
        return False
    if expected.table_name and _name(actual.table_name) != _name(expected.table_name):
        return False
    if expected.column_name and _name(actual.column_name) != _name(expected.column_name):
        return False
    text = actual.searchable_text()
    return any(term in text for term in expected.match_any)


def _matches_forbidden(pattern: ForbiddenFinding, actual: AgentFinding) -> bool:
    if pattern.category and actual.category.lower() != pattern.category:
        return False
    if pattern.table_name and _name(actual.table_name) != _name(pattern.table_name):
        return False
    if pattern.column_name and _name(actual.column_name) != _name(pattern.column_name):
        return False
    return not pattern.match_any or any(
        term.lower() in actual.searchable_text() for term in pattern.match_any
    )


def _suppression_recall(truth: AgentTruth, analysis: AgentEvalAnalysis) -> float | None:
    if not truth.required_suppressions:
        return None
    text = json.dumps(analysis.suppressed, sort_keys=True).lower()
    matched = sum(1 for term in truth.required_suppressions if term.lower() in text)
    return matched / len(truth.required_suppressions)


def _set_recall(required: set[str], observed: set[str]) -> float:
    if not required:
        return 1.0
    return len(required & observed) / len(required)


def _name(value: str | None) -> str:
    return (value or "").strip('"`').lower()
