"""View definition edge extraction — extracted from DependencyGraphBuilder."""

from __future__ import annotations

from datetime import datetime, timezone

from schemint.drift.constants import CONFIDENCE_VIEW_DEFINITION
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
    SchemaSnapshot,
)
from schemint.drift.sql_utils import extract_tables_from_sql


class ViewEdgeExtractor:
    """Extract dependency edges from view definitions."""

    def extract(self, views: dict[str, str]) -> list[DependencyEdge]:
        """Extract edges from CREATE VIEW AS SELECT statements."""
        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for view_name, view_sql in views.items():
            source_tables = extract_tables_from_sql(view_sql, context=f"view {view_name}")

            for source_table in source_tables:
                if source_table.lower() == view_name.lower():
                    continue

                edges.append(
                    DependencyEdge(
                        from_element=source_table,
                        to_element=view_name,
                        direction="upstream",
                        usage_type="select",
                        sources=[
                            DependencySource(
                                source_type="view_definition",
                                confidence=CONFIDENCE_VIEW_DEFINITION,
                                extracted_at=now,
                            )
                        ],
                        final_confidence=CONFIDENCE_VIEW_DEFINITION,
                    )
                )

        return edges

    def extract_from_schema(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract edges from views in a schema snapshot."""
        views = {name: v.definition for name, v in schema.views.items()}
        return self.extract(views)
