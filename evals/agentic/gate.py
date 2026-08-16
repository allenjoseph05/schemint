"""Absolute acceptance gate for a complete paid AgentAnalyzer sweep."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import BaseModel

from evals.agentic.report import aggregate_agent_scores
from evals.agentic.store import DEFAULT_AGENT_DB, AgentEvalStore

DEFAULT_GATE_CONFIG = Path("evals") / "agentic" / "gate.json"


class GateThreshold(BaseModel):
    direction: str
    value: float


class AgentGateConfig(BaseModel):
    required_tasks: int
    required_trials: int
    max_errors: int
    thresholds: dict[str, GateThreshold]


def evaluate_agent_gate(
    metrics: dict[str, float], config: AgentGateConfig
) -> tuple[bool, list[str]]:
    messages: list[str] = []
    passed = True
    required_tasks = config.required_tasks
    required_trials = config.required_trials
    checks = [
        ("tasks", "eq", float(required_tasks)),
        ("cells", "eq", float(required_tasks * required_trials)),
        ("errors", "max", float(config.max_errors)),
    ]
    for name, rule in config.thresholds.items():
        checks.append((name, rule.direction, rule.value))

    for metric, direction, boundary in checks:
        actual = metrics.get(metric)
        ok = actual is not None and (
            (direction == "min" and actual >= boundary)
            or (direction == "max" and actual <= boundary)
            or (direction == "eq" and actual == boundary)
        )
        passed &= ok
        messages.append(
            f"[{'PASS' if ok else 'FAIL'}] {metric}: "
            f"{actual if actual is not None else 'missing'} ({direction} {boundary})"
        )
    return passed, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_AGENT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_GATE_CONFIG)
    args = parser.parse_args()
    config = AgentGateConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    metrics = aggregate_agent_scores(AgentEvalStore(args.db).latest_scores())
    passed, messages = evaluate_agent_gate(metrics, config)
    print("Agent evaluation acceptance gate")
    print("\n".join(messages))
    return 0 if passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
