"""API endpoints for schema drift detection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DependencyGraph,
    ExecutionPlan,
    ExecutionReport,
    SchemaDiffResult,
    SchemaSnapshot,
    VerificationReport,
)
from schemint.drift.snapshot import SnapshotService

router = APIRouter()


# =============================================================================
# Request models
# =============================================================================


class DDLSnapshotRequest(BaseModel):
    sql: str
    database_type: str = "postgresql"
    project_id: str | None = None


class LiveSnapshotRequest(BaseModel):
    connection_string: str
    project_id: str | None = None


class BuildGraphRequest(BaseModel):
    dbt_manifest_path: str | None = None
    sql_files: dict[str, str] | None = None  # filename → SQL content
    view_definitions: dict[str, str] | None = None  # view_name → SQL


# =============================================================================
# Snapshot endpoints
# =============================================================================


@router.post("/snapshot/ddl", response_model=SchemaSnapshot)
async def capture_ddl_snapshot(request: DDLSnapshotRequest) -> SchemaSnapshot:
    """Capture a schema snapshot from DDL SQL."""
    try:
        service = SnapshotService()
        snapshot = service.capture_from_ddl(request.sql, request.database_type)
        return snapshot
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/snapshot/live", response_model=SchemaSnapshot)
async def capture_live_snapshot(request: LiveSnapshotRequest) -> SchemaSnapshot:
    """Capture a schema snapshot from a live PostgreSQL database."""
    try:
        service = SnapshotService()
        snapshot = service.capture_from_live_db(request.connection_string)
        return snapshot
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/snapshot/{project_id}/latest", response_model=SchemaSnapshot | None)
async def get_latest_snapshot(project_id: str) -> SchemaSnapshot | None:
    """Get the latest snapshot for a project."""
    try:
        from schemint.drift.store import get_drift_store
        store = get_drift_store()
        return store.get_latest_snapshot(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Dependency graph endpoints
# =============================================================================


@router.post("/graph/{project_id}/build", response_model=DependencyGraph)
async def build_dependency_graph(
    project_id: str, request: BuildGraphRequest
) -> DependencyGraph:
    """Build a dependency graph from deterministic sources."""
    try:
        builder = DependencyGraphBuilder()
        all_edges = []

        # Get latest snapshot for FK extraction
        try:
            from schemint.drift.store import get_drift_store
            store = get_drift_store()
            snapshot = store.get_latest_snapshot(project_id)
            if snapshot:
                all_edges.extend(builder.from_fk_constraints(snapshot))
        except Exception:
            pass  # No DB configured, skip FK extraction

        # dbt manifest
        if request.dbt_manifest_path:
            all_edges.extend(builder.from_dbt_manifest(request.dbt_manifest_path))

        # SQL files
        if request.sql_files:
            for filename, sql_content in request.sql_files.items():
                all_edges.extend(builder.from_sql_ast(sql_content))

        # View definitions
        if request.view_definitions:
            all_edges.extend(builder.from_view_definitions(request.view_definitions))

        graph = builder.build(all_edges)

        # Try to persist
        try:
            from schemint.drift.store import get_drift_store
            store = get_drift_store()
            store.save_dependency_graph(project_id, graph)
        except Exception:
            pass  # No DB configured, return in-memory only

        return graph
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/graph/{project_id}", response_model=DependencyGraph | None)
async def get_dependency_graph(project_id: str) -> DependencyGraph | None:
    """Get the current dependency graph for a project."""
    try:
        from schemint.drift.store import get_drift_store
        store = get_drift_store()
        return store.get_dependency_graph(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Diff endpoints
# =============================================================================


@router.post("/diff/{project_id}", response_model=SchemaDiffResult)
async def diff_snapshots(project_id: str) -> SchemaDiffResult:
    """Diff the latest two snapshots for a project."""
    try:
        from schemint.drift.store import get_drift_store
        store = get_drift_store()

        # Get the two most recent snapshots
        from psycopg2.extras import RealDictCursor
        with store._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT snapshot_data FROM schema_snapshots
                    WHERE project_id = %s
                    ORDER BY captured_at DESC
                    LIMIT 2
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()

        if len(rows) < 2:
            raise HTTPException(
                status_code=400,
                detail="Need at least 2 snapshots to diff",
            )

        import json
        new_data = rows[0]["snapshot_data"]
        old_data = rows[1]["snapshot_data"]
        if isinstance(new_data, str):
            new_data = json.loads(new_data)
        if isinstance(old_data, str):
            old_data = json.loads(old_data)

        new_snapshot = SchemaSnapshot(**new_data)
        old_snapshot = SchemaSnapshot(**old_data)

        differ = SchemaDiffer()
        return differ.diff(old_snapshot, new_snapshot)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Context assembly endpoints
# =============================================================================


@router.post("/context/{project_id}", response_model=list[ContextPackage])
async def assemble_context(project_id: str) -> list[ContextPackage]:
    """Assemble context packages for the latest diff."""
    try:
        from schemint.drift.store import get_drift_store
        store = get_drift_store()

        # Get latest diff
        snapshot = store.get_latest_snapshot(project_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="No snapshots found")

        graph = store.get_dependency_graph(project_id)
        if not graph:
            graph = DependencyGraph()

        # Get the two most recent snapshots for diffing
        import json
        from psycopg2.extras import RealDictCursor
        with store._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT snapshot_data FROM schema_snapshots
                    WHERE project_id = %s
                    ORDER BY captured_at DESC
                    LIMIT 2
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()

        if len(rows) < 2:
            return []

        new_data = rows[0]["snapshot_data"]
        old_data = rows[1]["snapshot_data"]
        if isinstance(new_data, str):
            new_data = json.loads(new_data)
        if isinstance(old_data, str):
            old_data = json.loads(old_data)

        new_snap = SchemaSnapshot(**new_data)
        old_snap = SchemaSnapshot(**old_data)

        differ = SchemaDiffer()
        diff_result = differ.diff(old_snap, new_snap)

        assembler = ContextAssembler()
        return assembler.assemble_all(diff_result, graph, new_snap)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# AI Agent endpoints (Phases 3 & 4)
# =============================================================================


class JudgeRequest(BaseModel):
    context: ContextPackage


class PlanRequest(BaseModel):
    context: ContextPackage


@router.post("/judge/{project_id}", response_model=AgentDecision)
async def judge_schema_change(
    project_id: str, request: JudgeRequest
) -> AgentDecision:
    """Phase 3: Judge severity of a schema change.

    Uses AI with deterministic guardrails. Falls back to deterministic
    judgment if Claude is unavailable.
    """
    from schemint.drift.agent_brain import DriftAgent, get_drift_agent

    agent = get_drift_agent()
    if agent is None:
        # Deterministic fallback — no AI available
        from schemint.drift.models import ImpactMetrics

        ctx = request.context
        return AgentDecision(
            severity=ctx.impact_metrics.criticality,
            confidence_in_decision=0.0,
            requires_human_review=True,
            rationale=["AI service unavailable — deterministic fallback"],
            recommended_action_categories=["notify_owner"],
            context_quality=ctx.context_quality,
        )

    return agent.judge(request.context)


@router.post("/plan/{project_id}", response_model=ExecutionPlan)
async def plan_schema_change(
    project_id: str, request: PlanRequest
) -> ExecutionPlan:
    """Phase 3+4 combined: Judge severity then generate execution plan.

    Falls back to notification-only plan if AI is unavailable.
    """
    from schemint.drift.agent_brain import get_drift_agent
    from schemint.drift.planning_agent import PlanningAgent, get_planning_agent
    from schemint.drift.models import PlanStep

    ctx = request.context

    # Phase 3: Judge
    agent = get_drift_agent()
    if agent is not None:
        decision = agent.judge(ctx)
    else:
        decision = AgentDecision(
            severity=ctx.impact_metrics.criticality,
            confidence_in_decision=0.0,
            requires_human_review=True,
            rationale=["AI service unavailable — deterministic fallback"],
            recommended_action_categories=["notify_owner"],
            context_quality=ctx.context_quality,
        )

    # Phase 4: Plan
    planner = get_planning_agent()
    if planner is not None:
        return planner.plan(decision, ctx)

    # Deterministic fallback plan
    return ExecutionPlan(
        plan=[
            PlanStep(
                step=1,
                action="notify_table_owner",
                target=ctx.schema_change.table,
                notes="AI unavailable — notification-only fallback",
                reversible=True,
            )
        ],
        requires_execution_approval=True,
        source_severity=decision.severity,
        source_requires_human_review=decision.requires_human_review,
    )


# =============================================================================
# Execution endpoints (Phase 5)
# =============================================================================


class ExecuteRequest(BaseModel):
    plan: ExecutionPlan


@router.post("/execute/{project_id}", response_model=ExecutionReport)
async def execute_plan(
    project_id: str, request: ExecuteRequest
) -> ExecutionReport:
    """Phase 5: Execute an approved plan.

    Deterministic execution — no LLM calls.
    If plan requires approval, returns pending_approval status.
    """
    from schemint.drift.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    return engine.execute(request.plan)


# =============================================================================
# Verification endpoints (Phase 6)
# =============================================================================


class VerifyRequest(BaseModel):
    execution_report: ExecutionReport
    source_requires_human_review: bool = False


@router.post("/verify/{project_id}", response_model=VerificationReport)
async def verify_execution(
    project_id: str, request: VerifyRequest
) -> VerificationReport:
    """Phase 6: Verify execution outcome.

    Deterministic verification — no LLM calls.
    Produces structured signals for the agent controller.
    """
    from schemint.drift.verification import VerificationEngine

    engine = VerificationEngine()
    return engine.verify(
        execution_report=request.execution_report,
        source_requires_human_review=request.source_requires_human_review,
    )
