"""Schema Drift Detection — Phases 0–6.

Deterministic schema drift detection (Phases 0–2), AI-powered severity
judgment (Phase 3), constrained plan generation (Phase 4), deterministic
execution (Phase 5), and verification & feedback loop (Phase 6).
"""

from schemint.drift.action_templates import ACTION_REGISTRY, ActionTemplate
from schemint.drift.agent_brain import DriftAgent, get_drift_agent
from schemint.drift.context_assembler import ContextAssembler
from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.execution_engine import ExecutionEngine
from schemint.drift.planning_agent import PlanningAgent, get_planning_agent
from schemint.drift.snapshot import SnapshotService
from schemint.drift.verification import VerificationEngine

__all__ = [
    # Phase 0
    "SnapshotService",
    "DependencyGraphBuilder",
    # Phase 1
    "SchemaDiffer",
    # Phase 2
    "ContextAssembler",
    # Phase 3
    "DriftAgent",
    "get_drift_agent",
    # Phase 4
    "PlanningAgent",
    "get_planning_agent",
    "ACTION_REGISTRY",
    "ActionTemplate",
    # Phase 5
    "ExecutionEngine",
    # Phase 6
    "VerificationEngine",
]
