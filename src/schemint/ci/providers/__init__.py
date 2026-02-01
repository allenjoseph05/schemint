"""
Git Provider Integrations.

Provides adapters for different git hosting providers:
- GitHub
- GitLab
- Bitbucket
- Azure DevOps
- Generic Git

Each provider implements:
- get_diff(base_ref, head_ref) -> list of file changes
- get_file_content(ref, path) -> file content
- set_check_status(status) -> update CI status
"""

# Placeholder for Phase 2 implementation
# from schemint.ci.providers.base import GitProvider
# from schemint.ci.providers.github import GitHubProvider
# from schemint.ci.providers.gitlab import GitLabProvider
