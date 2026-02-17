"""
GitLab Git Provider.

Integration with GitLab API for diff extraction and pipeline status updates.
"""

import urllib.parse
from typing import Any

import httpx

from schemint.ci.providers.base import BaseGitProvider, CheckStatus, DiffFile


class GitLabProvider(BaseGitProvider):
    """
    GitLab provider implementation.

    Uses GitLab REST API to:
    - Fetch diffs between refs
    - Get file contents
    - Update commit/pipeline status
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://gitlab.com",
    ):
        """
        Initialize GitLab provider.

        Args:
            token: GitLab personal access token or CI job token
            base_url: GitLab instance URL (for self-hosted)
        """
        super().__init__(token)
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v4"
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get headers for GitLab API requests."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
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

    def _encode_project(self, repo: str) -> str:
        """URL-encode project path for GitLab API."""
        return urllib.parse.quote(repo, safe="")

    async def get_diff(
        self,
        repo: str,
        base_ref: str,
        head_ref: str,
    ) -> list[DiffFile]:
        """
        Get the diff between two refs using GitLab Compare API.

        Args:
            repo: Project path (e.g., "group/project" or "group/subgroup/project")
            base_ref: Base ref (e.g., "main")
            head_ref: Head ref (e.g., "feature-branch")

        Returns:
            List of changed files
        """
        client = await self._get_client()

        # URL-encode the project path
        encoded_repo = self._encode_project(repo)

        # Get comparison between refs
        url = f"{self.api_base}/projects/{encoded_repo}/repository/compare"
        params = {
            "from": base_ref,
            "to": head_ref,
        }

        response = await client.get(url, params=params)

        if response.status_code == 404:
            raise ValueError(f"Project or refs not found: {repo}")
        response.raise_for_status()

        data = response.json()
        files: list[DiffFile] = []

        for diff_data in data.get("diffs", []):
            change_type = self._determine_change_type(diff_data)

            diff_file = DiffFile(
                path=diff_data.get("new_path") or diff_data.get("old_path"),
                change_type=change_type,
                content=None,
                previous_content=None,
            )

            # Get content for added/modified files
            if change_type in ("added", "modified"):
                content = await self.get_file_content(
                    repo, head_ref, diff_file.path
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
            repo: Project path
            ref: Git ref
            path: File path

        Returns:
            File content or None
        """
        client = await self._get_client()

        encoded_repo = self._encode_project(repo)
        encoded_path = urllib.parse.quote(path, safe="")

        url = f"{self.api_base}/projects/{encoded_repo}/repository/files/{encoded_path}/raw"
        params = {"ref": ref}

        response = await client.get(url, params=params)

        if response.status_code == 404:
            return None
        response.raise_for_status()

        return response.text

    async def set_check_status(
        self,
        repo: str,
        ref: str,
        check_status: CheckStatus,
    ) -> bool:
        """
        Set commit status on GitLab.

        Args:
            repo: Project path
            ref: Commit SHA
            check_status: Status to set

        Returns:
            True if successful
        """
        client = await self._get_client()

        encoded_repo = self._encode_project(repo)
        url = f"{self.api_base}/projects/{encoded_repo}/statuses/{ref}"

        state = self._map_status(check_status.status)

        payload: dict[str, Any] = {
            "state": state,
            "name": "schemint",
            "description": check_status.summary[:255],  # GitLab limit
        }

        if check_status.details_url:
            payload["target_url"] = check_status.details_url

        response = await client.post(url, json=payload)

        # 201 = created, 200 = updated
        return response.status_code in (200, 201)

    async def create_merge_request_note(
        self,
        repo: str,
        mr_iid: int,
        body: str,
    ) -> bool:
        """
        Add a note (comment) to a merge request.

        Args:
            repo: Project path
            mr_iid: Merge request IID (not ID)
            body: Comment body (supports markdown)

        Returns:
            True if successful
        """
        client = await self._get_client()

        encoded_repo = self._encode_project(repo)
        url = f"{self.api_base}/projects/{encoded_repo}/merge_requests/{mr_iid}/notes"

        payload = {"body": body}
        response = await client.post(url, json=payload)

        return response.status_code == 201

    def _determine_change_type(self, diff: dict[str, Any]) -> str:
        """Determine the change type from GitLab diff data."""
        if diff.get("new_file"):
            return "added"
        if diff.get("deleted_file"):
            return "deleted"
        if diff.get("renamed_file"):
            return "modified"
        return "modified"

    def _map_status(self, status: str) -> str:
        """Map our status to GitLab commit status state."""
        status_map = {
            "pending": "pending",
            "success": "success",
            "failure": "failed",
            "error": "failed",
        }
        return status_map.get(status, "pending")
