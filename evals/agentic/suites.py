"""Discovery, validation, and freshness hashing for agent-eval tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from evals.agentic.models import AgentSuiteRecord

DEFAULT_AGENT_ROOT = Path("evals") / "agent_suites"
DEFAULT_CATALOG = DEFAULT_AGENT_ROOT / "catalog.json"
EXPECTED_CATEGORY_COUNTS = {
    "structural": 8,
    "performance": 8,
    "security": 8,
    "convention": 8,
    "memory": 6,
    "adversarial": 8,
    "clean": 6,
    "scale": 8,
}


class AgentSuiteError(ValueError):
    """Raised when the versioned agent corpus is invalid or stale."""


@dataclass(frozen=True)
class AgentSuiteDefinition:
    record: AgentSuiteRecord
    root: Path
    schema_path: Path

    def schema_sql(self) -> str:
        return self.schema_path.read_text(encoding="utf-8")

    def input_hash(self) -> str:
        """Hash every model-visible input, excluding expected truth."""
        task = self.record.task
        visible = task.model_dump(exclude={"tool_faults"})
        digest = hashlib.sha256()
        digest.update(json.dumps(visible, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\0")
        digest.update(self.schema_path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
        # Faults are also model-visible, but only after the relevant tool call.
        digest.update(
            json.dumps(
                [fault.model_dump() for fault in task.tool_faults],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        return digest.hexdigest()


def discover_agent_suites(
    catalog: str | Path = DEFAULT_CATALOG,
) -> list[AgentSuiteDefinition]:
    catalog_path = Path(catalog)
    if not catalog_path.is_file():
        raise AgentSuiteError(f"Agent suite catalog does not exist: {catalog_path}")
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        records = [AgentSuiteRecord.model_validate(item) for item in payload]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AgentSuiteError(f"Invalid agent suite catalog: {exc}") from exc

    root = catalog_path.parent.resolve()
    suites: list[AgentSuiteDefinition] = []
    for record in records:
        schema_path = (root / record.task.schema_file).resolve()
        if root not in schema_path.parents:
            raise AgentSuiteError(
                f"Schema path escapes agent suite root: {record.task.schema_file!r}"
            )
        if not schema_path.is_file():
            raise AgentSuiteError(f"Missing schema for {record.task.id}: {schema_path}")
        suites.append(AgentSuiteDefinition(record=record, root=root, schema_path=schema_path))
    return sorted(suites, key=lambda suite: suite.record.task.id)


def validate_agent_suites(
    suites: list[AgentSuiteDefinition],
    *,
    require_fresh_hashes: bool = True,
) -> list[str]:
    errors: list[str] = []
    ids = [suite.record.task.id for suite in suites]
    if len(suites) != 60:
        errors.append(f"expected exactly 60 agent tasks, found {len(suites)}")
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        errors.append(f"duplicate task IDs: {', '.join(duplicates)}")

    counts = dict.fromkeys(EXPECTED_CATEGORY_COUNTS, 0)
    for suite in suites:
        counts[suite.record.task.category] += 1
        truth = suite.record.truth
        if require_fresh_hashes and truth.input_hash != suite.input_hash():
            errors.append(f"{truth.task_id}: stale or missing input_hash")
        expected_ids = [
            finding.id for finding in [*truth.required_findings, *truth.optional_findings]
        ]
        if len(expected_ids) != len(set(expected_ids)):
            errors.append(f"{truth.task_id}: duplicate expected finding IDs")
        if len(truth.required_tools) != len(set(truth.required_tools)):
            errors.append(f"{truth.task_id}: duplicate required tools")
        if len(truth.required_inspections) != len(set(truth.required_inspections)):
            errors.append(f"{truth.task_id}: duplicate required inspections")

    for category, expected in EXPECTED_CATEGORY_COUNTS.items():
        if counts[category] != expected:
            errors.append(f"{category}: expected {expected} tasks, found {counts[category]}")

    pairs: dict[str, set[str]] = {}
    for suite in suites:
        task = suite.record.task
        if task.injection_pair and task.injection_role:
            pairs.setdefault(task.injection_pair, set()).add(task.injection_role)
    for pair, roles in pairs.items():
        if roles != {"control", "attack"}:
            errors.append(f"injection pair {pair!r} must have control and attack")
    return errors


def select_agent_suites(
    suites: list[AgentSuiteDefinition], selector: str
) -> list[AgentSuiteDefinition]:
    if selector == "all":
        return suites
    if selector == "fast":
        return suites[:5]
    if selector in EXPECTED_CATEGORY_COUNTS:
        return [suite for suite in suites if suite.record.task.category == selector]
    requested = selector.split(",")
    by_id = {suite.record.task.id: suite for suite in suites}
    missing = sorted(set(requested) - set(by_id))
    if missing:
        raise AgentSuiteError(f"Unknown agent task(s): {', '.join(missing)}")
    return [by_id[task_id] for task_id in requested]
