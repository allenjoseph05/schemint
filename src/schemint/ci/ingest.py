"""
CI Ingestion Handler.

Main entry point for CI/CD integration.
Handles incoming CI events, extracts diffs, runs analysis.
"""

import logging
import time
from typing import Any
from uuid import UUID

from schemint.ci.diff_extractor import DiffExtractor
from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    CIIngestRequest,
    DecisionStatus,
    FindingLocation,
    GitProvider,
    SchemaDiff,
)
from schemint.ci.providers.base import BaseGitProvider, CheckStatus
from schemint.ci.providers.generic import GenericGitProvider
from schemint.ci.providers.github import GitHubProvider
from schemint.ci.providers.gitlab import GitLabProvider
from schemint.ci.report_builder import CIReportBuilder
from schemint.ci.sql_utils import detect_dangerous_patterns, is_sql_content
from schemint.core.analyzer import analyze_sql
from schemint.memory import MemoryStore, get_memory_store
from schemint.models.analysis import AnalysisResult

logger = logging.getLogger(__name__)


class CIIngestError(Exception):
    """Error during CI ingestion."""



class CIIngestHandler:
    """
    Handles CI event ingestion and analysis.

    Flow:
    1. Validate project exists in memory store
    2. Create git provider based on request
    3. Extract diff and detect SQL files
    4. Run analysis on SQL changes
    5. Return decision with findings
    """

    def __init__(
        self,
        memory_store: MemoryStore | None = None,
        diff_extractor: DiffExtractor | None = None,
    ):
        """
        Initialize handler.

        Args:
            memory_store: Memory store for project data
            diff_extractor: Diff extractor (uses default if None)
        """
        self.memory_store = memory_store
        self.diff_extractor = diff_extractor or DiffExtractor()

    def _get_memory_store(self) -> MemoryStore:
        """Get memory store, creating if needed."""
        if self.memory_store is None:
            self.memory_store = get_memory_store()
        return self.memory_store

    def _create_provider(self, request: CIIngestRequest) -> BaseGitProvider:
        """Create git provider based on request."""
        if request.provider == GitProvider.GITHUB:
            return GitHubProvider(token=request.provider_token)
        if request.provider == GitProvider.GITLAB:
            return GitLabProvider(token=request.provider_token)
        return GenericGitProvider(token=request.provider_token)

    async def ingest(self, request: CIIngestRequest) -> AnalysisDecision:
        """
        Ingest a CI event and run analysis.

        Args:
            request: CI ingest request

        Returns:
            Analysis decision with findings
        """
        start_time = time.time()

        # Validate project
        store = self._get_memory_store()
        project = store.get_project_by_external_id(request.project_id)

        if project is None:
            # Auto-register project if not exists
            project = store.register_project(
                external_id=request.project_id,
                name=self._project_name_from_id(request.project_id),
            )

        # Create provider and extract diff
        provider = self._create_provider(request)

        try:
            # Extract repo from project_id (e.g., "github:org/repo" -> "org/repo")
            repo = self._extract_repo(request.project_id)

            # Get schema diff
            schema_diff = await self.diff_extractor.extract(
                provider=provider,
                repo=repo,
                base_ref=request.base_ref,
                head_ref=request.ref,
            )

            # Run analysis on SQL changes
            findings, analysis_results = await self._analyze_diff(
                schema_diff, project.id, store
            )

            # Calculate decision status
            status = self._determine_status(findings)

            # Calculate counts
            critical_count = sum(1 for f in findings if f.severity == "critical")
            warning_count = sum(1 for f in findings if f.severity == "warning")
            suggestion_count = sum(1 for f in findings if f.severity == "suggestion")
            suppressed_count = sum(1 for f in findings if f.suppressed_by_memory)

            # Build decision
            duration_ms = int((time.time() - start_time) * 1000)

            decision = AnalysisDecision(
                project_id=request.project_id,
                ref=request.ref,
                status=status,
                findings=findings,
                critical_count=critical_count,
                warning_count=warning_count,
                suggestion_count=suggestion_count,
                suppressed_count=suppressed_count,
                duration_ms=duration_ms,
            )

            # Build report
            builder = CIReportBuilder()
            decision.report_score = builder.build_score(analysis_results)
            decision.annotations = builder.build_annotations(findings)
            decision.summary = builder.build_summary(decision, analysis_results)

            # Record analysis in history
            store.record_analysis(
                project_id=project.id,
                ref=request.ref,
                event_type=request.event_type.value,
                status=status.value,
                finding_count=len(findings) - suppressed_count,
                findings_hash=self._hash_findings(findings),
                duration_ms=duration_ms,
            )

            # Update CI status (non-blocking)
            await self._update_ci_status(provider, repo, request.ref, decision)

            return decision

        finally:
            # Cleanup provider resources
            if hasattr(provider, "close"):
                await provider.close()

    async def _analyze_diff(
        self,
        schema_diff: SchemaDiff,
        project_id: UUID,
        store: MemoryStore,
    ) -> tuple[list[AnalysisFinding], list[AnalysisResult]]:
        """Analyze SQL changes in the diff.

        Returns:
            Tuple of (findings, analysis_results)
        """
        findings: list[AnalysisFinding] = []
        analysis_results: list[AnalysisResult] = []

        logger.info(f"Analyzing diff: {len(schema_diff.sql_files)} SQL files")

        # If no SQL files changed, return empty
        if not schema_diff.sql_files:
            logger.info("No SQL files in diff, skipping analysis")
            return findings, analysis_results

        # Analyze each SQL file
        for sql_change in schema_diff.sql_changes:
            logger.info(f"Processing SQL change: {sql_change.file_path} ({sql_change.change_type})")

            # Skip deleted files
            if sql_change.change_type == "deleted":
                logger.debug(f"Skipping deleted file: {sql_change.file_path}")
                continue

            # Get file content from the diff
            file_content = self._get_file_content(schema_diff, sql_change.file_path)
            if not file_content:
                logger.warning(f"No content found for {sql_change.file_path}")
                continue

            logger.info(f"Got content for {sql_change.file_path}: {len(file_content)} chars")
            logger.debug(f"Content preview: {file_content[:200]}...")

            # Only analyze actual SQL content
            if self._is_sql_content(file_content):
                logger.info(f"Analyzing SQL content for {sql_change.file_path}")

                # First, check for dangerous patterns (ALTER TABLE issues, etc.)
                dangerous_findings = self._check_dangerous_patterns(file_content, sql_change.file_path)
                for finding in dangerous_findings:
                    logger.info(f"  Dangerous pattern: {finding.type} - {finding.title}")
                    findings.append(finding)

                # Then run the standard schema analysis
                try:
                    result = analyze_sql(
                        sql=file_content,
                        database_type="mysql",  # TODO: detect from project settings
                        project_id=str(project_id),
                    )

                    analysis_results.append(result)

                    logger.info(f"Analysis result for {sql_change.file_path}: {len(result.issues)} issues found")

                    # Convert issues to findings
                    for issue in result.issues:
                        logger.info(f"  Issue: {issue.category.value} - {issue.title}")
                        finding = AnalysisFinding(
                            type=issue.category.value,
                            severity=issue.severity.value,
                            title=issue.title,
                            description=issue.description or "",
                            location=FindingLocation(
                                file=sql_change.file_path,
                                table=issue.table_name,
                                column=issue.column_name,
                            ),
                        )

                        # Check memory for suppression
                        suppressed = self._check_memory_suppression(
                            store, project_id, issue
                        )
                        if suppressed:
                            finding.suppressed_by_memory = True
                            finding.memory_context = suppressed.get("reason")
                            logger.info(f"  Issue suppressed by memory: {suppressed.get('reason')}")

                        findings.append(finding)

                except Exception as e:
                    # Log parse errors but continue
                    logger.error(f"Failed to parse SQL in {sql_change.file_path}: {e}")
                    findings.append(
                        AnalysisFinding(
                            type="parse_error",
                            severity="warning",
                            title=f"Failed to parse SQL in {sql_change.file_path}",
                            description=str(e),
                            location=FindingLocation(file=sql_change.file_path),
                        )
                    )
            else:
                logger.debug(f"Content does not look like SQL: {sql_change.file_path}")

        logger.info(f"Analysis complete: {len(findings)} findings")
        return findings, analysis_results

    def _check_memory_suppression(
        self,
        store: MemoryStore,
        project_id: UUID,
        issue: Any,
    ) -> dict[str, Any] | None:
        """Check if finding should be suppressed by memory."""
        try:
            accepted = store.check_finding_accepted(project_id, issue)
            if accepted:
                return {
                    "reason": accepted.reason,
                    "accepted_by": accepted.accepted_by,
                    "scope": accepted.scope.value,
                }
        except Exception:
            pass
        return None

    def _determine_status(self, findings: list[AnalysisFinding]) -> DecisionStatus:
        """Determine overall decision status from findings."""
        # Filter out suppressed findings
        active_findings = [f for f in findings if not f.suppressed_by_memory]

        if not active_findings:
            return DecisionStatus.PASS

        # Check for critical issues
        has_critical = any(f.severity == "critical" for f in active_findings)
        if has_critical:
            return DecisionStatus.FAIL

        # Check for warnings
        has_warning = any(f.severity == "warning" for f in active_findings)
        if has_warning:
            return DecisionStatus.WARN

        return DecisionStatus.PASS

    async def _update_ci_status(
        self,
        provider: BaseGitProvider,
        repo: str,
        ref: str,
        decision: AnalysisDecision,
    ) -> None:
        """Update CI status on the git provider."""
        try:
            status_map = {
                DecisionStatus.PASS: "success",
                DecisionStatus.WARN: "success",  # Warnings don't fail
                DecisionStatus.FAIL: "failure",
                DecisionStatus.ERROR: "error",
            }

            title_map = {
                DecisionStatus.PASS: "Schema analysis passed",
                DecisionStatus.WARN: f"Schema analysis: {decision.warning_count} warnings",
                DecisionStatus.FAIL: f"Schema analysis failed: {decision.critical_count} critical issues",
                DecisionStatus.ERROR: "Schema analysis error",
            }

            check_status = CheckStatus(
                status=status_map[decision.status],
                title=title_map[decision.status],
                summary=self._build_summary(decision),
                details_url=decision.check_url,
            )

            await provider.set_check_status(repo, ref, check_status)
        except Exception:
            # Don't fail the analysis if CI status update fails
            pass

    def _build_summary(self, decision: AnalysisDecision) -> str:
        """Build summary text for CI status.

        Uses decision.summary (markdown report) if available,
        truncated for provider character limits.
        """
        if decision.summary:
            # Truncate for provider limits (GitHub max ~65535 chars)
            max_len = 60000
            if len(decision.summary) > max_len:
                return decision.summary[:max_len] + "\n\n_(truncated)_"
            return decision.summary

        parts = []

        if decision.critical_count:
            parts.append(f"{decision.critical_count} critical")
        if decision.warning_count:
            parts.append(f"{decision.warning_count} warnings")
        if decision.suggestion_count:
            parts.append(f"{decision.suggestion_count} suggestions")
        if decision.suppressed_count:
            parts.append(f"{decision.suppressed_count} suppressed")

        if not parts:
            return "No issues found"

        return "Found: " + ", ".join(parts)

    def _extract_repo(self, project_id: str) -> str:
        """Extract repo path from project_id."""
        # Format: "provider:org/repo" -> "org/repo"
        if ":" in project_id:
            return project_id.split(":", 1)[1]
        return project_id

    def _project_name_from_id(self, project_id: str) -> str:
        """Generate project name from project_id."""
        repo = self._extract_repo(project_id)
        # "org/repo" -> "Repo"
        if "/" in repo:
            return repo.split("/")[-1].title()
        return repo.title()

    def _get_file_content(self, schema_diff: SchemaDiff, file_path: str) -> str | None:
        """Get content for a file from the schema diff."""
        for sql_change in schema_diff.sql_changes:
            if sql_change.file_path == file_path:
                return sql_change.content
        return None

    def _is_sql_content(self, content: str) -> bool:
        """Check if content looks like SQL."""
        return is_sql_content(content)

    def _check_dangerous_patterns(self, content: str, file_path: str) -> list[AnalysisFinding]:
        """Check for dangerous SQL patterns using sqlparse-based detection."""
        dangerous = detect_dangerous_patterns(content)
        findings = []

        for dp in dangerous:
            if dp.pattern_type == "blocking_migration":
                findings.append(
                    AnalysisFinding(
                        type="blocking_migration",
                        severity="critical",
                        title=f"ADD COLUMN with DEFAULT on '{dp.table_name}'",
                        description=dp.description,
                        location=FindingLocation(
                            file=file_path,
                            table=dp.table_name,
                            column=dp.column_name,
                        ),
                        suggested_action="block",
                    )
                )
            elif dp.pattern_type == "destructive_change" and dp.column_name:
                findings.append(
                    AnalysisFinding(
                        type="destructive_change",
                        severity="critical",
                        title=f"DROP COLUMN '{dp.column_name}' on '{dp.table_name}'",
                        description=dp.description,
                        location=FindingLocation(
                            file=file_path,
                            table=dp.table_name,
                            column=dp.column_name,
                        ),
                        suggested_action="warn",
                    )
                )
            elif dp.pattern_type == "destructive_change" and not dp.column_name:
                findings.append(
                    AnalysisFinding(
                        type="destructive_change",
                        severity="critical",
                        title=f"DROP TABLE '{dp.table_name}'",
                        description=dp.description,
                        location=FindingLocation(
                            file=file_path,
                            table=dp.table_name,
                        ),
                        suggested_action="block",
                    )
                )
            elif dp.pattern_type == "unsafe_migration":
                findings.append(
                    AnalysisFinding(
                        type="unsafe_migration",
                        severity="warning",
                        title="ADD NOT NULL column without DEFAULT",
                        description=dp.description,
                        location=FindingLocation(
                            file=file_path,
                            table=dp.table_name,
                            column=dp.column_name,
                        ),
                        suggested_action="warn",
                    )
                )

        return findings

    def _hash_findings(self, findings: list[AnalysisFinding]) -> str:
        """Create a hash of finding types for comparison."""
        import hashlib

        finding_types = sorted(f.type for f in findings if not f.suppressed_by_memory)
        content = ",".join(finding_types)
        return hashlib.sha256(content.encode()).hexdigest()[:32]


# Module-level convenience function
async def ingest_ci_event(request: CIIngestRequest) -> AnalysisDecision:
    """
    Ingest a CI event using the default handler.

    Args:
        request: CI ingest request

    Returns:
        Analysis decision
    """
    handler = CIIngestHandler()
    return await handler.ingest(request)
