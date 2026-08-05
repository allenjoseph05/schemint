"""Structural fidelity of AlterApplier predictions against live Postgres."""

from __future__ import annotations

from typing import Any


def snapshot_fidelity(
    real: dict[str, Any] | None, predicted: dict[str, Any] | None
) -> float | None:
    """Return Jaccard similarity over stable structural schema facts, as a percentage."""
    if real is None or predicted is None:
        return None
    real_facts = schema_facts(real)
    predicted_facts = schema_facts(predicted)
    union = real_facts | predicted_facts
    if not union:
        return 100.0
    return round(100.0 * len(real_facts & predicted_facts) / len(union), 3)


def schema_facts(snapshot: dict[str, Any]) -> set[tuple[str, ...]]:
    """Extract stable schema facts, excluding timestamps and runtime statistics."""
    facts: set[tuple[str, ...]] = set()
    tables = snapshot.get("tables") or {}
    for table_name, table in tables.items():
        table_key = _norm(table_name)
        facts.add(("table", table_key))
        for column_name, column in (table.get("columns") or {}).items():
            column_key = _norm(column_name)
            facts.add(("column", table_key, column_key))
            facts.add(("type", table_key, column_key, _norm(column.get("type"))))
            facts.add(("nullable", table_key, column_key, str(bool(column.get("nullable")))))
            facts.add(("default", table_key, column_key, _default(column.get("default"))))
        for position, column_name in enumerate(table.get("primary_key") or []):
            facts.add(("primary_key", table_key, str(position), _norm(column_name)))
        for foreign_key in table.get("foreign_keys") or []:
            facts.add(
                (
                    "foreign_key",
                    table_key,
                    _norm(foreign_key.get("name")),
                    _norm(foreign_key.get("column")),
                    _norm(foreign_key.get("references_table")),
                    _norm(foreign_key.get("references_column")),
                    _action(foreign_key.get("on_delete")),
                    _action(foreign_key.get("on_update")),
                )
            )
        for index in table.get("indexes") or []:
            facts.add(
                (
                    "index",
                    table_key,
                    _norm(index.get("name")),
                    ",".join(_norm(column) for column in index.get("columns") or []),
                    str(bool(index.get("is_unique"))),
                    str(bool(index.get("is_primary"))),
                )
            )
    for field in (
        "views",
        "triggers",
        "sequences",
        "enums",
        "functions",
        "policies",
        "materialized_views",
    ):
        for name, value in (snapshot.get(field) or {}).items():
            facts.update(_object_facts(field, name, value))
    return facts


def _object_facts(field: str, name: Any, value: Any) -> set[tuple[str, ...]]:
    """Flatten a schema object into independently comparable field facts."""
    prefix = (field, _norm(name))
    facts: set[tuple[str, ...]] = {prefix}

    def visit(path: tuple[str, ...], item: Any) -> None:
        if isinstance(item, dict):
            for key, child in sorted(item.items()):
                if key in {"last_value", "is_populated"}:
                    continue
                visit((*path, _norm(key)), child)
        elif isinstance(item, list):
            for position, child in enumerate(item):
                visit((*path, str(position)), child)
        else:
            facts.add((*prefix, *path, _norm(item)))

    visit((), value)
    return facts


def _norm(value: Any) -> str:
    return str(value or "").strip().strip('"').lower()


def _default(value: Any) -> str:
    if value is None:
        return "<none>"
    return " ".join(_norm(value).split())


def _action(value: Any) -> str:
    return _norm(value or "no action").replace("_", " ")
