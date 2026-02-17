"""Pydantic models for schema drift detection.

Design invariant:
    "Only record what can be provably extracted.
     Missing information results in uncertainty, not inference."
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# =============================================================================
# Snapshot Models (Phase 0)
# =============================================================================


class ColumnSnapshot(BaseModel):
    """Snapshot of a single column's state.

    All values come from the DDL parser or information_schema — no inference.
    Type names are stored in canonical lowercase form for stable comparison.
    """

    name: str
    type: str  # Canonical lowercase, e.g. "integer", "varchar(255)"
    nullable: bool = True
    default: str | None = None
    constraints: list[str] = Field(default_factory=list)


class ForeignKeySnapshot(BaseModel):
    """Snapshot of a single foreign key constraint.

    Replaces untyped dict with explicit fields for type safety
    and attribute access (fk.column instead of fk.get("column")).
    """

    name: str = ""
    column: str = ""
    references_table: str = ""
    references_column: str = ""
    on_delete: str | None = None
    on_update: str | None = None


class IndexSnapshot(BaseModel):
    """Snapshot of a single index.

    Replaces untyped dict with explicit fields for type safety.
    """

    name: str = ""
    columns: list[str] = Field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False
    definition: str | None = None  # Raw index definition from pg_indexes


class TableSnapshot(BaseModel):
    """Snapshot of a single table's state.

    Column order is preserved from the source (DDL ordinal or
    information_schema.ordinal_position) via insertion-ordered dict.

    foreign_keys and indexes accept both typed models and raw dicts
    (for backward compatibility). The field_validator coerces dicts
    to the typed model automatically.
    """

    name: str
    columns: dict[str, ColumnSnapshot] = Field(default_factory=dict)
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[ForeignKeySnapshot | IndexSnapshot | dict[str, Any]] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySnapshot | dict[str, Any]] = Field(default_factory=list)

    @field_validator("foreign_keys", mode="before")
    @classmethod
    def coerce_foreign_keys(cls, v: list[Any]) -> list[Any]:
        """Coerce raw dicts to ForeignKeySnapshot models."""
        _str_fields = {"name", "column", "references_table", "references_column"}
        result = []
        for item in v:
            if isinstance(item, dict):
                # Replace None with "" for required string fields
                cleaned = {}
                for k, val in item.items():
                    if k not in ForeignKeySnapshot.model_fields:
                        continue
                    if val is None and k in _str_fields:
                        val = ""
                    cleaned[k] = val
                result.append(ForeignKeySnapshot(**cleaned))
            else:
                result.append(item)
        return result

    @field_validator("indexes", mode="before")
    @classmethod
    def coerce_indexes(cls, v: list[Any]) -> list[Any]:
        """Coerce raw dicts to IndexSnapshot models."""
        result = []
        for item in v:
            if isinstance(item, dict):
                cleaned = {k: v for k, v in item.items() if k in IndexSnapshot.model_fields}
                result.append(IndexSnapshot(**cleaned))
            else:
                result.append(item)
        return result


class ViewSnapshot(BaseModel):
    """Snapshot of a database view definition.

    Captures the view name, its SQL body, and source tables referenced.
    """

    name: str
    definition: str  # SQL body (SELECT ...)
    source_tables: list[str] = Field(default_factory=list)  # Tables referenced


class TriggerSnapshot(BaseModel):
    """Snapshot of a database trigger.

    Captures the trigger name, the table it's attached to, the event/timing,
    the function it calls, and optionally the full trigger body.
    """

    name: str
    table: str  # Table this trigger is attached to
    event: str  # INSERT, UPDATE, DELETE, or combination
    timing: str  # BEFORE, AFTER, INSTEAD OF
    function_name: str  # The function it calls
    definition: str | None = None  # Full trigger body if available


class SequenceSnapshot(BaseModel):
    """Snapshot of a database sequence.

    Sequence changes affect auto-increment columns. Resetting a sequence
    can cause duplicate key violations; exhausting the range causes INSERT
    failures.
    """

    name: str
    data_type: str = "bigint"
    start_value: int = 1
    increment_by: int = 1
    min_value: int = 1
    max_value: int | None = None
    cache_size: int = 1
    cycle: bool = False
    last_value: int | None = None


class EnumSnapshot(BaseModel):
    """Snapshot of a PostgreSQL enum type.

    Value ordering matters: many applications rely on enum ordering
    for business logic. Value removal is always breaking (existing
    data references the removed value).
    """

    name: str
    values: list[str] = Field(default_factory=list)


class FunctionSnapshot(BaseModel):
    """Snapshot of a database function or procedure.

    Triggers call functions. Computed columns use functions. Views may
    reference functions. A function change silently changes the behavior
    of everything that depends on it.
    """

    name: str
    argument_types: str = ""
    return_type: str = ""
    language: str = "sql"
    volatility: Literal["volatile", "stable", "immutable"] = "volatile"
    definition: str | None = None


class TableStatistics(BaseModel):
    """Runtime statistics for a table from pg_stat_user_tables.

    The AI agent uses these to gauge migration risk:
    - row_count: ALTER on 100 rows is safe; on 500M rows it locks for minutes
    - dead_tuple_ratio: indicates table health / vacuum needs
    - size: determines lock duration for DDL operations
    """

    table_name: str
    row_count: int = 0
    dead_tuples: int = 0
    total_size_bytes: int = 0
    table_size_bytes: int = 0
    index_size_bytes: int = 0
    seq_scan_count: int = 0
    idx_scan_count: int = 0
    last_vacuum: datetime | None = None
    last_analyze: datetime | None = None


class IndexStatistics(BaseModel):
    """Runtime statistics for an index from pg_stat_user_indexes.

    When index_dropped is detected, the AI needs to know whether the
    index was actively used (breaking) or unused (safe cleanup).
    """

    index_name: str
    table_name: str
    idx_scan: int = 0
    idx_tup_read: int = 0
    idx_tup_fetch: int = 0
    size_bytes: int = 0


class ExtensionSnapshot(BaseModel):
    """Snapshot of an installed PostgreSQL extension.

    Extension version changes can alter available functions, operators,
    and types. Extension removal breaks all objects that depend on it
    (e.g. removing pg_trgm breaks GIN indexes using gin_trgm_ops).
    """

    name: str
    version: str = ""
    installed_schema: str = "public"


class PermissionSnapshot(BaseModel):
    """Snapshot of table-level permissions/ACLs.

    Permission changes can silently break applications that rely on
    specific grants. Revoking SELECT from an application role causes
    immediate query failures.
    """

    table_name: str
    grantee: str
    privilege_type: str  # SELECT, INSERT, UPDATE, DELETE, etc.
    is_grantable: bool = False


class PolicySnapshot(BaseModel):
    """Snapshot of a Row-Level Security (RLS) policy.

    RLS policy changes silently filter query results — a policy change
    can cause an application to return zero rows where it previously
    returned thousands, with no error.
    """

    name: str
    table: str
    command: str = "ALL"  # SELECT, INSERT, UPDATE, DELETE, ALL
    permissive: bool = True  # True = PERMISSIVE, False = RESTRICTIVE
    roles: list[str] = Field(default_factory=list)
    qual_expression: str | None = None  # USING clause
    with_check_expression: str | None = None  # WITH CHECK clause


class PartitionInfo(BaseModel):
    """Snapshot of a single partition within a partitioned table.

    Partition boundary changes affect which rows land in which partition.
    Missing partitions for new data ranges cause INSERT failures.
    """

    partition_name: str
    parent_table: str
    partition_bound: str = ""  # e.g. "FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')"


class MaterializedViewSnapshot(BaseModel):
    """Snapshot of a materialized view.

    Unlike regular views, materialized views store data physically.
    They must be explicitly refreshed — stale matviews serve outdated data.
    Dropping/changing a matview's source tables does NOT error, it just
    makes REFRESH fail later.
    """

    name: str
    definition: str = ""
    is_populated: bool = True
    tablespace: str | None = None
    source_tables: list[str] = Field(default_factory=list)


class ColumnStatistics(BaseModel):
    """Column-level statistics from pg_stats.

    Detects data distribution drift — if null_frac changes from 0.01
    to 0.5, something is wrong upstream. Useful for the AI agent to
    assess whether a NOT NULL constraint is safe to add.
    """

    column_name: str
    table_name: str
    null_frac: float = 0.0  # Fraction of NULLs (0.0 = no nulls, 1.0 = all nulls)
    n_distinct: float = 0.0  # Estimated distinct values (-1 = all unique)
    avg_width: int = 0  # Average width in bytes
    correlation: float = 0.0  # Physical vs logical row ordering correlation


class DataQualitySignals(BaseModel):
    """Data quality signals derived from table statistics.

    Provides actionable indicators for the AI agent:
    - dead_tuple_ratio: high ratio indicates vacuum needed
    - seq_scan_ratio: high ratio indicates missing indexes
    - last_vacuum_age_hours: hours since last vacuum
    - last_analyze_age_hours: hours since last analyze
    - is_vacuum_needed: True if dead_tuple_ratio > 10%
    - is_analyze_stale: True if last_analyze > 72 hours ago
    """

    dead_tuple_ratio: float = 0.0
    seq_scan_ratio: float = 0.0
    last_vacuum_age_hours: float | None = None
    last_analyze_age_hours: float | None = None
    is_vacuum_needed: bool = False
    is_analyze_stale: bool = False


class SchemaSnapshot(BaseModel):
    """Complete snapshot of a single database schema at a point in time.

    Scope: single schema (e.g. "public"). Multi-schema and cross-database
    snapshots are NOT supported — each snapshot covers exactly one schema.
    The schema_name field records which schema was captured.
    """

    snapshot_id: str  # Timestamp-based, never random — e.g. "ddl_20240101_120000"
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["ddl", "live_db", "composed", "desired_state"]
    database_type: str = "postgresql"
    schema_name: str = "public"  # The single schema this snapshot covers
    environment: str = "default"  # e.g. "dev", "staging", "prod"
    is_desired_state: bool = False  # True if this represents desired (target) state
    tables: dict[str, TableSnapshot] = Field(default_factory=dict)
    views: dict[str, ViewSnapshot] = Field(default_factory=dict)
    triggers: dict[str, TriggerSnapshot] = Field(default_factory=dict)
    sequences: dict[str, SequenceSnapshot] = Field(default_factory=dict)
    enums: dict[str, EnumSnapshot] = Field(default_factory=dict)
    functions: dict[str, FunctionSnapshot] = Field(default_factory=dict)
    table_statistics: dict[str, TableStatistics] = Field(default_factory=dict)
    index_statistics: dict[str, IndexStatistics] = Field(default_factory=dict)
    extensions: dict[str, ExtensionSnapshot] = Field(default_factory=dict)
    permissions: list[PermissionSnapshot] = Field(default_factory=list)
    policies: dict[str, PolicySnapshot] = Field(default_factory=dict)
    partitions: dict[str, list[PartitionInfo]] = Field(default_factory=dict)
    materialized_views: dict[str, MaterializedViewSnapshot] = Field(default_factory=dict)
    column_statistics: dict[str, list[ColumnStatistics]] = Field(default_factory=dict)


# =============================================================================
# Dependency Models (Phase 0)
# =============================================================================


class DependencySource(BaseModel):
    """A single provable source for a dependency edge.

    Every edge MUST have at least one source with explicit provenance.
    No edge may exist without proof of origin.
    """

    source_type: Literal[
        "dbt_manifest", "sql_ast", "view_definition", "fk_constraint", "trigger_definition"
    ]
    confidence: float = Field(..., ge=0.0, le=1.0)
    file_path: str | None = None
    line_number: int | None = None
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # For dbt sources: the full unique_id (e.g. "model.project.users")
    # Preserves context that the short name alone would lose.
    dbt_unique_id: str | None = None
    # Whether table aliases in SQL were fully resolved to real table names.
    # False means the edge references may contain unresolved aliases.
    alias_resolved: bool = True

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Confidence must be between 0 and 1")
        return v


class DependencyEdge(BaseModel):
    """A directed dependency between two schema elements.

    Direction semantics (consistent across ALL extractors):
        from_element = UPSTREAM (depended upon, data source).
        to_element   = DOWNSTREAM (depends on from_element, data consumer).

    BFS downstream traversal follows from_element → to_element.
    BFS upstream traversal follows to_element → from_element.

    Example: FK orders.user_id → users.id means
        from_element="users.id"       (upstream, referenced)
        to_element="orders.user_id"   (downstream, has the FK)
        direction="downstream"

    Every edge MUST have ≥1 source. Edges without proof are forbidden.
    final_confidence = max(source confidences), never averaged.
    """

    from_element: str  # e.g. "orders.user_id"
    to_element: str  # e.g. "users.id"
    direction: Literal["upstream", "downstream"] = "downstream"
    usage_type: Literal["join_key", "fk", "select", "filter", "group_by", "transform"]
    sources: list[DependencySource] = Field(default_factory=list)
    final_confidence: float = Field(0.0, ge=0.0, le=1.0)
    lineage_type: Literal["table", "column"] = "table"

    @field_validator("sources")
    @classmethod
    def validate_has_source(cls, v: list[DependencySource]) -> list[DependencySource]:
        # Allow empty during construction (e.g. test fixtures) but warn
        # In production, build() enforces this invariant.
        return v


class DependencyGraph(BaseModel):
    """Complete dependency graph built from deterministic sources.

    Invariant: every edge has ≥1 explicit DependencySource.
    No inferred edges. Missing lineage = uncertainty, not fabricated edges.
    """

    edges: list[DependencyEdge] = Field(default_factory=list)
    built_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DependencyCoverage(BaseModel):
    """Coverage metrics for the dependency graph.

    Surfaces what percentage of the schema has provable lineage
    and explicitly lists tables with NO lineage (opaque/untracked).
    Missing lineage reduces confidence — it does not invent edges.
    """

    tables_total: int = 0
    tables_with_lineage: int = 0
    coverage_pct: float = 0.0
    untracked_tables: list[str] = Field(default_factory=list)


# =============================================================================
# Diff Models (Phase 1)
# =============================================================================


class SchemaChangeEvent(BaseModel):
    """A single detected schema change."""

    change_type: Literal[
        "column_added",
        "column_dropped",
        "column_type_change",
        "column_nullable_change",
        "column_default_change",
        "column_constraint_change",
        "table_added",
        "table_dropped",
        "table_renamed",
        "pk_added",
        "pk_dropped",
        "pk_changed",
        "index_added",
        "index_dropped",
        "index_changed",
        "fk_added",
        "fk_dropped",
        "fk_action_change",
        "view_added",
        "view_dropped",
        "view_definition_change",
        "trigger_added",
        "trigger_dropped",
        "trigger_changed",
        "sequence_added",
        "sequence_dropped",
        "sequence_changed",
        "enum_added",
        "enum_dropped",
        "enum_value_added",
        "enum_value_removed",
        "function_added",
        "function_dropped",
        "function_changed",
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
        "matview_added",
        "matview_dropped",
        "matview_definition_changed",
    ]
    table: str
    column: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    change_risk: Literal["safe", "needs_review", "potentially_breaking", "breaking"] | None = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SchemaDiffResult(BaseModel):
    """Result of diffing two schema snapshots."""

    old_snapshot_id: str
    new_snapshot_id: str
    changes: list[SchemaChangeEvent] = Field(default_factory=list)
    diffed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Context Models (Phase 2)
# =============================================================================


class ImpactAssessment(BaseModel):
    """Impact assessment for a single downstream dependency."""

    table: str
    usage: str
    dependency_count: int = 0
    confidence: float = 0.0


class ImpactMetrics(BaseModel):
    """Aggregate impact metrics for a schema change."""

    downstream_tables: int = 0
    downstream_columns: int = 0
    max_depth: int = 0
    criticality: Literal["low", "medium", "high", "critical"] = "low"


class ParseHealth(BaseModel):
    """Tracks parse success/failure rates for dependency extraction.

    Surfaces which SQL files failed to parse so the system knows
    its dependency graph may be incomplete.
    """

    total_files: int = 0
    parsed_ok: int = 0
    parse_failures: list[str] = Field(default_factory=list)  # file paths that failed

    @property
    def success_rate(self) -> float:
        if self.total_files == 0:
            return 1.0
        return self.parsed_ok / self.total_files


class ContextGaps(BaseModel):
    """Explicitly surfaces what information is missing from context.

    The AI agent can use this to calibrate confidence and decide
    whether to escalate to a human.
    """

    missing_upstream_tables: list[str] = Field(default_factory=list)
    missing_downstream_tables: list[str] = Field(default_factory=list)
    untracked_tables: list[str] = Field(default_factory=list)
    low_confidence_edges: int = 0
    parse_health: ParseHealth = Field(default_factory=ParseHealth)
    has_gaps: bool = False


class ContextPackage(BaseModel):
    """Complete context package for a single schema change — sole input to AI.

    context_quality signals how much the AI can trust this package:
        - "complete": Full coverage, high confidence, no depth truncation.
        - "partial": Coverage gaps, low-confidence edges, or depth was truncated.
        - "insufficient": Coverage <50% or all edges below confidence threshold.
    """

    schema_change: SchemaChangeEvent
    environment: str = "default"  # Environment this context was assembled from
    impacted_dependencies: list[ImpactAssessment] = Field(default_factory=list)
    impact_metrics: ImpactMetrics = Field(default_factory=ImpactMetrics)
    dependency_coverage: DependencyCoverage = Field(default_factory=DependencyCoverage)
    context_quality: Literal["complete", "partial", "insufficient"] = "complete"
    context_gaps: ContextGaps | None = None
    upstream_impacts: list[ImpactAssessment] = Field(default_factory=list)
    affected_table_stats: TableStatistics | None = None
    affected_index_stats: list[IndexStatistics] = Field(default_factory=list)
    affected_column_stats: list[ColumnStatistics] = Field(default_factory=list)
    affected_permissions: list[PermissionSnapshot] = Field(default_factory=list)
    affected_functions: list[FunctionSnapshot] = Field(default_factory=list)
    data_quality_signals: DataQualitySignals | None = None


# =============================================================================
# Agent Decision Models (Phase 3)
# =============================================================================


class AgentDecision(BaseModel):
    """AI agent's severity judgment for a schema change.

    The LLM may escalate severity above the deterministic floor
    but NEVER below it. Post-AI invariants enforce safety constraints.
    """

    severity: Literal["low", "medium", "high", "critical"]
    confidence_in_decision: float = Field(..., ge=0.0, le=1.0)
    requires_human_review: bool
    rationale: list[str] = Field(..., min_length=1)
    recommended_action_categories: list[
        Literal[
            "backward_compatibility",
            "downstream_updates",
            "monitor_only",
            "block_deploy",
            "notify_owner",
        ]
    ] = Field(..., max_length=3)
    context_quality: Literal["complete", "partial", "insufficient"]


# =============================================================================
# Execution Plan Models (Phase 4)
# =============================================================================


class PlanStep(BaseModel):
    """A single step in an execution plan.

    action must match an ActionTemplate.action_id from the registry.
    """

    step: int = Field(..., ge=1)
    action: str
    target: str
    notes: str = ""
    reversible: bool = True


class ExecutionPlan(BaseModel):
    """Constrained execution plan generated from an AgentDecision.

    Plans are always subject to execution approval by default.
    """

    plan: list[PlanStep]
    requires_execution_approval: bool = True
    source_severity: Literal["low", "medium", "high", "critical"]
    source_requires_human_review: bool


# =============================================================================
# Execution Models (Phase 5)
# =============================================================================


class ExecutionResult(BaseModel):
    """Result of executing a single plan step.

    Every step produces exactly one result — no silent gaps.
    """

    step: int
    action: str
    status: Literal["success", "failed", "skipped"]
    error_message: str | None = None
    reversible: bool = True
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionReport(BaseModel):
    """Complete report for an execution run.

    Immutable once recorded. Captures overall status and rollback need.
    """

    execution_id: str
    overall_status: Literal["success", "partial_failure", "failed", "pending_approval"]
    step_results: list[ExecutionResult] = Field(default_factory=list)
    requires_rollback: bool = False
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Verification Models (Phase 6)
# =============================================================================


class VerificationReport(BaseModel):
    """Structured verification of whether the agent's goal was achieved.

    Purely deterministic — no AI reasoning. Produces signals for the
    agent controller to decide: retry, rollback, escalate, or terminate.
    """

    execution_id: str
    schema_valid: bool = False
    dependency_valid: bool = False
    tests_passed: bool = False
    downstream_breakage_detected: bool = False
    goal_satisfied: bool = False
    requires_rollback: bool = False
    requires_human_escalation: bool = False
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Desired State + Migration Models (Infrastructure)
# =============================================================================


class MigrationGap(BaseModel):
    """Gap between current schema state and desired target state.

    Produced by diffing a live/DDL snapshot against a desired-state snapshot.
    The changes list describes what migrations are needed to reach the target.
    """

    current_snapshot_id: str
    desired_snapshot_id: str
    environment: str = "default"
    changes: list[SchemaChangeEvent] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MigrationRecord(BaseModel):
    """Record of a single migration applied to an environment.

    Tracks what was applied, where, when, and whether it succeeded.
    The checksum enables duplicate detection across environments.
    """

    migration_id: str
    project_id: str
    environment: str = "default"
    migration_type: Literal["ddl_script", "alter_table", "data_migration", "rollback"]
    migration_sql: str | None = None
    checksum: str  # SHA256 of whitespace-normalized SQL
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    applied_by: str | None = None
    execution_time_ms: int | None = None
    success: bool = True
    error_message: str | None = None


# =============================================================================
# Multi-Schema Models (Cross-Schema Composition)
# =============================================================================


class MultiSchemaSnapshot(BaseModel):
    """Composes multiple single-schema snapshots for cross-schema analysis.

    Enables diff and context analysis across schema boundaries by
    combining multiple SchemaSnapshots into a single queryable structure.
    """

    snapshot_id: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["ddl", "live_db", "composed"]
    database_type: str = "postgresql"
    schemas: dict[str, SchemaSnapshot] = Field(default_factory=dict)
