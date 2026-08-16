"""Run the paid AgentAnalyzer trajectory evaluation with explicit consent."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.agentic.runner import run_agent_evaluations
from evals.agentic.store import DEFAULT_AGENT_DB, AgentEvalStore
from evals.agentic.suites import (
    DEFAULT_CATALOG,
    discover_agent_suites,
    select_agent_suites,
    validate_agent_suites,
)

PAID_CONFIRMATION = "RUN_PAID_AGENT_EVAL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--suite", default="all")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--budget-usd", type=float, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_AGENT_DB)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--confirm-paid",
        help=f"must equal {PAID_CONFIRMATION!r}; prevents accidental API spending",
    )
    args = parser.parse_args()
    if args.confirm_paid != PAID_CONFIRMATION:
        parser.error(f"--confirm-paid must equal {PAID_CONFIRMATION!r}")

    suites = discover_agent_suites(args.catalog)
    errors = validate_agent_suites(suites)
    if errors:
        parser.error("agent corpus is invalid:\n- " + "\n- ".join(errors))
    selected = select_agent_suites(suites, args.suite)
    summary = run_agent_evaluations(
        selected,
        trials=args.trials,
        budget_usd=args.budget_usd,
        force=args.force,
        store=AgentEvalStore(args.db),
    )
    print(
        f"agent_analyzer: {summary.completed} rows, {summary.errors} errors, "
        f"{summary.skipped} skipped, ${summary.cost_usd:.4f}"
    )
    if summary.budget_exhausted:
        print("Budget cap reached; remaining cells were not started.")
        return 2
    return 1 if summary.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
