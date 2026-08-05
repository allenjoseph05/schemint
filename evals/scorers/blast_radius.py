"""Set-overlap scoring on the shared blast-radius key namespace."""

from __future__ import annotations

from dataclasses import dataclass

from evals.core.keys import key_set


@dataclass(frozen=True)
class BlastRadiusScores:
    recall: float
    precision: float
    f1: float
    true_count: int
    pred_count: int


def blast_radius_scores(truth_keys: list[str], predicted_keys: list[str]) -> BlastRadiusScores:
    """Compute precision/recall, treating two empty sets as a perfect answer."""
    truth = key_set(truth_keys)
    predicted = key_set(predicted_keys)
    overlap = len(truth & predicted)
    recall = overlap / len(truth) if truth else 1.0
    precision = overlap / len(predicted) if predicted else (1.0 if not truth else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return BlastRadiusScores(
        recall=recall,
        precision=precision,
        f1=f1,
        true_count=len(truth),
        pred_count=len(predicted),
    )
