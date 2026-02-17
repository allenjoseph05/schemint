"""
Git Provider Integrations.

Provides adapters for different git hosting providers:
- GitHub
- GitLab
- Generic (Bitbucket, Azure DevOps, local git, etc.)

Each provider implements:
- get_diff(repo, base_ref, head_ref) -> list of file changes
- get_file_content(repo, ref, path) -> file content
- set_check_status(repo, ref, status) -> update CI status
"""

from schemint.ci.providers.base import BaseGitProvider, CheckStatus, DiffFile
from schemint.ci.providers.generic import GenericGitProvider
from schemint.ci.providers.github import GitHubProvider
from schemint.ci.providers.gitlab import GitLabProvider

__all__ = [
    # Base classes
    "BaseGitProvider",
    "CheckStatus",
    "DiffFile",
    "GenericGitProvider",
    # Implementations
    "GitHubProvider",
    "GitLabProvider",
]
