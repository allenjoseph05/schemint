"""dbt manifest edge extraction — extracted from DependencyGraphBuilder."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from schemint.drift.constants import CONFIDENCE_DBT
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
)


class DbtEdgeExtractor:
    """Extract dependency edges from a dbt manifest.json."""

    def extract(self, manifest_path: str) -> list[DependencyEdge]:
        """Extract edges from dbt manifest. Confidence = 1.0."""
        with open(manifest_path) as f:
            manifest = json.load(f)

        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)
        nodes = manifest.get("nodes", {})
        sources = manifest.get("sources", {})

        for _node_id, node in nodes.items():
            if node.get("resource_type") not in ("model", "snapshot", "seed"):
                continue

            node_fqn = self._dbt_fqn(node)
            depends_on = node.get("depends_on", {}).get("nodes", [])

            for dep_id in depends_on:
                dep_node = nodes.get(dep_id) or sources.get(dep_id)
                if dep_node:
                    dep_fqn = self._dbt_fqn(dep_node)
                else:
                    dep_fqn = dep_id.split(".")[-1] if "." in dep_id else dep_id

                edges.append(
                    DependencyEdge(
                        from_element=dep_fqn,
                        to_element=node_fqn,
                        direction="upstream",
                        usage_type="transform",
                        sources=[
                            DependencySource(
                                source_type="dbt_manifest",
                                confidence=CONFIDENCE_DBT,
                                file_path=manifest_path,
                                extracted_at=now,
                                dbt_unique_id=dep_id,
                            )
                        ],
                        final_confidence=CONFIDENCE_DBT,
                    )
                )

            # Column-level lineage if available
            columns = node.get("columns", {})
            for col_name, col_info in columns.items():
                depends_on_cols = col_info.get("depends_on", [])
                for dep_col in depends_on_cols:
                    edges.append(
                        DependencyEdge(
                            from_element=dep_col,
                            to_element=f"{node_fqn}.{col_name}",
                            direction="upstream",
                            usage_type="transform",
                            sources=[
                                DependencySource(
                                    source_type="dbt_manifest",
                                    confidence=CONFIDENCE_DBT,
                                    file_path=manifest_path,
                                    extracted_at=now,
                                )
                            ],
                            final_confidence=CONFIDENCE_DBT,
                        )
                    )

        return edges

    def _dbt_fqn(self, node: dict[str, Any]) -> str:
        """Build a fully-qualified name from dbt node metadata."""
        name: str = node.get("name", "")
        schema: str = node.get("schema", "")
        database: str = node.get("database", "")

        if database and schema:
            return f"{database}.{schema}.{name}"
        if schema:
            return f"{schema}.{name}"
        return name
