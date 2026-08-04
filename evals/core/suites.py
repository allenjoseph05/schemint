"""Discovery and loading for filesystem-backed eval suites."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from evals.core.models import EvalTask

DEFAULT_SUITES_ROOT = Path("evals") / "suites"


class SuiteError(ValueError):
    """Raised when a suite directory is incomplete or inconsistent."""


@dataclass(frozen=True)
class SuiteDefinition:
    """Resolved files for one eval task."""

    task: EvalTask
    directory: Path
    schema_path: Path
    seed_path: Path | None
    migration_path: Path
    probes_path: Path | None
    expected_path: Path
    meta_path: Path

    def schema_sql(self) -> str:
        return self.schema_path.read_text(encoding="utf-8")

    def seed_sql(self) -> str:
        return self.seed_path.read_text(encoding="utf-8") if self.seed_path else ""

    def migration_sql(self) -> str:
        return self.migration_path.read_text(encoding="utf-8")

    def probes_sql(self) -> str:
        return self.probes_path.read_text(encoding="utf-8") if self.probes_path else ""

    def truth_inputs(self) -> list[Path]:
        paths = [self.meta_path, self.schema_path, self.migration_path]
        if self.seed_path:
            paths.append(self.seed_path)
        if self.probes_path:
            paths.append(self.probes_path)
        return paths

    def input_hash(self) -> str:
        """Stable digest of every file that contributes to generated truth."""
        digest = hashlib.sha256()
        for path in sorted(self.truth_inputs(), key=lambda item: item.as_posix()):
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()


def discover_suites(root: str | Path = DEFAULT_SUITES_ROOT) -> list[SuiteDefinition]:
    """Load every task directory containing ``meta.json``."""
    suites_root = Path(root)
    if not suites_root.is_dir():
        raise SuiteError(f"Suite root does not exist: {suites_root}")
    suites = [_load_suite(path, suites_root) for path in suites_root.glob("*/meta.json")]
    return sorted(suites, key=lambda suite: suite.task.id)


def select_suites(
    task_ids: list[str] | None,
    root: str | Path = DEFAULT_SUITES_ROOT,
) -> list[SuiteDefinition]:
    """Return all suites or the explicitly requested task ids."""
    suites = discover_suites(root)
    if not task_ids:
        return suites
    by_id = {suite.task.id: suite for suite in suites}
    missing = sorted(set(task_ids) - set(by_id))
    if missing:
        raise SuiteError(f"Unknown task id(s): {', '.join(missing)}")
    return [by_id[task_id] for task_id in task_ids]


def _load_suite(meta_path: Path, suites_root: Path) -> SuiteDefinition:
    directory = meta_path.parent
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        task = EvalTask.model_validate({**payload, "directory": str(directory)})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SuiteError(f"Invalid suite metadata {meta_path}: {exc}") from exc
    if directory.name != task.id:
        raise SuiteError(f"Suite directory {directory.name!r} does not match task id {task.id!r}")

    schema_path = (
        (suites_root / task.shared_schema).resolve()
        if task.shared_schema
        else (directory / "schema.sql").resolve()
    )
    root_resolved = suites_root.resolve()
    if root_resolved not in schema_path.parents:
        raise SuiteError(f"Shared schema escapes suite root: {task.shared_schema!r}")
    migration_path = directory / "migration.sql"
    if not schema_path.is_file():
        raise SuiteError(f"Missing schema for {task.id}: {schema_path}")
    if not migration_path.is_file():
        raise SuiteError(f"Missing migration for {task.id}: {migration_path}")

    seed_path = directory / "seed.sql"
    probes_path = directory / "probes.sql"
    return SuiteDefinition(
        task=task,
        directory=directory,
        schema_path=schema_path,
        seed_path=seed_path if seed_path.is_file() else None,
        migration_path=migration_path,
        probes_path=probes_path if probes_path.is_file() else None,
        expected_path=directory / "expected.json",
        meta_path=meta_path,
    )
