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
from schemint.ci.report_builder import CIReportBuilder
from schemint.ci.providers import (
    BaseGitProvider,
    CheckStatus,
    DiffFile,
    GenericGitProvider,
    GitHubProvider,
    GitLabProvider,
)

__all__ = [
    # Main entry point
    "ingest_ci_event",
    "CIIngestHandler",
    # Models
    "CIEventType",
    "GitProvider",
    "CIIngestRequest",
    "AnalysisDecision",
    "AnalysisFinding",
    "CIAnnotation",
    "CIReportScore",
    "DecisionStatus",
    "FindingLocation",
    "SchemaDiff",
    "SQLChange",
    "FileChange",
    # Report
    "CIReportBuilder",
    # Diff extraction
    "DiffExtractor",
    "extract_diff",
    # File detection
    "SQLFileDetector",
    "DetectedFile",
    "DetectionResult",
    "detect_sql_files",
    "is_sql_file",
    # Providers
    "BaseGitProvider",
    "GitHubProvider",
    "GitLabProvider",
    "GenericGitProvider",
    "DiffFile",
    "CheckStatus",
]
