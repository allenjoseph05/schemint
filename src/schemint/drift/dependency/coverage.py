"""Coverage computation — extracted from DependencyGraphBuilder.

Computes what percentage of tables have lineage edges. This module
has NO dependency on DependencyGraphBuilder, breaking the circular
import between context_assembler and dependency_graph.
"""

from __future__ import annotations

from schemint.drift.models import (
    DependencyCoverage,
    DependencyGraph,
    SchemaSnapshot,
)


class CoverageComputer:
    """Computes dependency graph coverage metrics.

    Extracted from DependencyGraphBuilder to break the circular import
    in context_assembler.py. This class has no dependencies on the
    graph builder — it only needs a built DependencyGraph and a
    SchemaSnapshot.
    """

    def compute_coverage(
        self, graph: DependencyGraph, schema: SchemaSnapshot
    ) -> DependencyCoverage:
        """Compute what percentage of tables have at least one lineage edge.

        Tables with no edges are explicitly surfaced as untracked.
        Missing lineage reduces confidence — it does not invent edges.
        """
        all_tables = set(schema.tables.keys())
        tables_with_lineage: set[str] = set()

        for edge in graph.edges:
            from_table = edge.from_element.split(".")[0]
            to_table = edge.to_element.split(".")[0]
            if from_table in all_tables:
                tables_with_lineage.add(from_table)
            if to_table in all_tables:
                tables_with_lineage.add(to_table)

        total = len(all_tables)
        with_lineage = len(tables_with_lineage)
        untracked = sorted(all_tables - tables_with_lineage)

        return DependencyCoverage(
            tables_total=total,
            tables_with_lineage=with_lineage,
            coverage_pct=(with_lineage / total * 100.0) if total > 0 else 0.0,
            untracked_tables=untracked,
        )
