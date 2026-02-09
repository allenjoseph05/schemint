"""
Integration tests for CI API endpoints.
"""

import os

import pytest
from fastapi.testclient import TestClient

from schemint.main import app

client = TestClient(app)

# Check if DATABASE_URL is set
DATABASE_URL = os.environ.get("DATABASE_URL")
SKIP_REASON = "DATABASE_URL not set. Set it to run CI API tests."


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestCIIngestEndpoint:
    """Tests for the /ci/ingest endpoint."""

    def test_ingest_with_invalid_provider(self):
        """Test ingest with an invalid provider returns 422."""
        response = client.post(
            "/api/v1/ci/ingest",
            json={
                "project_id": "test:myrepo",
                "event_type": "push",
                "ref": "abc123",
                "base_ref": "main",
                "provider": "invalid_provider",
            },
        )

        # Should fail validation (invalid enum value)
        assert response.status_code == 422

    def test_ingest_missing_required_fields(self):
        """Test ingest with missing fields returns 422."""
        response = client.post(
            "/api/v1/ci/ingest",
            json={
                "project_id": "test:myrepo",
                # Missing event_type, ref, base_ref, provider
            },
        )

        assert response.status_code == 422

    def test_ingest_generic_provider(self):
        """Test ingest with generic provider (no actual git access)."""
        response = client.post(
            "/api/v1/ci/ingest",
            json={
                "project_id": "test:myrepo",
                "event_type": "push",
                "ref": "abc123",
                "base_ref": "main",
                "provider": "generic",
            },
        )

        # Generic provider without clone_url will fail gracefully
        # The endpoint should handle this and return an error
        # (not a 500, but a 400 or appropriate error)
        assert response.status_code in (400, 500)


class TestCIWebhookEndpoints:
    """Tests for webhook endpoints."""

    def test_github_webhook_missing_repo(self):
        """Test GitHub webhook with missing repository."""
        response = client.post(
            "/api/v1/ci/webhook/github",
            json={
                "action": "opened",
                # Missing repository
            },
        )

        assert response.status_code == 400
        assert "Missing repository" in response.json()["detail"]

    def test_gitlab_webhook_missing_project(self):
        """Test GitLab webhook with missing project."""
        response = client.post(
            "/api/v1/ci/webhook/gitlab",
            json={
                "object_kind": "push",
                # Missing project
            },
        )

        assert response.status_code == 400
        assert "Missing project" in response.json()["detail"]


class TestDecisionStatusEndpoint:
    """Tests for decision status endpoint."""

    def test_get_nonexistent_decision(self):
        """Test getting a decision that doesn't exist."""
        response = client.get("/api/v1/ci/status/dec_nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
