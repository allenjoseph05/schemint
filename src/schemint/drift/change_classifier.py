"""Change risk classifier — deterministic breaking-change detection.

Classifies every SchemaChangeEvent as safe, needs_review, potentially_breaking,
or breaking based on type compatibility rules and structural change semantics.

Design principles:
    - NO hardcoded type pairs. Uses type families (groups of compatible types)
      so new types are automatically handled by their family membership.
    - Type widening (e.g. int→bigint, varchar(50)→varchar(255)) is safe.
    - Type narrowing (e.g. bigint→int, varchar(255)→varchar(50)) is breaking.
    - Cross-family changes (e.g. integer→text) are always breaking.
    - Nullable→NOT NULL is breaking (existing NULLs would fail).
    - NOT NULL→nullable is safe (relaxes constraint).
    - FK action changes (CASCADE→RESTRICT) can break delete workflows.
"""

from __future__ import annotations

import re
from typing import Literal

from schemint.drift.models import SchemaChangeEvent

# =============================================================================
# Type Family System
# =============================================================================

# Each family is an ordered list from NARROWEST to WIDEST.
# Position in the list determines widening/narrowing direction.
# A type moving to a LATER position in the same family = safe widening.
# A type moving to an EARLIER position = potentially breaking narrowing.

TYPE_FAMILIES: dict[str, list[str]] = {
    # Numeric — narrowest to widest
    "integer": ["tinyint", "smallint", "integer", "int", "bigint"],
    "unsigned_integer": [
        "tinyint unsigned",
        "smallint unsigned",
        "integer unsigned",
        "bigint unsigned",
    ],
    "float": ["real", "float", "double", "double precision"],
    "decimal": ["decimal", "numeric", "money"],
    "serial": ["smallserial", "serial", "bigserial"],
    # String — narrowest to widest
    "string": ["char", "varchar", "citext", "text"],
    # Binary
    "binary": ["binary", "bytea", "blob"],
    # Date/Time — narrowest to widest
    "datetime": ["date", "time", "timestamp", "timestamptz"],
    "interval": ["interval"],
    # Boolean
    "boolean": ["boolean", "bool"],
    # JSON — json is text-based, jsonb is binary-indexed
    "json": ["json", "jsonb"],
    # UUID
    "uuid": ["uuid"],
    # Enum
    "enum": ["enum"],
    # Network types
    "network": ["inet", "cidr", "macaddr", "macaddr8"],
    # Range types
    "range": ["int4range", "int8range", "numrange", "tsrange", "tstzrange", "daterange"],
    # Full-text search
    "textsearch": ["tsvector", "tsquery"],
    # XML
    "xml": ["xml"],
    # Array — all array types in one family
    "array": ["array"],
    # Geometric
    "geometric": ["point", "line", "lseg", "box", "path", "polygon", "circle"],
    # Bit string
    "bit": ["bit", "bit varying", "varbit"],
}

# Reverse lookup: type_name → (family_name, position_in_family)
_TYPE_TO_FAMILY: dict[str, tuple[str, int]] = {}
for _family_name, _members in TYPE_FAMILIES.items():
    for _idx, _type_name in enumerate(_members):
        _TYPE_TO_FAMILY[_type_name] = (_family_name, _idx)


def _extract_base_type(type_str: str) -> str:
    """Extract base type name from a type string with optional parameters.

    "varchar(255)" → "varchar"
    "decimal(10,2)" → "decimal"
    "integer" → "integer"
    """
    match = re.match(r"^(\w+)", type_str.strip().lower())
    return match.group(1) if match else type_str.strip().lower()


def _extract_type_length(type_str: str) -> int | None:
    """Extract length/precision parameter from a type string.

    "varchar(255)" → 255
    "decimal(10,2)" → 10
    "integer" → None
    """
    match = re.match(r"^\w+\((\d+)", type_str.strip().lower())
    return int(match.group(1)) if match else None


# =============================================================================
# FK Action Risk
# =============================================================================

