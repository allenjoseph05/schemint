"""Tests for the eval harness data model.

Every model is written to SQLite as JSON and read back by the scorers and the
report, so JSON round-tripping is load-bearing, not incidental.
"""

from __future__ import annotations

import pytest

from evals.core.models import (
    BREAKING_THRESHOLD,
    RISK_ORDER,
    SEVERITY_ORDER,
    EvalAnalysis,
    EvalTask,
    HealthReport,
    ObjectHealth,
    RunConfig,
    ScoreRow,
    Truth,
    is_breaking,
    max_risk,
    risk_index,
    severity_index,
)


@pytest.mark.unit
class TestVocabulary:
    def test_risk_order_matches_schemint(self):
        # Must stay identical to SchemaChangeEvent.change_risk in
        # schemint.drift.models, or scores stop mapping back to production.
        from schemint.drift.models import SchemaChangeEvent

        field = SchemaChangeEvent.model_fields["change_risk"]
        schemint_risks = {
            arg
            for annotation in (field.annotation,)
            for arg in _literal_args(annotation)
            if isinstance(arg, str)
        }
        assert schemint_risks == set(RISK_ORDER)

    def test_severity_order_matches_schemint(self):
        from schemint.drift.models import AgentDecision

        field = AgentDecision.model_fields["severity"]
        schemint_severities = {
            arg for arg in _literal_args(field.annotation) if isinstance(arg, str)
        }
        assert schemint_severities == set(SEVERITY_ORDER)

    def test_risk_index_orders_ascending(self):
        assert risk_index("safe") < risk_index("needs_review")
        assert risk_index("needs_review") < risk_index("potentially_breaking")
        assert risk_index("potentially_breaking") < risk_index("breaking")

    def test_risk_index_unknown_sorts_as_safe(self):
        assert risk_index("bogus") == risk_index("safe")

    def test_severity_index_orders_ascending(self):
        assert severity_index("low") < severity_index("critical")

    def test_max_risk_picks_highest(self):
        assert max_risk(["safe", "breaking", "needs_review"]) == "breaking"

    def test_max_risk_of_empty_is_safe(self):
        assert max_risk([]) == "safe"

    @pytest.mark.parametrize(
        ("risk", "expected"),
        [
            ("safe", False),
            ("needs_review", False),
            ("potentially_breaking", True),
            ("breaking", True),
        ],
    )
    def test_is_breaking_threshold(self, risk, expected):
        assert is_breaking(risk) is expected

    def test_breaking_threshold_constant(self):
        assert BREAKING_THRESHOLD == "potentially_breaking"


