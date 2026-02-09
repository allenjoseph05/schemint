"""Unit tests for the 9 new rule checks in RuleAnalyzer."""

import pytest

from schemint.core.analyzer.rule_analyzer import RuleAnalyzer
from schemint.core.parser import parse_sql
from schemint.models.issue import IssueCategory, IssueSeverity


def _get_issues(sql: str, category: IssueCategory | None = None):
    """Helper: parse SQL, run analyzer, return issues (optionally filtered)."""
    schema = parse_sql(sql)
    analyzer = RuleAnalyzer()
    issues, _ = analyzer.analyze(schema)
    if category:
        return [i for i in issues if i.category == category]
    return issues


class TestMissingForeignKey:
    """Tests for _check_missing_foreign_key."""

    def test_id_column_without_fk_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_FOREIGN_KEY)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.WARNING
        assert "user_id" in issues[0].title

    def test_id_column_with_fk_not_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_FOREIGN_KEY)
        assert len(issues) == 0

    def test_pk_id_not_flagged(self):
        """The primary key 'id' column should not be flagged."""
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_FOREIGN_KEY)
        assert len(issues) == 0

    def test_exception_columns_not_flagged(self):
        """Columns like external_id, device_id, session_id should not be flagged."""
        sql = """
        CREATE TABLE devices (
            id INT PRIMARY KEY,
            external_id VARCHAR(100),
            device_id VARCHAR(100),
            session_id VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_FOREIGN_KEY)
        assert len(issues) == 0


class TestOrphanedForeignKey:
    """Tests for _check_orphaned_foreign_keys."""

    def test_reference_to_missing_table_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
        issues = _get_issues(sql, IssueCategory.ORPHANED_FOREIGN_KEY)
        assert len(issues) == 1
        assert "users" in issues[0].title

    def test_reference_to_present_table_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
        issues = _get_issues(sql, IssueCategory.ORPHANED_FOREIGN_KEY)
        assert len(issues) == 0


class TestMissingConstraint:
    """Tests for _check_missing_constraint."""

    def test_email_without_unique_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            email VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_CONSTRAINT)
        email_issues = [i for i in issues if "email" in i.title.lower()]
        assert len(email_issues) == 1

    def test_email_with_unique_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            email VARCHAR(255) UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_CONSTRAINT)
        email_issues = [i for i in issues if "email" in i.title.lower()]
        assert len(email_issues) == 0

    def test_status_without_enum_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            status VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_CONSTRAINT)
        status_issues = [i for i in issues if "status" in i.title.lower()]
        assert len(status_issues) == 1


class TestInefficientType:
    """Tests for _check_inefficient_type."""

    def test_int_for_boolean_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            is_active INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.INEFFICIENT_TYPE)
        assert len(issues) >= 1
        assert any("is_active" in i.title for i in issues)

    def test_text_for_status_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.INEFFICIENT_TYPE)
        assert any("status" in i.title for i in issues)

    def test_varchar_for_status_not_flagged(self):
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            status VARCHAR(20) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.INEFFICIENT_TYPE)
        status_issues = [i for i in issues if "status" in i.title]
        assert len(status_issues) == 0


class TestSecurityRisk:
    """Tests for _check_security_risk."""

    def test_password_column_flagged_critical(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            password VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.SECURITY_RISK)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.CRITICAL
        assert "password" in issues[0].title

    def test_password_hash_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.SECURITY_RISK)
        assert len(issues) == 0

    def test_api_key_flagged(self):
        sql = """
        CREATE TABLE api_tokens (
            id INT PRIMARY KEY,
            api_key VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.SECURITY_RISK)
        assert len(issues) == 1

    def test_token_encrypted_not_flagged(self):
        sql = """
        CREATE TABLE api_tokens (
            id INT PRIMARY KEY,
            token_encrypted VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.SECURITY_RISK)
        assert len(issues) == 0


class TestPIIDetected:
    """Tests for _check_pii_detected."""

    def test_email_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            email VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.PII_DETECTED)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.WARNING

    def test_ssn_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            ssn VARCHAR(11),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.PII_DETECTED)
        assert len(issues) == 1

    def test_phone_flagged(self):
        sql = """
        CREATE TABLE contacts (
            id INT PRIMARY KEY,
            phone VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.PII_DETECTED)
        assert len(issues) == 1


class TestSoftDelete:
    """Tests for _check_soft_delete."""

    def test_table_without_soft_delete_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_SOFT_DELETE)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.SUGGESTION

    def test_table_with_deleted_at_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at TIMESTAMP NULL
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_SOFT_DELETE)
        assert len(issues) == 0

    def test_table_with_is_deleted_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_SOFT_DELETE)
        assert len(issues) == 0


class TestMissingNotNull:
    """Tests for _check_missing_not_null."""

    def test_nullable_name_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_NOT_NULL)
        assert len(issues) >= 1
        assert any("name" in i.column_name for i in issues)

    def test_not_null_name_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_NOT_NULL)
        name_issues = [i for i in issues if i.column_name == "name"]
        assert len(name_issues) == 0

    def test_nullable_email_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            email VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.MISSING_NOT_NULL)
        email_issues = [i for i in issues if i.column_name == "email"]
        assert len(email_issues) == 1


class TestMultiTenancy:
    """Tests for _check_multi_tenancy."""

    def test_table_without_tenant_id_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_MULTI_TENANCY)
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.SUGGESTION

    def test_table_with_tenant_id_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            tenant_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_MULTI_TENANCY)
        assert len(issues) == 0

    def test_table_with_organization_id_not_flagged(self):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            organization_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        issues = _get_issues(sql, IssueCategory.NO_MULTI_TENANCY)
        assert len(issues) == 0
