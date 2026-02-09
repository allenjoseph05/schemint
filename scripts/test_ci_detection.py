#!/usr/bin/env python3
"""
Test Schemint CI detection locally.

This script demonstrates how the CI file detection and diff extraction work.
Run this to verify the CI integration is working correctly.

Usage:
    cd schemint
    python scripts/test_ci_detection.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schemint.ci import (
    DiffExtractor,
    detect_sql_files,
    is_sql_file,
)
from schemint.ci.providers import DiffFile


def main():
    print("=" * 70)
    print("SCHEMINT CI DETECTION TEST")
    print("=" * 70)

    # Simulate diff files (as if from git diff)
    diff_files = [
        # Migration file - should be detected
        DiffFile(
            path="migrations/001_create_users.sql",
            change_type="added",
            content="""
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
            """,
        ),
        # Migration with issues - should be detected
        DiffFile(
            path="migrations/002_create_orders.sql",
            change_type="added",
            content="""
-- This table has issues: no primary key, FLOAT for money
CREATE TABLE orders (
    order_id INT,
    user_id INT,
    total FLOAT,
    status VARCHAR(50)
);
            """,
        ),
        # Schema file modification
        DiffFile(
            path="schema/products.sql",
            change_type="modified",
            content="""
CREATE TABLE products (
    id INT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10, 2),
    stock INT DEFAULT 0
);

ALTER TABLE products ADD COLUMN category_id INT;
            """,
        ),
        # Alembic migration
        DiffFile(
            path="alembic/versions/abc123_add_payments.py",
            change_type="added",
            content="""
def upgrade():
    op.create_table('payments',
        Column('id', Integer, primary_key=True),
        Column('amount', Float),
        Column('order_id', Integer)
    )
    op.add_column('orders', Column('payment_id', Integer))

def downgrade():
    op.drop_table('payments')
            """,
        ),
        # Prisma schema
        DiffFile(
            path="prisma/schema.prisma",
            change_type="modified",
            content="""
model User {
    id        Int      @id @default(autoincrement())
    email     String   @unique
    name      String?
    orders    Order[]
}

model Order {
    id      Int    @id @default(autoincrement())
    user    User   @relation(fields: [userId], references: [id])
    userId  Int
    total   Float
}
            """,
        ),
        # SQLAlchemy models
        DiffFile(
            path="app/models.py",
            change_type="modified",
            content="""
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True)

class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    total = Column(Float)
            """,
        ),
        # Non-SQL files - should NOT be detected
        DiffFile(
            path="README.md",
            change_type="modified",
            content="# Project README\nUpdated documentation.",
        ),
        DiffFile(
            path="package.json",
            change_type="modified",
            content='{"name": "test-project", "version": "1.0.0"}',
        ),
        DiffFile(
            path="src/app.ts",
            change_type="added",
            content="console.log('Hello World');",
        ),
    ]

    # =========================================================================
    # Test 1: File Detection
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 1: SQL FILE DETECTION")
    print("-" * 70)

    result = detect_sql_files(diff_files)

    print(f"\nTotal files scanned: {result.total_files_scanned}")
    print(f"SQL files found: {result.sql_files_found}")
    print(f"Has SQL changes: {result.has_sql_changes}")

    print("\nDetected SQL Files:")
    for f in result.files:
        print(f"\n  [+] {f.path}")
        print(f"      Type: {f.file_type}")
        print(f"      Change: {f.change_type}")
        print(f"      Pattern: {f.matched_pattern}")

    print("\nFiles by Type:")
    for file_type in ["sql", "migration", "orm"]:
        files = result.by_type(file_type)
        if files:
            print(f"\n  {file_type.upper()}: {len(files)} files")
            for f in files:
                print(f"    - {f.path}")

    # =========================================================================
    # Test 2: Diff Extraction
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 2: DIFF EXTRACTION")
    print("-" * 70)

    extractor = DiffExtractor()
    schema_diff = extractor.extract_from_diff_files(
        diff_files,
        base_ref="main",
        head_ref="feature/add-payments",
    )

    print(f"\nBase ref: {schema_diff.base_ref}")
    print(f"Head ref: {schema_diff.ref}")
    print(f"Total files changed: {len(schema_diff.files_changed)}")
    print(f"SQL files in diff: {len(schema_diff.sql_files)}")
    print(f"Tables affected: {schema_diff.total_tables_affected}")
    print(f"Columns affected: {schema_diff.total_columns_affected}")

    print("\nSQL Changes Extracted:")
    for change in schema_diff.sql_changes:
        print(f"\n  File: {change.file_path} ({change.change_type})")
        if change.tables_added:
            print(f"    [+] Tables added: {change.tables_added}")
        if change.tables_modified:
            print(f"    [~] Tables modified: {change.tables_modified}")
        if change.tables_dropped:
            print(f"    [-] Tables dropped: {change.tables_dropped}")
        if change.columns_added:
            print(f"    [+] Columns added: {change.columns_added}")

    # =========================================================================
    # Test 3: Individual File Detection
    # =========================================================================
    print("\n" + "-" * 70)
    print("TEST 3: INDIVIDUAL FILE PATTERN MATCHING")
    print("-" * 70)

    test_paths = [
        # Should match
        ("schema/users.sql", True),
        ("migrations/001_init.sql", True),
        ("migrations/sub/002_update.sql", True),
        ("alembic/versions/abc123.py", True),
        ("db/migrate/20240101_create.rb", True),
        ("prisma/schema.prisma", True),
        ("app/models.py", True),
        ("src/entities/user.ts", True),
        ("src/entity/order.ts", True),
        ("database/schema.sql", True),
        # Should NOT match
        ("README.md", False),
        ("package.json", False),
        ("src/app.ts", False),
        ("config/database.yml", False),
        (".env", False),
    ]

    print("\nPattern Matching Results:")
    all_passed = True
    for path, expected in test_paths:
        actual = is_sql_file(path)
        status = "PASS" if actual == expected else "FAIL"
        if actual != expected:
            all_passed = False
        expected_str = "SQL" if expected else "Not SQL"
        actual_str = "SQL" if actual else "Not SQL"

        if actual == expected:
            print(f"  [{status}] {path}")
            print(f"         Expected: {expected_str}, Got: {actual_str}")
        else:
            print(f"  [{status}] {path} ** MISMATCH! **")
            print(f"         Expected: {expected_str}, Got: {actual_str}")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    print(f"\n[OK] File detection working: {result.sql_files_found} SQL files found")
    print(f"[OK] Diff extraction working: {len(schema_diff.sql_changes)} changes extracted")
    print(f"[OK] Pattern matching: {'All tests passed' if all_passed else 'Some tests failed'}")

    if result.sql_files_found > 0 and len(schema_diff.sql_changes) > 0 and all_passed:
        print("\n*** All CI detection tests passed! ***")
        return 0
    else:
        print("\n*** Some tests may need attention. ***")
        return 1


if __name__ == "__main__":
    sys.exit(main())
