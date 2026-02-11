"""Context assembler — builds scoped context packages for AI reasoning.

Each ContextPackage is the sole input to the AI agent brain (Phase 3+).
This module is purely deterministic — no AI involved.

Design constraints:
    - Context packages aggregate all edges per table, not one-per-edge.
    - Table-level changes expand to all columns in the table for BFS seeding.
    - context_quality is derived from coverage, confidence, and depth truncation.
    - BFS depth is always bounded by max_depth (default 10).
    - Depth truncation explicitly downgrades context_quality.
"""

from __future__ import annotations

from collections import defaultdict, deque

from schemint.drift.models import (
    ContextPackage,
    DependencyCoverage,
    DependencyGraph,
    ImpactAssessment,
    ImpactMetrics,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
)


class ContextAssembler:
    """Assembles context packages for schema change events.

    BFS traversal is bounded by max_depth. If traversal is truncated
    (more edges exist beyond max_depth), context_quality is downgraded.
    """

    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth

    def assemble(
        self,
        change: SchemaChangeEvent,
        graph: DependencyGraph,
        schema: SchemaSnapshot,
    ) -> ContextPackage:
        """Build a context package for a single schema change.

        Looks up the affected element in the dependency graph, traverses
        downstream edges via BFS, and computes impact metrics.

        For table-level changes (no column specified), all columns in the
        table are used as BFS seeds to capture full downstream impact.
        """
        # Determine the affected element(s)
        affected_elements = self._get_affected_elements(change, schema)

        # BFS to find downstream impacts
        downstream, was_truncated = self._traverse_downstream(
            affected_elements, graph
        )

        # Aggregate impacts per table: merge edges targeting the same table
        table_impacts: dict[str, _TableAgg] = {}
        downstream_tables: set[str] = set()
        downstream_columns: set[str] = set()
        max_depth = 0

        for element, depth, usage, confidence in downstream:
            table = element.split(".")[0]
            downstream_tables.add(table)
            downstream_columns.add(element)
            if depth > max_depth:
                max_depth = depth

            if table not in table_impacts:
                table_impacts[table] = _TableAgg()
            agg = table_impacts[table]
            agg.edge_count += 1
            agg.usages.add(usage)
            agg.max_confidence = max(agg.max_confidence, confidence)

        # Build aggregated impact assessments (one per table, not per edge)
        impacted: list[ImpactAssessment] = []
        for table in sorted(table_impacts):
            agg = table_impacts[table]
            impacted.append(ImpactAssessment(
                table=table,
                usage=",".join(sorted(agg.usages)),
                dependency_count=agg.edge_count,
                confidence=agg.max_confidence,
            ))

        # Compute criticality
        num_downstream = len(downstream_tables)
        if num_downstream > 10:
            criticality = "critical"
        elif num_downstream > 5:
            criticality = "high"
        elif num_downstream > 2:
            criticality = "medium"
        else:
            criticality = "low"

        metrics = ImpactMetrics(
            downstream_tables=len(downstream_tables),
            downstream_columns=len(downstream_columns),
            max_depth=max_depth,
            criticality=criticality,
        )

        # Compute coverage
        from schemint.drift.dependency_graph import DependencyGraphBuilder
        coverage = DependencyGraphBuilder().compute_coverage(graph, schema)

        # Compute context quality
        context_quality = self._compute_context_quality(
            coverage, impacted, was_truncated
        )

        return ContextPackage(
            schema_change=change,
            impacted_dependencies=impacted,
            impact_metrics=metrics,
            dependency_coverage=coverage,
            context_quality=context_quality,
        )

    def assemble_all(
        self,
        diff: SchemaDiffResult,
        graph: DependencyGraph,
        schema: SchemaSnapshot,
    ) -> list[ContextPackage]:
        """Assemble context packages for all changes in a diff result."""
        return [
            self.assemble(change, graph, schema)
            for change in diff.changes
        ]

    def _get_affected_elements(
        self, change: SchemaChangeEvent, schema: SchemaSnapshot
    ) -> list[str]:
        """Determine which graph elements are affected by a change.

        For column-level changes: returns ["table.column"].
        For table-level changes: expands to all columns in the table
        (e.g. ["orders.id", "orders.user_id", "orders.total"]) plus
        the bare table name, so BFS can find all downstream edges.
        """
        if change.column:
            return [f"{change.table}.{change.column}"]

        # Table-level change — seed BFS with all columns + bare table name
        seeds = [change.table]
        table_snap = schema.tables.get(change.table)
        if table_snap:
            for col_name in table_snap.columns:
                seeds.append(f"{change.table}.{col_name}")
        return seeds

    def _traverse_downstream(
        self,
        start_elements: list[str],
        graph: DependencyGraph,
    ) -> tuple[list[tuple[str, int, str, float]], bool]:
        """BFS traversal of downstream dependencies.

        Returns (results, was_truncated) where:
            results: list of (element, depth, usage_type, confidence)
            was_truncated: True if BFS hit max_depth and more edges existed
        """
        # Build adjacency: from_element → list of (to_element, usage_type, confidence)
        adjacency: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for edge in graph.edges:
            adjacency[edge.from_element].append(
                (edge.to_element, edge.usage_type, edge.final_confidence)
            )

        visited: set[str] = set()
        results: list[tuple[str, int, str, float]] = []
        queue: deque[tuple[str, int]] = deque()
        was_truncated = False

        for elem in start_elements:
            if elem not in visited:
                queue.append((elem, 0))
                visited.add(elem)

        while queue:
            current, depth = queue.popleft()

            if depth >= self.max_depth:
                # Check if there would have been more to traverse
                if adjacency.get(current):
                    was_truncated = True
                continue

            neighbors = adjacency.get(current, [])
            for to_element, usage_type, confidence in neighbors:
                if to_element not in visited:
                    visited.add(to_element)
                    results.append((to_element, depth + 1, usage_type, confidence))
                    queue.append((to_element, depth + 1))

        return results, was_truncated

    def _compute_context_quality(
        self,
        coverage: DependencyCoverage,
        impacted: list[ImpactAssessment],
        was_truncated: bool,
    ) -> str:
        """Derive context_quality from coverage, confidence, and truncation.

        Rules:
            - "insufficient": coverage < 50% OR all impact confidences < 0.5
            - "partial": depth was truncated, OR coverage < 80%,
                         OR any impact confidence < 0.7
            - "complete": full coverage (>=80%), all confidences >= 0.7,
                          no depth truncation
        """
        # insufficient: coverage below 50%
        if coverage.tables_total > 0 and coverage.coverage_pct < 50.0:
            return "insufficient"

        # insufficient: all edges below confidence threshold
        if impacted and all(dep.confidence < 0.5 for dep in impacted):
            return "insufficient"

        # partial: depth was truncated
        if was_truncated:
            return "partial"

        # partial: coverage below 80%
        if coverage.tables_total > 0 and coverage.coverage_pct < 80.0:
            return "partial"

        # partial: any low-confidence edge
        if any(dep.confidence < 0.7 for dep in impacted):
            return "partial"

        return "complete"


class _TableAgg:
    """Mutable accumulator for per-table impact aggregation."""
    __slots__ = ("edge_count", "usages", "max_confidence")

    def __init__(self) -> None:
        self.edge_count = 0
        self.usages: set[str] = set()
        self.max_confidence = 0.0
