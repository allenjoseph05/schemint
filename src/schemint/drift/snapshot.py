"""Snapshot service — thin facade delegating to snapshot_pkg subpackage.

Design constraints:
    - Snapshots are schema-scoped (single schema, e.g. "public").
    - Multi-schema and cross-database capture is NOT supported.
    - Cross-schema foreign keys are captured as-is (the referenced table
      name is stored verbatim) but the referenced table will NOT appear
      in this snapshot's tables dict — only same-schema tables are captured.
    - Output is fully normalized: canonical lowercase type names,
      stable column ordering (insertion order from source).
    - Snapshot IDs include the schema name for disambiguation, e.g.
      "ddl_public_20240101_120000".
    - Snapshot IDs are timestamp-based, never random.
    - A snapshot represents what exists, not logical intent. If a column
      has no DEFAULT in the DDL, default is None — not "inferred absent".
    - No inference. Only records what the parser or database reports.

Enhancements:
    - CHECK constraints are captured from DDL (extracted as table-level
      constraints stored in column constraints list).
    - Live DB introspection captures CHECK constraints from pg_constraint.
"""

from __future__ import annotations

from schemint.drift.constants import CANONICAL_TYPES
from schemint.drift.models import (
    MultiSchemaSnapshot,
    SchemaSnapshot,
    ViewSnapshot,
)
from schemint.drift.snapshot_pkg.check_constraints import extract_check_constraints
from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture
from schemint.drift.snapshot_pkg.live_db_capture import LiveDBSnapshotCapture
from schemint.drift.snapshot_pkg.multi_schema import MultiSchemaCapture
from schemint.drift.snapshot_pkg.view_capture import extract_views_from_ddl
from schemint.drift.types import canonicalize_type

# Backward-compatible aliases — existing tests import these.
_canonicalize_type = canonicalize_type
_CANONICAL_TYPES = CANONICAL_TYPES
_extract_check_constraints = extract_check_constraints


class SnapshotService:
    """Captures schema snapshots from DDL strings or live databases.

    Thin facade that delegates to focused subpackage classes:
    - DDLSnapshotCapture: DDL parsing
    - LiveDBSnapshotCapture: PostgreSQL introspection
    - MultiSchemaCapture: Cross-schema composition

    All existing method signatures are preserved for backward compatibility.
    """

    def __init__(self) -> None:
        self._ddl = DDLSnapshotCapture()
        self._live = LiveDBSnapshotCapture()
        self._multi = MultiSchemaCapture()

    def capture_from_ddl(
        self,
        sql: str,
        database_type: str = "postgresql",
        schema_name: str = "public",
        environment: str = "default",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from DDL SQL strings."""
        snapshot = self._ddl.capture(sql, database_type=database_type, schema_name=schema_name)
        snapshot.environment = environment
        return snapshot

    def capture_from_live_db(
        self,
        connection_string: str,
        schema_name: str = "public",
        environment: str = "default",
    ) -> SchemaSnapshot:
        """Capture a schema snapshot from a live PostgreSQL database."""
        snapshot = self._live.capture(connection_string, schema_name=schema_name)
        snapshot.environment = environment
        return snapshot

    def capture_multi_schema(
        self, connection_string: str, schema_names: list[str]
    ) -> MultiSchemaSnapshot:
        """Capture snapshots across multiple schemas."""
        return self._multi.capture(connection_string, schema_names)

    def flatten_multi_schema(
        self, multi: MultiSchemaSnapshot
    ) -> SchemaSnapshot:
        """Merge all schemas into one snapshot with qualified table names."""
        return self._multi.flatten(multi)

    # Backward-compatible private methods — delegate to subpackage.
    def _extract_tables_from_view_sql(self, sql: str) -> list[str]:
        """Extract table names referenced in view SQL using sqlglot."""
        from schemint.drift.sql_utils import extract_tables_from_sql
        return extract_tables_from_sql(sql, context="view SQL")

    def _extract_views_from_ddl(self, sql: str) -> dict[str, ViewSnapshot]:
        """Extract CREATE VIEW definitions from DDL using sqlglot."""
        return extract_views_from_ddl(sql)
