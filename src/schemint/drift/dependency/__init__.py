"""Dependency graph subpackage — extracted from DependencyGraphBuilder.

Provides focused modules for edge extraction, coverage computation,
and edge merging.
"""

from schemint.drift.dependency.column_lineage import ColumnLineageExtractor
from schemint.drift.dependency.coverage import CoverageComputer
from schemint.drift.dependency.dbt_extractor import DbtEdgeExtractor
from schemint.drift.dependency.edge_merger import EdgeMerger
from schemint.drift.dependency.fk_extractor import FKEdgeExtractor
from schemint.drift.dependency.sql_ast_extractor import SqlAstEdgeExtractor
from schemint.drift.dependency.trigger_extractor import TriggerEdgeExtractor
from schemint.drift.dependency.view_extractor import ViewEdgeExtractor

__all__ = [
    "ColumnLineageExtractor",
    "CoverageComputer",
    "DbtEdgeExtractor",
    "EdgeMerger",
    "FKEdgeExtractor",
    "SqlAstEdgeExtractor",
    "TriggerEdgeExtractor",
    "ViewEdgeExtractor",
]
