"""The production agent exposes complete traces without making real API calls."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from schemint.core.parser.sql_parser import parse_sql
from schemint.services.agent import AgentAnalyzer


def _settings() -> MagicMock:
    return MagicMock(
        claude_api_key="test-key",
        claude_model="claude-sonnet-4-20250514",
        claude_model_simple="claude-haiku-4-5-20251001",
        claude_model_complex="claude-sonnet-4-5-20250929",
        claude_max_agent_turns=10,
        claude_temperature=0.0,
        ai_enabled=True,
    )


def _response(content: list[SimpleNamespace]) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.stop_reason = "tool_use"
    response.usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=10,
        cache_creation_input_tokens=5,
    )
    return response


@patch("schemint.services.agent.get_settings")
def test_observer_receives_tool_calls_results_and_terminal_event(mock_settings: MagicMock) -> None:
    mock_settings.return_value = _settings()
    overview = SimpleNamespace(
        type="tool_use", id="overview-1", name="get_schema_overview", input={}
    )
    final = SimpleNamespace(
        type="tool_use",
        id="submit-1",
        name="submit_analysis",
        input={
            "findings": [],
            "score": {
                "total": 100,
                "structural": 100,
                "performance": 100,
                "naming": 100,
                "best_practices": 100,
            },
            "good_practices": [],
            "recommendations": [],
            "summary": "ok",
        },
    )
    trace: list[dict] = []
    with (
        patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        patch("schemint.services.agent.anthropic") as mock_anthropic,
    ):
        client = MagicMock()
        client.messages.create.side_effect = [_response([overview]), _response([final])]
        mock_anthropic.Anthropic.return_value = client
        agent = AgentAnalyzer(trace_observer=trace.append)
        result = agent.analyze(
            parse_sql("CREATE TABLE users (id BIGINT PRIMARY KEY);", "postgresql")
        )

    assert result["summary"] == "ok"
    assert [event["event"] for event in trace] == [
        "run_started",
        "model_turn_started",
        "model_response",
        "tool_call",
        "tool_result",
        "model_turn_started",
        "model_response",
        "tool_call",
        "run_completed",
    ]
    assert trace[3]["tool_name"] == "get_schema_overview"
    assert trace[4]["tool_use_id"] == "overview-1"
    assert trace[-2]["terminal"] is True


@patch("schemint.services.agent.get_settings")
def test_broken_observer_cannot_break_analysis(mock_settings: MagicMock) -> None:
    mock_settings.return_value = _settings()
    final = SimpleNamespace(
        type="tool_use",
        id="submit-1",
        name="submit_analysis",
        input={"findings": [], "score": {}, "good_practices": [], "summary": "ok"},
    )

    def fail(_: dict) -> None:
        raise RuntimeError("telemetry is unavailable")

    with (
        patch("schemint.services.agent.CLAUDE_AVAILABLE", True),
        patch("schemint.services.agent.anthropic") as mock_anthropic,
    ):
        client = MagicMock()
        client.messages.create.return_value = _response([final])
        mock_anthropic.Anthropic.return_value = client
        result = AgentAnalyzer(trace_observer=fail).analyze(
            parse_sql("CREATE TABLE users (id BIGINT PRIMARY KEY);", "postgresql")
        )
    assert result["summary"] == "ok"
