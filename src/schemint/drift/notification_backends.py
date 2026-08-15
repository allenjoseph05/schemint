"""Notification backend implementations for Phase 5 execution.

Each backend is a lightweight HTTP client. All network calls are
wrapped so failures return (success=False, detail=str) rather than
raising — the adapter layer decides whether a failure is fatal.

Backends read credentials from schemint Settings (env vars / .env).
If a required credential is absent the backend returns skipped=True,
which the adapter turns into ExecutionResult(status="skipped").
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type shared by all backends
# ---------------------------------------------------------------------------


@dataclass
class BackendResult:
    success: bool
    skipped: bool = False  # True when credentials are absent
    detail: str = ""  # Human-readable outcome / error
    metadata: dict[str, Any] = field(default_factory=dict)  # Extra structured output


# ---------------------------------------------------------------------------
# Slack (incoming webhook)
# ---------------------------------------------------------------------------


class SlackNotifier:
    """Send messages to a Slack channel via an incoming webhook URL.

    Credentials:
        SCHEMINT_WEBHOOK_URL (or Settings.webhook_url)

    Fallback: if webhook_url is None, returns BackendResult(skipped=True).
    """

    def __init__(self, webhook_url: str | None) -> None:
        self._webhook_url = webhook_url

    def send(self, message: str) -> BackendResult:
        if not self._webhook_url:
            logger.debug("Slack notifier: no webhook_url configured — skipping")
            return BackendResult(success=True, skipped=True, detail="Slack not configured")

        payload = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                body = resp.read().decode()
                if resp.status == 200 and body == "ok":
                    logger.info("Slack notification sent successfully")
                    return BackendResult(
                        success=True,
                        detail="Slack message delivered",
                        metadata={"http_status": resp.status},
                    )
                logger.warning("Slack returned unexpected response: %s %s", resp.status, body)
                return BackendResult(
                    success=False,
                    detail=f"Slack responded {resp.status}: {body}",
                    metadata={"http_status": resp.status},
                )
        except urllib.error.URLError as exc:
            logger.error("Slack notification failed: %s", exc)
            return BackendResult(success=False, detail=str(exc))


# ---------------------------------------------------------------------------
# GitHub Issues
# ---------------------------------------------------------------------------


class GitHubIssueNotifier:
    """Create GitHub issues for schema drift review tickets.

    Credentials:
        SCHEMINT_GITHUB_TOKEN (or Settings.github_token)
        SCHEMINT_GITHUB_REPO  (or Settings.github_repo)  — format: "owner/repo"

    Fallback: if either credential is absent, returns BackendResult(skipped=True).
    """

    _API_BASE = "https://api.github.com"

    def __init__(self, token: str | None, repo: str | None) -> None:
        self._token = token
        self._repo = repo

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> BackendResult:
        if not self._token or not self._repo:
            logger.debug("GitHub issue notifier: credentials absent — skipping")
            return BackendResult(success=True, skipped=True, detail="GitHub not configured")

        url = f"{self._API_BASE}/repos/{self._repo}/issues"
        payload = json.dumps(
            {"title": title, "body": body, "labels": labels or ["schema-drift"]}
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read().decode())
                issue_url = data.get("html_url", "")
                issue_number = data.get("number")
                logger.info("GitHub issue created: %s", issue_url)
                return BackendResult(
                    success=True,
                    detail=f"Issue #{issue_number} created",
                    metadata={"issue_url": issue_url, "issue_number": issue_number},
                )
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode() if exc.fp else ""
            logger.error("GitHub issue creation failed %s: %s", exc.code, body_text)
            return BackendResult(success=False, detail=f"HTTP {exc.code}: {body_text}")
        except urllib.error.URLError as exc:
            logger.error("GitHub issue creation network error: %s", exc)
            return BackendResult(success=False, detail=str(exc))


# ---------------------------------------------------------------------------
# GitHub Commit Status
# ---------------------------------------------------------------------------


class GitHubStatusSetter:
    """Set GitHub commit status checks (used by CIPipelineRunner).

    Setting a commit status to "pending" with context "schemint/drift-block"
    causes branch protection rules to block the PR merge until the status
    is resolved (set to "success" or "failure").

    Credentials:
        SCHEMINT_GITHUB_TOKEN (or Settings.github_token)
        SCHEMINT_GITHUB_REPO  (or Settings.github_repo)

    Fallback: if credentials absent, returns BackendResult(skipped=True).
    """

    _API_BASE = "https://api.github.com"

    def __init__(self, token: str | None, repo: str | None) -> None:
        self._token = token
        self._repo = repo

    def set_status(
        self,
        sha: str,
        state: str,  # "pending" | "success" | "failure" | "error"
        context: str,  # e.g. "schemint/drift-block"
        description: str = "",
        target_url: str | None = None,
    ) -> BackendResult:
        if not self._token or not self._repo:
            logger.debug("GitHub status setter: credentials absent — skipping")
            return BackendResult(success=True, skipped=True, detail="GitHub not configured")

        if not sha:
            return BackendResult(
                success=False,
                detail="No commit SHA provided — cannot set GitHub status",
            )

        url = f"{self._API_BASE}/repos/{self._repo}/statuses/{sha}"
        body: dict[str, Any] = {"state": state, "context": context, "description": description}
        if target_url:
            body["target_url"] = target_url

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read().decode())
                status_url = data.get("url", "")
                logger.info(
                    "GitHub status set: sha=%s state=%s context=%s",
                    sha[:8],
                    state,
                    context,
                )
                return BackendResult(
                    success=True,
                    detail=f"Commit status set to '{state}'",
                    metadata={
                        "status_url": status_url,
                        "sha": sha,
                        "state": state,
                        "context": context,
                    },
                )
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode() if exc.fp else ""
            logger.error("GitHub status set failed %s: %s (sha=%s)", exc.code, body_text, sha[:8])
            return BackendResult(success=False, detail=f"HTTP {exc.code}: {body_text}")
        except urllib.error.URLError as exc:
            logger.error("GitHub status network error: %s", exc)
            return BackendResult(success=False, detail=str(exc))

    def request_review(self, pr_number: int, reviewer: str) -> BackendResult:
        """Request a review on a pull request."""
        if not self._token or not self._repo:
            return BackendResult(success=True, skipped=True, detail="GitHub not configured")

        url = f"{self._API_BASE}/repos/{self._repo}/pulls/{pr_number}/requested_reviewers"
        payload = json.dumps({"reviewers": [reviewer]}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                resp.read()
                logger.info("Review requested from %s on PR #%s", reviewer, pr_number)
                return BackendResult(
                    success=True,
                    detail=f"Review requested from {reviewer}",
                    metadata={"reviewer": reviewer, "pr_number": pr_number},
                )
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode() if exc.fp else ""
            return BackendResult(success=False, detail=f"HTTP {exc.code}: {body_text}")
        except urllib.error.URLError as exc:
            return BackendResult(success=False, detail=str(exc))
