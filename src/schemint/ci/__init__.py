"""
CI Integration Module.

This module provides CI/CD integration for Schemint:
- Webhook handlers for GitHub, GitLab, etc.
- Git diff extraction
- SQL file detection
- CI status updates

Usage:
    from schemint.ci import ingest_ci_event, CIIngestRequest

    request = CIIngestRequest(
        project_id="github:org/repo",
        event_type="pull_request",
        ref="abc123def",
        base_ref="main",
        provider="github",
        provider_token="..."
    )
    result = await ingest_ci_event(request)
"""

from schemint.ci.diff_extractor import DiffExtractor, extract_diff
from schemint.ci.file_detector import (
    DetectedFile,
    DetectionResult,
    SQLFileDetector,
    detect_sql_files,
    is_sql_file,
)
from schemint.ci.ingest import CIIngestHandler, ingest_ci_event
from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    CIAnnotation,
    CIEventType,
    CIIngestRequest,
    CIReportScore,
    DecisionStatus,
    FileChange,
    FindingLocation,
    GitProvider,
    SchemaDiff,
    SQLChange,
)
from schemint.ci.providers import (
    BaseGitProvider,
    CheckStatus,
    DiffFile,
    GenericGitProvider,
    GitHubProvider,
    GitLabProvider,
)
from schemint.ci.report_builder import CIReportBuilder

__all__ = [
    "AnalysisDecision",
    "AnalysisFinding",
    # Providers
    "BaseGitProvider",
    "CIAnnotation",
    # Models
    "CIEventType",
    "CIIngestHandler",
    "CIIngestRequest",
    # Report
    "CIReportBuilder",
    "CIReportScore",
    "CheckStatus",
    "DecisionStatus",
    "DetectedFile",
    "DetectionResult",
    # Diff extraction
    "DiffExtractor",
    "DiffFile",
    "FileChange",
    "FindingLocation",
    "GenericGitProvider",
    "GitHubProvider",
    "GitLabProvider",
    "GitProvider",
    "SQLChange",
    # File detection
    "SQLFileDetector",
    "SchemaDiff",
    "detect_sql_files",
    "extract_diff",
    # Main entry point
    "ingest_ci_event",
    "is_sql_file",
]
