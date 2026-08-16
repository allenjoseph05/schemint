"""Offline trajectory and final-answer scorer tests."""

from pathlib import Path

import pytest

from evals.agentic.models import (
    AgentEvalAnalysis,
    AgentFinding,
    AgentRunConfig,
    AgentSuiteRecord,
    AgentTraceEvent,
)
from evals.agentic.scorer import score_agent_analysis
from evals.agentic.suites import AgentSuiteDefinition


def _suite() -> AgentSuiteDefinition:
    record = AgentSuiteRecord.model_validate(
        {
            "task": {
                "id": "money",
                "category": "performance",
                "notes": "money",
                "schema_file": "schema.sql",
            },
            "truth": {
                "task_id": "money",
                "required_findings": [
                    {
                        "id": "float_money",
                        "category": "performance",
                        "table_name": "invoices",
                        "column_name": "amount",
                        "allowed_severities": ["warning", "critical"],
                        "match_any": ["decimal", "float"],
                        "evidence_tools": ["get_schema_overview", "inspect_table"],
                    }
                ],
                "required_inspections": ["invoices"],
                "closed_world": True,
            },
        }
    )
    return AgentSuiteDefinition(record=record, root=Path("."), schema_path=Path("schema.sql"))


def _config() -> AgentRunConfig:
    return AgentRunConfig(
        model_id="test",
        prompt_version="prompt",
        temperature=0,
        max_turns=10,
        trial=0,
        truth_version="agent-v1",
    )


def test_scores_final_finding_and_tool_trajectory_separately() -> None:
    analysis = AgentEvalAnalysis(
        findings=[
            AgentFinding(
                severity="warning",
                category="performance",
                title="FLOAT used for money",
                description="Use DECIMAL for invoice amounts",
                table_name="invoices",
                column_name="amount",
            )
        ],
        trace=[
            AgentTraceEvent(
                event="tool_call",
                turn=1,
                tool_name="get_schema_overview",
                tool_input={},
                terminal=False,
            ),
            AgentTraceEvent(
                event="tool_result",
                turn=1,
                tool_name="get_schema_overview",
                content="overview",
                is_error=False,
            ),
            AgentTraceEvent(
                event="tool_call",
                turn=2,
                tool_name="inspect_table",
                tool_input={"table_name": "invoices"},
                terminal=False,
            ),
            AgentTraceEvent(
                event="tool_result",
                turn=2,
                tool_name="inspect_table",
                content="details",
                is_error=False,
            ),
            AgentTraceEvent(
                event="tool_call", turn=3, tool_name="submit_analysis", tool_input={}, terminal=True
            ),
        ],
        completed=True,
        terminal_tool="submit_analysis",
        turns=3,
    )
    score = score_agent_analysis(_suite(), analysis, _config())
    assert score.finding_recall == 1
    assert score.finding_precision == 1
    assert score.severity_accuracy == 1
    assert score.evidence_grounding == 1
    assert score.inspection_recall == 1
    assert score.overview_first is True
    assert score.terminal_compliance is True


def test_lucky_answer_does_not_hide_missing_tools() -> None:
    analysis = AgentEvalAnalysis(
        findings=[
            AgentFinding(
                severity="warning",
                category="performance",
                title="Use DECIMAL",
                description="FLOAT money is imprecise",
                table_name="invoices",
                column_name="amount",
            )
        ],
        completed=True,
        turns=1,
    )
    score = score_agent_analysis(_suite(), analysis, _config())
    assert score.finding_recall == 1
    assert score.required_tool_recall == 0
    assert score.inspection_recall == 0
    assert score.evidence_grounding == 0
    assert score.terminal_compliance is False


def test_wrong_severity_is_not_conflated_with_finding_recall() -> None:
    analysis = AgentEvalAnalysis(
        findings=[
            AgentFinding(
                severity="suggestion",
                category="performance",
                title="FLOAT money",
                description="Prefer DECIMAL",
                table_name="invoices",
                column_name="amount",
            )
        ]
    )
    score = score_agent_analysis(_suite(), analysis, _config())
    assert score.finding_recall == 1
    assert score.severity_accuracy == 0
    assert score.finding_precision == pytest.approx(1)
