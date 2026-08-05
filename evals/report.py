"""Text comparison report for stored evaluation results."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.adapters.base import ADAPTER_NAMES
from evals.core.aggregate import Estimate, aggregate_scores
from evals.core.models import ScoreRow
from evals.core.store import DEFAULT_DB_PATH, EvalStore

_METRICS = (
    ("classification_f1", "Breaking F1", "percent"),
    ("false_positive_rate", "False-positive rate", "percent"),
    ("false_negative_rate", "False-negative rate", "percent"),
    ("risk_exact_match", "Risk exact match", "percent"),
    ("never_underestimates", "Never underestimates", "percent"),
    ("blast_recall", "Blast recall", "percent"),
    ("blast_precision", "Blast precision", "percent"),
    ("simulator_fidelity", "Simulator fidelity", "percent"),
    ("blocked_accuracy", "Block accuracy", "percent"),
    ("error_rate", "Error rate", "percent"),
    ("cost_per_task_usd", "Cost/task", "currency"),
    ("latency_p95_ms", "Latency p95", "milliseconds"),
)


def render_text(store: EvalStore | None = None) -> str:
    """Render the newest stored configuration for all four adapters."""
    result_store = store or EvalStore()
    score_sets = {name: _latest_scores(result_store, name) for name in ADAPTER_NAMES}
    summaries = {
        name: aggregate_scores(rows) if rows else None for name, rows in score_sets.items()
    }
    headers = ["Metric", *ADAPTER_NAMES]
    rows = [headers]
    rows.append(
        [
            "Tasks / trials",
            *[
                f"{summary.tasks} / {summary.trials}" if summary else "n/a"
                for summary in summaries.values()
            ],
        ]
    )
    for metric, label, style in _METRICS:
        rows.append(
            [
                label,
                *[
                    _format_estimate(summary.metrics[metric], style) if summary else "n/a"
                    for summary in summaries.values()
                ],
            ]
        )

    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]
    lines = [_format_row(rows[0], widths)]
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(_format_row(row, widths) for row in rows[1:])
    lines.append("")
    lines.append("95% bootstrap CIs are shown in brackets; identical trials have degenerate CIs.")
    lines.extend(_render_confusion(score_sets))
    lines.extend(_render_categories(score_sets))
    lines.extend(_render_failures(score_sets))
    return "\n".join(lines)


def _latest_scores(store: EvalStore, adapter: str) -> list[ScoreRow]:
    runs = store.latest_runs(adapter=adapter)
    if not runs:
        return []
    latest = max(runs, key=lambda run: run.created_at)
    return store.latest_scores(adapter=adapter, config_hash=latest.config.config_hash())


def _render_confusion(score_sets: dict[str, list[ScoreRow]]) -> list[str]:
    lines = ["", "Classification confusion", "Adapter         | TP | FP | TN | FN"]
    lines.append("----------------+----+----+----+---")
    for adapter, rows in score_sets.items():
        tp = sum(row.true_breaking and row.pred_breaking for row in rows)
        fp = sum(row.false_positive for row in rows)
        tn = sum(not row.true_breaking and not row.pred_breaking for row in rows)
        fn = sum(row.false_negative for row in rows)
        values = f"{tp:2} | {fp:2} | {tn:2} | {fn:2}" if rows else "n/a"
        lines.append(f"{adapter.ljust(15)} | {values}")
    return lines


def _render_categories(score_sets: dict[str, list[ScoreRow]]) -> list[str]:
    categories = sorted({row.category for rows in score_sets.values() for row in rows})
    if not categories:
        return []
    lines = ["", "Accuracy by category"]
    table = [["Category", *ADAPTER_NAMES]]
    for category in categories:
        values = []
        for adapter in ADAPTER_NAMES:
            rows = [row for row in score_sets[adapter] if row.category == category]
            values.append(
                f"{sum(row.correct for row in rows) / len(rows) * 100:.1f}%" if rows else "n/a"
            )
        table.append([category, *values])
    widths = [max(len(row[index]) for row in table) for index in range(len(table[0]))]
    lines.append(_format_row(table[0], widths))
    lines.append("-+-".join("-" * width for width in widths))
    lines.extend(_format_row(row, widths) for row in table[1:])
    return lines


def _render_failures(score_sets: dict[str, list[ScoreRow]]) -> list[str]:
    lines = ["", "Top failure clusters"]
    for adapter, rows in score_sets.items():
        clusters = {
            "errors": sum(row.errored for row in rows),
            "false negatives": sum(row.false_negative for row in rows),
            "false positives": sum(row.false_positive for row in rows),
            "risk underestimates": sum(row.underestimated for row in rows),
            "blast misses": sum(
                row.blast_true_count > 0 and row.blast_recall < 1.0 for row in rows
            ),
            "simulator mismatches": sum(
                row.fidelity_pct is not None and row.fidelity_pct < 100.0 for row in rows
            ),
        }
        ranked = sorted(
            ((count, label) for label, count in clusters.items() if count),
            reverse=True,
        )[:3]
        detail = ", ".join(f"{label}={count}" for count, label in ranked) or "none"
        lines.append(f"{adapter}: {detail if rows else 'n/a'}")
    return lines


def _format_estimate(estimate: Estimate, style: str) -> str:
    if style == "percent":
        return f"{estimate.value * 100:.1f}% [{estimate.low * 100:.1f}, {estimate.high * 100:.1f}]"
    if style == "currency":
        return f"${estimate.value:.4f} [${estimate.low:.4f}, ${estimate.high:.4f}]"
    return f"{estimate.value:.0f} [{estimate.low:.0f}, {estimate.high:.0f}] ms"


def _format_row(row: list[str], widths: list[int]) -> str:
    return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="render the text report")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    print(render_text(EvalStore(args.db)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
