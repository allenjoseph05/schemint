"""Run migration evaluation adapters and compare stored results."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.adapters.base import ADAPTER_NAMES, get_adapter
from evals.core.runner import run_evaluations
from evals.core.store import DEFAULT_DB_PATH, EvalStore
from evals.core.suites import SuiteDefinition, discover_suites
from evals.report import render_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", choices=ADAPTER_NAMES)
    parser.add_argument(
        "--suite",
        default="all",
        help="all, fast, a category, or a comma-separated list of task ids",
    )
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--score-artifacts",
        action="store_true",
        help="execute generated rollback and alternative SQL in throwaway Postgres databases",
    )
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not args.adapter and not args.compare:
        parser.error("--adapter is required unless --compare is supplied")

    store = EvalStore(args.db)
    if args.adapter:
        suites = _select_suites(args.suite)
        summary = run_evaluations(
            get_adapter(args.adapter),
            suites,
            trials=args.trials,
            force=args.force,
            score_artifacts=args.score_artifacts,
            store=store,
        )
        print(
            f"{args.adapter}: {summary.completed} rows, {summary.errors} errors, "
            f"{summary.skipped} skipped, ${summary.cost_usd:.4f}"
        )
    if args.compare:
        print(render_text(store))
    return 0


def _select_suites(selector: str) -> list[SuiteDefinition]:
    suites = discover_suites()
    if selector == "all":
        return suites
    if selector == "fast":
        return suites[:5]
    categories = {suite.task.category for suite in suites}
    if selector in categories:
        return [suite for suite in suites if suite.task.category == selector]
    requested = selector.split(",")
    by_id = {suite.task.id: suite for suite in suites}
    missing = sorted(set(requested) - set(by_id))
    if missing:
        raise ValueError(f"Unknown suite selector(s): {', '.join(missing)}")
    return [by_id[task_id] for task_id in requested]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
