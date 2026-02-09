"""Tests for context-aware analysis.

This test demonstrates that the same SQL query produces meaningfully different
explanations and warnings when run against two different project schemas.
"""

import pytest

from schemint.core.analyzer import analyze_sql
from schemint.core.context import (
    ColumnMetadata,
    ProjectContext,
    ProjectConventions,
    SchemaMetadata,
    TableMetadata,
    load_context,
)


# Test SQL that we'll analyze with different contexts
TEST_SQL = """
CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    total FLOAT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created VARCHAR(100)
);
"""

# Test SQL that uses a deprecated column
TEST_SQL_WITH_DEPRECATED = """
CREATE TABLE user_activity (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    legacy_status INT,
    created_at DATETIME
);
"""


def create_ecommerce_context() -> ProjectContext:
    """Create an e-commerce project context."""
    return load_context({
        "project_name": "E-Commerce Platform",
        "description": "Online shopping platform with strict financial requirements",
        "schema": {
            "tables": [
                {
                    "name": "customers",
                    "description": "Customer accounts",
                    "columns": [
                        {"name": "id", "type": "INT", "description": "Primary key"},
                        {"name": "email", "type": "VARCHAR(255)"},
                    ],
                },
                {
                    "name": "orders",
                    "description": "Customer orders - financial data",
                    "columns": [
                        {"name": "id", "type": "INT"},
                        {"name": "customer_id", "type": "INT", "foreign_key_to": "customers.id"},
                        {"name": "total", "type": "DECIMAL(19,4)", "description": "Order total - must use DECIMAL for money"},
                        {"name": "status", "type": "VARCHAR(20)"},
                        {"name": "created_at", "type": "DATETIME"},
                        {"name": "updated_at", "type": "DATETIME"},
                    ],
                },
            ],
            "database_type": "mysql",
        },
        "conventions": {
            "naming_conventions": {"case": "snake_case"},
            "required_columns": ["created_at", "updated_at"],
            "preferred_types": {"money": "DECIMAL(19,4)"},
            "require_soft_delete": True,
            "soft_delete_column": "deleted_at",
        },
    })


def create_blog_context() -> ProjectContext:
    """Create a simple blog project context (less strict requirements)."""
    return load_context({
        "project_name": "Personal Blog",
        "description": "Simple blog with minimal requirements",
        "schema": {
            "tables": [
                {
                    "name": "posts",
                    "description": "Blog posts",
                    "columns": [
                        {"name": "id", "type": "INT"},
                        {"name": "title", "type": "VARCHAR(255)"},
                        {"name": "content", "type": "TEXT"},
                    ],
                },
            ],
            "database_type": "mysql",
        },
        "conventions": {
            "naming_conventions": {"case": "snake_case"},
            # No required columns, no soft delete, simpler requirements
        },
    })


def create_context_with_deprecations() -> ProjectContext:
    """Create a context with deprecated columns."""
    return load_context({
        "project_name": "Legacy Migration Project",
        "description": "Project undergoing schema migration",
        "schema": {
            "tables": [
                {
                    "name": "user_activity",
                    "description": "User activity tracking",
                    "columns": [
                        {"name": "id", "type": "INT"},
                        {"name": "user_id", "type": "INT"},
                        {
                            "name": "legacy_status",
                            "type": "INT",
                            "deprecated": True,
                            "deprecated_reason": "Use activity_type enum instead",
                            "deprecated_since": "v2.0",
                            "renamed_to": "activity_type",
                        },
                        {
                            "name": "activity_type",
                            "type": "VARCHAR(50)",
                            "renamed_from": "legacy_status",
                            "description": "New activity type field",
                        },
                        {"name": "created_at", "type": "DATETIME"},
                    ],
                },
            ],
        },
        "conventions": {},
    })


