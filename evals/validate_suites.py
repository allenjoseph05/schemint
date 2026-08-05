"""Validate eval suite structure, balance, freshness, and generated truth."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from evals.core.models import TaskCategory, Truth, is_breaking
from evals.core.suites import DEFAULT_SUITES_ROOT, SuiteError, discover_suites
from evals.oracle.rules import GENERATOR_VERSION


def validate(root: str | Path = DEFAULT_SUITES_ROOT) -> list[str]:
    """Return every validation error; an empty list means the suites are valid."""
    errors: list[str] = []
    try:
        suites = discover_suites(root)
    except SuiteError as exc:
        return [str(exc)]
    ids = [suite.task.id for suite in suites]
    duplicates = sorted(task_id for task_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate task ids: {', '.join(duplicates)}")
    counts = Counter(suite.task.category for suite in suites)
    expected_counts: dict[TaskCategory, int] = {
        "breaking": 15,
        "safe": 15,
        "subtle": 15,
        "adversarial": 10,
        "ambiguous": 5,
    }
    if len(suites) != 60:
        errors.append(f"Expected 60 tasks, found {len(suites)}")
    for category, expected_count in expected_counts.items():
        if counts[category] != expected_count:
            errors.append(
                f"Expected {expected_count} {category} tasks, found {counts[category]}"
            )

    seen_pairs: dict[str, str] = {}
    for suite in suites:
        pair_hash = hashlib.sha256(
            f"{suite.schema_sql()}\0{suite.migration_sql()}".encode()
        ).hexdigest()
        if pair_hash in seen_pairs:
            errors.append(
                f"Duplicate schema/migration pair: {seen_pairs[pair_hash]} and {suite.task.id}"
            )
        seen_pairs[pair_hash] = suite.task.id
        if not suite.expected_path.is_file():
            errors.append(f"{suite.task.id}: missing expected.json")
            continue
        try:
            truth = Truth.model_validate_json(suite.expected_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{suite.task.id}: invalid expected.json: {exc}")
            continue
        if truth.task_id != suite.task.id:
            errors.append(f"{suite.task.id}: truth task_id is {truth.task_id!r}")
        if truth.generator_version != GENERATOR_VERSION:
            errors.append(
                f"{suite.task.id}: generator {truth.generator_version}, expected {GENERATOR_VERSION}"
            )
        if truth.input_hash != suite.input_hash():
            errors.append(f"{suite.task.id}: expected.json is stale for its current inputs")
        if not truth.rule_fired:
            errors.append(f"{suite.task.id}: truth has no rule_fired")
        expected_breaking = suite.task.category == "breaking"
        if suite.task.category in ("breaking", "safe") and is_breaking(
            truth.risk
        ) != expected_breaking:
            errors.append(
                f"{suite.task.id}: category {suite.task.category} conflicts with risk {truth.risk}"
            )
        if suite.task.category == "ambiguous" and suite.task.expected_outcome != "escalate":
            errors.append(f"{suite.task.id}: ambiguous task must expect escalation")

    injection_pairs: dict[str, set[str]] = {}
    for suite in suites:
        if suite.task.injection_pair and suite.task.injection_role:
            injection_pairs.setdefault(suite.task.injection_pair, set()).add(
                suite.task.injection_role
            )
    for pair, roles in injection_pairs.items():
        if roles != {"control", "attack"}:
            errors.append(f"Injection pair {pair!r} must have control and attack tasks")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_SUITES_ROOT), help="Suite root")
    args = parser.parse_args()
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Suite validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
