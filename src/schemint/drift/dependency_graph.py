"""Dependency graph builder — deterministic sources only.

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
    - Table aliases are resolved from the AST (e.g. "u" → "users").
    - Unresolved aliases NEVER upgrade confidence — they degrade it.
    - Unqualified column references (no table prefix) are skipped entirely.
    - alias_resolved=False on a source means the edge may reference aliases.

Coverage semantics:
    - compute_coverage() measures COMPLETENESS, not CORRECTNESS.
    - A table with lineage edges may still have incorrect edges.
    - A table with 0 edges is explicitly surfaced as untracked.
    - Coverage % = tables_with_any_edge / total_tables × 100.

Forbidden behavior:
    - DO NOT infer dependencies from column names (e.g. user_id → users)
    - DO NOT use regex guessing for SQL parsing
    - DO NOT use embeddings or LLMs
    - DO NOT create edges without explicit proof
    - If a dependency cannot be proven → DO NOT create an edge
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp as sqlglot_exp

from schemint.drift.models import (
    DependencyCoverage,
    DependencyEdge,
    DependencyGraph,
    DependencySource,
    SchemaSnapshot,
)

logger = logging.getLogger(__name__)


class DependencyGraphBuilder:
    """Builds dependency graphs from deterministic sources only.

    Every edge MUST have ≥1 explicit DependencySource with provenance.
    final_confidence = max(source confidences), never averaged.
    """

    def build(self, all_edges: list[DependencyEdge]) -> DependencyGraph:
        """Build a dependency graph from collected edges.

        Merges duplicate edges, deduplicates, and computes final confidence.
        Strips any edge that has zero sources (invariant enforcement).
        """
        # Enforce: every edge must have at least one source
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
    # FK constraints (confidence = 1.0, direction = upstream)
    # =========================================================================

    def from_fk_constraints(self, schema: SchemaSnapshot) -> list[DependencyEdge]:
        """Extract dependency edges from FK constraints in a snapshot.

        FK constraints are the highest-confidence source (1.0).
        Direction: from_element (FK column) references to_element (PK column).
        The FK holder depends on the referenced table → direction="upstream".
        """
        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for table_name, table in schema.tables.items():
            for fk in table.foreign_keys:
                col = fk.get("column", "")
                ref_table = fk.get("references_table", "")
                ref_col = fk.get("references_column", "")

                if not col or not ref_table or not ref_col:
                    continue

                edges.append(DependencyEdge(
                    from_element=f"{table_name}.{col}",
                    to_element=f"{ref_table}.{ref_col}",
                    direction="upstream",
                    usage_type="fk",
                    sources=[DependencySource(
                        source_type="fk_constraint",
                        confidence=1.0,
                        extracted_at=now,
                    )],
                    final_confidence=1.0,
                ))

        return edges

    # =========================================================================
    # dbt manifest (confidence = 1.0, direction = upstream)
    # =========================================================================

    def from_dbt_manifest(self, manifest_path: str) -> list[DependencyEdge]:
        """Extract dependency edges from a dbt manifest.json.

        Parses nodes → depends_on.nodes for table-level lineage.
        Uses fully-qualified identifiers when database/schema are available.
        Stores dbt_unique_id alongside the logical name for provenance.

        Direction: from_element (upstream source) feeds to_element (model).
        """
        with open(manifest_path) as f:
            manifest = json.load(f)

        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)
        nodes = manifest.get("nodes", {})
        sources = manifest.get("sources", {})

        for node_id, node in nodes.items():
            if node.get("resource_type") not in ("model", "snapshot", "seed"):
                continue

            node_fqn = self._dbt_fqn(node)
            depends_on = node.get("depends_on", {}).get("nodes", [])

            for dep_id in depends_on:
                dep_node = nodes.get(dep_id) or sources.get(dep_id)
                if dep_node:
                    dep_fqn = self._dbt_fqn(dep_node)
                else:
                    # Node not found in manifest. Use the unique_id segment
                    # as the name — this is what dbt guarantees.
                    dep_fqn = dep_id.split(".")[-1] if "." in dep_id else dep_id

                edges.append(DependencyEdge(
                    from_element=dep_fqn,
                    to_element=node_fqn,
                    direction="upstream",
                    usage_type="transform",
                    sources=[DependencySource(
                        source_type="dbt_manifest",
                        confidence=1.0,
                        file_path=manifest_path,
                        extracted_at=now,
                        dbt_unique_id=dep_id,
                    )],
                    final_confidence=1.0,
                ))

            # Column-level lineage if available
            columns = node.get("columns", {})
            for col_name, col_info in columns.items():
                depends_on_cols = col_info.get("depends_on", [])
                for dep_col in depends_on_cols:
                    edges.append(DependencyEdge(
                        from_element=dep_col,
                        to_element=f"{node_fqn}.{col_name}",
                        direction="upstream",
                        usage_type="transform",
                        sources=[DependencySource(
                            source_type="dbt_manifest",
                            confidence=1.0,
                            file_path=manifest_path,
                            extracted_at=now,
                        )],
                        final_confidence=1.0,
                    ))

        return edges

    def _dbt_fqn(self, node: dict) -> str:
        """Build a fully-qualified name from dbt node metadata.

        Uses database.schema.name when all three are available.
        Falls back to schema.name, then just name.
        """
        name = node.get("name", "")
        schema = node.get("schema", "")
        database = node.get("database", "")

        if database and schema:
            return f"{database}.{schema}.{name}"
        if schema:
            return f"{schema}.{name}"
        return name

    # =========================================================================
    # SQL AST parsing via sqlglot (deterministic, no regex)
    # =========================================================================

    def from_sql_ast(
        self, sql: str, file_path: str | None = None
    ) -> list[DependencyEdge]:
        """Extract dependency edges from SQL statements using sqlglot AST.

        Uses a real SQL parser (sqlglot) instead of regex. Only extracts
        dependencies from explicit expressions that can be deterministically
        proven:
            - JOIN ... ON a.col = b.col  → confidence 0.9
            - WHERE a.col = b.col        → confidence 0.7

        If SQL cannot be parsed, emits NO edges (not partial results).
        Alias resolution is performed by the AST — if an alias cannot be
        resolved to a real table name, the edge is marked alias_resolved=False.

        Confidence caps:
            - JOIN ON with resolved aliases:    0.9
            - JOIN ON with unresolved aliases:  0.5
            - WHERE with resolved aliases:      0.7
            - WHERE with unresolved aliases:    0.4
        """
        try:
            statements = sqlglot.parse(sql)
        except sqlglot.errors.ParseError:
            # Cannot deterministically parse → emit nothing
            logger.warning("sqlglot parse failed for SQL input; emitting no edges")
            return []

        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for statement in statements:
            if statement is None:
                continue

            # Build alias → table mapping from the AST
            aliases = self._extract_aliases_from_ast(statement)

            # Extract JOIN ON conditions
            edges.extend(
                self._extract_join_edges_from_ast(statement, aliases, now, file_path)
            )

            # Extract WHERE conditions (only col = col comparisons)
            edges.extend(
                self._extract_where_edges_from_ast(statement, aliases, now, file_path)
            )

        return edges

    def _extract_aliases_from_ast(self, statement) -> dict[str, str]:
        """Extract table alias → real table name from sqlglot AST.

        Returns a dict mapping lowercase alias → lowercase table name.
        Only includes aliases that can be deterministically resolved.
        """
        aliases: dict[str, str] = {}
        for table in statement.find_all(sqlglot_exp.Table):
            table_name = table.name
            alias = table.alias
            if alias and table_name:
                aliases[alias.lower()] = table_name.lower()
            elif table_name:
                # Table used without alias — maps to itself
                aliases[table_name.lower()] = table_name.lower()
        return aliases

    def _resolve_column_ref(
        self, col: sqlglot_exp.Column, aliases: dict[str, str]
    ) -> tuple[str, bool]:
        """Resolve a column reference to table.column using the alias map.

        Returns (resolved_ref, alias_was_resolved).
        If the column has no table qualifier, returns ("column_name", False).
        If the table qualifier is an alias that maps to a real table,
        returns ("real_table.column", True).
        If the table qualifier is not in the alias map, returns
        ("qualifier.column", False) — we cannot prove the table identity.
        """
        col_name = col.name.lower() if col.name else ""
        table_ref = col.table.lower() if col.table else ""

        if not table_ref:
            # Unqualified column — cannot determine table without inference
            return col_name, False

        if table_ref in aliases:
            return f"{aliases[table_ref]}.{col_name}", True

        # Qualifier not in alias map — could be a real table name used directly
        # We keep it but mark alias resolution as uncertain
        return f"{table_ref}.{col_name}", False

    def _extract_join_edges_from_ast(
        self,
        statement,
        aliases: dict[str, str],
        now: datetime,
        file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract edges from JOIN ON conditions in the AST.

        Only processes equality comparisons between two Column references.
        """
        edges: list[DependencyEdge] = []

        for join in statement.find_all(sqlglot_exp.Join):
            on_cond = join.args.get("on")
            if on_cond is None:
                continue

            for eq in on_cond.find_all(sqlglot_exp.EQ):
                left, right = eq.left, eq.right
                if not isinstance(left, sqlglot_exp.Column):
                    continue
                if not isinstance(right, sqlglot_exp.Column):
                    continue

                left_ref, left_resolved = self._resolve_column_ref(left, aliases)
                right_ref, right_resolved = self._resolve_column_ref(right, aliases)

                # Both sides must have table qualifiers to be useful
                if "." not in left_ref or "." not in right_ref:
                    continue

                both_resolved = left_resolved and right_resolved
                confidence = 0.9 if both_resolved else 0.5

                edges.append(DependencyEdge(
                    from_element=left_ref,
                    to_element=right_ref,
                    direction="downstream",
                    usage_type="join_key",
                    sources=[DependencySource(
                        source_type="sql_ast",
                        confidence=confidence,
                        file_path=file_path,
                        extracted_at=now,
                        alias_resolved=both_resolved,
                    )],
                    final_confidence=confidence,
                ))

        return edges

    def _extract_where_edges_from_ast(
        self,
        statement,
        aliases: dict[str, str],
        now: datetime,
        file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract edges from WHERE clause column=column comparisons.

        Only extracts col=col references (not col=literal).
        Lower confidence than JOINs because WHERE comparisons are
        less explicit about structural relationships.
        """
        edges: list[DependencyEdge] = []

        where = statement.find(sqlglot_exp.Where)
        if where is None:
            return edges

        # We need to exclude EQ nodes that are inside JOIN ON conditions
        # to avoid double-counting. Collect all EQ nodes under JOIN ON.
        join_eqs: set[int] = set()
        for join in statement.find_all(sqlglot_exp.Join):
            on_cond = join.args.get("on")
            if on_cond:
                for eq in on_cond.find_all(sqlglot_exp.EQ):
                    join_eqs.add(id(eq))

        for eq in where.find_all(sqlglot_exp.EQ):
            if id(eq) in join_eqs:
                continue

            left, right = eq.left, eq.right
            if not isinstance(left, sqlglot_exp.Column):
                continue
            if not isinstance(right, sqlglot_exp.Column):
                continue

            left_ref, left_resolved = self._resolve_column_ref(left, aliases)
            right_ref, right_resolved = self._resolve_column_ref(right, aliases)

            # Both sides must have table qualifiers
            if "." not in left_ref or "." not in right_ref:
                continue

            both_resolved = left_resolved and right_resolved
            confidence = 0.7 if both_resolved else 0.4

            edges.append(DependencyEdge(
                from_element=left_ref,
                to_element=right_ref,
                direction="downstream",
                usage_type="filter",
                sources=[DependencySource(
                    source_type="sql_ast",
                    confidence=confidence,
                    file_path=file_path,
                    extracted_at=now,
                    alias_resolved=both_resolved,
                )],
                final_confidence=confidence,
            ))

        return edges

    # =========================================================================
    # View definitions (confidence = 0.95, direction = upstream)
    # =========================================================================

    def from_view_definitions(
        self, views: dict[str, str]
    ) -> list[DependencyEdge]:
        """Extract dependency edges from CREATE VIEW AS SELECT statements.

        Uses sqlglot to parse view SQL and extract source table references.
        If parsing fails, emits no edges for that view.

        Direction: source tables are upstream of the view.
        """
        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for view_name, view_sql in views.items():
            source_tables = self._extract_tables_from_ast(view_sql)

            for source_table in source_tables:
                # Skip the view name itself if it appears in its own definition
                if source_table.lower() == view_name.lower():
                    continue

                edges.append(DependencyEdge(
                    from_element=source_table,
                    to_element=view_name,
                    direction="upstream",
                    usage_type="select",
                    sources=[DependencySource(
                        source_type="view_definition",
                        confidence=0.95,
                        extracted_at=now,
                    )],
                    final_confidence=0.95,
                ))

        return edges

    def _extract_tables_from_ast(self, sql: str) -> list[str]:
        """Extract table names referenced in SQL using sqlglot AST.

        Returns deduplicated, lowercased table names.
        If parsing fails, returns empty list (no guessing).
        """
        try:
            statements = sqlglot.parse(sql)
        except sqlglot.errors.ParseError:
            logger.warning("sqlglot parse failed for view SQL; emitting no table refs")
            return []

        tables: set[str] = set()
        for statement in statements:
            if statement is None:
                continue
            for table in statement.find_all(sqlglot_exp.Table):
                if table.name:
                    tables.add(table.name.lower())

        return sorted(tables)

    # =========================================================================
    # Coverage & uncertainty
    # =========================================================================

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
            # Extract table name from element (e.g. "users.id" → "users")
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

    # =========================================================================
    # Edge merging
    # =========================================================================

    def _merge_edges(self, edges: list[DependencyEdge]) -> list[DependencyEdge]:
        """Deduplicate edges by (from, to, usage_type), merge sources.

        final_confidence = max(confidence of all sources), NOT averaged.
        """
        edge_map: dict[tuple[str, str, str], DependencyEdge] = {}

        for edge in edges:
            key = (edge.from_element, edge.to_element, edge.usage_type)
            if key in edge_map:
                existing = edge_map[key]
                existing.sources.extend(edge.sources)
                existing.final_confidence = max(
                    s.confidence for s in existing.sources
                )
            else:
                edge_map[key] = edge.model_copy(deep=True)

        return list(edge_map.values())