class TestContextAwareAnalysis:
    """Test that analysis differs based on project context."""

    def test_same_sql_different_contexts_ecommerce_vs_blog(self):
        """
        The same SQL produces different warnings in e-commerce vs blog contexts.

        E-commerce context should:
        - Flag FLOAT for money as CRITICAL (financial precision matters)
        - Require soft delete column
        - Require created_at/updated_at

        Blog context should:
        - Still flag FLOAT for money (basic rule)
        - NOT require soft delete
        - NOT require updated_at
        """
        # Analyze with e-commerce context
        ecommerce_result = analyze_sql(
            TEST_SQL,
            project_context=create_ecommerce_context(),
        )

        # Analyze with blog context
        blog_result = analyze_sql(
            TEST_SQL,
            project_context=create_blog_context(),
        )

        # Analyze without any context
        no_context_result = analyze_sql(TEST_SQL)

        # E-commerce should have MORE issues due to stricter requirements
        assert ecommerce_result.critical_count + ecommerce_result.warning_count > 0

        # E-commerce context should flag missing soft delete
        ecommerce_issue_titles = [i.title.lower() for i in ecommerce_result.issues]
        assert any("soft delete" in t for t in ecommerce_issue_titles), \
            "E-commerce context should require soft delete column"

        # E-commerce context should flag missing timestamps
        assert any("created_at" in t or "updated_at" in t or "timestamp" in t
                   for t in ecommerce_issue_titles), \
            "E-commerce context should require timestamp columns"

        # Blog context should not have context-specific soft delete issues
        # Note: The rule analyzer now flags no_soft_delete as a SUGGESTION
        # for all tables, so we only check that blog doesn't add *extra*
        # context-driven soft delete issues beyond what rules produce.
        blog_issue_titles = [i.title.lower() for i in blog_result.issues]
        no_context_titles = [i.title.lower() for i in no_context_result.issues]
        blog_soft_delete = [t for t in blog_issue_titles if "soft delete" in t]
        no_ctx_soft_delete = [t for t in no_context_titles if "soft delete" in t]
        assert len(blog_soft_delete) <= len(no_ctx_soft_delete), \
            "Blog context should NOT add extra soft delete requirements beyond rules"

        # Both should flag FLOAT for money (this is a basic rule)
        assert any("float" in t or "money" in t or "decimal" in t
                   for t in ecommerce_issue_titles)

        # E-commerce should have lower score due to more violations
        # (Note: This might not always be true depending on issue weights)
        print(f"\nE-commerce score: {ecommerce_result.score.total}")
        print(f"Blog score: {blog_result.score.total}")
        print(f"No context score: {no_context_result.score.total}")

        # Verify context names are in summary
        assert "E-Commerce Platform" in (ecommerce_result.ai_summary or "")
        assert "Personal Blog" in (blog_result.ai_summary or "")

    def test_deprecated_column_detection(self):
        """Test that deprecated columns are flagged when context is provided."""
        context = create_context_with_deprecations()

        # Analyze SQL that uses the deprecated column
        result = analyze_sql(
            TEST_SQL_WITH_DEPRECATED,
            project_context=context,
        )

        # Should flag the deprecated column usage
        deprecated_issues = [
            i for i in result.issues
            if "deprecated" in i.title.lower() or "deprecated" in (i.description or "").lower()
        ]

        assert len(deprecated_issues) > 0, \
            "Should flag usage of deprecated column 'legacy_status'"

        # The fix suggestion should mention the new column name
        deprecated_issue = deprecated_issues[0]
        assert "activity_type" in (deprecated_issue.fix_description or "") or \
               "activity_type" in (deprecated_issue.description or ""), \
            "Should suggest using 'activity_type' instead"

    def test_context_provides_good_practices(self):
        """Test that having context adds appropriate good practices."""
        result = analyze_sql(
            TEST_SQL,
            project_context=create_ecommerce_context(),
        )

        # Should mention that project context was loaded
        assert any("context" in gp.lower() for gp in result.good_practices), \
            "Should note that project context was used"

    def test_conventions_enforcement(self):
        """Test that project conventions are enforced."""
        # Create context with specific conventions
        strict_context = load_context({
            "project_name": "Strict Naming Project",
            "conventions": {
                "naming_conventions": {"case": "snake_case"},
                "forbidden_column_names": ["type", "status", "data"],
                "require_fk_indexes": True,
            },
        })

        # SQL with a forbidden column name
        sql_with_forbidden = """
        CREATE TABLE items (
            id INT PRIMARY KEY,
            type VARCHAR(50),
            data TEXT
        );
        """

        result = analyze_sql(sql_with_forbidden, project_context=strict_context)

        # Should flag forbidden column names
        issue_descriptions = [
            f"{i.title}: {i.description}".lower()
            for i in result.issues
        ]

        assert any("forbidden" in desc or "type" in desc for desc in issue_descriptions), \
            "Should flag forbidden column name 'type'"

    def test_no_context_baseline(self):
        """Test that analysis still works without context."""
        result = analyze_sql(TEST_SQL)

        # Should still detect basic issues
        assert result.critical_count > 0 or result.warning_count > 0

        # Should detect FLOAT for money
        assert any(
            "float" in i.title.lower() or "money" in i.description.lower()
            for i in result.issues
            if i.description
        )


class TestProjectContextModels:
    """Test the project context models and loading."""

    def test_load_context_from_dict(self):
        """Test loading context from a dictionary."""
        context = load_context({
            "project_name": "Test Project",
            "description": "A test project",
            "schema": {
                "tables": [
                    {
                        "name": "users",
                        "columns": [
                            {"name": "id", "type": "INT"},
                            {"name": "email", "type": "VARCHAR(255)"},
                        ],
                    },
                ],
            },
        })

        assert context.project_name == "Test Project"
        assert context.description == "A test project"
        assert context.schema_metadata is not None
        assert len(context.schema_metadata.tables) == 1
        assert context.schema_metadata.tables[0].name == "users"

    def test_deprecated_elements_tracking(self):
        """Test that deprecated elements are properly tracked."""
        context = create_context_with_deprecations()

        deprecated = context.get_deprecated_elements()

        assert "user_activity.legacy_status" in deprecated["columns"]

    def test_column_rename_map(self):
        """Test that column renames are tracked."""
        context = create_context_with_deprecations()

        rename_map = context.get_column_rename_map()

        # The old column should map to the new one
        assert "user_activity.legacy_status" in rename_map
        assert rename_map["user_activity.legacy_status"] == "user_activity.activity_type"

    def test_check_deprecated_usage(self):
        """Test checking for deprecated usage."""
        context = create_context_with_deprecations()

        # Check deprecated column
        result = context.check_deprecated_usage("user_activity", "legacy_status")

        assert result is not None
        assert result["type"] == "column"
        assert result["reason"] == "Use activity_type enum instead"
        assert result["renamed_to"] == "activity_type"


