"""Versioned, deterministic severity rules for generated eval truth."""

from __future__ import annotations

import re
from typing import Any

from evals.core.keys import make_column_key, make_key
from evals.core.models import HealthReport, RiskLevel, Truth

GENERATOR_VERSION = "v1"

_DEPENDENCY_IN_ERROR = re.compile(
    r"\b(view|materialized view|constraint|function|trigger|index|table)\s+"
    r"[\"']?([a-zA-Z_][a-zA-Z0-9_$]*)[\"']?\s+depends\b",
    re.IGNORECASE,
)
_ERROR_TYPES = {
    "view": "view",
    "materialized view": "matview",
    "constraint": "constraint",
    "function": "function",
    "trigger": "trigger",
    "index": "index",
    "table": "table",
}


def classify_truth(
    *,
    task_id: str,
    pre_health: HealthReport,
    post_health: HealthReport,
    pre_snapshot: Any,
    post_snapshot: Any,
    migration_error: str | None,
) -> Truth:
    """Apply the ordered oracle rule table and return one truth artefact."""
    regressions = _health_regressions(pre_health, post_health)
    rows_lost = _rows_lost(pre_health, post_health)
    destructive = _destructive_schema_changes(pre_snapshot, post_snapshot, pre_health)
    blast_radius = set(regressions) | set(destructive)

    if migration_error:
        blast_radius.update(_dependencies_from_error(migration_error))
        return _truth(
            task_id,
            "breaking",
            True,
            "migration_error",
            migration_error,
            blast_radius,
            rows_lost,
            pre_snapshot,
            post_snapshot,
        )
    if regressions:
        return _truth(
            task_id,
            "breaking",
            True,
            "dependent_object_regression",
            None,
            blast_radius,
            rows_lost,
            pre_snapshot,
            post_snapshot,
        )
    if rows_lost or destructive:
        return _truth(
            task_id,
            "breaking",
            True,
            "data_or_schema_loss",
            None,
            blast_radius,
            rows_lost,
            pre_snapshot,
            post_snapshot,
        )
    if _risky_column_change(pre_snapshot, post_snapshot, pre_health):
        return _truth(
            task_id,
            "potentially_breaking",
            False,
            "risky_populated_column_change",
            None,
            blast_radius,
            rows_lost,
            pre_snapshot,
            post_snapshot,
        )
    if _is_additive(pre_snapshot, post_snapshot):
        return _truth(
            task_id,
            "safe",
            False,
            "additive_change",
            None,
            blast_radius,
            rows_lost,
            pre_snapshot,
            post_snapshot,
        )
    return _truth(
        task_id,
        "needs_review",
        False,
        "unclassified_change",
        None,
        blast_radius,
        rows_lost,
        pre_snapshot,
        post_snapshot,
    )


def _truth(
    task_id: str,
    risk: RiskLevel,
    must_block: bool,
    rule_fired: str,
    migration_error: str | None,
    blast_radius: set[str],
    rows_lost: dict[str, int],
    pre_snapshot: Any,
    post_snapshot: Any,
) -> Truth:
    return Truth(
        task_id=task_id,
        generator_version=GENERATOR_VERSION,
        migration_error=migration_error,
        risk=risk,
        must_block=must_block,
        blast_radius=sorted(blast_radius),
        rows_lost=rows_lost,
        rule_fired=rule_fired,
        real_pre_snapshot=pre_snapshot.model_dump(mode="json"),
        real_post_snapshot=post_snapshot.model_dump(mode="json"),
    )


def _health_regressions(pre: HealthReport, post: HealthReport) -> list[str]:
    post_by_key = post.by_key()
    regressions: set[str] = set()
    for key, before in pre.by_key().items():
        after = post_by_key.get(key)
        changed_query = (
            before.ok
            and after is not None
            and after.ok
            and key.startswith("query:")
            and before.detail != after.detail
        )
        if before.ok and (after is None or not after.ok or changed_query):
            regressions.add(key)
    pre_keys = pre.by_key()
    regressions.update(
        key for key, item in post_by_key.items() if key not in pre_keys and not item.ok
    )
    return sorted(regressions)


