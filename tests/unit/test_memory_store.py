"""
Tests for the Memory Store (Phase 1).

Tests cover:
- Project registration and retrieval
- Accepted findings management
- Business rules
- Pattern hashing
- Memory summary

NOTE: These tests require PostgreSQL.
Set DATABASE_URL environment variable or tests will be skipped.
"""

import os
from uuid import uuid4

import pytest

from schemint.memory.models import FindingSeverity
from schemint.memory.patterns import patterns_match
from schemint.models.issue import Issue, IssueCategory, IssueSeverity

# Check if DATABASE_URL is set
DATABASE_URL = os.environ.get("DATABASE_URL")
SKIP_REASON = "DATABASE_URL not set. Set it to run memory store tests."


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def store():
    """Create a memory store connected to test database."""
    if not DATABASE_URL:
        pytest.skip(SKIP_REASON)

    from schemint.memory.store import MemoryStore

    store = MemoryStore(database_url=DATABASE_URL)

    # Clean up test data before each test
    with store._get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM analysis_history WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM schema_semantics WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM business_rules WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM accepted_findings WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM known_safe_patterns WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM historical_inflection_points WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM projects WHERE external_id LIKE 'test:%'")

    yield store

    # Clean up after test
    with store._get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM analysis_history WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM schema_semantics WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM business_rules WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM accepted_findings WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM known_safe_patterns WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM historical_inflection_points WHERE project_id IN (SELECT id FROM projects WHERE external_id LIKE 'test:%')")
            cur.execute("DELETE FROM projects WHERE external_id LIKE 'test:%'")


@pytest.fixture
def sample_project(store):
    """Create a sample project."""
    return store.register_project(
        external_id=f"test:repo-{uuid4().hex[:8]}",
        name="Test Repository",
        settings={"default_severity": "warning"},
    )


@pytest.fixture
def sample_finding():
    """Create a sample Issue for testing."""
    return Issue(
        category=IssueCategory.WRONG_DATA_TYPE,
        severity=IssueSeverity.WARNING,
        title="FLOAT used for money column",
        description="Column 'price' uses FLOAT which can cause precision loss.",
        table_name="products",
        column_name="price",
    )


# =============================================================================
# Project Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestProjectOperations:
    """Tests for project registration and retrieval."""

    def test_register_project(self, store):
        """Test registering a new project."""
        project = store.register_project(
            external_id=f"test:acme-{uuid4().hex[:8]}",
            name="ACME E-Commerce",
            settings={"auto_block": ["missing_pk"]},
        )

        assert "test:acme" in project.external_id
        assert project.name == "ACME E-Commerce"
        assert project.settings == {"auto_block": ["missing_pk"]}
        assert project.id is not None

    def test_register_project_idempotent(self, store):
        """Test that registering same project twice returns existing."""
        ext_id = f"test:acme-{uuid4().hex[:8]}"
        project1 = store.register_project(
            external_id=ext_id,
            name="ACME E-Commerce",
        )
        project2 = store.register_project(
            external_id=ext_id,
            name="Different Name",
        )

        assert project1.id == project2.id
        assert project2.name == "ACME E-Commerce"

    def test_get_project_by_id(self, store, sample_project):
        """Test retrieving project by UUID."""
        retrieved = store.get_project(sample_project.id)

        assert retrieved is not None
        assert retrieved.id == sample_project.id
        assert retrieved.external_id == sample_project.external_id

    def test_get_project_by_external_id(self, store, sample_project):
        """Test retrieving project by external ID."""
        retrieved = store.get_project_by_external_id(sample_project.external_id)

        assert retrieved is not None
        assert retrieved.id == sample_project.id

    def test_get_nonexistent_project(self, store):
        """Test that getting nonexistent project returns None."""
        assert store.get_project(uuid4()) is None
        assert store.get_project_by_external_id("nonexistent") is None