# Ordered from most restrictive to least restrictive.
# Moving toward MORE restrictive = potentially breaking (may block operations
# that previously succeeded).
# Moving toward LESS restrictive = safe (relaxes constraints).
_FK_ACTION_ORDER: dict[str, int] = {
    "restrict": 0,
    "no action": 1,
    "set null": 2,
    "set default": 3,
    "cascade": 4,
}


def _normalize_fk_action(action: str | None) -> str:
    """Normalize FK action to lowercase. None defaults to 'no action' (SQL standard)."""
    if action is None:
        return "no action"
    return action.strip().lower()


# =============================================================================
# Risk Classification Functions
# =============================================================================


def classify_type_change(
    old_type: str, new_type: str
) -> Literal["safe", "needs_review", "potentially_breaking", "breaking"]:
    """Classify the risk of a column type change.

    Rules:
        1. Same family, widening (later position) → safe
        2. Same family, narrowing (earlier position) → potentially_breaking
        3. Same family, same position but length increased → safe
        4. Same family, same position but length decreased → potentially_breaking
        5. Different families → breaking
        6. Unknown types (not in any family) → needs_review
    """
    old_base = _extract_base_type(old_type)
    new_base = _extract_base_type(new_type)

    # Identical types — check length changes only
    if old_base == new_base:
        old_len = _extract_type_length(old_type)
        new_len = _extract_type_length(new_type)

        if old_len is not None and new_len is not None:
            if new_len > old_len:
                return "safe"  # widening
            if new_len < old_len:
                return "potentially_breaking"  # narrowing
        return "safe"  # same type, no length change

    old_family = _TYPE_TO_FAMILY.get(old_base)
    new_family = _TYPE_TO_FAMILY.get(new_base)

    # Unknown type in either position — can't determine risk
    if old_family is None or new_family is None:
        return "needs_review"

    old_family_name, old_pos = old_family
    new_family_name, new_pos = new_family

    # Same family — compare position
    if old_family_name == new_family_name:
        if new_pos > old_pos:
            return "safe"  # widening within family
        if new_pos < old_pos:
            return "potentially_breaking"  # narrowing within family
        return "safe"  # same position (shouldn't happen, caught above)

    # Cross-family change is always breaking
    return "breaking"


def classify_fk_action_change(
    old_action: str | None, new_action: str | None
) -> Literal["safe", "needs_review", "potentially_breaking", "breaking"]:
    """Classify the risk of an FK action (ON DELETE/ON UPDATE) change.

    Moving to a MORE restrictive action = potentially_breaking.
    Moving to a LESS restrictive action = safe.
    Unknown actions = needs_review.
    """
    old_norm = _normalize_fk_action(old_action)
    new_norm = _normalize_fk_action(new_action)

    if old_norm == new_norm:
        return "safe"

    old_rank = _FK_ACTION_ORDER.get(old_norm)
    new_rank = _FK_ACTION_ORDER.get(new_norm)

    if old_rank is None or new_rank is None:
        return "needs_review"

    if new_rank < old_rank:
        return "potentially_breaking"  # more restrictive
    return "safe"  # less restrictive


