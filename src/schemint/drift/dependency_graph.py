"""Dependency graph builder — thin facade delegating to dependency/ subpackage.

Only provable deterministic sources: dbt manifest, SQL AST, FK constraints,
view definitions. NO inference from naming conventions, heuristics, embeddings,
or LLM reasoning.

Design invariant:
    "The dependency graph records only what can be proven.
     Missing lineage results in uncertainty, not inference."

Unsupported SQL constructs (emit NO edges, not partial results):
    - Recursive CTEs (WITH RECURSIVE)
    - Dynamic SQL (EXECUTE IMMEDIATE, sp_executesql)
    - Database-specific macros (dbt Jinja, PL/pgSQL variables)
    - UNION across differently-aliased subqueries
    If sqlglot cannot parse the SQL, zero edges are emitted.

Alias resolution rules:
    - Table aliases are resolved from the AST (e.g. "u" -> "users").
    - Unresolved aliases NEVER upgrade confidence — they degrade it.
    - Unqualified column references (no table prefix) are skipped entirely.
    - alias_resolved=False on a source means the edge may reference aliases.

Coverage semantics:
    - compute_coverage() measures COMPLETENESS, not CORRECTNESS.
    - A table with lineage edges may still have incorrect edges.
    - A table with 0 edges is explicitly surfaced as untracked.
    - Coverage % = tables_with_any_edge / total_tables x 100.

Forbidden behavior:
    - DO NOT infer dependencies from column names (e.g. user_id -> users)
    - DO NOT use regex guessing for SQL parsing
    - DO NOT use embeddings or LLMs
    - DO NOT create edges without explicit proof
    - If a dependency cannot be proven -> DO NOT create an edge
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from schemint.drift.dependency.column_lineage import ColumnLineageExtractor
from schemint.drift.dependency.coverage import CoverageComputer
from schemint.drift.dependency.dbt_extractor import DbtEdgeExtractor
from schemint.drift.dependency.edge_merger import EdgeMerger
from schemint.drift.dependency.fk_extractor import FKEdgeExtractor
from schemint.drift.dependency.sql_ast_extractor import SqlAstEdgeExtractor
from schemint.drift.dependency.trigger_extractor import TriggerEdgeExtractor
from schemint.drift.dependency.view_extractor import ViewEdgeExtractor
from schemint.drift.models import (
    DependencyCoverage,
    DependencyEdge,
    DependencyGraph,
    ParseHealth,
    SchemaSnapshot,
)
from schemint.drift.sql_utils import (
    extract_aliases_from_ast,
    extract_tables_from_sql,
    resolve_column_ref,
)

logger = logging.getLogger(__name__)


class DependencyGraphBuilder:
    """Builds dependency graphs from deterministic sources only.

    Thin facade that delegates to focused extractor classes in the
    dependency/ subpackage. All existing method signatures are preserved.
    """

    def __init__(self) -> None:
        self._fk_extractor = FKEdgeExtractor()
        self._dbt_extractor = DbtEdgeExtractor()
        self._sql_extractor = SqlAstEdgeExtractor()
        self._view_extractor = ViewEdgeExtractor()
        self._trigger_extractor = TriggerEdgeExtractor()
        self._lineage_extractor = ColumnLineageExtractor()
        self._merger = EdgeMerger()
        self._coverage = CoverageComputer()

    def build(self, all_edges: list[DependencyEdge]) -> DependencyGraph:
        """Build a dependency graph from collected edges.

        Merges duplicate edges, deduplicates, and computes final confidence.
        Strips any edge that has zero sources (invariant enforcement).
        """
        valid_edges = [e for e in all_edges if len(e.sources) > 0]
        if len(valid_edges) < len(all_edges):
            dropped = len(all_edges) - len(valid_edges)
            logger.warning(
                "Dropped %d edges with no provenance sources", dropped
            )

        merged = self._merge_edges(valid_edges)
        return DependencyGraph(
            edges=merged,
            built_at=datetime.now(timezone.utc),
        )

    # =========================================================================
    # FK constraints
    # =========================================================================

    def from_fk_constraints(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract dependency edges from FK constraints in a snapshot."""
        return self._fk_extractor.extract(schema)

    # =========================================================================
    # dbt manifest
    # =========================================================================

    def from_dbt_manifest(self, manifest_path: str) -> list[DependencyEdge]:
        """Extract dependency edges from a dbt manifest.json."""
        return self._dbt_extractor.extract(manifest_path)

    def _dbt_fqn(self, node: dict[str, Any]) -> str:
        """Build a fully-qualified name from dbt node metadata."""
        return self._dbt_extractor._dbt_fqn(node)

    # =========================================================================
    # SQL AST parsing
    # =========================================================================

    def from_sql_ast(
        self, sql: str, file_path: str | None = None
    ) -> list[DependencyEdge]:
        """Extract dependency edges from SQL statements using sqlglot AST.

        Also extracts column-level lineage from SELECT clauses.
        """
        import sqlglot

        try:
            statements = sqlglot.parse(sql)
        except sqlglot.errors.ParseError:
            logger.warning("sqlglot parse failed for SQL input; emitting no edges")
            return []

        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        # Get table-level edges from the extractor
        edges.extend(self._sql_extractor.extract(sql, file_path=file_path))

        # Get column-level lineage
        for statement in statements:
            if statement is None:
                continue
            edges.extend(
                self._lineage_extractor.extract(statement, now, file_path)
            )

        return edges

    def _extract_aliases_from_ast(self, statement: Any) -> dict[str, str]:
        """Extract table alias -> real table name from sqlglot AST."""
        return extract_aliases_from_ast(statement)

    def _resolve_column_ref(self, col: Any, aliases: dict[str, str]) -> tuple[str, bool]:
        """Resolve a column reference to table.column using the alias map."""
        return resolve_column_ref(col, aliases)

    def _extract_join_edges_from_ast(self, statement: Any, aliases: dict[str, str], now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to sql_ast_extractor."""
        return self._sql_extractor._extract_join_edges(statement, aliases, now, file_path)

    def _extract_where_edges_from_ast(self, statement: Any, aliases: dict[str, str], now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to sql_ast_extractor."""
        return self._sql_extractor._extract_where_edges(statement, aliases, now, file_path)

    def _extract_cte_edges(self, statement: Any, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to sql_ast_extractor."""
        return self._sql_extractor._extract_cte_edges(statement, now, file_path)

    def _extract_subquery_edges(self, statement: Any, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to sql_ast_extractor."""
        return self._sql_extractor._extract_subquery_edges(statement, now, file_path)

    def _extract_insert_select_edges(self, statement: Any, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to sql_ast_extractor."""
        return self._sql_extractor._extract_insert_select_edges(statement, now, file_path)

    def _extract_column_lineage(self, statement: Any, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to column_lineage extractor."""
        return self._lineage_extractor.extract(statement, now, file_path)

    def _determine_target_name(self, statement: Any) -> str | None:
        """Backward-compatible — delegates to column_lineage extractor."""
        return self._lineage_extractor._determine_target_name(statement)

    def _extract_select_column_lineage(self, select_node: Any, aliases: dict[str, str], target_name: str, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to column_lineage extractor."""
        return self._lineage_extractor._extract_select_column_lineage(
            select_node, aliases, target_name, now, file_path
        )

    def _extract_insert_column_lineage(self, select_node: Any, aliases: dict[str, str], target_table: str, insert_cols: list[str], now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Backward-compatible — delegates to column_lineage extractor."""
        return self._lineage_extractor._extract_insert_column_lineage(
            select_node, aliases, target_table, insert_cols, now, file_path
        )

    def _get_output_name(self, expr: Any) -> str | None:
        """Backward-compatible — delegates to column_lineage extractor."""
        return self._lineage_extractor._get_output_name(expr)

    # =========================================================================
    # Batch SQL file processing
    # =========================================================================

    def from_sql_files(
        self, sql_files: dict[str, str]
    ) -> tuple[list[DependencyEdge], ParseHealth]:
        """Extract edges from multiple SQL files, tracking parse failures."""
        all_edges: list[DependencyEdge] = []
        health = ParseHealth(total_files=len(sql_files))

        for file_path, sql_content in sql_files.items():
            try:
                edges = self.from_sql_ast(sql_content, file_path=file_path)
                all_edges.extend(edges)
                health.parsed_ok += 1
            except Exception as e:
                logger.warning("Failed to parse %s: %s", file_path, e)
                health.parse_failures.append(file_path)

        return all_edges, health

    # =========================================================================
    # View definitions
    # =========================================================================

    def from_view_definitions(
        self, views: dict[str, str]
    ) -> list[DependencyEdge]:
        """Extract dependency edges from CREATE VIEW AS SELECT statements."""
        return self._view_extractor.extract(views)

    def from_schema_views(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract dependency edges from views in a schema snapshot."""
        return self._view_extractor.extract_from_schema(schema)

    def from_trigger_definitions(
        self, schema: SchemaSnapshot
    ) -> list[DependencyEdge]:
        """Extract dependency edges from triggers in a schema snapshot."""
        return self._trigger_extractor.extract(schema)

    def _extract_tables_from_ast(self, sql: str) -> list[str]:
        """Extract table names from SQL using sqlglot AST."""
        return extract_tables_from_sql(sql, context="view/trigger SQL")

    # =========================================================================
    # Coverage & uncertainty
    # =========================================================================

    def compute_coverage(
        self, graph: DependencyGraph, schema: SchemaSnapshot
    ) -> DependencyCoverage:
        """Compute what percentage of tables have at least one lineage edge."""
        return self._coverage.compute_coverage(graph, schema)

    # =========================================================================
    # Edge merging
    # =========================================================================

    def _merge_edges(self, edges: list[DependencyEdge]) -> list[DependencyEdge]:
        """Deduplicate edges by (from, to, usage_type), merge sources."""
        return self._merger.merge(edges)
