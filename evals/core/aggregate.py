"""Metric aggregation with deterministic bootstrap confidence intervals."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from evals.core.models import ScoreRow


@dataclass(frozen=True)
class Estimate:
    value: float
    low: float
    high: float


@dataclass(frozen=True)
class AdapterSummary:
    adapter: str
    config_hash: str
    tasks: int
    trials: int
    errors: int
    metrics: dict[str, Estimate]


def aggregate_scores(rows: list[ScoreRow]) -> AdapterSummary:
    """Aggregate one adapter/config across trials."""
    if not rows:
        raise ValueError("cannot aggregate an empty score set")
    by_trial: dict[int, list[ScoreRow]] = {}
    for row in rows:
        by_trial.setdefault(row.trial, []).append(row)

    metric_values: dict[str, list[float]] = {}
    for trial_rows in by_trial.values():
        for name, value in _trial_metrics(trial_rows).items():
            metric_values.setdefault(name, []).append(value)
    metrics = {
        name: bootstrap_mean(values, seed=f"{rows[0].adapter}:{name}")
        for name, values in metric_values.items()
    }
    return AdapterSummary(
        adapter=rows[0].adapter,
        config_hash=rows[0].config_hash,
        tasks=len({row.task_id for row in rows}),
        trials=len(by_trial),
        errors=sum(row.errored for row in rows),
        metrics=metrics,
    )


def bootstrap_mean(values: list[float], *, seed: str, samples: int = 2000) -> Estimate:
    """Bootstrap a mean; a constant sample produces an honest degenerate CI."""
    value = statistics.fmean(values)
    if len(values) == 1 or len(set(values)) == 1:
        return Estimate(value=value, low=value, high=value)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(values) for _ in values) for _ in range(samples))
    return Estimate(
        value=value,
        low=means[math.floor(0.025 * (samples - 1))],
        high=means[math.ceil(0.975 * (samples - 1))],
    )


def _trial_metrics(rows: list[ScoreRow]) -> dict[str, float]:
    tp = sum(row.true_breaking and row.pred_breaking for row in rows)
    fp = sum(row.false_positive for row in rows)
    fn = sum(row.false_negative for row in rows)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    negatives = sum(not row.true_breaking for row in rows)
    positives = sum(row.true_breaking for row in rows)
    fidelity = [row.fidelity_pct for row in rows if row.fidelity_pct is not None]
    latencies = sorted(row.latency_ms for row in rows)
    blast_true = sum(row.blast_true_count for row in rows)
    blast_pred = sum(row.blast_pred_count for row in rows)
    blast_overlap = sum(row.blast_recall * row.blast_true_count for row in rows)
    blast_recall = blast_overlap / blast_true if blast_true else 1.0
    blast_precision = blast_overlap / blast_pred if blast_pred else (1.0 if not blast_true else 0.0)
    blast_f1 = (
        2 * blast_precision * blast_recall / (blast_precision + blast_recall)
        if blast_precision + blast_recall
        else 0.0
    )
    return {
        "classification_f1": f1,
        "false_positive_rate": fp / negatives if negatives else 0.0,
        "false_negative_rate": fn / positives if positives else 0.0,
        "risk_exact_match": _mean(row.risk_exact_match for row in rows),
        "never_underestimates": _mean(not row.underestimated for row in rows),
        "blast_recall": blast_recall,
        "blast_precision": blast_precision,
        "blast_f1": blast_f1,
        "blocked_accuracy": _mean(row.blocked_correctly for row in rows),
        "simulator_fidelity": statistics.fmean(fidelity) / 100.0 if fidelity else 0.0,
        "cost_per_task_usd": statistics.fmean(row.cost_usd for row in rows),
        "latency_p50_ms": float(statistics.median(latencies)),
        "latency_p95_ms": float(latencies[math.ceil(0.95 * len(latencies)) - 1]),
        "error_rate": _mean(row.errored for row in rows),
    }


def _mean(values: Iterable[bool | float]) -> float:
    materialized = [float(value) for value in values]
    return statistics.fmean(materialized) if materialized else 0.0