def _rows_lost(pre: HealthReport, post: HealthReport) -> dict[str, int]:
    losses: dict[str, int] = {}
    for table, before in pre.row_counts.items():
        after = post.row_counts.get(table, 0)
        if after < before:
            losses[table] = before - after
    return losses


def _destructive_schema_changes(
    pre_snapshot: Any,
    post_snapshot: Any,
    pre_health: HealthReport,
) -> list[str]:
    keys: set[str] = set()
    for table_name, table in pre_snapshot.tables.items():
        if pre_health.row_counts.get(table_name, 0) <= 0:
            continue
        post_table = post_snapshot.tables.get(table_name)
        if post_table is None:
            keys.add(make_key("table", table_name))
            continue
        for column_name in table.columns:
            if column_name not in post_table.columns:
                keys.add(make_column_key(table_name, column_name))
    for enum_name, enum in pre_snapshot.enums.items():
        post_enum = post_snapshot.enums.get(enum_name)
        if post_enum is None or not set(enum.values).issubset(post_enum.values):
            keys.add(make_key("enum", enum_name))
    return sorted(keys)


def _risky_column_change(pre_snapshot: Any, post_snapshot: Any, pre: HealthReport) -> bool:
    for table_name, table in pre_snapshot.tables.items():
        if pre.row_counts.get(table_name, 0) <= 0:
            continue
        post_table = post_snapshot.tables.get(table_name)
        if post_table is None:
            continue
        for column_name, column in table.columns.items():
            post_column = post_table.columns.get(column_name)
            if post_column is None:
                continue
            if column.nullable and not post_column.nullable:
                return True
            if _type_narrowed(column.type, post_column.type):
                return True
    return False


def _type_narrowed(before: str, after: str) -> bool:
    before_match = re.fullmatch(r"(?:var)?char\((\d+)\)", before.lower())
    after_match = re.fullmatch(r"(?:var)?char\((\d+)\)", after.lower())
    return bool(
        before_match and after_match and int(after_match.group(1)) < int(before_match.group(1))
    )


def _is_additive(pre_snapshot: Any, post_snapshot: Any) -> bool:
    for table_name, table in pre_snapshot.tables.items():
        post_table = post_snapshot.tables.get(table_name)
        if post_table is None or table.primary_key != post_table.primary_key:
            return False
        for column_name, column in table.columns.items():
            post_column = post_table.columns.get(column_name)
            if post_column is None or _dump(column) != _dump(post_column):
                return False
        if not _named_models_preserved(table.foreign_keys, post_table.foreign_keys):
            return False
        if not _named_models_preserved(table.indexes, post_table.indexes):
            return False
    for field in (
        "views",
        "triggers",
        "sequences",
        "functions",
        "policies",
        "materialized_views",
    ):
        before = getattr(pre_snapshot, field)
        after = getattr(post_snapshot, field)
        for name, value in before.items():
            if name not in after or _dump(value) != _dump(after[name]):
                return False
    for name, enum in pre_snapshot.enums.items():
        post_enum = post_snapshot.enums.get(name)
        if post_enum is None:
            return False
        retained = [value for value in post_enum.values if value in enum.values]
        if retained != enum.values:
            return False
    return True


def _named_models_preserved(before: list[Any], after: list[Any]) -> bool:
    after_by_name = {item.name: item for item in after}
    return all(
        item.name in after_by_name and _dump(item) == _dump(after_by_name[item.name])
        for item in before
    )


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _dependencies_from_error(message: str) -> list[str]:
    keys = {
        make_key(_ERROR_TYPES[kind.lower()], name)
        for kind, name in _DEPENDENCY_IN_ERROR.findall(message)
    }
    return sorted(keys)