def classify_change(
    event: SchemaChangeEvent,
) -> Literal["safe", "needs_review", "potentially_breaking", "breaking"]:
    """Classify the risk of any schema change event.

    This is the main entry point. Handles all change_type values.
    Returns a risk level that the differ can attach to the event.
    """
    ct = event.change_type

    # Table-level changes
    if ct == "table_dropped":
        return "breaking"
    if ct == "table_added":
        return "safe"
    if ct == "table_renamed":
        return "breaking"  # all downstream refs break

    # Column additions — NOT NULL without DEFAULT is breaking on non-empty tables
    if ct == "column_added":
        if event.new_value and "NOT NULL" in event.new_value and "DEFAULT" not in event.new_value:
            return "potentially_breaking"
        return "safe"

    # Column drops — always breaking
    if ct == "column_dropped":
        return "breaking"

    # Type changes — delegate to type family logic
    if ct == "column_type_change":
        if event.old_value and event.new_value:
            return classify_type_change(event.old_value, event.new_value)
        return "needs_review"

    # Nullable changes
    if ct == "column_nullable_change":
        if event.old_value == "True" and event.new_value == "False":
            return "potentially_breaking"  # nullable → NOT NULL
        if event.old_value == "False" and event.new_value == "True":
            return "safe"  # NOT NULL → nullable (relaxes)
        return "needs_review"

    # Default changes
    if ct == "column_default_change":
        if event.old_value is None and event.new_value is not None:
            return "safe"  # adding a default
        if event.old_value is not None and event.new_value is None:
            return "needs_review"  # removing a default
        return "needs_review"  # changing default value

    # Constraint changes
    if ct == "column_constraint_change":
        return "needs_review"

    # Primary key changes — always structurally significant
    if ct == "pk_added":
        return "needs_review"
    if ct == "pk_dropped":
        return "breaking"  # removes uniqueness + NOT NULL guarantee
    if ct == "pk_changed":
        return "potentially_breaking"  # may invalidate FK references

    # Index changes — generally safe (performance only)
    if ct == "index_added":
        return "safe"
    if ct == "index_dropped":
        return "needs_review"  # may affect query performance
    if ct == "index_changed":
        return "needs_review"  # property change (e.g. uniqueness)

    # FK changes
    if ct == "fk_added":
        return "potentially_breaking"  # may reject existing data
    if ct == "fk_dropped":
        return "needs_review"  # relaxes constraint but loses referential integrity

    # FK action changes — delegate to FK action logic
    if ct == "fk_action_change":
        return classify_fk_action_change(event.old_value, event.new_value)

    # View changes
    if ct == "view_added":
        return "safe"
    if ct == "view_dropped":
        return "breaking"  # downstream queries may depend on the view
    if ct == "view_definition_change":
        return "needs_review"

    # Trigger changes
    if ct == "trigger_added":
        return "needs_review"  # new side effects on table operations
    if ct == "trigger_dropped":
        return "needs_review"  # may remove expected side effects
    if ct == "trigger_changed":
        return "needs_review"

    # Sequence changes
    if ct == "sequence_added":
        return "safe"
    if ct == "sequence_dropped":
        return "potentially_breaking"  # auto-increment columns depend on it
    if ct == "sequence_changed":
        return "needs_review"  # increment/bounds change may affect inserts

    # Enum changes
    if ct == "enum_added":
        return "safe"
    if ct == "enum_dropped":
        return "breaking"  # columns referencing this type break
    if ct == "enum_value_added":
        return "safe"  # additive, existing data unaffected
    if ct == "enum_value_removed":
        return "breaking"  # existing rows may reference removed value

    # Function changes
    if ct == "function_added":
        return "safe"
    if ct == "function_dropped":
        return "potentially_breaking"  # triggers/views may depend on it
    if ct == "function_changed":
        return "needs_review"  # silent behavior change for all callers

    # Extension changes
    if ct == "extension_added":
        return "safe"
    if ct == "extension_dropped":
        return "breaking"  # all objects using extension types/operators break
    if ct == "extension_version_changed":
        return "needs_review"  # may change function behavior

    # Permission changes
    if ct == "permission_granted":
        return "safe"  # additive, no existing access broken
    if ct == "permission_revoked":
        return "potentially_breaking"  # application queries may fail

    # RLS Policy changes
    if ct == "policy_added":
        return "potentially_breaking"  # may silently filter existing queries
    if ct == "policy_dropped":
        return "potentially_breaking"  # may expose previously hidden rows
    if ct == "policy_changed":
        return "needs_review"  # may change visible data set

    # Partition changes
    if ct == "partition_added":
        return "safe"  # extends data range
    if ct == "partition_dropped":
        return "breaking"  # data in that partition is lost/inaccessible

    # Materialized view changes
    if ct == "matview_added":
        return "safe"
    if ct == "matview_dropped":
        return "breaking"  # queries referencing it break
    if ct == "matview_definition_changed":
        return "needs_review"  # next REFRESH will produce different data

    return "needs_review"
