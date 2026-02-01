"""
CI Integration Module.

This module provides CI/CD integration for Schemint:
- Webhook handlers for GitHub, GitLab, etc.
- Git diff extraction
- SQL file detection
- CI status updates

Usage (Phase 2):
    from schemint.ci import ingest_ci_event

    result = await ingest_ci_event(
        project_id="github:org/repo",
        event_type="pull_request",
        ref="refs/pull/123/head",
        base_ref="main",
        provider="github",
        token="..."
    )
"""

# Placeholder for Phase 2 implementation
# from schemint.ci.ingest import ingest_ci_event
# from schemint.ci.diff_extractor import DiffExtractor
# from schemint.ci.file_detector import SQLFileDetector
