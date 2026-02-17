"""Trigger definition edge extraction — extracted from DependencyGraphBuilder."""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.constants import CONFIDENCE_TRIGGER
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
    SchemaSnapshot,
)
from schemint.drift.sql_utils import extract_tables_from_sql


class TriggerEdgeExtractor:
    """Extract dependency edges from trigger definitions."""

    def extract(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract edges from triggers in a schema snapshot."""
        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for trigger_name, trigger in schema.triggers.items():
            if not trigger.definition:
                continue

            source_table = trigger.table.lower()
            referenced_tables = extract_tables_from_sql(
                trigger.definition, context=f"trigger {trigger_name}"
            )

            for ref_table in referenced_tables:
                if ref_table == source_table:
                    continue

                edges.append(DependencyEdge(
                    from_element=source_table,
                    to_element=ref_table,
                    direction="downstream",
                    usage_type="transform",
                    sources=[DependencySource(
                        source_type="trigger_definition",
                        confidence=CONFIDENCE_TRIGGER,
                        extracted_at=now,
                    )],
                    final_confidence=CONFIDENCE_TRIGGER,
                ))

        return edges
