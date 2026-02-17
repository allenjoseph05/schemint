"""
Base Git Provider.

Abstract base class for git provider integrations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DiffFile:
    """A file in a diff."""

    path: str
    change_type: str  # added, modified, deleted
    content: str | None = None  # Content for added/modified files
    previous_content: str | None = None  # For modified files


@dataclass
class CheckStatus:
    """Status to set on a CI check."""

    status: str  # pending, success, failure, error
    title: str
    summary: str
    details_url: str | None = None


class BaseGitProvider(ABC):
    """
    Abstract base class for git providers.

    Each provider (GitHub, GitLab, etc.) implements this interface
    to provide diff extraction and CI status updates.
    """

    def __init__(self, token: str | None = None):
        """
        Initialize provider.

        Args:
            token: Authentication token for the provider
        """
        self.token = token

    @abstractmethod
    async def get_diff(
        self,
        repo: str,
        base_ref: str,
        head_ref: str,
    ) -> list[DiffFile]:
        """
        Get the diff between two refs.

        Args:
            repo: Repository identifier (e.g., "org/repo")
            base_ref: Base ref (e.g., "main")
            head_ref: Head ref (e.g., "feature-branch" or commit SHA)

        Returns:
            List of files that changed
        """

    @abstractmethod
    async def get_file_content(
        self,
        repo: str,
        ref: str,
        path: str,
    ) -> str | None:
        """
        Get content of a file at a specific ref.

        Args:
            repo: Repository identifier
            ref: Git ref (branch, tag, or commit SHA)
            path: Path to the file

        Returns:
            File content or None if not found
        """

    @abstractmethod
    async def set_check_status(
        self,
        repo: str,
        ref: str,
        check_status: CheckStatus,
    ) -> bool:
        """
        Set the status of a CI check.

        Args:
            repo: Repository identifier
            ref: Git ref (usually commit SHA)
            check_status: Status to set

        Returns:
            True if successful
        """

    def _is_sql_file(self, path: str) -> bool:
        """Check if a file path is a SQL-related file."""
        sql_patterns = [
            ".sql",
            "migrations/",
            "schema/",
            "prisma/schema.prisma",
        ]
        path_lower = path.lower()
        return any(pattern in path_lower for pattern in sql_patterns)
