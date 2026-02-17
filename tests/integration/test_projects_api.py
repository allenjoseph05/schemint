"""
Integration tests for the Projects API (Phase 1).

Tests the HTTP endpoints for project management.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from schemint.main import app
from schemint.memory.store import MemoryStore, set_memory_store

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def client(temp_db):
    """Create test client with isolated memory store."""
    # Use a temporary database for tests
    store = MemoryStore(db_path=temp_db)
    set_memory_store(store)

    with TestClient(app) as client:
        yield client


# =============================================================================
# Project Registration Tests
# =============================================================================


class TestProjectRegistration:
    """Tests for project registration endpoint."""

    def test_register_project(self, client):
        """Test registering a new project."""
        response = client.post(
            "/api/v1/projects",
            json={
                "external_id": "github:acme/ecommerce",
                "name": "ACME E-Commerce",
                "settings": {"default_severity": "warning"},
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["external_id"] == "github:acme/ecommerce"
        assert data["name"] == "ACME E-Commerce"
        assert data["settings"] == {"default_severity": "warning"}
        assert "id" in data

    def test_register_project_idempotent(self, client):
        """Test that re-registering returns existing project."""
        # First registration
        response1 = client.post(
            "/api/v1/projects",
            json={
                "external_id": "github:acme/ecommerce",
                "name": "ACME E-Commerce",
            },
        )
        assert response1.status_code == 201
        id1 = response1.json()["id"]

        # Second registration with same external_id
        response2 = client.post(
            "/api/v1/projects",
            json={
                "external_id": "github:acme/ecommerce",
                "name": "Different Name",
            },
        )
        assert response2.status_code == 201
        id2 = response2.json()["id"]

        # Should be same project
        assert id1 == id2

    def test_register_project_minimal(self, client):
        """Test registering with minimal required fields."""
        response = client.post(
            "/api/v1/projects",
            json={
                "external_id": "gitlab:team/project",
                "name": "Team Project",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["settings"] == {}


# =============================================================================
# Project Retrieval Tests
# =============================================================================


class TestProjectRetrieval:
    """Tests for project retrieval endpoints."""

    def test_get_project_by_uuid(self, client):
        """Test getting project by UUID."""
        # Create project
        create_response = client.post(
            "/api/v1/projects",
            json={"external_id": "github:test/repo", "name": "Test"},
        )
        project_id = create_response.json()["id"]

        # Get by UUID
        response = client.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200
        assert response.json()["id"] == project_id

    def test_get_project_by_external_id(self, client):
        """Test getting project by external ID."""
        from urllib.parse import quote

        # Create project - use external ID without slash for simpler URL handling
        client.post(
            "/api/v1/projects",
            json={"external_id": "github:test-repo", "name": "Test"},
        )

        # Get by external ID (URL encode to be safe)
        encoded_id = quote("github:test-repo", safe="")
        response = client.get(f"/api/v1/projects/{encoded_id}")
        assert response.status_code == 200
        assert response.json()["external_id"] == "github:test-repo"

    def test_get_nonexistent_project(self, client):
        """Test 404 for nonexistent project."""
        response = client.get("/api/v1/projects/nonexistent")
        assert response.status_code == 404


# =============================================================================
# Project Memory Tests
# =============================================================================


class TestProjectMemory:
    """Tests for project memory endpoints."""

    def test_get_memory_summary(self, client):
        """Test getting memory summary."""
        # Create project
        create_response = client.post(
            "/api/v1/projects",
            json={"external_id": "github:test/repo", "name": "Test"},
        )
        project_id = create_response.json()["id"]

        # Get memory summary
        response = client.get(f"/api/v1/projects/{project_id}/memory")
        assert response.status_code == 200

        data = response.json()
        assert data["project_name"] == "Test"
        assert data["accepted_findings_count"] == 0
        assert data["business_rules_count"] == 0
        assert data["total_analyses"] == 0

    def test_get_accepted_findings_empty(self, client):
        """Test getting accepted findings when empty."""
        # Create project
        create_response = client.post(
            "/api/v1/projects",
            json={"external_id": "github:test/repo", "name": "Test"},
        )
        project_id = create_response.json()["id"]

        # Get accepted findings
        response = client.get(f"/api/v1/projects/{project_id}/memory/accepted")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_business_rules_empty(self, client):
        """Test getting business rules when empty."""
        # Create project
        create_response = client.post(
            "/api/v1/projects",
            json={"external_id": "github:test/repo", "name": "Test"},
        )
        project_id = create_response.json()["id"]

        # Get business rules
        response = client.get(f"/api/v1/projects/{project_id}/memory/rules")
        assert response.status_code == 200
        assert response.json() == []


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    def test_missing_required_fields(self, client):
        """Test validation error for missing fields."""
        response = client.post(
            "/api/v1/projects",
            json={"external_id": "github:test/repo"},  # Missing 'name'
        )

        assert response.status_code == 422  # Validation error

    def test_memory_for_nonexistent_project(self, client):
        """Test 404 for memory of nonexistent project."""
        response = client.get("/api/v1/projects/nonexistent/memory")
        assert response.status_code == 404
