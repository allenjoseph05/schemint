"""
GitHub Git Provider.

Integration with GitHub API for diff extraction and check status updates.
"""

import base64
from typing import Any

import httpx

from schemint.ci.providers.base import BaseGitProvider, CheckStatus, DiffFile


class GitHubProvider(BaseGitProvider):
    """
    GitHub provider implementation.

    Uses GitHub REST API to:
    - Fetch diffs between refs
    - Get file contents
    - Update check run status
    """

    API_BASE = "https://api.github.com"

    def __init__(self, token: str | None = None):
        """
        Initialize GitHub provider.

        Args:
            token: GitHub personal access token or GitHub App token
        """
        super().__init__(token)
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._get_headers(),
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_diff(
        self,
        repo: str,
        base_ref: str,
        head_ref: str,
    ) -> list[DiffFile]:
        """
        Get the diff between two refs using GitHub Compare API.

        Args:
            repo: Repository in "owner/repo" format
            base_ref: Base ref (e.g., "main")
            head_ref: Head ref (e.g., "feature-branch")

        Returns:
            List of changed files with their content
        """
        client = await self._get_client()

        # Get comparison between refs
        url = f"{self.API_BASE}/repos/{repo}/compare/{base_ref}...{head_ref}"
        response = await client.get(url)

        if response.status_code == 404:
            raise ValueError(f"Repository or refs not found: {repo}")
        response.raise_for_status()

        data = response.json()
        files: list[DiffFile] = []

        for file_data in data.get("files", []):
            status = file_data.get("status", "modified")
            change_type = self._map_status(status)

            diff_file = DiffFile(
                path=file_data["filename"],
                change_type=change_type,
                content=None,
                previous_content=None,
            )

            # Get content for added/modified files
            if change_type in ("added", "modified"):
                content = await self.get_file_content(
                    repo, head_ref, file_data["filename"]
                )
                diff_file.content = content

            files.append(diff_file)

        return files

    async def get_file_content(
        self,
        repo: str,
        ref: str,
        path: str,
    ) -> str | None:
        """
        Get content of a file at a specific ref.

        Args:
            repo: Repository in "owner/repo" format
            ref: Git ref
            path: File path

        Returns:
            File content or None
        """
        client = await self._get_client()

        url = f"{self.API_BASE}/repos/{repo}/contents/{path}"
        params = {"ref": ref}

        response = await client.get(url, params=params)

        if response.status_code == 404:
            return None
        response.raise_for_status()

        data = response.json()

        # Content is base64 encoded
        if "content" in data:
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content

        return None

    async def set_check_status(
        self,
        repo: str,
        ref: str,
        check_status: CheckStatus,
    ) -> bool:
        """
        Create or update a check run on GitHub.

        Args:
            repo: Repository in "owner/repo" format
            ref: Commit SHA
            check_status: Status to set

        Returns:
            True if successful
        """
        client = await self._get_client()

        # Create a check run
        url = f"{self.API_BASE}/repos/{repo}/check-runs"

        conclusion = self._map_conclusion(check_status.status)

        payload: dict[str, Any] = {
            "name": "Schemint Schema Analysis",
            "head_sha": ref,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": check_status.title,
                "summary": check_status.summary,
            },
        }

        if check_status.details_url:
            payload["details_url"] = check_status.details_url

        response = await client.post(url, json=payload)

        # 201 = created successfully
        return response.status_code == 201

    async def create_commit_status(
        self,
        repo: str,
        ref: str,
        state: str,
        description: str,
        context: str = "schemint/analysis",
        target_url: str | None = None,
    ) -> bool:
        """
        Create a commit status (simpler than check runs).

        Args:
            repo: Repository in "owner/repo" format
            ref: Commit SHA
            state: pending, success, error, failure
            description: Short description
            context: Status context name
            target_url: URL to link to

        Returns:
            True if successful
        """
        client = await self._get_client()

        url = f"{self.API_BASE}/repos/{repo}/statuses/{ref}"

        payload: dict[str, Any] = {
            "state": state,
            "description": description[:140],  # GitHub limit
            "context": context,
        }

        if target_url:
            payload["target_url"] = target_url

        response = await client.post(url, json=payload)

        return response.status_code == 201

    def _map_status(self, github_status: str) -> str:
        """Map GitHub file status to our change type."""
        status_map = {
            "added": "added",
            "removed": "deleted",
            "modified": "modified",
            "renamed": "modified",
            "copied": "added",
            "changed": "modified",
        }
        return status_map.get(github_status, "modified")

    def _map_conclusion(self, status: str) -> str:
        """Map our status to GitHub check conclusion."""
        conclusion_map = {
            "pending": "neutral",
            "success": "success",
            "failure": "failure",
            "error": "failure",
        }
        return conclusion_map.get(status, "neutral")
