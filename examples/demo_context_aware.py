#!/usr/bin/env python3
"""
Demonstration script showing context-aware SQL analysis.

This script shows how the same SQL produces different results when analyzed
with different project contexts.

Usage:
    python examples/demo_context_aware.py
"""

from schemint.core.analyzer import analyze_sql
from schemint.core.context import load_context

# Test SQL - a new table being added to the schema
TEST_SQL = """
CREATE TABLE invoices (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT NOT NULL,
    customer_id INT NOT NULL,
    amount FLOAT NOT NULL,
    tax FLOAT,
    status VARCHAR(20) DEFAULT 'draft',
    created VARCHAR(100)
);
"""


def main():
    print("=" * 70)
    print("SCHEMINT: Context-Aware SQL Analysis Demo")
    print("=" * 70)
    print("\nThis demo shows how the same SQL produces different analysis")
    print("results when evaluated against different project contexts.\n")

    print("-" * 70)
    print("SQL BEING ANALYZED:")
    print("-" * 70)
    print(TEST_SQL)

    # Load contexts
    ecommerce_context = load_context({
        "project_name": "E-Commerce Platform",
        "description": "Financial application with strict requirements",
        "schema": {
            "tables": [
                {"name": "orders", "columns": [{"name": "id", "type": "INT"}]},
                {"name": "customers", "columns": [{"name": "id", "type": "INT"}]},
            ],
        },
        "conventions": {
            "naming_conventions": {"case": "snake_case"},
            "required_columns": ["created_at", "updated_at"],
            "preferred_types": {"money": "DECIMAL(19,4)"},
            "require_soft_delete": True,
            "soft_delete_column": "deleted_at",
            "require_cascade_actions": True,
        },
    })

    blog_context = load_context({
        "project_name": "Simple Blog",
        "description": "Basic blog with minimal requirements",
        "schema": {
            "tables": [
                {"name": "posts", "columns": [{"name": "id", "type": "INT"}]},
            ],
        },
        "conventions": {
            "naming_conventions": {"case": "snake_case"},
        },
    })

    # Analyze with different contexts
    contexts = [
        ("E-Commerce Context (Strict)", ecommerce_context),
        ("Blog Context (Minimal)", blog_context),
        ("No Context (Basic Rules Only)", None),
    ]

    for name, context in contexts:
        print("\n" + "=" * 70)
        print(f"ANALYSIS: {name}")
        print("=" * 70)

        result = analyze_sql(TEST_SQL, project_context=context)

        print(f"\nScore: {result.score.total}/100 (Grade: {result.score.grade})")
        print(f"Issues: {result.critical_count} critical, {result.warning_count} warnings, {result.suggestion_count} suggestions")

        if result.issues:
            print("\nIssues Found:")
            for issue in result.issues:
                severity_icon = {"critical": "!!", "warning": "!", "suggestion": "~"}
                icon = severity_icon.get(issue.severity.value, "?")
                print(f"  [{icon}] {issue.title}")
                if issue.description:
                    # Truncate long descriptions
                    desc = issue.description[:70] + "..." if len(issue.description) > 70 else issue.description
                    print(f"      {desc}")

        if result.good_practices:
            print("\nGood Practices:")
            for practice in result.good_practices[:3]:  # Show first 3
                print(f"  + {practice}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Key Differences Observed:

1. E-Commerce Context (Strict):
   - Flags FLOAT for money columns (financial precision critical)
   - Requires created_at, updated_at, deleted_at columns
   - Enforces soft delete for audit compliance
   - Requires ON DELETE/UPDATE actions on foreign keys

2. Blog Context (Minimal):
   - Still flags FLOAT for money (basic best practice)
   - Does NOT require soft delete
   - Does NOT require updated_at
   - Fewer conventions to enforce

3. No Context:
   - Only checks basic SQL best practices
   - No project-specific rules applied

This demonstrates how context-aware analysis provides more relevant
feedback based on your project's specific requirements.
""")


if __name__ == "__main__":
    main()
