"""FK constraint edge extraction — extracted from DependencyGraphBuilder."""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.constants import CONFIDENCE_FK
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
    SchemaSnapshot,
)


class FKEdgeExtractor:
    """Extract dependency edges from FK constraints in a snapshot."""

    def extract(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract FK edges. Confidence = 1.0 (highest)."""
        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for table_name, table in schema.tables.items():
            for fk in table.foreign_keys:
                col = fk.column if hasattr(fk, "column") else fk.get("column", "")
                ref_table = fk.references_table if hasattr(fk, "references_table") else fk.get("references_table", "")
                ref_col = fk.references_column if hasattr(fk, "references_column") else fk.get("references_column", "")

                if not col or not ref_table or not ref_col:
                    continue

                edges.append(DependencyEdge(
                    from_element=f"{table_name}.{col}",
                    to_element=f"{ref_table}.{ref_col}",
                    direction="upstream",
                    usage_type="fk",
                    sources=[DependencySource(
                        source_type="fk_constraint",
                        confidence=CONFIDENCE_FK,
                        extracted_at=now,
                    )],
                    final_confidence=CONFIDENCE_FK,
                ))

        return edges
