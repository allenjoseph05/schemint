"""Type normalization for SQL column types.

Extracted from snapshot.py to provide a reusable, testable type normalizer.
The canonical type mapping ensures stable comparisons across DDL variants.
"""

from __future__ import annotations

import re

from schemint.drift.constants import CANONICAL_TYPES


class TypeNormalizer:
    """Normalizes SQL type strings to canonical lowercase form.

    Handles types with parameters: "VARCHAR(255)" -> "varchar(255)".
    Handles bare types: "INT" -> "integer".
    Unknown types are lowercased but not mapped.

    The canonical mapping can be extended at construction time for
    project-specific type aliases.
    """

    def __init__(self, extra_mappings: dict[str, str] | None = None):
        self._mappings = dict(CANONICAL_TYPES)
        if extra_mappings:
            self._mappings.update(extra_mappings)

    def canonicalize(self, raw_type: str) -> str:
        """Normalize a SQL type string to canonical lowercase form."""
        raw_lower = raw_type.strip().lower()

        match = re.match(r"^(\w+)(.*)", raw_lower)
        if not match:
            return raw_lower

        base = match.group(1)
        params = match.group(2).strip()

        canonical_base = self._mappings.get(base, base)
        if params:
            return f"{canonical_base}{params}"
        return canonical_base


# Module-level convenience instance and function.
_default_normalizer = TypeNormalizer()


def canonicalize_type(raw_type: str) -> str:
    """Normalize a SQL type string using the default type mappings."""
    return _default_normalizer.canonicalize(raw_type)
