"""Write drift learnings back to MemoryStore.

Converts completed drift runs into accepted findings so the memory
loop can suppress previously-seen safe changes in future runs.

Best-effort — failure never blocks API responses.
"""

from __future__ import annotations

import logging

from schemint.drift.models import DriftRunResult

logger = logging.getLogger(__name__)

# Severity levels considered "safe enough" to auto-accept
_SAFE_SEVERITIES = {"low", "medium"}


class DriftMemoryWriter:
    """Writes drift run learnings back to the MemoryStore."""

    def record_completed_run(self, result: DriftRunResult, project_id: str) -> None:
        """Record learnings from a completed drift run.

        When a run completes with low/medium severity, mark safe changes
        as accepted findings so they are not re-flagged in future runs.
        """
        if result.status != "complete":
            return

        decision = result.decision
        if decision is None:
            return

        if decision.severity not in _SAFE_SEVERITIES:
            return

        try:
            from schemint.memory.store import get_memory_store

            store = get_memory_store()
        except Exception as e:
            logger.debug("MemoryStore unavailable for write-back: %s", e)
            return

        # Find the project in MemoryStore
        try:
            project = store.get_project_by_external_id(project_id)
            if project is None:
                logger.debug(
                    "No MemoryStore project found for '%s'; skipping write-back", project_id
                )
                return
        except Exception as e:
            logger.debug("Failed to look up project '%s': %s", project_id, e)
            return

        # Convert safe schema changes to accepted findings
        # We need to reconstruct changes from the run context
        memory_ctx = result.memory_context
        if memory_ctx is None:
            return

        # Record table change frequencies as accepted patterns
        for table, count in memory_ctx.table_change_frequency.items():
            try:
                from schemint.models.issue import Issue, IssueCategory, IssueSeverity

                issue = Issue(
                    severity=IssueSeverity.SUGGESTION,
                    category=IssueCategory.MISSING_CONSTRAINT,
                    title=f"Schema drift on {table}",
                    description=f"Safe schema change on table {table} (seen {count} times)",
                    table_name=table,
                )
                store.accept_finding(
                    project_id=project.id,
                    finding=issue,
                    reason=f"Auto-accepted: drift run {result.run_id} completed with {decision.severity} severity",
                    accepted_by="schemint_drift_agent",
                )
            except Exception as e:
                logger.debug("Failed to record accepted finding for %s: %s", table, e)
