"""
Generic Git Provider.

A provider that works with any git repository via local git commands
or by accepting pre-computed diff data.
"""

import asyncio
import tempfile
from typing import Any

from schemint.ci.providers.base import BaseGitProvider, CheckStatus, DiffFile


class GenericGitProvider(BaseGitProvider):
    """
    Generic git provider for non-GitHub/GitLab setups.

    Can work in two modes:
    1. Local mode: Clone repo and compute diff locally
    2. Diff mode: Accept pre-computed diff data

    Useful for:
    - Jenkins pipelines
    - Bitbucket (basic support)
    - Azure DevOps (basic support)
    - Self-hosted git servers
    - Local development/testing
    """

    def __init__(
        self,
        token: str | None = None,
        clone_url: str | None = None,
        local_path: str | None = None,
    ):
        """
        Initialize generic provider.

        Args:
            token: Optional auth token (for authenticated clone URLs)
            clone_url: Git clone URL (for remote repos)
            local_path: Local repository path (for local repos)
        """
        super().__init__(token)
        self.clone_url = clone_url
        self.local_path = local_path

    async def get_diff(
        self,
        repo: str,
        base_ref: str,
        head_ref: str,
    ) -> list[DiffFile]:
        """
        Get the diff between two refs.

        If local_path is set, uses that repo.
        Otherwise, clones from clone_url to a temp directory.

        Args:
            repo: Repository identifier (used for clone URL construction if needed)
            base_ref: Base ref
            head_ref: Head ref

        Returns:
            List of changed files
        """
        work_dir = self.local_path

        # If no local path, we need a clone URL
        if not work_dir:
            if not self.clone_url:
                raise ValueError(
                    "Either local_path or clone_url must be provided for GenericGitProvider"
                )
            # Clone to temp directory
            work_dir = await self._clone_repo(self.clone_url)

        try:
            # Get list of changed files
            changed_files = await self._run_git_command(
                work_dir,
                ["diff", "--name-status", f"{base_ref}...{head_ref}"],
            )

            files: list[DiffFile] = []
            for line in changed_files.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                status = parts[0]
                path = parts[-1]  # For renames, use new path

                change_type = self._map_status(status)

                diff_file = DiffFile(
                    path=path,
                    change_type=change_type,
                    content=None,
                    previous_content=None,
                )

                # Get content for added/modified files
                if change_type in ("added", "modified"):
                    content = await self.get_file_content(repo, head_ref, path)
                    diff_file.content = content

                files.append(diff_file)

            return files

        finally:
            # Clean up temp directory if we created one
            if not self.local_path and work_dir:
                await self._cleanup_dir(work_dir)

    async def get_file_content(
        self,
        _repo: str,
        ref: str,
        path: str,
    ) -> str | None:
        """
        Get content of a file at a specific ref.

        Args:
            repo: Repository identifier (unused in local mode)
            ref: Git ref
            path: File path

        Returns:
            File content or None
        """
        work_dir = self.local_path
        if not work_dir:
            return None

        try:
            return await self._run_git_command(
                work_dir,
                ["show", f"{ref}:{path}"],
            )
        except Exception:
            return None

    async def set_check_status(
        self,
        _repo: str,
        _ref: str,
        _check_status: CheckStatus,
    ) -> bool:
        """
        Set check status.

        For generic provider, this just logs the status.
        CI systems typically handle status updates themselves.

        Args:
            repo: Repository identifier
            ref: Commit ref
            check_status: Status to set

        Returns:
            True (always succeeds - just logs)
        """
        # Generic provider doesn't update external status
        # The CI system (Jenkins, etc.) handles that
        return True

    async def get_diff_from_data(
        self,
        diff_data: list[dict[str, Any]],
    ) -> list[DiffFile]:
        """
        Create DiffFile list from pre-computed diff data.

        Useful when the CI system has already computed the diff.

        Args:
            diff_data: List of dicts with path, change_type, content keys

        Returns:
            List of DiffFile objects
        """
        files: list[DiffFile] = []
        for item in diff_data:
            files.append(
                DiffFile(
                    path=item["path"],
                    change_type=item.get("change_type", "modified"),
                    content=item.get("content"),
                    previous_content=item.get("previous_content"),
                )
            )
        return files

    async def _clone_repo(self, clone_url: str) -> str:
        """Clone repository to temp directory."""
        temp_dir = tempfile.mkdtemp(prefix="schemint_")

        # Add token to URL if provided
        auth_url = clone_url
        if self.token and "://" in clone_url:
            # Insert token into URL for authenticated access
            protocol, rest = clone_url.split("://", 1)
            auth_url = f"{protocol}://x-access-token:{self.token}@{rest}"

        # Shallow clone (faster, less disk)
        await self._run_command(["git", "clone", "--depth", "100", auth_url, temp_dir])

        return temp_dir

    async def _cleanup_dir(self, path: str) -> None:
        """Remove temp directory."""
        import contextlib
        import shutil

        with contextlib.suppress(Exception):
            shutil.rmtree(path)

    async def _run_git_command(self, work_dir: str, args: list[str]) -> str:
        """Run a git command in the working directory."""
        cmd = ["git", "-C", work_dir, *args]
        return await self._run_command(cmd)

    async def _run_command(self, cmd: list[str]) -> str:
        """Run a command and return stdout."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Command failed: {error_msg}")

        return stdout.decode("utf-8", errors="replace")

    def _map_status(self, git_status: str) -> str:
        """Map git diff status to change type."""
        status_map = {
            "A": "added",
            "D": "deleted",
            "M": "modified",
            "R": "modified",  # Renamed
            "C": "added",  # Copied
            "T": "modified",  # Type change
        }
        # Status can be like "R100" for rename with 100% similarity
        return status_map.get(git_status[0], "modified")
