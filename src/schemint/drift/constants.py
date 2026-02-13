"""Named constants for schema drift detection.

Centralizes magic numbers scattered across snapshot, dependency_graph,
context_assembler, and change_classifier modules.
"""

from __future__ import annotations


# =============================================================================
# Confidence Levels (used by DependencyGraphBuilder)
# =============================================================================

# FK constraints are the highest-confidence deterministic source.
CONFIDENCE_FK: float = 1.0

# dbt manifests are authoritative — they describe explicit transformations.
CONFIDENCE_DBT: float = 1.0

# INSERT INTO ... SELECT — very explicit data flow.
CONFIDENCE_INSERT_SELECT: float = 0.95

# View definitions — explicit SQL constructs parsed by sqlglot.
CONFIDENCE_VIEW_DEFINITION: float = 0.95

# JOIN ON with both aliases resolved — strong structural proof.
CONFIDENCE_JOIN_RESOLVED: float = 0.9

# Column-level lineage for direct column references.
CONFIDENCE_COLUMN_REF: float = 0.9

# CTE references — explicit SQL constructs.
CONFIDENCE_CTE: float = 0.85

# Subquery table references — slightly less explicit than CTEs.
CONFIDENCE_SUBQUERY: float = 0.8

# Function-wrapped column references (e.g. LOWER(t.col)).
CONFIDENCE_FUNCTION: float = 0.75

# WHERE col=col with both aliases resolved.
CONFIDENCE_WHERE_RESOLVED: float = 0.7

# Aggregate-wrapped column references (e.g. SUM(t.col)).
CONFIDENCE_AGGREGATE: float = 0.7

# Trigger body table references — parsed from function body.
CONFIDENCE_TRIGGER: float = 0.7

# Star (*) select — lowest column lineage confidence.
CONFIDENCE_STAR: float = 0.6

# JOIN ON or column ref with unresolved aliases.
CONFIDENCE_UNRESOLVED_JOIN: float = 0.5

# Column ref with unresolved alias in lineage.
CONFIDENCE_UNRESOLVED_REF: float = 0.5

# WHERE col=col with unresolved aliases.
CONFIDENCE_UNRESOLVED_WHERE: float = 0.4


# =============================================================================
# Context Quality Thresholds (used by ContextAssembler)
# =============================================================================

# Below this coverage %, context_quality = "insufficient".
COVERAGE_INSUFFICIENT_PCT: float = 50.0

# Below this coverage %, context_quality = "partial".
COVERAGE_PARTIAL_PCT: float = 80.0

# Below this confidence, an edge is considered low-confidence.
LOW_CONFIDENCE_THRESHOLD: float = 0.7

# If ALL impact confidences are below this, context = "insufficient".
ALL_INSUFFICIENT_THRESHOLD: float = 0.5


# =============================================================================
# Canonical Type Mapping (used by snapshot.py / types.py)
# =============================================================================

CANONICAL_TYPES: dict[str, str] = {
    "int": "integer",
    "integer": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "tinyint",
    "float": "float",
    "double": "double",
    "decimal": "decimal",
    "numeric": "numeric",
    "varchar": "varchar",
    "char": "char",
    "text": "text",
    "longtext": "text",
    "date": "date",
    "time": "time",
    "datetime": "timestamp",
    "timestamp": "timestamp",
    "blob": "blob",
    "binary": "binary",
    "boolean": "boolean",
    "bool": "boolean",
    "json": "json",
    "jsonb": "jsonb",
    "uuid": "uuid",
    "enum": "enum",
    "serial": "serial",
    "bigserial": "bigserial",
}
