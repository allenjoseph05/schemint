"""The blast-radius key namespace.

Every blast-radius element — whether produced by the oracle from a live
database or by an adapter from schemint's own analysis — is identified by a
single string of the form ``{object_type}:{name}``.

Precision and recall are set overlap on these strings, so both sides MUST
build keys through this module. Nothing else constructs them by hand: a
namespace mismatch turns every score into silent noise rather than a visible
failure.

Names are normalized (lowercased, unquoted, ``public.`` stripped) because the
oracle reads ``pg_catalog`` while adapters read parsed DDL, and the two
disagree on casing and schema qualification for the same object.
"""

from __future__ import annotations

from typing import Literal, get_args

ObjectType = Literal[
    "table",
    "column",
    "view",
    "matview",
    "trigger",
    "foreign_key",
    "index",
    "function",
    "sequence",
    "enum",
    "policy",
    "constraint",
    "query",
]

VALID_OBJECT_TYPES: frozenset[str] = frozenset(get_args(ObjectType))

# Schemas stripped from qualified names. Objects in other schemas keep their
# qualifier so cross-schema tasks stay distinguishable.
_IMPLICIT_SCHEMAS = ("public.",)


class BlastRadiusKeyError(ValueError):
    """Raised for a malformed or unknown blast-radius key."""


def normalize_name(name: str) -> str:
    """Normalize an object name for use in a key.

    Lowercases, strips double quotes and surrounding whitespace, and drops an
    implicit ``public.`` qualifier. Applied to each dotted part separately so
    ``"Users"."Email"`` and ``users.email`` collapse to the same string.
    """
    cleaned = name.strip().strip('"')
    parts = [part.strip().strip('"').lower() for part in cleaned.split(".")]
    joined = ".".join(part for part in parts if part)

    for schema in _IMPLICIT_SCHEMAS:
        if joined.startswith(schema):
            joined = joined[len(schema) :]
            break

    return joined


def make_key(obj_type: str, name: str) -> str:
    """Build a blast-radius key. Raises on an unknown object type."""
    if obj_type not in VALID_OBJECT_TYPES:
        raise BlastRadiusKeyError(
            f"Unknown object type {obj_type!r}. "
            f"Valid types: {', '.join(sorted(VALID_OBJECT_TYPES))}"
        )
    normalized = normalize_name(name)
    if not normalized:
        raise BlastRadiusKeyError(f"Empty object name for type {obj_type!r}")
    return f"{obj_type}:{normalized}"


def make_column_key(table: str, column: str) -> str:
    """Build a column key from separate table and column names."""
    return make_key("column", f"{normalize_name(table)}.{normalize_name(column)}")


def parse_key(key: str) -> tuple[str, str]:
    """Split a key back into ``(object_type, name)``. Raises if malformed.

    Exactly one colon is allowed. Normalized names never contain one, so a
    second colon means the key was assembled by string concatenation instead
    of ``make_key`` — that should fail loudly rather than parse into a
    plausible-looking type and a nonsense name.
    """
    obj_type, sep, name = key.partition(":")
    if not sep:
        raise BlastRadiusKeyError(f"Malformed key {key!r}: expected '<type>:<name>'")
    if obj_type not in VALID_OBJECT_TYPES:
        raise BlastRadiusKeyError(f"Malformed key {key!r}: unknown object type {obj_type!r}")
    if not name:
        raise BlastRadiusKeyError(f"Malformed key {key!r}: empty name")
    if ":" in name:
        raise BlastRadiusKeyError(f"Malformed key {key!r}: name contains a ':'")
    return obj_type, name


def is_valid_key(key: str) -> bool:
    """True if the key parses cleanly."""
    try:
        parse_key(key)
    except BlastRadiusKeyError:
        return False
    return True


def key_set(keys: list[str]) -> set[str]:
    """Normalize an iterable of keys into a deduplicated set.

    Adapters emit keys built from parsed DDL, which can carry quoting or
    casing the oracle's catalog reads do not. Round-tripping through
    ``parse_key``/``make_key`` makes both sides comparable. Unparseable keys
    are dropped rather than raising — a malformed key from an adapter is a
    scoring miss, not a harness crash.
    """
    out: set[str] = set()
    for key in keys:
        try:
            obj_type, name = parse_key(key)
        except BlastRadiusKeyError:
            continue
        out.add(make_key(obj_type, name))
    return out
