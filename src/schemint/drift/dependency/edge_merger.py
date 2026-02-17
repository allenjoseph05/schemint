"""Edge deduplication and merging — extracted from DependencyGraphBuilder."""

from __future__ import annotations

from schemint.drift.models import DependencyEdge


class EdgeMerger:
    """Deduplicates edges by (from, to, usage_type), merging sources.

    When duplicates exist:
    - Merge sources: keep all DependencySource objects.
    - final_confidence: take MAX confidence (most confident source wins).
    """

    def merge(self, edges: list[DependencyEdge]) -> list[DependencyEdge]:
        """Deduplicate and merge edges."""
        edge_map: dict[tuple[str, str, str], DependencyEdge] = {}

        for edge in edges:
            key = (edge.from_element, edge.to_element, edge.usage_type)
            if key in edge_map:
                existing = edge_map[key]
                existing.sources.extend(edge.sources)
                existing.final_confidence = max(s.confidence for s in existing.sources)
            else:
                edge_map[key] = edge.model_copy(deep=True)

        return list(edge_map.values())
