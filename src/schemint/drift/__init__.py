"""Schema Drift Detection — Phases 0-6.

Deterministic schema drift detection (Phases 0-2), AI-powered severity
judgment (Phase 3), constrained plan generation (Phase 4), deterministic
execution (Phase 5), and verification & feedback loop (Phase 6).
"""

from schemint.drift.action_templates import ACTION_REGISTRY, ActionTemplate
from schemint.drift.agent_brain import DriftAgent, get_drift_agent
from schemint.drift.alter_parser import AlterParser
from schemint.drift.change_classifier import (
    classify_change,
    classify_fk_action_change,
    classify_type_change,
)
from schemint.drift.context_assembler import ContextAssembler, CriticalityThresholds
from schemint.drift.dependency.coverage import CoverageComputer
from schemint.drift.dependency_graph import DependencyGraphBuilder
from schemint.drift.differ import SchemaDiffer
from schemint.drift.exceptions import (
    DDLParseError,
    DependencyError,
    DiffError,
    DriftError,
    LiveDBError,
    ManifestParseError,
    SnapshotError,
    SqlParseError,
    StoreError,
)
from schemint.drift.execution_engine import ExecutionEngine
from schemint.drift.planning_agent import PlanningAgent, get_planning_agent
from schemint.drift.protocols import (
    DatabaseIntrospector,
    DriftStoreProtocol,
    EdgeExtractor,
)
from schemint.drift.snapshot import SnapshotService
from schemint.drift.types import TypeNormalizer, canonicalize_type
from schemint.drift.verification import VerificationEngine

__all__ = [
    "ACTION_REGISTRY",
    "ActionTemplate",
    "AlterParser",
    # Phase 2 — Context Assembly
    "ContextAssembler",
    "CoverageComputer",
    "CriticalityThresholds",
    "DDLParseError",
    # Protocols
    "DatabaseIntrospector",
    "DependencyError",
    "DependencyGraphBuilder",
    "DiffError",
    # Phase 3 — AI Agent Brain
    "DriftAgent",
    # Exceptions
    "DriftError",
    "DriftStoreProtocol",
    "EdgeExtractor",
    # Phase 5 — Execution
    "ExecutionEngine",
    "LiveDBError",
    "ManifestParseError",
    # Phase 4 — Planning
    "PlanningAgent",
    # Phase 1 — Diffing
    "SchemaDiffer",
    "SnapshotError",
    # Phase 0 — Snapshot & Dependencies
    "SnapshotService",
    "SqlParseError",
    "StoreError",
    # Utilities
    "TypeNormalizer",
    # Phase 6 — Verification
    "VerificationEngine",
    "canonicalize_type",
    "classify_change",
    "classify_fk_action_change",
    "classify_type_change",
    "get_drift_agent",
    "get_planning_agent",
]
