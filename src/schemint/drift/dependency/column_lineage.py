"""Column-level lineage extraction — extracted from DependencyGraphBuilder."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlglot import exp as sqlglot_exp

from schemint.drift.constants import (
    CONFIDENCE_AGGREGATE,
    CONFIDENCE_COLUMN_REF,
    CONFIDENCE_FUNCTION,
    CONFIDENCE_STAR,
    CONFIDENCE_UNRESOLVED_REF,
)
from schemint.drift.models import (
    DependencyEdge,
    DependencySource,
)
from schemint.drift.sql_utils import extract_aliases_from_ast, resolve_column_ref


class ColumnLineageExtractor:
    """Extract column-level lineage from SELECT clauses."""

    def extract(self, statement: Any, now: datetime, file_path: str | None) -> list[DependencyEdge]:
        """Extract column-level lineage from a SQL statement."""
        edges: list[DependencyEdge] = []

        target_name = self._determine_target_name(statement)
        if not target_name:
            return edges

        aliases = extract_aliases_from_ast(statement)

        # Process CTE column lineage
        for cte in statement.find_all(sqlglot_exp.CTE):
            cte_name = cte.alias
            if not cte_name:
                continue
            cte_name = str(cte_name).lower()
            cte_aliases = extract_aliases_from_ast(cte)
            select = cte.find(sqlglot_exp.Select)
            if select:
                edges.extend(
                    self._extract_select_column_lineage(
                        select, cte_aliases, cte_name, now, file_path
                    )
                )

        # Process INSERT INTO ... SELECT column lineage
        if isinstance(statement, sqlglot_exp.Insert):
            select = statement.find(sqlglot_exp.Select)
            insert_table = statement.find(sqlglot_exp.Table)
            if select and insert_table and insert_table.name:
                insert_target = insert_table.name.lower()
                insert_cols = []
                schema_node = statement.args.get("this")
                if schema_node:
                    for col in schema_node.find_all(sqlglot_exp.Column):
                        if col.name:
                            insert_cols.append(col.name.lower())

                edges.extend(
                    self._extract_insert_column_lineage(
                        select, aliases, insert_target, insert_cols, now, file_path
                    )
                )

        # Process top-level SELECT column lineage
        select = statement.find(sqlglot_exp.Select)
        if select and not isinstance(statement, sqlglot_exp.Insert):
            edges.extend(
                self._extract_select_column_lineage(select, aliases, target_name, now, file_path)
            )

        return edges

    def _determine_target_name(self, statement: Any) -> str | None:
        """Determine the target name for column lineage edges."""
        if isinstance(statement, sqlglot_exp.Insert):
            table = statement.find(sqlglot_exp.Table)
            if table and table.name:
                return table.name.lower()

        if isinstance(statement, sqlglot_exp.Create):
            kind = statement.args.get("kind")
            if kind and str(kind).upper() == "VIEW":
                table = statement.find(sqlglot_exp.Table)
                if table and table.name:
                    return table.name.lower()

        if isinstance(statement, sqlglot_exp.Select):
            return "__query__"

        if statement.find(sqlglot_exp.CTE):
            return "__query__"

        return None

    def _extract_select_column_lineage(
        self,
        select_node: Any,
        aliases: dict[str, str],
        target_name: str,
        now: datetime,
        file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract column-level lineage from a SELECT clause."""
        edges: list[DependencyEdge] = []

        expressions = select_node.args.get("expressions", [])
        for expr in expressions:
            output_name = self._get_output_name(expr)

            if isinstance(expr, sqlglot_exp.Star):
                for _alias_name, real_table in aliases.items():
                    edges.append(
                        DependencyEdge(
                            from_element=f"{real_table}.*",
                            to_element=f"{target_name}.*",
                            direction="upstream",
                            usage_type="select",
                            lineage_type="column",
                            sources=[
                                DependencySource(
                                    source_type="sql_ast",
                                    confidence=CONFIDENCE_STAR,
                                    file_path=file_path,
                                    extracted_at=now,
                                )
                            ],
                            final_confidence=CONFIDENCE_STAR,
                        )
                    )
                continue

            columns = list(expr.find_all(sqlglot_exp.Column))
            if not columns:
                continue

            has_agg = bool(expr.find(sqlglot_exp.AggFunc))
            has_func = bool(expr.find(sqlglot_exp.Func)) and not has_agg

            for col in columns:
                ref, resolved = resolve_column_ref(col, aliases)
                source_tables = set(aliases.values()) - {target_name}
                if "." not in ref and len(source_tables) == 1:
                    ref = f"{next(iter(source_tables))}.{ref}"
                    resolved = True
                if "." not in ref:
                    continue

                if has_agg:
                    confidence = CONFIDENCE_AGGREGATE
                elif has_func:
                    confidence = CONFIDENCE_FUNCTION
                else:
                    confidence = CONFIDENCE_COLUMN_REF if resolved else CONFIDENCE_UNRESOLVED_REF

                to_element = f"{target_name}.{output_name}" if output_name else target_name
                edges.append(
                    DependencyEdge(
                        from_element=ref,
                        to_element=to_element,
                        direction="upstream",
                        usage_type="select",
                        lineage_type="column",
                        sources=[
                            DependencySource(
                                source_type="sql_ast",
                                confidence=confidence,
                                file_path=file_path,
                                extracted_at=now,
                                alias_resolved=resolved,
                            )
                        ],
                        final_confidence=confidence,
                    )
                )

        return edges

    def _extract_insert_column_lineage(
        self,
        select_node: Any,
        aliases: dict[str, str],
        target_table: str,
        insert_cols: list[str],
        now: datetime,
        file_path: str | None,
    ) -> list[DependencyEdge]:
        """Extract column-level lineage from INSERT ... SELECT."""
        edges: list[DependencyEdge] = []

        expressions = select_node.args.get("expressions", [])
        for i, expr in enumerate(expressions):
            if insert_cols and i < len(insert_cols):
                target_col = insert_cols[i]
            else:
                target_col = self._get_output_name(expr) or f"col_{i}"

            columns = list(expr.find_all(sqlglot_exp.Column))
            for col in columns:
                ref, resolved = resolve_column_ref(col, aliases)
                source_tables = set(aliases.values()) - {target_table}
                if "." not in ref and len(source_tables) == 1:
                    ref = f"{next(iter(source_tables))}.{ref}"
                    resolved = True
                if "." not in ref:
                    continue

                confidence = CONFIDENCE_COLUMN_REF if resolved else CONFIDENCE_UNRESOLVED_REF
                edges.append(
                    DependencyEdge(
                        from_element=ref,
                        to_element=f"{target_table}.{target_col}",
                        direction="upstream",
                        usage_type="transform",
                        lineage_type="column",
                        sources=[
                            DependencySource(
                                source_type="sql_ast",
                                confidence=confidence,
                                file_path=file_path,
                                extracted_at=now,
                                alias_resolved=resolved,
                            )
                        ],
                        final_confidence=confidence,
                    )
                )

        return edges

    def _get_output_name(self, expr: Any) -> str | None:
        """Get the output column name from a SELECT expression."""
        if hasattr(expr, "alias") and expr.alias:
            return str(expr.alias).lower()
        if isinstance(expr, sqlglot_exp.Column) and expr.name:
            return expr.name.lower()
        return None