# =============================================================================
# Accepted Findings Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestAcceptedFindings:
    """Tests for accepted findings management."""

    def test_accept_finding(self, store, sample_project, sample_finding):
        """Test accepting a finding."""
        from schemint.memory import FeedbackScope

        accepted = store.accept_finding(
            project_id=sample_project.id,
            finding=sample_finding,
            reason="FLOAT is acceptable for product prices in this legacy system",
            accepted_by="developer@example.com",
            scope=FeedbackScope.PATTERN,
        )

        assert accepted.finding_type == sample_finding.category.value
        assert accepted.reason == "FLOAT is acceptable for product prices in this legacy system"
        assert accepted.accepted_by == "developer@example.com"
        assert accepted.scope == FeedbackScope.PATTERN

    def test_check_finding_accepted(self, store, sample_project, sample_finding):
        """Test checking if a finding is accepted."""
        from schemint.memory import FeedbackScope

        # Initially not accepted
        assert store.check_finding_accepted(sample_project.id, sample_finding) is None

        # Accept it
        store.accept_finding(
            project_id=sample_project.id,
            finding=sample_finding,
            reason="Acceptable",
            accepted_by="dev@example.com",
            scope=FeedbackScope.ONCE,
        )

        # Now should be accepted
        accepted = store.check_finding_accepted(sample_project.id, sample_finding)
        assert accepted is not None
        assert accepted.reason == "Acceptable"

    def test_rule_scope_acceptance(self, store, sample_project, sample_finding):
        """Test that rule-scope acceptance matches any finding of same type."""
        from schemint.memory import FeedbackScope

        # Accept with rule scope
        store.accept_finding(
            project_id=sample_project.id,
            finding=sample_finding,
            reason="We ignore all data type warnings",
            accepted_by="tech-lead@example.com",
            scope=FeedbackScope.RULE,
        )

        # Create a different finding of same category
        different_finding = Issue(
            category=IssueCategory.WRONG_DATA_TYPE,
            severity=IssueSeverity.WARNING,
            title="Different type issue",
            description="Different description",
            table_name="orders",
            column_name="amount",
        )

        # Should match due to rule scope
        accepted = store.check_finding_accepted(sample_project.id, different_finding)
        assert accepted is not None

    def test_get_all_accepted_findings(self, store, sample_project, sample_finding):
        """Test retrieving all accepted findings for a project."""

        # Accept multiple findings
        store.accept_finding(
            project_id=sample_project.id,
            finding=sample_finding,
            reason="Reason 1",
            accepted_by="dev@example.com",
        )

        finding2 = Issue(
            category=IssueCategory.NAMING_CONVENTION,
            severity=IssueSeverity.SUGGESTION,
            title="Naming issue",
            description="Column name does not follow convention",
            table_name="users",
        )
        store.accept_finding(
            project_id=sample_project.id,
            finding=finding2,
            reason="Reason 2",
            accepted_by="dev@example.com",
        )

        findings = store.get_accepted_findings(sample_project.id)
        assert len(findings) == 2


# =============================================================================
# Business Rules Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestBusinessRules:
    """Tests for business rules management."""

    def test_add_business_rule(self, store, sample_project):
        """Test adding a business rule."""
        rule = store.add_business_rule(
            project_id=sample_project.id,
            rule_type="require_tenant_id",
            severity="critical",
            rationale="Multi-tenant architecture requires tenant isolation",
            created_by="architect@example.com",
            rule_config={"column_name": "tenant_id", "type": "UUID"},
            applies_to={"tables": ["*"], "except": ["schema_migrations"]},
        )

        assert rule.rule_type == "require_tenant_id"
        assert rule.severity == FindingSeverity.CRITICAL
        assert rule.active is True

    def test_get_business_rules(self, store, sample_project):
        """Test retrieving business rules."""
        store.add_business_rule(
            project_id=sample_project.id,
            rule_type="rule1",
            severity="warning",
            rationale="Test rule 1",
            created_by="dev@example.com",
        )
        store.add_business_rule(
            project_id=sample_project.id,
            rule_type="rule2",
            severity="critical",
            rationale="Test rule 2",
            created_by="dev@example.com",
        )

        rules = store.get_business_rules(sample_project.id)
        assert len(rules) == 2

    def test_business_rule_table_filtering(self, store, sample_project):
        """Test that rules can be filtered by table name."""
        store.add_business_rule(
            project_id=sample_project.id,
            rule_type="specific_rule",
            severity="warning",
            rationale="Only for orders table",
            created_by="dev@example.com",
            applies_to={"tables": ["orders"]},
        )

        # Should find for orders table
        rules = store.get_business_rules(sample_project.id, table_name="orders")
        assert len(rules) == 1

        # Should not find for users table
        rules = store.get_business_rules(sample_project.id, table_name="users")
        assert len(rules) == 0


# =============================================================================
# Pattern Hashing Tests (No DB Required)
# =============================================================================


