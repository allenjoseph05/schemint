"""Offline contract tests for the versioned AgentAnalyzer corpus."""

import pytest

from evals.agentic.models import AgentSuiteRecord
from evals.agentic.suites import discover_agent_suites, validate_agent_suites


def test_committed_agent_corpus_is_complete_and_fresh() -> None:
    suites = discover_agent_suites()
    assert len(suites) == 60
    assert validate_agent_suites(suites) == []


def test_task_and_truth_ids_cannot_diverge() -> None:
    suite = discover_agent_suites()[0]
    payload = suite.record.model_dump()
    payload["truth"]["task_id"] = "different"
    with pytest.raises(ValueError, match=r"task\.id must equal truth\.task_id"):
        AgentSuiteRecord.model_validate(payload)
