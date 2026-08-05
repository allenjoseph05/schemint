"""Resumable execution of adapters over filesystem-backed suites."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from evals.adapters.base import EvalAdapter
from evals.core.metering import meter
from evals.core.models import EvalAnalysis, Truth
from evals.core.store import EvalStore
from evals.core.suites import SuiteDefinition
from evals.scorers.classification import score_analysis
from evals.scorers.live import LiveArtifactScorer


@dataclass(frozen=True)
class RunSummary:
    attempted: int
    completed: int
    skipped: int
    errors: int
    cost_usd: float


def run_evaluations(
    adapter: EvalAdapter,
    suites: list[SuiteDefinition],
    *,
    trials: int = 1,
    force: bool = False,
    score_artifacts: bool = False,
    store: EvalStore | None = None,
) -> RunSummary:
    """Run and score each task, preserving errors as visible result rows."""
    if trials < 1:
        raise ValueError("trials must be at least 1")
    result_store = store or EvalStore()
    completed = skipped = errors = 0
    total_cost = 0.0
    artifact_scorer = LiveArtifactScorer() if score_artifacts else None

    try:
        for suite in suites:
            truth = load_truth(suite)
            for trial in range(trials):
                config = adapter.config(truth, trial)
                if not force and result_store.has_run(
                    suite.task.id, config.config_hash(), config.trial
                ):
                    skipped += 1
                    continue

                started = time.perf_counter()
                with meter() as usage:
                    try:
                        analysis = adapter.analyze(suite)
                    except Exception as exc:
                        analysis = EvalAnalysis(error=f"{type(exc).__name__}: {exc}")
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                analysis = analysis.model_copy(
                    update={
                        "tokens_in": usage.input_tokens,
                        "tokens_out": usage.output_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                        "cache_write_tokens": usage.cache_write_tokens,
                        "llm_calls": usage.calls,
                        "cost_usd": usage.cost_usd,
                        "latency_ms": elapsed_ms,
                    }
                )
                score = score_analysis(suite.task, truth, analysis, config)
                if artifact_scorer is not None:
                    score = artifact_scorer.score(suite, analysis, score)
                run_id = result_store.record_run(
                    suite.task.id,
                    suite.task.category,
                    config,
                    analysis,
                )
                result_store.record_score(score, run_id=run_id)
                completed += 1
                total_cost += analysis.cost_usd
                errors += analysis.error is not None
    finally:
        if artifact_scorer is not None:
            artifact_scorer.close()

    return RunSummary(
        attempted=len(suites) * trials,
        completed=completed,
        skipped=skipped,
        errors=errors,
        cost_usd=total_cost,
    )


def load_truth(suite: SuiteDefinition) -> Truth:
    """Load and freshness-check a generated truth artifact."""
    if not suite.expected_path.is_file():
        raise FileNotFoundError(f"Missing generated truth: {suite.expected_path}")
    payload = json.loads(Path(suite.expected_path).read_text(encoding="utf-8"))
    truth = Truth.model_validate(payload)
    if truth.task_id != suite.task.id:
        raise ValueError(f"Truth task id {truth.task_id!r} does not match {suite.task.id!r}")
    if truth.input_hash and truth.input_hash != suite.input_hash():
        raise ValueError(f"Generated truth is stale for {suite.task.id}")
    return truth
