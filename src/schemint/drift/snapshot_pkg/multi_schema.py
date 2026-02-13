"""Multi-schema capture and flattening — extracted from SnapshotService.

Handles cross-schema snapshot composition and flattening for
downstream analysis by the differ and context assembler.
"""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.models import (
    MultiSchemaSnapshot,
    SchemaSnapshot,
    TableSnapshot,
    TriggerSnapshot,
    ViewSnapshot,
)


class MultiSchemaCapture:
    """Multi-schema snapshot capture and flattening."""

    def capture(
        self,
        connection_string: str,
        schema_names: list[str],
    ) -> MultiSchemaSnapshot:
        """Capture snapshots across multiple schemas."""
        from schemint.drift.snapshot_pkg.live_db_capture import LiveDBSnapshotCapture

        now = datetime.now(timezone.utc)
        live_capture = LiveDBSnapshotCapture()
        schemas: dict[str, SchemaSnapshot] = {}

        for name in schema_names:
            schemas[name] = live_capture.capture(
                connection_string, schema_name=name
            )

        return MultiSchemaSnapshot(
            snapshot_id=f"multi_{'_'.join(schema_names)}_{now.strftime('%Y%m%d_%H%M%S')}",
            captured_at=now,
            source="composed",
            database_type="postgresql",
            schemas=schemas,
        )

    def flatten(self, multi: MultiSchemaSnapshot) -> SchemaSnapshot:
        """Merge all schemas into one snapshot with qualified names (schema.table)."""
        now = datetime.now(timezone.utc)
        tables: dict[str, TableSnapshot] = {}
        views: dict[str, ViewSnapshot] = {}
        triggers: dict[str, TriggerSnapshot] = {}

        for schema_name, schema in multi.schemas.items():
            for table_name, table in schema.tables.items():
                qualified = f"{schema_name}.{table_name}"
                qualified_fks = []
                for fk in table.foreign_keys:
                    ref_table = fk.references_table if hasattr(fk, "references_table") else fk.get("references_table", "")
                    qualified_ref = f"{schema_name}.{ref_table}" if ref_table in schema.tables else ref_table
                    if hasattr(fk, "model_copy"):
                        new_fk = fk.model_copy(update={"references_table": qualified_ref})
                    else:
                        new_fk = dict(fk)
                        new_fk["references_table"] = qualified_ref
                    qualified_fks.append(new_fk)

                tables[qualified] = TableSnapshot(
                    name=qualified,
                    columns=table.columns,
                    primary_key=table.primary_key,
                    indexes=table.indexes,
                    foreign_keys=qualified_fks,
                )

            for view_name, view in schema.views.items():
                qualified = f"{schema_name}.{view_name}"
                views[qualified] = ViewSnapshot(
                    name=qualified,
                    definition=view.definition,
                    source_tables=[
                        f"{schema_name}.{t}" if t in schema.tables else t
                        for t in view.source_tables
                    ],
                )

            for trigger_name, trigger in schema.triggers.items():
                qualified = f"{schema_name}.{trigger_name}"
                triggers[qualified] = TriggerSnapshot(
                    name=qualified,
                    table=f"{schema_name}.{trigger.table}",
                    event=trigger.event,
                    timing=trigger.timing,
                    function_name=trigger.function_name,
                    definition=trigger.definition,
                )

        return SchemaSnapshot(
            snapshot_id=f"flat_{multi.snapshot_id}",
            captured_at=now,
            source="composed",
            database_type=multi.database_type,
            schema_name="__multi__",
            tables=tables,
            views=views,
            triggers=triggers,
        )
