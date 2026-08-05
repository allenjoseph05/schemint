"""Text comparison report for stored evaluation results."""

from __future__ import annotations

import argparse
import html
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
    (
        "breaking_never_underestimates",
        "Never underestimates breaking tasks",
        "percent",
    ),
    ("blast_recall", "Blast recall", "percent"),
    ("blast_precision", "Blast precision", "percent"),
    ("simulator_fidelity", "Simulator fidelity", "percent"),
    ("blocked_accuracy", "Block accuracy", "percent"),
    ("escalation_accuracy", "Escalation accuracy", "percent"),
    ("injection_resistance", "Injection resistance", "percent"),
    ("injection_decision_delta", "Injection risk delta", "number"),
    ("rollback_executes", "Rollback executes", "percent"),
    ("rollback_restores", "Rollback restores", "percent"),
    ("alternative_executes", "Alternative executes", "percent"),
    ("alternative_safe", "Alternative safe", "percent"),
    ("alternative_changes_schema", "Alternative changes schema", "percent"),
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
                    _format_estimate(summary.metrics[metric], style)
                    if summary and metric in summary.metrics
                    else "n/a"
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


def render_html(store: EvalStore | None = None) -> str:
    """Render a self-contained, screenshot-ready comparison report."""
    result_store = store or EvalStore()
    score_sets = {name: _latest_scores(result_store, name) for name in ADAPTER_NAMES}
    summaries = {
        name: aggregate_scores(rows) if rows else None for name, rows in score_sets.items()
    }
    metric_rows = []
    for metric, label, style in _METRICS:
        cells = []
        for adapter in ADAPTER_NAMES:
            summary = summaries[adapter]
            value = (
                _format_estimate(summary.metrics[metric], style)
                if summary and metric in summary.metrics
                else "n/a"
            )
            cells.append(f"<td>{html.escape(value)}</td>")
        metric_rows.append(f"<tr><th>{html.escape(label)}</th>{''.join(cells)}</tr>")

    categories = sorted({row.category for rows in score_sets.values() for row in rows})
    category_rows = []
    for category in categories:
        cells = []
        for adapter in ADAPTER_NAMES:
            rows = [row for row in score_sets[adapter] if row.category == category]
            value = f"{sum(row.correct for row in rows) / len(rows) * 100:.1f}%" if rows else "n/a"
            cells.append(f"<td>{value}</td>")
        category_rows.append(f"<tr><th>{html.escape(category)}</th>{''.join(cells)}</tr>")

    headers = "".join(f"<th>{html.escape(name)}</th>" for name in ADAPTER_NAMES)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Schemint evaluation report</title>
<style>
:root {{ color-scheme: light; --ink:#17202a; --muted:#5d6772; --line:#d7dce1; --panel:#f7f8fa; --accent:#006d5b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:#fff; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
header {{ padding:28px 32px 20px; border-bottom:3px solid var(--accent); }}
h1 {{ margin:0; font-size:28px; letter-spacing:0; }}
header p {{ margin:6px 0 0; color:var(--muted); }}
main {{ padding:24px 32px 40px; max-width:1500px; margin:0 auto; }}
section {{ margin:0 0 28px; }}
h2 {{ margin:0 0 10px; font-size:17px; letter-spacing:0; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; }}
table {{ width:100%; border-collapse:collapse; min-width:920px; }}
th,td {{ padding:9px 12px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
thead th {{ background:var(--panel); font-weight:650; }}
tbody th {{ font-weight:600; }}
tbody tr:last-child th,tbody tr:last-child td {{ border-bottom:0; }}
.note {{ color:var(--muted); font-size:13px; }}
</style>
</head>
<body>
<header><h1>Schemint evaluation report</h1><p>Generated from the latest append-only result for each adapter configuration.</p></header>
<main>
<section><h2>Adapter comparison</h2><div class="table-wrap"><table><thead><tr><th>Metric</th>{headers}</tr></thead><tbody>{"".join(metric_rows)}</tbody></table></div><p class="note">Values include bootstrap 95% confidence intervals where trials are available.</p></section>
<section><h2>Accuracy by category</h2><div class="table-wrap"><table><thead><tr><th>Category</th>{headers}</tr></thead><tbody>{"".join(category_rows)}</tbody></table></div></section>
</main>
</body>
</html>"""


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
        rows = [row for row in rows if row.classification_scored]
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
    if style == "number":
        return f"{estimate.value:.2f} [{estimate.low:.2f}, {estimate.high:.2f}]"
    return f"{estimate.value:.0f} [{estimate.low:.0f}, {estimate.high:.0f}] ms"


def _format_row(row: list[str], widths: list[int]) -> str:
    return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="render the text report")
    parser.add_argument("--html", type=Path, help="write a self-contained HTML report")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    store = EvalStore(args.db)
    if args.html:
        args.html.write_text(render_html(store), encoding="utf-8")
        print(f"Wrote {args.html}")
    if args.text or not args.html:
        print(render_text(store))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
