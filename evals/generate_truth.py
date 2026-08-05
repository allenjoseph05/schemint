"""CLI for generating committed PostgreSQL truth artefacts."""

from __future__ import annotations

import argparse
import logging

from evals.core.suites import DEFAULT_SUITES_ROOT, select_suites
from evals.oracle.generate import generate_all


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Generate every discovered suite")
    parser.add_argument("--task", action="append", default=[], help="Generate one task id")
    parser.add_argument("--root", default=str(DEFAULT_SUITES_ROOT), help="Suite root")
    args = parser.parse_args()
    if not args.all and not args.task:
        parser.error("pass --all or at least one --task")
    if args.all and args.task:
        parser.error("--all and --task are mutually exclusive")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    suites = select_suites(None if args.all else args.task, args.root)
    truths = generate_all(suites)
    print(f"Generated {len(truths)} truth artefact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
