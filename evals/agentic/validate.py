"""Validate the 60-task agent corpus without importing or calling Anthropic."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.agentic.suites import DEFAULT_CATALOG, discover_agent_suites, validate_agent_suites


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--allow-missing-hashes",
        action="store_true",
        help="authoring only; committed corpus must contain fresh hashes",
    )
    args = parser.parse_args()
    suites = discover_agent_suites(args.catalog)
    errors = validate_agent_suites(
        suites,
        require_fresh_hashes=not args.allow_missing_hashes,
    )
    if errors:
        print("Agent suite validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Agent suite validation passed: {len(suites)} tasks")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