@pytest.mark.unit
class TestRoundTrip:
    def test_truth_round_trips(self):
        truth = Truth(
            task_id="drop_column_behind_view",
            generator_version="v1",
            migration_error="cannot drop column email of table users",
            risk="breaking",
            must_block=True,
            blast_radius=["view:user_summary"],
            rows_lost={"users": 3},
            rule_fired="migration_error",
            real_post_snapshot={"tables": {}},
        )
        assert Truth.model_validate_json(truth.model_dump_json()) == truth

    def test_truth_migration_failed_derives_from_error(self):
        failed = Truth(
            task_id="t",
            generator_version="v1",
            risk="breaking",
            must_block=True,
            migration_error="boom",
        )
        clean = Truth(task_id="t", generator_version="v1", risk="safe", must_block=False)
        assert failed.migration_failed is True
        assert clean.migration_failed is False

    def test_eval_analysis_round_trips(self):
        analysis = EvalAnalysis(
            risk="potentially_breaking",
            severity="high",
            blast_radius=["view:user_summary", "foreign_key:orders_user_id_fkey"],
            blocked=True,
            safety_score=60,
            tokens_in=1200,
            tokens_out=300,
            cost_usd=0.0123,
            latency_ms=845,
        )
        assert EvalAnalysis.model_validate_json(analysis.model_dump_json()) == analysis

    def test_eval_analysis_defaults_to_safe_and_unblocked(self):
        analysis = EvalAnalysis()
        assert analysis.risk == "safe"
        assert analysis.is_breaking is False
        assert analysis.blocked is False
        assert analysis.severity is None

    def test_eval_analysis_is_breaking_derives_from_risk(self):
        assert EvalAnalysis(risk="breaking").is_breaking is True
        assert EvalAnalysis(risk="needs_review").is_breaking is False

    def test_task_round_trips(self):
        task = EvalTask(
            id="add_nullable_column",
            category="safe",
            notes="additive change, no dependents",
            directory="evals/suites/add_nullable_column",
        )
        assert EvalTask.model_validate_json(task.model_dump_json()) == task

    def test_task_defaults_to_classify(self):
        assert EvalTask(id="t", category="safe").expected_outcome == "classify"

    def test_score_row_round_trips(self):
        score = ScoreRow(
            task_id="t",
            category="breaking",
            adapter="rules_only",
            config_hash="abc123",
            trial=0,
            true_breaking=True,
            pred_breaking=True,
            correct=True,
            false_positive=False,
            false_negative=False,
            risk_exact_match=True,
            underestimated=False,
            overestimated=False,
        )
        assert ScoreRow.model_validate_json(score.model_dump_json()) == score

    def test_health_report_helpers(self):
        report = HealthReport(
            objects=[
                ObjectHealth(key="view:a", ok=True),
                ObjectHealth(key="view:b", ok=False, detail="relation does not exist"),
            ],
            row_counts={"users": 10},
        )
        assert report.healthy_keys() == {"view:a"}
        assert report.by_key()["view:b"].detail == "relation does not exist"


@pytest.mark.unit
class TestRunConfig:
    def _config(self, **overrides):
        base = {
            "adapter": "drift_pipeline",
            "adapter_version": "1",
            "model_id": "claude-sonnet-4-20250514",
            "prompt_version": "abc123def456",
            "temperature": 0.0,
            "generator_version": "v1",
        }
        base.update(overrides)
        return RunConfig(**base)

    def test_hash_is_stable_across_instances(self):
        assert self._config().config_hash() == self._config().config_hash()

    def test_trial_does_not_change_hash(self):
        # Trials of one configuration must aggregate together.
        assert self._config(trial=0).config_hash() == self._config(trial=7).config_hash()

    @pytest.mark.parametrize(
        "override",
        [
            {"adapter": "naive_llm"},
            {"adapter_version": "2"},
            {"model_id": "claude-haiku-4-5"},
            {"prompt_version": "changed"},
            {"temperature": 1.0},
            {"generator_version": "v2"},
        ],
    )
    def test_every_other_field_splits_the_hash(self, override):
        assert self._config().config_hash() != self._config(**override).config_hash()

    def test_prompt_edit_separates_results(self):
        # The point of hashing the prompt: editing it must not silently mix
        # old and new numbers in the same bucket.
        before = self._config(prompt_version="v1hash")
        after = self._config(prompt_version="v2hash")
        assert before.config_hash() != after.config_hash()

    def test_label_omits_model_when_absent(self):
        assert RunConfig(adapter="rules_only").label() == "rules_only"

    def test_label_includes_model_when_present(self):
        assert self._config().label() == "drift_pipeline@claude-sonnet-4-20250514"

    def test_temperature_none_is_distinct_from_zero(self):
        # None means "parameter not sent" — required on models that reject it.
        assert (
            self._config(temperature=None).config_hash()
            != self._config(temperature=0.0).config_hash()
        )


def _literal_args(annotation):
    """Extract Literal members from a possibly-Optional annotation."""
    from typing import get_args

    args = get_args(annotation)
    out = []
    for arg in args:
        nested = get_args(arg)
        out.extend(nested if nested else [arg])
    return out
