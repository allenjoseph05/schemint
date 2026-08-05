"""Shared live-Postgres helpers for generated SQL scoring."""

from __future__ import annotations

import hashlib
from typing import Any

from evals.core.models import HealthReport
from evals.core.suites import SuiteDefinition
from evals.oracle.health import capture_health, parse_probe_queries
from evals.oracle.postgres import PostgresFixture
from evals.scorers.fidelity import schema_facts
from schemint.drift.snapshot_pkg.live_db_capture import LiveDBSnapshotCapture


def database_name(kind: str, task_id: str, trial: int, item: int = 0) -> str:
    digest = hashlib.sha256(f"{kind}:{task_id}:{trial}:{item}".encode()).hexdigest()[:12]
    return f"score_{kind}_{digest}"


def prepare_database(pg: PostgresFixture, dbname: str, suite: SuiteDefinition) -> None:
    pg.create_database(dbname)
    pg.apply_sql(dbname, suite.schema_sql())
    pg.apply_sql(dbname, suite.seed_sql())


def capture_state(
    pg: PostgresFixture, dbname: str, suite: SuiteDefinition
) -> tuple[dict[str, Any], HealthReport]:
    probes = parse_probe_queries(suite.probes_sql())
    with pg.connect(dbname) as connection:
        health = capture_health(connection, probes)
    snapshot = LiveDBSnapshotCapture().capture(pg.url_for(dbname)).model_dump(mode="json")
    return snapshot, health


def health_restored(before: HealthReport, after: HealthReport) -> bool:
    before_objects = {key: (item.ok, item.detail) for key, item in before.by_key().items()}
    after_objects = {key: (item.ok, item.detail) for key, item in after.by_key().items()}
    return before.row_counts == after.row_counts and before_objects == after_objects


def health_preserved(before: HealthReport, after: HealthReport) -> bool:
    after_by_key = after.by_key()
    objects_ok = all(
        not item.ok or (key in after_by_key and after_by_key[key].ok)
        for key, item in before.by_key().items()
    )
    rows_preserved = all(
        after.row_counts.get(table, 0) >= count for table, count in before.row_counts.items()
    )
    return objects_ok and rows_preserved


def schema_restored(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return schema_facts(before) == schema_facts(after)
