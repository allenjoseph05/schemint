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
    # Integer family
    "int": "integer",
    "int2": "smallint",
    "int4": "integer",
    "int8": "bigint",
    "integer": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "tinyint",
    # Serial family
    "serial": "serial",
    "serial4": "serial",
    "serial8": "bigserial",
    "smallserial": "smallserial",
    "serial2": "smallserial",
    "bigserial": "bigserial",
    # Float family
    "float": "float",
    "float4": "real",
    "float8": "double precision",
    "real": "real",
    "double": "double precision",
    "double precision": "double precision",
    # Decimal family
    "decimal": "decimal",
    "numeric": "numeric",
    "money": "money",
    # String family
    "varchar": "varchar",
    "character varying": "varchar",
    "char": "char",
    "character": "char",
    "text": "text",
    "longtext": "text",
    "citext": "citext",
    # Binary family
    "bytea": "bytea",
    "blob": "blob",
    "binary": "binary",
    # Date/Time family
    "date": "date",
    "time": "time",
    "time without time zone": "time",
    "time with time zone": "timetz",
    "timetz": "timetz",
    "datetime": "timestamp",
    "timestamp": "timestamp",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "timestamptz": "timestamptz",
    "interval": "interval",
    # Boolean
    "boolean": "boolean",
    "bool": "boolean",
    # JSON
    "json": "json",
    "jsonb": "jsonb",
    # UUID
    "uuid": "uuid",
    # Enum
    "enum": "enum",
    # Network types
    "inet": "inet",
    "cidr": "cidr",
    "macaddr": "macaddr",
    "macaddr8": "macaddr8",
    # Full-text search
    "tsvector": "tsvector",
    "tsquery": "tsquery",
    # XML
    "xml": "xml",
    # Range types
    "int4range": "int4range",
    "int8range": "int8range",
    "numrange": "numrange",
    "tsrange": "tsrange",
    "tstzrange": "tstzrange",
    "daterange": "daterange",
    # Geometric types
    "point": "point",
    "line": "line",
    "lseg": "lseg",
    "box": "box",
    "path": "path",
    "polygon": "polygon",
    "circle": "circle",
    # Bit string
    "bit": "bit",
    "bit varying": "varbit",
    "varbit": "varbit",
}
