"""Context assembler — builds scoped context packages for AI reasoning.

Each ContextPackage is the sole input to the AI agent brain (Phase 3+).
This module is purely deterministic — no AI involved.

Design constraints:
    - Context packages aggregate all edges per table, not one-per-edge.
    - Table-level changes expand to all columns in the table for BFS seeding.
    - context_quality is derived from coverage, confidence, and depth truncation.
    - BFS depth is always bounded by max_depth (default 10).
    - Depth truncation explicitly downgrades context_quality.

Enhancements:
    - Criticality thresholds are configurable (not hardcoded).
    - ContextGaps explicitly surfaces missing information.
    - Upstream impact analysis traverses reverse edges.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from schemint.drift.constants import (
    ALL_INSUFFICIENT_THRESHOLD,
    COVERAGE_INSUFFICIENT_PCT,
    COVERAGE_PARTIAL_PCT,
    LOW_CONFIDENCE_THRESHOLD,
)
from schemint.drift.models import (
    ContextGaps,
    ContextPackage,
    DataQualitySignals,
    DependencyCoverage,
    DependencyGraph,
    ImpactAssessment,
    ImpactMetrics,
    MemoryContext,
    ParseHealth,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
    TableStatistics,
)


@dataclass
class CriticalityThresholds:
    """Configurable thresholds for computing criticality from downstream count and table size.

    Avoids hardcoded magic numbers. Users can tune these per project.
    Size thresholds allow large tables (by row count or byte size) to
    escalate criticality even with zero downstream dependencies.
    """

    critical: int = 10
    high: int = 5
    medium: int = 2

    # Row count thresholds
    critical_row_count: int = 100_000_000  # 100M rows
    high_row_count: int = 10_000_000  # 10M rows
    medium_row_count: int = 1_000_000  # 1M rows

    # Byte size thresholds
    critical_size_bytes: int = 10 * 1024 * 1024 * 1024  # 10GB
    high_size_bytes: int = 1 * 1024 * 1024 * 1024  # 1GB
    medium_size_bytes: int = 100 * 1024 * 1024  # 100MB

    def compute(
        self,
        downstream_table_count: int,
        table_stats: TableStatistics | None = None,
    ) -> str:
        """Map downstream table count and table size to criticality level.

        Returns the maximum of dependency-based and size-based criticality.
        Backward compatible: table_stats defaults to None.
        """
        dep_criticality = self._from_deps(downstream_table_count)

        if table_stats is None:
            return dep_criticality

        size_criticality = self._from_size(table_stats)
        return self._max_criticality(dep_criticality, size_criticality)

    def _from_deps(self, downstream_table_count: int) -> str:
        """Criticality from downstream dependency count."""
        if downstream_table_count > self.critical:
            return "critical"
        if downstream_table_count > self.high:
            return "high"
        if downstream_table_count > self.medium:
            return "medium"
        return "low"

    def _from_size(self, stats: TableStatistics) -> str:
        """Criticality from table size (row count and byte size)."""
        row_level = "low"
        if stats.row_count >= self.critical_row_count:
            row_level = "critical"
        elif stats.row_count >= self.high_row_count:
            row_level = "high"
        elif stats.row_count >= self.medium_row_count:
            row_level = "medium"

        size_level = "low"
        if stats.total_size_bytes >= self.critical_size_bytes:
            size_level = "critical"
        elif stats.total_size_bytes >= self.high_size_bytes:
            size_level = "high"
        elif stats.total_size_bytes >= self.medium_size_bytes:
            size_level = "medium"

        return self._max_criticality(row_level, size_level)

    @staticmethod
    def _max_criticality(a: str, b: str) -> str:
        """Return the higher criticality level."""
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return a if order.get(a, 0) >= order.get(b, 0) else b


class ContextAssembler:
    """Assembles context packages for schema change events.

    BFS traversal is bounded by max_depth. If traversal is truncated
    (more edges exist beyond max_depth), context_quality is downgraded.
    """

    def __init__(
        self,
        max_depth: int = 10,
        criticality_thresholds: CriticalityThresholds | None = None,
    ):
        self.max_depth = max_depth
        self.thresholds = criticality_thresholds or CriticalityThresholds()

    def assemble(
        self,
        change: SchemaChangeEvent,
        graph: DependencyGraph,
        schema: SchemaSnapshot,
        parse_health: ParseHealth | None = None,
        memory_context: MemoryContext | None = None,
    ) -> ContextPackage:
        """Build a context package for a single schema change.

        Looks up the affected element in the dependency graph, traverses
        downstream AND upstream edges via BFS, and computes impact metrics.
        """
        affected_elements = self._get_affected_elements(change, schema)

        # BFS downstream
        downstream, was_truncated = self._traverse_downstream(affected_elements, graph)

        # BFS upstream
        upstream_results, _ = self._traverse_upstream(affected_elements, graph)

        # Aggregate downstream impacts per table
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

        impacted: list[ImpactAssessment] = []
        for table in sorted(table_impacts):
            agg = table_impacts[table]
            impacted.append(
                ImpactAssessment(
                    table=table,
                    usage=",".join(sorted(agg.usages)),
                    dependency_count=agg.edge_count,
                    confidence=agg.max_confidence,
                )
            )

        # Aggregate upstream impacts
        upstream_table_impacts: dict[str, _TableAgg] = {}
        for element, _depth, usage, confidence in upstream_results:
            table = element.split(".")[0]
            if table not in upstream_table_impacts:
                upstream_table_impacts[table] = _TableAgg()
            agg = upstream_table_impacts[table]
            agg.edge_count += 1
            agg.usages.add(usage)
            agg.max_confidence = max(agg.max_confidence, confidence)

        upstream_impacts: list[ImpactAssessment] = []
        for table in sorted(upstream_table_impacts):
            agg = upstream_table_impacts[table]
            upstream_impacts.append(
                ImpactAssessment(
                    table=table,
                    usage=",".join(sorted(agg.usages)),
                    dependency_count=agg.edge_count,
                    confidence=agg.max_confidence,
                )
            )

        # Attach table statistics for the affected table (needed for criticality)
        affected_table_stats = schema.table_statistics.get(change.table)

        # Compute criticality with configurable thresholds (includes table size)
        num_downstream = len(downstream_tables)
        criticality = self.thresholds.compute(num_downstream, affected_table_stats)

        metrics = ImpactMetrics(
            downstream_tables=len(downstream_tables),
            downstream_columns=len(downstream_columns),
            max_depth=max_depth,
            criticality=criticality,  # type: ignore[arg-type]
        )

        # Compute coverage (uses extracted CoverageComputer — no circular import)
        from schemint.drift.dependency.coverage import CoverageComputer

        coverage = CoverageComputer().compute_coverage(graph, schema)

        # Compute context quality
        context_quality = self._compute_context_quality(coverage, impacted, was_truncated)

        # Compute context gaps
        context_gaps = self._compute_context_gaps(
            change, graph, schema, coverage, impacted, parse_health
        )

        # Attach index statistics for affected table's indexes
        affected_index_stats = [
            stat for stat in schema.index_statistics.values() if stat.table_name == change.table
        ]

        # Attach column statistics for the affected table
        affected_column_stats = schema.column_statistics.get(change.table, [])

        # Attach permissions for the affected table
        affected_permissions = [
            perm for perm in schema.permissions if perm.table_name == change.table
        ]

        # Attach functions that reference the affected table
        affected_functions = [
            fn
            for fn in schema.functions.values()
            if fn.definition and change.table in fn.definition
        ]

        return ContextPackage(
            schema_change=change,
            environment=schema.environment,
            impacted_dependencies=impacted,
            impact_metrics=metrics,
            dependency_coverage=coverage,
            context_quality=context_quality,  # type: ignore[arg-type]
            context_gaps=context_gaps,
            upstream_impacts=upstream_impacts,
            affected_table_stats=affected_table_stats,
            affected_index_stats=affected_index_stats,
            affected_column_stats=affected_column_stats,
            affected_permissions=affected_permissions,
            affected_functions=affected_functions,
            data_quality_signals=self._compute_data_quality_signals(affected_table_stats),
            memory_context=memory_context,
        )

    def assemble_all(
        self,
        diff: SchemaDiffResult,
        graph: DependencyGraph,
        schema: SchemaSnapshot,
        parse_health: ParseHealth | None = None,
        memory_context: MemoryContext | None = None,
    ) -> list[ContextPackage]:
        """Assemble context packages for all changes in a diff result."""
        return [
            self.assemble(change, graph, schema, parse_health, memory_context)
            for change in diff.changes
        ]

    def _get_affected_elements(
        self, change: SchemaChangeEvent, schema: SchemaSnapshot
    ) -> list[str]:
        """Determine which graph elements are affected by a change.

        For column-level changes: returns ["table.column"].
        For table-level changes: expands to all columns in the table
        plus the bare table name, so BFS can find all downstream edges.
        For view changes: uses the view name as a seed.
        For trigger changes: uses the trigger's table as a seed.
        For sequence changes: finds tables with nextval('seq') defaults.
        For enum changes: finds columns using that enum type.
        For function changes: finds triggers/views referencing the function.
        For matview changes: seeds with source tables.
        For extension/permission/policy/partition: seeds with the table name.
        """
        ct = change.change_type

        # View changes — seed with the view name
        if ct in ("view_added", "view_dropped", "view_definition_change"):
            seeds = [change.table]
            view_snap = schema.views.get(change.table)
            if view_snap:
                for src_table in view_snap.source_tables:
                    seeds.append(src_table)
            return seeds

        # Trigger changes — seed with the trigger's table
        if ct in ("trigger_added", "trigger_dropped", "trigger_changed"):
            seeds = [change.table]
            table_snap = schema.tables.get(change.table)
            if table_snap:
                for col_name in table_snap.columns:
                    seeds.append(f"{change.table}.{col_name}")
            return seeds

        # Sequence changes — find tables using nextval('seq_name')
        if ct in ("sequence_added", "sequence_dropped", "sequence_changed"):
            seq_name = change.table
            seeds = [seq_name]
            for tbl_name, tbl_snap in schema.tables.items():
                for col_snap in tbl_snap.columns.values():
                    if col_snap.default and seq_name in col_snap.default:
                        seeds.append(tbl_name)
                        seeds.append(f"{tbl_name}.{col_snap.name}")
            return seeds

        # Enum changes — find columns using that enum type
        if ct in ("enum_added", "enum_dropped", "enum_value_added", "enum_value_removed"):
            enum_name = change.table
            seeds = [enum_name]
            for tbl_name, tbl_snap in schema.tables.items():
                for col_snap in tbl_snap.columns.values():
                    if col_snap.type == enum_name:
                        seeds.append(tbl_name)
                        seeds.append(f"{tbl_name}.{col_snap.name}")
            return seeds

        # Function changes — find triggers/views referencing the function
        if ct in ("function_added", "function_dropped", "function_changed"):
            fn_name = change.table
            seeds = [fn_name]
            for trigger in schema.triggers.values():
                if trigger.function_name == fn_name:
                    seeds.append(trigger.table)
            for view in schema.views.values():
                if fn_name in view.definition:
                    seeds.append(view.name)
            return seeds

        # Materialized view changes — seed with source tables
        if ct in ("matview_added", "matview_dropped", "matview_definition_changed"):
            mv_name = change.table
            seeds = [mv_name]
            mv_snap = schema.materialized_views.get(mv_name)
            if mv_snap:
                for src_table in mv_snap.source_tables:
                    seeds.append(src_table)
            return seeds

        # Extension/permission/policy/partition — seed with table name
        if ct in (
            "extension_added",
            "extension_dropped",
            "extension_version_changed",
            "permission_granted",
            "permission_revoked",
            "policy_added",
            "policy_dropped",
            "policy_changed",
            "partition_added",
            "partition_dropped",
        ):
            return self._expand_table_seeds(change.table, schema)

        if change.column:
            return [f"{change.table}.{change.column}"]

        return self._expand_table_seeds(change.table, schema)

    @staticmethod
    def _compute_data_quality_signals(
        table_stats: TableStatistics | None,
    ) -> DataQualitySignals | None:
        """Derive data quality signals from table statistics.

        Returns None if no stats are available. Otherwise computes:
        - dead_tuple_ratio: dead_tuples / (row_count + dead_tuples)
        - seq_scan_ratio: seq_scans / (seq_scans + idx_scans)
        - is_vacuum_needed: dead_tuple_ratio > 10%
        - is_analyze_stale: last_analyze > 72 hours ago
        """
        if table_stats is None:
            return None

        total_rows = table_stats.row_count + table_stats.dead_tuples
        dead_tuple_ratio = table_stats.dead_tuples / total_rows if total_rows > 0 else 0.0

        total_scans = table_stats.seq_scan_count + table_stats.idx_scan_count
        seq_scan_ratio = table_stats.seq_scan_count / total_scans if total_scans > 0 else 0.0

        last_vacuum_age_hours: float | None = None
        if table_stats.last_vacuum:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            delta = (
                now - table_stats.last_vacuum.replace(tzinfo=timezone.utc)
                if table_stats.last_vacuum.tzinfo is None
                else now - table_stats.last_vacuum
            )
            last_vacuum_age_hours = delta.total_seconds() / 3600

        last_analyze_age_hours: float | None = None
        if table_stats.last_analyze:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            delta = (
                now - table_stats.last_analyze.replace(tzinfo=timezone.utc)
                if table_stats.last_analyze.tzinfo is None
                else now - table_stats.last_analyze
            )
            last_analyze_age_hours = delta.total_seconds() / 3600

        is_vacuum_needed = dead_tuple_ratio > 0.1
        is_analyze_stale = last_analyze_age_hours is not None and last_analyze_age_hours > 72

        return DataQualitySignals(
            dead_tuple_ratio=dead_tuple_ratio,
            seq_scan_ratio=seq_scan_ratio,
            last_vacuum_age_hours=last_vacuum_age_hours,
            last_analyze_age_hours=last_analyze_age_hours,
            is_vacuum_needed=is_vacuum_needed,
            is_analyze_stale=is_analyze_stale,
        )

    def _expand_table_seeds(self, table_name: str, schema: SchemaSnapshot) -> list[str]:
        """Expand a table name to seeds including all its columns."""
        seeds = [table_name]
        table_snap = schema.tables.get(table_name)
        if table_snap:
            for col_name in table_snap.columns:
                seeds.append(f"{table_name}.{col_name}")
        return seeds

    def _traverse_downstream(
        self,
        start_elements: list[str],
        graph: DependencyGraph,
    ) -> tuple[list[tuple[str, int, str, float]], bool]:
        """BFS traversal of downstream dependencies.

        Returns (results, was_truncated).
        """
        adjacency: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for edge in graph.edges:
            adjacency[edge.from_element].append(
                (edge.to_element, edge.usage_type, edge.final_confidence)
            )

        return self._bfs(start_elements, adjacency)

    def _traverse_upstream(
        self,
        start_elements: list[str],
        graph: DependencyGraph,
    ) -> tuple[list[tuple[str, int, str, float]], bool]:
        """BFS traversal of upstream dependencies (reverse edges).

        Returns (results, was_truncated).
        """
        adjacency: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for edge in graph.edges:
            adjacency[edge.to_element].append(
                (edge.from_element, edge.usage_type, edge.final_confidence)
            )

        return self._bfs(start_elements, adjacency)

    def _bfs(
        self,
        start_elements: list[str],
        adjacency: dict[str, list[tuple[str, str, float]]],
    ) -> tuple[list[tuple[str, int, str, float]], bool]:
        """Generic BFS traversal over an adjacency map.

        Returns (results, was_truncated) where:
            results: list of (element, depth, usage_type, confidence)
            was_truncated: True if BFS hit max_depth and more edges existed
        """
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
            - "insufficient": coverage < COVERAGE_INSUFFICIENT_PCT OR
                              all impact confidences < ALL_INSUFFICIENT_THRESHOLD
            - "partial": depth was truncated, OR coverage < COVERAGE_PARTIAL_PCT,
                         OR any impact confidence < LOW_CONFIDENCE_THRESHOLD
            - "complete": full coverage, all confidences above threshold,
                          no depth truncation
        """
        if coverage.tables_total > 0 and coverage.coverage_pct < COVERAGE_INSUFFICIENT_PCT:
            return "insufficient"

        if impacted and all(dep.confidence < ALL_INSUFFICIENT_THRESHOLD for dep in impacted):
            return "insufficient"

        if was_truncated:
            return "partial"

        if coverage.tables_total > 0 and coverage.coverage_pct < COVERAGE_PARTIAL_PCT:
            return "partial"

        if any(dep.confidence < LOW_CONFIDENCE_THRESHOLD for dep in impacted):
            return "partial"

        return "complete"

    def _compute_context_gaps(
        self,
        change: SchemaChangeEvent,
        graph: DependencyGraph,
        schema: SchemaSnapshot,
        coverage: DependencyCoverage,
        _impacted: list[ImpactAssessment],
        parse_health: ParseHealth | None,
    ) -> ContextGaps:
        """Explicitly surface what information is missing from context.

        Computes:
            - Tables referenced in edges but not in schema (missing)
            - Untracked tables (no lineage edges at all)
            - Low-confidence edge count
            - Parse health from SQL file processing
        """
        schema_tables = set(schema.tables.keys())

        # Find tables referenced in edges but not in schema snapshot
        edge_tables: set[str] = set()
        for edge in graph.edges:
            from_t = edge.from_element.split(".")[0]
            to_t = edge.to_element.split(".")[0]
            edge_tables.add(from_t)
            edge_tables.add(to_t)

        missing_from_schema = sorted(edge_tables - schema_tables)

        # Separate into upstream/downstream based on the change's position
        changed_table = change.table
        missing_upstream: list[str] = []
        missing_downstream: list[str] = []

        for table in missing_from_schema:
            # Check if this missing table appears as upstream or downstream
            is_upstream = any(
                edge.from_element.split(".")[0] == table
                and edge.to_element.split(".")[0] == changed_table
                for edge in graph.edges
            )
            if is_upstream:
                missing_upstream.append(table)
            else:
                missing_downstream.append(table)

        # Count low-confidence edges
        low_confidence_count = sum(
            1 for edge in graph.edges if edge.final_confidence < LOW_CONFIDENCE_THRESHOLD
        )

        has_gaps = bool(
            missing_upstream
            or missing_downstream
            or coverage.untracked_tables
            or low_confidence_count > 0
            or (parse_health and parse_health.parse_failures)
        )

        return ContextGaps(
            missing_upstream_tables=missing_upstream,
            missing_downstream_tables=missing_downstream,
            untracked_tables=coverage.untracked_tables,
            low_confidence_edges=low_confidence_count,
            parse_health=parse_health or ParseHealth(),
            has_gaps=has_gaps,
        )


@dataclass
class _TableAgg:
    """Mutable accumulator for per-table impact aggregation."""

    edge_count: int = 0
    usages: set[str] = field(default_factory=set)
    max_confidence: float = 0.0
