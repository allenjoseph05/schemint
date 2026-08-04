"""Tests for the append-only eval results store.

The append-only property is the point: a re-run must leave the previous
numbers recoverable, and readers must see the newest row per cell.
"""

from __future__ import annotations

import pytest

from evals.core.models import EvalAnalysis, RunConfig, ScoreRow
from evals.core.store import EvalStore


@pytest.fixture
def store(tmp_path):
    return EvalStore(tmp_path / "results.db")


def _config(adapter="rules_only", trial=0, **overrides):
    return RunConfig(adapter=adapter, trial=trial, **overrides)


def _score(task_id="t1", adapter="rules_only", config_hash="h", trial=0, **overrides):
    base = {
        "task_id": task_id,
        "category": "breaking",
        "adapter": adapter,
        "config_hash": config_hash,
        "trial": trial,
        "true_breaking": True,
        "pred_breaking": True,
        "correct": True,
        "false_positive": False,
        "false_negative": False,
        "risk_exact_match": True,
        "underestimated": False,
        "overestimated": False,
    }
    base.update(overrides)
    return ScoreRow(**base)


@pytest.mark.unit
class TestSchema:
    def test_creates_db_file_and_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "results.db"
        EvalStore(path)
        assert path.exists()

    def test_reopening_is_idempotent(self, tmp_path):
        path = tmp_path / "results.db"
        EvalStore(path).record_run("t1", "safe", _config(), EvalAnalysis())
        assert EvalStore(path).run_count() == 1


@pytest.mark.unit
class TestRuns:
    def test_record_and_read_back(self, store):
        config = _config()
        analysis = EvalAnalysis(risk="breaking", blast_radius=["view:v"], cost_usd=0.01)
        store.record_run("drop_column", "breaking", config, analysis)

        runs = store.latest_runs()
        assert len(runs) == 1
        assert runs[0].task_id == "drop_column"
        assert runs[0].analysis.risk == "breaking"
        assert runs[0].analysis.blast_radius == ["view:v"]
        assert runs[0].config.adapter == "rules_only"

    def test_rerun_appends_and_latest_wins(self, store):
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis(risk="safe"))
        store.record_run("t1", "safe", config, EvalAnalysis(risk="breaking"))

        # Both rows survive — the old number is still recoverable.
        assert store.run_count() == 2
        latest = store.latest_runs()
        assert len(latest) == 1
        assert latest[0].analysis.risk == "breaking"

    def test_trials_are_separate_rows(self, store):
        for trial in range(3):
            store.record_run("t1", "safe", _config(trial=trial), EvalAnalysis())
        assert len(store.latest_runs()) == 3

    def test_different_configs_do_not_collide(self, store):
        store.record_run("t1", "safe", _config(adapter="rules_only"), EvalAnalysis())
        store.record_run("t1", "safe", _config(adapter="naive_llm"), EvalAnalysis())
        assert len(store.latest_runs()) == 2

    def test_filter_by_adapter(self, store):
        store.record_run("t1", "safe", _config(adapter="rules_only"), EvalAnalysis())
        store.record_run("t1", "safe", _config(adapter="naive_llm"), EvalAnalysis())
        runs = store.latest_runs(adapter="naive_llm")
        assert [r.adapter for r in runs] == ["naive_llm"]

    def test_filter_by_config_hash(self, store):
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis())
        store.record_run("t1", "safe", _config(adapter="naive_llm"), EvalAnalysis())
        runs = store.latest_runs(config_hash=config.config_hash())
        assert len(runs) == 1
        assert runs[0].adapter == "rules_only"

    def test_config_hashes_lists_distinct_pairs(self, store):
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis())
        store.record_run("t2", "safe", config, EvalAnalysis())
        assert store.config_hashes() == [("rules_only", config.config_hash())]


@pytest.mark.unit
class TestResume:
    def test_has_run_is_false_before_recording(self, store):
        config = _config()
        assert store.has_run("t1", config.config_hash(), 0) is False

    def test_has_run_is_true_after_a_clean_run(self, store):
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis())
        assert store.has_run("t1", config.config_hash(), 0) is True

    def test_errored_run_does_not_count_as_done(self, store):
        # A resumed sweep must retry failures, not skip them.
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis(error="boom"))
        assert store.has_run("t1", config.config_hash(), 0) is False

    def test_has_run_is_trial_specific(self, store):
        config = _config(trial=0)
        store.record_run("t1", "safe", config, EvalAnalysis())
        assert store.has_run("t1", config.config_hash(), 1) is False

    def test_error_count_tracks_failures(self, store):
        config = _config()
        store.record_run("t1", "safe", config, EvalAnalysis())
        store.record_run("t2", "safe", config, EvalAnalysis(error="boom"))
        assert store.error_count() == 1


@pytest.mark.unit
class TestScores:
    def test_record_and_read_back(self, store):
        store.record_score(_score())
        scores = store.latest_scores()
        assert len(scores) == 1
        assert scores[0].task_id == "t1"
        assert scores[0].correct is True

    def test_rescoring_appends_and_latest_wins(self, store):
        store.record_score(_score(correct=True))
        store.record_score(_score(correct=False))
        latest = store.latest_scores()
        assert len(latest) == 1
        assert latest[0].correct is False

    def test_filter_by_adapter(self, store):
        store.record_score(_score(adapter="rules_only", config_hash="a"))
        store.record_score(_score(adapter="naive_llm", config_hash="b"))
        assert len(store.latest_scores(adapter="naive_llm")) == 1

    def test_links_to_run_id(self, store):
        run_id = store.record_run("t1", "breaking", _config(), EvalAnalysis())
        score_id = store.record_score(_score(), run_id=run_id)
        assert score_id > 0
