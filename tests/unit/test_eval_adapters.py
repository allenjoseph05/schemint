"""Unit tests for Phase 3 evaluation adapters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evals.adapters import ADAPTER_NAMES, get_adapter
from evals.adapters.naive_llm import _parse_json
from evals.adapters.rules_only import RulesOnlyAdapter
from evals.core.suites import SuiteDefinition
from schemint.config import Settings
from schemint.drift.agent_brain import DriftAgent
from schemint.drift.copilot_agent import CopilotAgent
from schemint.drift.models import ContextPackage, SchemaChangeEvent


@pytest.mark.unit
def test_registry_constructs_all_four_adapters():
    assert tuple(get_adapter(name).name for name in ADAPTER_NAMES) == ADAPTER_NAMES


@pytest.mark.unit
def test_rules_only_runs_production_sandbox_without_copilot(tmp_path):
    suite = _suite(tmp_path)
    with patch("schemint.drift.sandbox.get_copilot_agent") as get_copilot:
        analysis = RulesOnlyAdapter().analyze(suite)
    get_copilot.assert_not_called()
    assert analysis.risk == "breaking"
    assert analysis.llm_calls == 0
    assert analysis.predicted_snapshot is not None
    assert "email" not in analysis.predicted_snapshot["tables"]["users"]["columns"]


@pytest.mark.unit
def test_naive_parser_accepts_markdown_json():
    assert _parse_json('answer\n```json\n{"risk": "safe"}\n```')["risk"] == "safe"


@pytest.mark.unit
def test_settings_temperature_defaults_to_zero():
    assert Settings(_env_file=None).claude_temperature == 0.0  # type: ignore[call-arg]


@pytest.mark.unit
def test_drift_agent_forwards_temperature():
    agent = DriftAgent.__new__(DriftAgent)
    agent.client = MagicMock()
    agent.model = "test-model"
    agent.temperature = 0.25
    agent.client.messages.create.return_value = MagicMock(
        content=[
            MagicMock(
                type="text",
                text=(
                    '{"severity":"low","confidence_in_decision":0.9,'
                    '"requires_human_review":false,"rationale":["safe"],'
                    '"recommended_action_categories":["monitor_only"],'
                    '"context_quality":"complete"}'
                ),
            )
        ]
    )
    context = ContextPackage(
        schema_change=SchemaChangeEvent(change_type="table_added", table="audit")
    )
    agent._call_claude(context)
    assert agent.client.messages.create.call_args.kwargs["temperature"] == 0.25


@pytest.mark.unit
def test_copilot_agent_forwards_temperature():
    agent = CopilotAgent.__new__(CopilotAgent)
    agent.client = MagicMock()
    agent.model = "test-model"
    agent.temperature = 0.0
    agent.client.messages.create.return_value = MagicMock(
        content=[MagicMock(type="text", text="{}")]
    )
    assert agent._call_claude("system", "user") == "{}"
    assert agent.client.messages.create.call_args.kwargs["temperature"] == 0.0


def _suite(tmp_path: Path) -> SuiteDefinition:
    directory = tmp_path / "drop_email"
    directory.mkdir()
    schema = directory / "schema.sql"
    migration = directory / "migration.sql"
    meta = directory / "meta.json"
    expected = directory / "expected.json"
    schema.write_text(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);",
        encoding="utf-8",
    )
    migration.write_text("ALTER TABLE users DROP COLUMN email;", encoding="utf-8")
    meta.write_text('{"id":"drop_email","category":"breaking"}', encoding="utf-8")
    return SuiteDefinition(
        task=get_adapter_task(directory),
        directory=directory,
        schema_path=schema,
        seed_path=None,
        migration_path=migration,
        probes_path=None,
        expected_path=expected,
        meta_path=meta,
    )


def get_adapter_task(directory: Path):
    from evals.core.models import EvalTask

    return EvalTask(id="drop_email", category="breaking", directory=str(directory))