class TestPatternHashing:
    """Tests for pattern hashing utilities."""

    def test_normalize_pattern(self):
        """Test pattern normalization."""
        from schemint.memory import normalize_pattern

        finding = Issue(
            category=IssueCategory.WRONG_DATA_TYPE,
            severity=IssueSeverity.WARNING,
            title="FLOAT for money",
            description="FLOAT causes precision issues",
            table_name="products",
            column_name="price",
        )
        pattern = normalize_pattern(finding)

        assert pattern["category"] == "wrong_data_type"
        assert pattern["table"] == "products"
        assert pattern["column"] == "price"
        assert "money" in pattern["semantic_markers"]

    def test_compute_finding_hash(self):
        """Test finding hash computation."""
        from schemint.memory import compute_finding_hash

        finding = Issue(
            category=IssueCategory.WRONG_DATA_TYPE,
            severity=IssueSeverity.WARNING,
            title="FLOAT for money",
            description="FLOAT causes precision issues",
            table_name="products",
            column_name="price",
        )

        hash1 = compute_finding_hash(finding)
        hash2 = compute_finding_hash(finding)
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_different_findings_different_hashes(self):
        """Test that different findings produce different hashes."""
        from schemint.memory import compute_finding_hash

        finding1 = Issue(
            category=IssueCategory.WRONG_DATA_TYPE,
            severity=IssueSeverity.WARNING,
            title="FLOAT for money",
            description="FLOAT causes precision issues",
            table_name="products",
            column_name="price",
        )

        finding2 = Issue(
            category=IssueCategory.WRONG_DATA_TYPE,
            severity=IssueSeverity.WARNING,
            title="Same type, different location",
            description="Different column uses FLOAT",
            table_name="orders",
            column_name="total",
        )

        hash1 = compute_finding_hash(finding1)
        hash2 = compute_finding_hash(finding2)
        assert hash1 != hash2

    def test_patterns_match_same_category(self):
        """Test pattern matching for similar patterns."""
        pattern1 = {
            "category": "data_type",
            "semantic_markers": ["money"],
            "data_type": "FLOAT",
        }
        pattern2 = {
            "category": "data_type",
            "semantic_markers": ["money"],
            "data_type": "FLOAT",
        }

        assert patterns_match(pattern1, pattern2)

    def test_patterns_no_match_different_category(self):
        """Test that patterns with different categories don't match."""
        pattern1 = {"category": "data_type", "semantic_markers": []}
        pattern2 = {"category": "naming", "semantic_markers": []}

        assert not patterns_match(pattern1, pattern2)


# =============================================================================
# Memory Summary Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestMemorySummary:
    """Tests for memory summary retrieval."""

    def test_get_memory_summary(self, store, sample_project, sample_finding):
        """Test getting memory summary."""

        # Add some data
        store.accept_finding(
            project_id=sample_project.id,
            finding=sample_finding,
            reason="Test",
            accepted_by="dev@example.com",
        )
        store.add_business_rule(
            project_id=sample_project.id,
            rule_type="test_rule",
            severity="warning",
            rationale="Test",
            created_by="dev@example.com",
        )

        summary = store.get_memory_summary(sample_project.id)

        assert summary is not None
        assert summary.project_name == "Test Repository"
        assert summary.accepted_findings_count == 1
        assert summary.business_rules_count == 1

    def test_memory_summary_nonexistent_project(self, store):
        """Test that summary for nonexistent project returns None."""
        summary = store.get_memory_summary(uuid4())
        assert summary is None


# =============================================================================
# Schema Semantics Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestSchemaSemantics:
    """Tests for schema semantics management."""

    def test_set_schema_semantics(self, store, sample_project):
        """Test setting schema semantics."""
        semantics = store.set_schema_semantics(
            project_id=sample_project.id,
            element_path="orders.total",
            element_type="column",
            description="Total order amount in USD",
            semantic_tags=["money", "usd", "customer_facing"],
            constraints={"currency": "USD", "precision": 2},
        )

        assert semantics.element_path == "orders.total"
        assert "money" in semantics.semantic_tags

    def test_get_schema_semantics(self, store, sample_project):
        """Test retrieving schema semantics."""
        store.set_schema_semantics(
            project_id=sample_project.id,
            element_path="orders.total",
            element_type="column",
            description="Total amount",
        )

        semantics = store.get_schema_semantics(sample_project.id, "orders.total")
        assert len(semantics) == 1
        assert semantics[0].element_path == "orders.total"


# =============================================================================
# Analysis History Tests
# =============================================================================


@pytest.mark.skipif(not DATABASE_URL, reason=SKIP_REASON)
class TestAnalysisHistory:
    """Tests for analysis history recording."""

    def test_record_analysis(self, store, sample_project):
        """Test recording an analysis."""
        history = store.record_analysis(
            project_id=sample_project.id,
            ref="abc123def",
            event_type="pull_request",
            status="pass",
            finding_count=0,
            findings_hash="d41d8cd98f00b204e9800998ecf8427e",
            duration_ms=1234,
            memory_applied=[{"type": "accepted_finding", "id": "123"}],
        )

        assert history.ref == "abc123def"
        assert history.status == "pass"
        assert history.duration_ms == 1234

    def test_analysis_history_affects_summary(self, store, sample_project):
        """Test that recorded analyses appear in summary."""
        store.record_analysis(
            project_id=sample_project.id,
            ref="abc123",
            event_type="push",
            status="pass",
            finding_count=0,
            findings_hash="hash",
            duration_ms=100,
        )

        summary = store.get_memory_summary(sample_project.id)
        assert summary.total_analyses == 1
        assert summary.last_analysis is not None
