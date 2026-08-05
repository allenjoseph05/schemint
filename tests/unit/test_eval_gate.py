"""Tests for the Phase 5 baseline regression gate."""

from __future__ import annotations

from evals.core.models import ScoreRow
from evals.gate import BaselineConfig, evaluate_gate, load_baselines


def test_committed_baseline_configuration_is_valid():
    config = load_baselines()
    assert config.version == 3
    assert config.profiles["pr"].required_tasks == 60
    assert config.profiles["nightly"].required_trials == 3


def test_gate_goes_green_at_baseline():
    result = evaluate_gate("pr", {"rules_only": _passing_rows()}, _config())
    assert result.passed is True
    assert "Gate passed." in result.render()


def test_deliberate_metric_regression_goes_red_then_green():
    passing = _passing_rows()
    regressed = [
        passing[0].model_copy(
            update={
                "pred_breaking": False,
                "correct": False,
                "false_negative": True,
                "risk_exact_match": False,
                "underestimated": True,
            }
        ),
        passing[1],
    ]

    red = evaluate_gate("pr", {"rules_only": regressed}, _config())
    green = evaluate_gate("pr", {"rules_only": passing}, _config())

    assert red.passed is False
    assert any(check.name == "classification_f1" and not check.passed for check in red.checks)
    assert green.passed is True


def test_gate_fails_when_corpus_is_incomplete():
    result = evaluate_gate("pr", {"rules_only": _passing_rows()[:1]}, _config())
    assert result.passed is False
    assert any(check.name == "tasks" and not check.passed for check in result.checks)


def test_gate_fails_when_trial_matrix_has_a_missing_cell():
    payload = _config().model_dump(mode="json")
    payload["profiles"]["pr"]["required_trials"] = 2
    config = BaselineConfig.model_validate(payload)
    rows = [*_passing_rows(), _score("breaking-1").model_copy(update={"trial": 1})]

    result = evaluate_gate("pr", {"rules_only": rows}, config)

    assert result.passed is False
    assert any(check.name == "cells" and not check.passed for check in result.checks)


def test_nightly_skips_relative_threshold_until_paid_baseline_exists():
    payload = _config().model_dump(mode="json")
    payload["profiles"]["nightly"] = {
        "baseline": "current",
        "adapters": ["naive_llm"],
        "required_tasks": 2,
        "required_trials": 1,
        "max_errors": 0,
        "allow_missing_baselines": True,
        "thresholds": [
            {
                "metric": "classification_f1",
                "direction": "min",
                "baseline_delta": -0.05,
            },
            {
                "metric": "cost_per_task_usd",
                "direction": "max",
                "absolute": 0.25,
            },
        ],
    }
    payload["snapshots"]["current"]["adapters"]["naive_llm"] = None
    config = BaselineConfig.model_validate(payload)
    rows = [row.model_copy(update={"adapter": "naive_llm"}) for row in _passing_rows()]

    result = evaluate_gate("nightly", {"naive_llm": rows}, config)

    assert result.passed is True
    assert result.warnings == [
        "Skipped naive_llm.classification_f1 has no committed baseline in current"
    ]


def _config() -> BaselineConfig:
    return BaselineConfig.model_validate(
        {
            "version": 2,
            "snapshots": {
                "current": {
                    "tasks": 2,
                    "adapters": {
                        "rules_only": {
                            "classification_f1": 1.0,
                            "breaking_never_underestimates": 1.0,
                        }
                    },
                }
            },
            "profiles": {
                "pr": {
                    "baseline": "current",
                    "adapters": ["rules_only"],
                    "required_tasks": 2,
                    "required_trials": 1,
                    "max_errors": 0,
                    "thresholds": [
                        {
                            "metric": "classification_f1",
                            "direction": "min",
                            "baseline_delta": -0.1,
                        },
                        {
                            "metric": "breaking_never_underestimates",
                            "direction": "min",
                            "baseline_delta": 0.0,
                        },
                    ],
                }
            },
        }
    )


def _passing_rows() -> list[ScoreRow]:
    return [_score("breaking-1"), _score("breaking-2")]


def _score(task_id: str) -> ScoreRow:
    return ScoreRow(
        task_id=task_id,
        category="breaking",
        adapter="rules_only",
        config_hash="config",
        trial=0,
        true_risk="breaking",
        pred_risk="breaking",
        true_breaking=True,
        pred_breaking=True,
        correct=True,
        false_positive=False,
        false_negative=False,
        risk_exact_match=True,
        underestimated=False,
        overestimated=False,
        blocked_correctly=True,
    )
