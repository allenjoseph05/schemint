"""Tests for the ordered, deterministic eval truth rules."""

from __future__ import annotations

import pytest

from evals.core.models import HealthReport, ObjectHealth
from evals.oracle.rules import classify_truth
from schemint.drift.models import ColumnSnapshot, SchemaSnapshot, TableSnapshot

pytestmark = pytest.mark.unit


def _snapshot(*, nullable: bool = True, include_email: bool = True, extra: bool = False):
    columns = {"id": ColumnSnapshot(name="id", type="integer", nullable=False)}
    if include_email:
        columns["email"] = ColumnSnapshot(name="email", type="text", nullable=nullable)
    if extra:
        columns["timezone"] = ColumnSnapshot(name="timezone", type="text", nullable=True)
    return SchemaSnapshot(
        snapshot_id="test",
        source="live_db",
        tables={"users": TableSnapshot(name="users", columns=columns, primary_key=["id"])},
    )


def _health(*objects: ObjectHealth, rows: int = 1) -> HealthReport:
    return HealthReport(objects=list(objects), row_counts={"users": rows})


def test_migration_error_is_breaking_and_extracts_dependency() -> None:
    truth = classify_truth(
        task_id="drop_column",
        pre_health=_health(),
        post_health=_health(),
        pre_snapshot=_snapshot(),
        post_snapshot=_snapshot(),
        migration_error="DETAIL: view user_directory depends on column email",
    )
    assert truth.risk == "breaking"
    assert truth.must_block is True
    assert truth.rule_fired == "migration_error"
    assert truth.blast_radius == ["view:user_directory"]


def test_changed_probe_result_is_breaking() -> None:
    before = ObjectHealth(key="query:contract", ok=True, detail='{"rows":[[2]]}')
    after = ObjectHealth(key="query:contract", ok=True, detail='{"rows":[[1]]}')
    truth = classify_truth(
        task_id="policy_change",
        pre_health=_health(before),
        post_health=_health(after),
        pre_snapshot=_snapshot(),
        post_snapshot=_snapshot(),
        migration_error=None,
    )
    assert truth.risk == "breaking"
    assert truth.blast_radius == ["query:contract"]


def test_populated_column_removal_is_breaking() -> None:
    truth = classify_truth(
        task_id="drop_email",
        pre_health=_health(),
        post_health=_health(),
        pre_snapshot=_snapshot(),
        post_snapshot=_snapshot(include_email=False),
        migration_error=None,
    )
    assert truth.rule_fired == "data_or_schema_loss"
    assert truth.blast_radius == ["column:users.email"]


def test_not_null_on_populated_table_is_potentially_breaking() -> None:
    truth = classify_truth(
        task_id="not_null",
        pre_health=_health(),
        post_health=_health(),
        pre_snapshot=_snapshot(nullable=True),
        post_snapshot=_snapshot(nullable=False),
        migration_error=None,
    )
    assert truth.risk == "potentially_breaking"
    assert truth.must_block is False


def test_additive_column_is_safe() -> None:
    truth = classify_truth(
        task_id="add_timezone",
        pre_health=_health(),
        post_health=_health(),
        pre_snapshot=_snapshot(),
        post_snapshot=_snapshot(extra=True),
        migration_error=None,
    )
    assert truth.risk == "safe"
    assert truth.rule_fired == "additive_change"
