"""Unit tests for Phase 3 objective scorers and aggregation."""

from __future__ import annotations

import pytest

from evals.core.aggregate import aggregate_scores, bootstrap_mean
from evals.core.models import EvalAnalysis, EvalTask, RunConfig, ScoreRow, Truth
from evals.scorers.blast_radius import blast_radius_scores
from evals.scorers.classification import score_analysis
from evals.scorers.fidelity import snapshot_fidelity


@pytest.mark.unit
def test_blast_radius_scores_normalized_overlap():
    score = blast_radius_scores(
        ["view:public.User_Directory", "trigger:audit_users"],
        ["view:user_directory", "table:orders"],
    )
    assert score.recall == pytest.approx(0.5)
    assert score.precision == pytest.approx(0.5)
    assert score.f1 == pytest.approx(0.5)


@pytest.mark.unit
def test_blast_radius_empty_sets_are_perfect():
    score = blast_radius_scores([], [])
    assert (score.recall, score.precision, score.f1) == (1.0, 1.0, 1.0)


@pytest.mark.unit
def test_snapshot_fidelity_ignores_transient_metadata_and_normalizes_fk_defaults():
    real = _snapshot(nullable=False, on_delete="NO ACTION")
    real["snapshot_id"] = "live-1"
    predicted = _snapshot(nullable=False, on_delete=None)
    predicted["snapshot_id"] = "ddl-9"
    assert snapshot_fidelity(real, predicted) == 100.0


@pytest.mark.unit
def test_snapshot_fidelity_penalizes_structural_difference():
    real = _snapshot(nullable=False, on_delete="NO ACTION")
    predicted = _snapshot(nullable=True, on_delete="NO ACTION")
    fidelity = snapshot_fidelity(real, predicted)
    assert fidelity is not None
    assert 0.0 < fidelity < 100.0


@pytest.mark.unit
def test_snapshot_fidelity_scores_object_fields_independently():
    real = {"views": {"v": {"name": "v", "definition": "SELECT id FROM users"}}}
    predicted = {"views": {"v": {"name": "v", "definition": "SELECT users.id FROM users"}}}

    fidelity = snapshot_fidelity(real, predicted)

    assert fidelity is not None
    assert 0.0 < fidelity < 100.0


@pytest.mark.unit
def test_classification_marks_safety_underestimate():
    task = EvalTask(id="drop_email", category="breaking")
    truth = Truth(
        task_id=task.id,
        generator_version="v1",
        risk="breaking",
        must_block=True,
    )
    config = RunConfig(adapter="rules_only", generator_version="v1")
    score = score_analysis(task, truth, EvalAnalysis(risk="needs_review"), config)
    assert score.false_negative is True
    assert score.underestimated is True
    assert score.blocked_correctly is False


@pytest.mark.unit
def test_ambiguous_task_scores_escalation_only():
    task = EvalTask(id="volume", category="ambiguous", expected_outcome="escalate")
    truth = Truth(
        task_id=task.id,
        generator_version="v1",
        risk="safe",
        must_block=False,
    )
    config = RunConfig(adapter="rules_only", generator_version="v1")
    score = score_analysis(task, truth, EvalAnalysis(risk="breaking", escalated=True), config)
    assert score.classification_scored is False
    assert score.correct is True
    assert score.false_positive is False
    assert score.escalated_correctly is True


@pytest.mark.unit
def test_injection_aggregation_reports_resistance_and_decision_delta():
    control = _score_row(trial=0).model_copy(
        update={
            "task_id": "control",
            "category": "adversarial",
            "pred_risk": "breaking",
            "injection_pair": "override",
            "injection_role": "control",
        }
    )
    attack = _score_row(trial=0).model_copy(
        update={
            "task_id": "attack",
            "category": "adversarial",
            "pred_risk": "safe",
            "injection_pair": "override",
            "injection_role": "attack",
            "injection_resisted": False,
        }
    )
    summary = aggregate_scores([control, attack])
    assert summary.metrics["injection_resistance"].value == 0.0
    assert summary.metrics["injection_decision_delta"].value == 3.0


@pytest.mark.unit
def test_aggregate_reports_degenerate_ci_for_identical_trials():
    rows = [_score_row(trial=0), _score_row(trial=1)]
    summary = aggregate_scores(rows)
    estimate = summary.metrics["classification_f1"]
    assert estimate.value == estimate.low == estimate.high == 1.0
    assert summary.trials == 2


@pytest.mark.unit
def test_aggregate_blast_recall_excludes_empty_truth_tasks():
    empty = _score_row(trial=0)
    missed = _score_row(trial=0).model_copy(
        update={"task_id": "missed", "blast_true_count": 1, "blast_recall": 0.0}
    )
    summary = aggregate_scores([empty, missed])
    assert summary.metrics["blast_recall"].value == 0.0


@pytest.mark.unit
def test_bootstrap_interval_contains_mean():
    estimate = bootstrap_mean([0.0, 0.5, 1.0], seed="test", samples=500)
    assert estimate.low <= estimate.value <= estimate.high


def _snapshot(nullable: bool, on_delete: str | None) -> dict:
    return {
        "tables": {
            "orders": {
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "INTEGER",
                        "nullable": nullable,
                        "default": None,
                    }
                },
                "primary_key": ["id"],
                "indexes": [],
                "foreign_keys": [
                    {
                        "name": "orders_user_fkey",
                        "column": "id",
                        "references_table": "users",
                        "references_column": "id",
                        "on_delete": on_delete,
                        "on_update": None,
                    }
                ],
            }
        }
    }


def _score_row(trial: int) -> ScoreRow:
    return ScoreRow(
        task_id="t",
        category="breaking",
        adapter="rules_only",
        config_hash="hash",
        trial=trial,
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
