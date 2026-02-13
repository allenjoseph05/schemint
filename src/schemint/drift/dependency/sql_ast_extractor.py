"""SQL AST edge extraction — extracted from DependencyGraphBuilder.

Handles JOINs, WHERE, CTEs, subqueries, INSERT SELECT via sqlglot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp as sqlglot_exp

from schemint.drift.constants import (
    CONFIDENCE_CTE,
    CONFIDENCE_INSERT_SELECT,
    CONFIDENCE_JOIN_RESOLVED,
    CONFIDENCE_SUBQUERY,
    CONFIDENCE_UNRESOLVED_JOIN,
    CONFIDENCE_UNRESOLVED_WHERE,
    CONFIDENCE_WHERE_RESOLVED,
)
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
    ParseHealth,
)
from schemint.drift.sql_utils import extract_aliases_from_ast, resolve_column_ref

logger = logging.getLogger(__name__)


class SqlAstEdgeExtractor:
    """Extract dependency edges from SQL statements using sqlglot AST."""

    def extract(
        self, sql: str, file_path: str | None = None
    ) -> list[DependencyEdge]:
        """Extract all edge types from SQL. Returns empty list on parse failure."""
        try:
            statements = sqlglot.parse(sql)
        except sqlglot.errors.ParseError:
            logger.warning("sqlglot parse failed for SQL input; emitting no edges")
            return []

        edges: list[DependencyEdge] = []
        now = datetime.now(timezone.utc)

        for statement in statements:
            if statement is None:
                continue

            aliases = extract_aliases_from_ast(statement)

            edges.extend(self._extract_join_edges(statement, aliases, now, file_path))
            edges.extend(self._extract_where_edges(statement, aliases, now, file_path))
            edges.extend(self._extract_cte_edges(statement, now, file_path))
            edges.extend(self._extract_subquery_edges(statement, now, file_path))
            edges.extend(self._extract_insert_select_edges(statement, now, file_path))

        return edges

    def extract_batch(
        self, sql_files: dict[str, str]
    ) -> tuple[list[DependencyEdge], ParseHealth]:
        """Extract edges from multiple SQL files with parse health tracking."""
        all_edges: list[DependencyEdge] = []
        health = ParseHealth(total_files=len(sql_files))

        for file_path, sql_content in sql_files.items():
            try:
                edges = self.extract(sql_content, file_path=file_path)
                all_edges.extend(edges)
                health.parsed_ok += 1
            except Exception as e:
                logger.warning("Failed to parse %s: %s", file_path, e)
                health.parse_failures.append(file_path)

        return all_edges, health

    def _extract_join_edges(
        self, statement, aliases: dict[str, str],
        now: datetime, file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract edges from JOIN ON conditions."""
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

                left_ref, left_resolved = resolve_column_ref(left, aliases)
                right_ref, right_resolved = resolve_column_ref(right, aliases)

                if "." not in left_ref or "." not in right_ref:
                    continue

                both_resolved = left_resolved and right_resolved
                confidence = CONFIDENCE_JOIN_RESOLVED if both_resolved else CONFIDENCE_UNRESOLVED_JOIN

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

    def _extract_where_edges(
        self, statement, aliases: dict[str, str],
        now: datetime, file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract edges from WHERE clause column=column comparisons."""
        edges: list[DependencyEdge] = []

        where = statement.find(sqlglot_exp.Where)
        if where is None:
            return edges

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

            left_ref, left_resolved = resolve_column_ref(left, aliases)
            right_ref, right_resolved = resolve_column_ref(right, aliases)

            if "." not in left_ref or "." not in right_ref:
                continue

            both_resolved = left_resolved and right_resolved
            confidence = CONFIDENCE_WHERE_RESOLVED if both_resolved else CONFIDENCE_UNRESOLVED_WHERE

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

    def _extract_cte_edges(
        self, statement, now: datetime, file_path: str | None
    ) -> list[DependencyEdge]:
        """Extract edges from CTE (WITH ... AS) definitions."""
        edges: list[DependencyEdge] = []

        for cte in statement.find_all(sqlglot_exp.CTE):
            cte_name = cte.alias
            if not cte_name:
                continue
            cte_name = str(cte_name).lower()

            for table in cte.find_all(sqlglot_exp.Table):
                source_table = table.name
                if not source_table:
                    continue
                source_table = source_table.lower()

                if source_table == cte_name:
                    continue

                edges.append(DependencyEdge(
                    from_element=source_table,
                    to_element=cte_name,
                    direction="upstream",
                    usage_type="transform",
                    sources=[DependencySource(
                        source_type="sql_ast",
                        confidence=CONFIDENCE_CTE,
                        file_path=file_path,
                        extracted_at=now,
                    )],
                    final_confidence=CONFIDENCE_CTE,
                ))

        return edges

    def _extract_subquery_edges(
        self, statement, now: datetime, file_path: str | None
    ) -> list[DependencyEdge]:
        """Extract table references from subqueries."""
        edges: list[DependencyEdge] = []

        for subquery in statement.find_all(sqlglot_exp.Subquery):
            subquery_alias = subquery.alias
            if not subquery_alias:
                continue
            subquery_alias = str(subquery_alias).lower()

            for table in subquery.find_all(sqlglot_exp.Table):
                source_table = table.name
                if not source_table:
                    continue
                source_table = source_table.lower()

                edges.append(DependencyEdge(
                    from_element=source_table,
                    to_element=subquery_alias,
                    direction="upstream",
                    usage_type="select",
                    sources=[DependencySource(
                        source_type="sql_ast",
                        confidence=CONFIDENCE_SUBQUERY,
                        file_path=file_path,
                        extracted_at=now,
                    )],
                    final_confidence=CONFIDENCE_SUBQUERY,
                ))

        return edges

    def _extract_insert_select_edges(
        self, statement, now: datetime, file_path: str | None
    ) -> list[DependencyEdge]:
        """Extract dependencies from INSERT INTO ... SELECT ... FROM."""
        edges: list[DependencyEdge] = []

        if not isinstance(statement, sqlglot_exp.Insert):
            return edges

        insert_table = statement.find(sqlglot_exp.Table)
        if not insert_table or not insert_table.name:
            return edges
        target_table = insert_table.name.lower()

        select = statement.find(sqlglot_exp.Select)
        if not select:
            return edges

        for table in select.find_all(sqlglot_exp.Table):
            source_table = table.name
            if not source_table:
                continue
            source_table = source_table.lower()

            if source_table == target_table:
                continue

            edges.append(DependencyEdge(
                from_element=source_table,
                to_element=target_table,
                direction="upstream",
                usage_type="transform",
                sources=[DependencySource(
                    source_type="sql_ast",
                    confidence=CONFIDENCE_INSERT_SELECT,
                    file_path=file_path,
                    extracted_at=now,
                )],
                final_confidence=CONFIDENCE_INSERT_SELECT,
            ))

        return edges