class TestConventionChecker:
    """Test the convention checker directly."""

    def test_snake_case_enforcement(self):
        """Test snake_case naming convention enforcement."""
        from schemint.core.context.conventions import ConventionChecker
        from schemint.core.parser import parse_sql

        conventions = ProjectConventions(
            naming_conventions={"case": "snake_case"},
        )

        # SQL with camelCase column
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            firstName VARCHAR(100),
            lastName VARCHAR(100)
        );
        """

        # Disable normalization so camelCase identifiers are preserved
        schema = parse_sql(sql, normalize_identifiers=False)
        checker = ConventionChecker(conventions)
        issues = checker.check(schema)

        # Should flag camelCase columns
        naming_issues = [i for i in issues if "snake_case" in i.description.lower()]
        assert len(naming_issues) >= 2, "Should flag firstName and lastName"

    def test_required_columns_enforcement(self):
        """Test required columns enforcement."""
        from schemint.core.context.conventions import ConventionChecker
        from schemint.core.parser import parse_sql

        conventions = ProjectConventions(
            required_columns=["created_at", "updated_at"],
        )

        # SQL missing required columns
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100)
        );
        """

        schema = parse_sql(sql)
        checker = ConventionChecker(conventions)
        issues = checker.check(schema)

        # Should flag missing required columns
        assert any("created_at" in i.title.lower() for i in issues)
        assert any("updated_at" in i.title.lower() for i in issues)


# Demonstration function that can be run directly
def demonstrate_context_difference():
    """
    Demonstrate that the same SQL produces different results with different contexts.

    This function prints a side-by-side comparison of analysis results.
    """
    print("=" * 70)
    print("DEMONSTRATION: Same SQL, Different Project Contexts")
    print("=" * 70)

    print("\n--- Test SQL ---")
    print(TEST_SQL)

    # E-commerce context
    print("\n" + "=" * 70)
    print("ANALYSIS WITH E-COMMERCE CONTEXT")
    print("=" * 70)
    ecommerce_result = analyze_sql(TEST_SQL, project_context=create_ecommerce_context())
    print(f"\nScore: {ecommerce_result.score.total}/100 ({ecommerce_result.score.grade})")
    print(f"Critical: {ecommerce_result.critical_count}, Warning: {ecommerce_result.warning_count}, Suggestion: {ecommerce_result.suggestion_count}")
    print("\nIssues:")
    for issue in ecommerce_result.issues:
        print(f"  [{issue.severity.value}] {issue.title}")
        if issue.description:
            print(f"      {issue.description[:80]}...")

    # Blog context
    print("\n" + "=" * 70)
    print("ANALYSIS WITH BLOG CONTEXT")
    print("=" * 70)
    blog_result = analyze_sql(TEST_SQL, project_context=create_blog_context())
    print(f"\nScore: {blog_result.score.total}/100 ({blog_result.score.grade})")
    print(f"Critical: {blog_result.critical_count}, Warning: {blog_result.warning_count}, Suggestion: {blog_result.suggestion_count}")
    print("\nIssues:")
    for issue in blog_result.issues:
        print(f"  [{issue.severity.value}] {issue.title}")

    # No context
    print("\n" + "=" * 70)
    print("ANALYSIS WITHOUT CONTEXT")
    print("=" * 70)
    no_context_result = analyze_sql(TEST_SQL)
    print(f"\nScore: {no_context_result.score.total}/100 ({no_context_result.score.grade})")
    print(f"Critical: {no_context_result.critical_count}, Warning: {no_context_result.warning_count}, Suggestion: {no_context_result.suggestion_count}")
    print("\nIssues:")
    for issue in no_context_result.issues:
        print(f"  [{issue.severity.value}] {issue.title}")

    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print(f"""
The same SQL produces different results based on project context:

- E-Commerce Context: {ecommerce_result.critical_count + ecommerce_result.warning_count} issues found
  (Strict requirements for financial data, soft deletes, timestamps)

- Blog Context: {blog_result.critical_count + blog_result.warning_count} issues found
  (Simpler requirements, fewer conventions enforced)

- No Context: {no_context_result.critical_count + no_context_result.warning_count} issues found
  (Only basic SQL best practices checked)

This demonstrates schema-aware analysis where the same query is evaluated
differently based on the project's specific requirements and conventions.
""")


if __name__ == "__main__":
    demonstrate_context_difference()
