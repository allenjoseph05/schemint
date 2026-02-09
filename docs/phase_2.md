# Phase 2: CI Integration - Implementation Complete

## Overview

Phase 2 adds CI/CD integration capabilities, enabling Schemint to be triggered by CI events (pull requests, pushes) and analyze only the changed SQL files in a diff.

**Key Features:**
- Git provider integrations (GitHub, GitLab, Generic)
- Automatic SQL file detection in diffs
- SQL change extraction from migrations and ORM schemas
- CI ingestion endpoint (`POST /ci/ingest`)
- Webhook endpoints for GitHub and GitLab

## Completed Deliverables

### 1. Git Provider Integrations (`src/schemint/ci/providers/`)

**Files Created:**
- `base.py` - Abstract base class for git providers
- `github.py` - GitHub API integration
- `gitlab.py` - GitLab API integration
- `generic.py` - Generic git provider (local repos, Jenkins, etc.)

**Provider Interface:**

```python
from schemint.ci.providers import GitHubProvider

provider = GitHubProvider(token="ghp_xxx")

# Get diff between refs
diff_files = await provider.get_diff(
    repo="org/repo",
    base_ref="main",
    head_ref="feature-branch"
)

# Get file content at ref
content = await provider.get_file_content(
    repo="org/repo",
    ref="abc123",
    path="schema/users.sql"
)

# Set CI check status
await provider.set_check_status(
    repo="org/repo",
    ref="abc123",
    check_status=CheckStatus(
        status="success",
        title="Schema analysis passed",
        summary="No issues found"
    )
)
```

### 2. SQL File Detection (`src/schemint/ci/file_detector.py`)

Detects SQL-related files in a diff, including:
- Direct SQL files (`*.sql`)
- Migration directories (`migrations/`, `alembic/versions/`, `db/migrate/`)
- ORM schemas (Prisma, SQLAlchemy, TypeORM, Drizzle)

**Supported Patterns:**

| Pattern | Description | Type |
|---------|-------------|------|
| `**/*.sql` | SQL files | sql |
| `migrations/**/*.sql` | Migration SQL files | migration |
| `alembic/versions/**/*.py` | Alembic migrations | migration |
| `db/migrate/**/*.rb` | Rails migrations | migration |
| `prisma/schema.prisma` | Prisma schema | orm |
| `**/models.py` | SQLAlchemy models | orm |
| `**/entities/*.ts` | TypeORM entities | orm |

**Usage:**

```python
from schemint.ci import detect_sql_files, is_sql_file

# Check if a file is SQL-related
if is_sql_file("migrations/001_create_users.sql"):
    print("SQL file detected!")

# Detect SQL files in a diff
result = detect_sql_files(diff_files)
print(f"Found {result.sql_files_found} SQL files")
for file in result.by_type("migration"):
    print(f"  Migration: {file.path}")
```

### 3. Diff Extraction (`src/schemint/ci/diff_extractor.py`)

Extracts and parses SQL changes from diff files:
- Detects CREATE, ALTER, DROP TABLE statements
- Parses Alembic migrations (`op.create_table`, `op.add_column`)
- Parses Rails migrations (`create_table`, `drop_table`)
- Parses ORM schemas (Prisma models, SQLAlchemy classes, TypeORM entities)

**Usage:**

```python
from schemint.ci import DiffExtractor, GitHubProvider

extractor = DiffExtractor()
provider = GitHubProvider(token="...")

schema_diff = await extractor.extract(
    provider=provider,
    repo="org/repo",
    base_ref="main",
    head_ref="feature-branch"
)

print(f"Tables affected: {schema_diff.total_tables_affected}")
for change in schema_diff.sql_changes:
    print(f"  {change.file_path}: +{change.tables_added} ~{change.tables_modified}")
```

### 4. CI Ingestion Handler (`src/schemint/ci/ingest.py`)

Main entry point for CI integration:

```python
from schemint.ci import ingest_ci_event, CIIngestRequest

request = CIIngestRequest(
    project_id="github:acme/ecommerce",
    event_type="pull_request",
    ref="abc123def",
    base_ref="main",
    provider="github",
    provider_token="ghp_xxx",
    pr_number=123
)

decision = await ingest_ci_event(request)
print(f"Status: {decision.status}")
print(f"Findings: {len(decision.findings)}")
```

### 5. CI API Endpoints (`src/schemint/api/v1/ci.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/ci/ingest` | POST | Main CI ingestion endpoint |
| `/api/v1/ci/webhook/github` | POST | GitHub webhook handler |
| `/api/v1/ci/webhook/gitlab` | POST | GitLab webhook handler |
| `/api/v1/ci/status/{decision_id}` | GET | Get decision status |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PHASE 2 ARCHITECTURE                         │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────┐     ┌─────────────────────┐     ┌───────────────────┐
│   CI Systems     │     │   POST /ci/ingest   │     │   Git Providers   │
│                  │────▶│                     │────▶│                   │
│ - GitHub Actions │     │   CIIngestHandler   │     │ - GitHubProvider  │
│ - GitLab CI      │     │                     │     │ - GitLabProvider  │
│ - Jenkins        │     └──────────┬──────────┘     │ - GenericProvider │
└──────────────────┘                │                └───────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   DiffExtractor       │
                        │                       │
                        │ - SQLFileDetector     │
                        │ - SQL Parser          │
                        │ - Migration Parser    │
                        └───────────┬───────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   Analysis Pipeline   │
                        │                       │
                        │ - Rule Analyzer       │
                        │ - Memory Consultation │
                        │ - Finding Generation  │
                        └───────────┬───────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   AnalysisDecision    │
                        │                       │
                        │ - Status (pass/fail)  │
                        │ - Findings            │
                        │ - Memory Applied      │
                        └───────────────────────┘
```

## API Usage

### CI Ingestion Request

```bash
curl -X POST "http://localhost:8000/api/v1/ci/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "github:acme/ecommerce",
    "event_type": "pull_request",
    "ref": "abc123def",
    "base_ref": "main",
    "provider": "github",
    "provider_token": "ghp_xxxx",
    "pr_number": 123,
    "pr_title": "Add payments table"
  }'
```

**Response:**

```json
{
  "decision_id": "dec_abc123def456",
  "project_id": "github:acme/ecommerce",
  "ref": "abc123def",
  "status": "warn",
  "findings": [
    {
      "id": "find_001",
      "type": "missing_primary_key",
      "severity": "critical",
      "title": "Table 'payments' has no primary key",
      "description": "Tables should have a primary key for data integrity",
      "location": {
        "file": "migrations/001_create_payments.sql",
        "table": "payments"
      },
      "memory_context": null,
      "suppressed_by_memory": false
    }
  ],
  "critical_count": 1,
  "warning_count": 0,
  "suppressed_count": 0,
  "duration_ms": 234
}
```

### Decision Status Values

| Status | Meaning |
|--------|---------|
| `pass` | No blocking issues found |
| `warn` | Warnings found (non-blocking) |
| `fail` | Critical issues found (should block) |
| `error` | Analysis failed |

## GitHub Actions Integration

```yaml
# .github/workflows/schemint.yml
name: Schema Analysis

on:
  pull_request:
    paths:
      - '**/*.sql'
      - 'migrations/**'
      - 'prisma/schema.prisma'

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - name: Analyze Schema Changes
        run: |
          curl -X POST "${{ secrets.SCHEMINT_URL }}/api/v1/ci/ingest" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${{ secrets.SCHEMINT_TOKEN }}" \
            -d '{
              "project_id": "github:${{ github.repository }}",
              "event_type": "pull_request",
              "ref": "${{ github.event.pull_request.head.sha }}",
              "base_ref": "${{ github.event.pull_request.base.ref }}",
              "provider": "github",
              "provider_token": "${{ secrets.GITHUB_TOKEN }}",
              "pr_number": ${{ github.event.pull_request.number }}
            }'
```

## GitLab CI Integration

```yaml
# .gitlab-ci.yml
schemint:
  stage: test
  rules:
    - changes:
        - "**/*.sql"
        - "migrations/**"
  script:
    - |
      curl -X POST "$SCHEMINT_URL/api/v1/ci/ingest" \
        -H "Content-Type: application/json" \
        -d '{
          "project_id": "gitlab:'$CI_PROJECT_PATH'",
          "event_type": "merge_request",
          "ref": "'$CI_COMMIT_SHA'",
          "base_ref": "'$CI_MERGE_REQUEST_TARGET_BRANCH_NAME'",
          "provider": "gitlab",
          "provider_token": "'$CI_JOB_TOKEN'"
        }'
```

## Tests

### Unit Tests

- `tests/unit/test_ci_file_detector.py` - 11 tests for SQL file detection
- `tests/unit/test_ci_diff_extractor.py` - 11 tests for diff extraction

### Integration Tests

- `tests/integration/test_ci_api.py` - 6 tests for CI API endpoints

**Run tests:**

```bash
# Run CI tests
pytest tests/unit/test_ci_file_detector.py tests/unit/test_ci_diff_extractor.py -v

# Run API tests
pytest tests/integration/test_ci_api.py -v
```

## Files Changed

| File | Change |
|------|--------|
| `src/schemint/ci/__init__.py` | Updated - module exports |
| `src/schemint/ci/providers/__init__.py` | Updated - provider exports |
| `src/schemint/ci/providers/base.py` | Created - base provider class |
| `src/schemint/ci/providers/github.py` | Created - GitHub integration |
| `src/schemint/ci/providers/gitlab.py` | Created - GitLab integration |
| `src/schemint/ci/providers/generic.py` | Created - generic git provider |
| `src/schemint/ci/file_detector.py` | Created - SQL file detection |
| `src/schemint/ci/diff_extractor.py` | Created - diff extraction |
| `src/schemint/ci/ingest.py` | Created - CI ingestion handler |
| `src/schemint/api/v1/ci.py` | Created - CI API endpoints |
| `src/schemint/api/v1/__init__.py` | Updated - include CI router |
| `tests/unit/test_ci_file_detector.py` | Created - 11 tests |
| `tests/unit/test_ci_diff_extractor.py` | Created - 11 tests |
| `tests/integration/test_ci_api.py` | Created - 6 tests |

---

## Practical Usage Guide

This section explains how to actually use Schemint CI integration with real projects.

### Prerequisites

1. **Start Schemint Server:**
   ```bash
   cd schemint

   # Set up PostgreSQL (required)
   export DATABASE_URL=postgresql://schemint:schemint123@localhost:5432/schemint

   # Start the server
   uvicorn schemint.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Verify Server is Running:**
   ```bash
   curl http://localhost:8000/api/v1/health
   # Should return: {"status": "healthy", ...}
   ```

---

### Option 1: Test Locally with a Sample Project

Create a sample project to test Schemint's CI features without needing GitHub/GitLab.

#### Step 1: Create Sample Project Structure

```bash
mkdir -p ~/test-schemint-project
cd ~/test-schemint-project
git init

# Create directory structure
mkdir -p migrations schema db/migrate alembic/versions prisma
```

#### Step 2: Add Sample SQL Files

**Create `schema/users.sql`:**
```sql
-- Good table with primary key
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Create `migrations/001_create_orders.sql`:**
```sql
-- Table with issues (no primary key, FLOAT for money)
CREATE TABLE orders (
    order_id INT,
    user_id INT,
    total FLOAT,
    status VARCHAR(50)
);
```

**Create `migrations/002_create_products.sql`:**
```sql
-- Another table with issues
CREATE TABLE products (
    name VARCHAR(255),
    price FLOAT,
    description TEXT
);
```

**Create `alembic/versions/001_create_payments.py`:**
```python
"""Create payments table

Revision ID: 001
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table('payments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('amount', sa.Float),  # Should be DECIMAL
        sa.Column('currency', sa.String(3))
    )
    op.add_column('orders', sa.Column('payment_id', sa.Integer))

def downgrade():
    op.drop_table('payments')
```

**Create `prisma/schema.prisma`:**
```prisma
model Customer {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  name      String?
  orders    Order[]
}

model Order {
  id         Int      @id @default(autoincrement())
  customer   Customer @relation(fields: [customerId], references: [id])
  customerId Int
  total      Float    // Should be Decimal
}
```

#### Step 3: Commit the Files

```bash
git add .
git commit -m "Initial schema"

# Create a feature branch with changes
git checkout -b feature/add-inventory

# Add a new migration
cat > migrations/003_create_inventory.sql << 'EOF'
CREATE TABLE inventory (
    sku VARCHAR(50),
    quantity INT,
    price FLOAT,
    warehouse_id INT
);
EOF

git add .
git commit -m "Add inventory table"
```

#### Step 4: Test File Detection Locally

Create a Python script `test_detection.py`:

```python
#!/usr/bin/env python3
"""Test Schemint CI detection locally."""

import asyncio
from schemint.ci import (
    SQLFileDetector,
    DiffExtractor,
    detect_sql_files,
    is_sql_file
)
from schemint.ci.providers import DiffFile

# Simulate diff files (as if from git diff)
diff_files = [
    DiffFile(
        path="migrations/003_create_inventory.sql",
        change_type="added",
        content="""
CREATE TABLE inventory (
    sku VARCHAR(50),
    quantity INT,
    price FLOAT,
    warehouse_id INT
);
        """
    ),
    DiffFile(
        path="schema/users.sql",
        change_type="modified",
        content="""
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100),
    phone VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
        """
    ),
    DiffFile(
        path="README.md",
        change_type="modified",
        content="# Updated README"
    ),
]

# Test file detection
print("=" * 60)
print("SQL FILE DETECTION TEST")
print("=" * 60)

result = detect_sql_files(diff_files)
print(f"\nTotal files scanned: {result.total_files_scanned}")
print(f"SQL files found: {result.sql_files_found}")
print(f"Has SQL changes: {result.has_sql_changes}")

print("\nDetected SQL files:")
for f in result.files:
    print(f"  - {f.path}")
    print(f"    Type: {f.file_type}")
    print(f"    Change: {f.change_type}")
    print(f"    Pattern: {f.matched_pattern}")

# Test diff extraction
print("\n" + "=" * 60)
print("DIFF EXTRACTION TEST")
print("=" * 60)

extractor = DiffExtractor()
schema_diff = extractor.extract_from_diff_files(
    diff_files,
    base_ref="main",
    head_ref="feature/add-inventory"
)

print(f"\nBase ref: {schema_diff.base_ref}")
print(f"Head ref: {schema_diff.ref}")
print(f"SQL files in diff: {schema_diff.sql_files}")
print(f"Tables affected: {schema_diff.total_tables_affected}")

print("\nSQL Changes:")
for change in schema_diff.sql_changes:
    print(f"\n  File: {change.file_path}")
    print(f"  Change type: {change.change_type}")
    if change.tables_added:
        print(f"  Tables added: {change.tables_added}")
    if change.tables_modified:
        print(f"  Tables modified: {change.tables_modified}")
    if change.columns_added:
        print(f"  Columns added: {change.columns_added}")

# Test individual file detection
print("\n" + "=" * 60)
print("INDIVIDUAL FILE DETECTION")
print("=" * 60)

test_paths = [
    "schema/users.sql",
    "migrations/001_init.sql",
    "alembic/versions/abc123.py",
    "db/migrate/20240101_create.rb",
    "prisma/schema.prisma",
    "app/models.py",
    "src/entities/user.ts",
    "README.md",
    "package.json",
]

for path in test_paths:
    is_sql = is_sql_file(path)
    status = "[SQL]" if is_sql else "[---]"
    print(f"  {status}: {path}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
```

Run the test:

```bash
cd ~/path/to/schemint
python test_detection.py
```

**Expected Output:**
```
============================================================
SQL FILE DETECTION TEST
============================================================

Total files scanned: 3
SQL files found: 2
Has SQL changes: True

Detected SQL files:
  - migrations/003_create_inventory.sql
    Type: migration
    Change: added
    Pattern: migrations/**/*.sql
  - schema/users.sql
    Type: sql
    Change: modified
    Pattern: **/*.sql

============================================================
DIFF EXTRACTION TEST
============================================================

Base ref: main
Head ref: feature/add-inventory
SQL files in diff: ['migrations/003_create_inventory.sql', 'schema/users.sql']
Tables affected: 2

SQL Changes:

  File: migrations/003_create_inventory.sql
  Change type: added
  Tables added: ['INVENTORY']

  File: schema/users.sql
  Change type: modified
  Tables added: ['USERS']

============================================================
INDIVIDUAL FILE DETECTION
============================================================
  [SQL] schema/users.sql
  [SQL] migrations/001_init.sql
  [SQL] alembic/versions/abc123.py
  [SQL] db/migrate/20240101_create.rb
  [SQL] prisma/schema.prisma
  [SQL] app/models.py
  [SQL] src/entities/user.ts
  [---] README.md
  [---] package.json

============================================================
TEST COMPLETE
============================================================
```

---

### Option 2: Test with a Real GitHub Repository

#### Step 1: Create a GitHub Personal Access Token

1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token with scopes: `repo`, `read:org`
3. Copy the token (starts with `ghp_`)

#### Step 2: Create a Test Repository on GitHub

1. Create a new repository on GitHub (e.g., `my-test-db-project`)
2. Push the sample project from Option 1:

```bash
cd ~/test-schemint-project
git remote add origin https://github.com/YOUR_USERNAME/my-test-db-project.git
git push -u origin main
git push -u origin feature/add-inventory
```

3. Create a Pull Request from `feature/add-inventory` to `main`

#### Step 3: Test CI Ingestion with GitHub

```bash
# Replace with your values
GITHUB_TOKEN="ghp_your_token_here"
REPO="YOUR_USERNAME/my-test-db-project"
PR_HEAD_SHA="abc123..."  # Get from PR page or `git rev-parse HEAD`
BASE_REF="main"

curl -X POST "http://localhost:8000/api/v1/ci/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "github:'$REPO'",
    "event_type": "pull_request",
    "ref": "'$PR_HEAD_SHA'",
    "base_ref": "'$BASE_REF'",
    "provider": "github",
    "provider_token": "'$GITHUB_TOKEN'"
  }'
```

**Expected Response:**
```json
{
  "decision_id": "dec_abc123...",
  "project_id": "github:YOUR_USERNAME/my-test-db-project",
  "ref": "abc123...",
  "status": "fail",
  "findings": [
    {
      "id": "find_001",
      "type": "missing_primary_key",
      "severity": "critical",
      "title": "Table 'inventory' has no primary key",
      ...
    },
    {
      "id": "find_002",
      "type": "wrong_data_type",
      "severity": "warning",
      "title": "Column 'price' uses FLOAT",
      ...
    }
  ],
  "critical_count": 1,
  "warning_count": 1,
  ...
}
```

---

### Option 3: Test GitHub Provider Directly (Python)

Create `test_github_provider.py`:

```python
#!/usr/bin/env python3
"""Test GitHub provider directly."""

import asyncio
from schemint.ci.providers import GitHubProvider
from schemint.ci import DiffExtractor

# Configuration
GITHUB_TOKEN = "ghp_your_token_here"
REPO = "YOUR_USERNAME/my-test-db-project"
BASE_REF = "main"
HEAD_REF = "feature/add-inventory"

async def main():
    # Create provider
    provider = GitHubProvider(token=GITHUB_TOKEN)

    try:
        print(f"Fetching diff: {REPO} ({BASE_REF}...{HEAD_REF})")
        print("=" * 60)

        # Get diff
        diff_files = await provider.get_diff(
            repo=REPO,
            base_ref=BASE_REF,
            head_ref=HEAD_REF
        )

        print(f"\nFiles changed: {len(diff_files)}")
        for f in diff_files:
            print(f"  - {f.path} ({f.change_type})")
            if f.content:
                print(f"    Content length: {len(f.content)} chars")

        # Extract SQL changes
        print("\n" + "=" * 60)
        print("Extracting SQL changes...")

        extractor = DiffExtractor()
        schema_diff = extractor.extract_from_diff_files(
            diff_files,
            base_ref=BASE_REF,
            head_ref=HEAD_REF
        )

        print(f"\nSQL files: {schema_diff.sql_files}")
        print(f"Tables affected: {schema_diff.total_tables_affected}")

        for change in schema_diff.sql_changes:
            print(f"\n  {change.file_path}:")
            print(f"    Added tables: {change.tables_added}")
            print(f"    Modified tables: {change.tables_modified}")

    finally:
        await provider.close()

if __name__ == "__main__":
    asyncio.run(main())
```

Run:
```bash
python test_github_provider.py
```

---

### Sample Project Structures

Here are different project structures Schemint can analyze:

#### Structure 1: Plain SQL Files
```
my-project/
├── schema/
│   ├── users.sql
│   ├── orders.sql
│   └── products.sql
├── migrations/
│   ├── 001_initial.sql
│   ├── 002_add_payments.sql
│   └── 003_add_inventory.sql
└── README.md
```

#### Structure 2: Python with Alembic (SQLAlchemy)
```
my-python-app/
├── app/
│   ├── models.py          # SQLAlchemy models
│   └── __init__.py
├── alembic/
│   ├── versions/
│   │   ├── 001_create_users.py
│   │   ├── 002_create_orders.py
│   │   └── 003_add_payment.py
│   └── env.py
├── alembic.ini
└── requirements.txt
```

#### Structure 3: Node.js with Prisma
```
my-node-app/
├── prisma/
│   ├── schema.prisma      # Prisma schema
│   └── migrations/
│       ├── 20240101_init/
│       │   └── migration.sql
│       └── 20240102_add_orders/
│           └── migration.sql
├── src/
│   └── index.ts
└── package.json
```

#### Structure 4: Ruby on Rails
```
my-rails-app/
├── db/
│   ├── migrate/
│   │   ├── 20240101000000_create_users.rb
│   │   ├── 20240102000000_create_orders.rb
│   │   └── 20240103000000_add_payment_to_orders.rb
│   └── schema.rb
├── app/
│   └── models/
│       ├── user.rb
│       └── order.rb
└── Gemfile
```

#### Structure 5: TypeScript with TypeORM
```
my-ts-app/
├── src/
│   ├── entities/
│   │   ├── User.ts        # TypeORM entities
│   │   ├── Order.ts
│   │   └── Product.ts
│   └── migrations/
│       ├── 1704067200000-CreateUsers.ts
│       └── 1704153600000-CreateOrders.ts
├── ormconfig.json
└── package.json
```

---

### Complete End-to-End Test Script

Create `test_e2e.sh`:

```bash
#!/bin/bash
# End-to-end test for Schemint CI integration

set -e

SCHEMINT_URL="http://localhost:8000"

echo "=============================================="
echo "SCHEMINT CI INTEGRATION E2E TEST"
echo "=============================================="

# 1. Check server health
echo -e "\n[1/5] Checking server health..."
curl -s "$SCHEMINT_URL/api/v1/health" | python3 -m json.tool

# 2. Register a test project
echo -e "\n[2/5] Registering test project..."
curl -s -X POST "$SCHEMINT_URL/api/v1/projects" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "test:e2e-sample",
    "name": "E2E Test Project"
  }' | python3 -m json.tool

# 3. Get project memory (should be empty)
echo -e "\n[3/5] Getting project memory..."
# URL encode the external_id
curl -s "$SCHEMINT_URL/api/v1/projects/test%3Ae2e-sample/memory" | python3 -m json.tool

# 4. Test the existing analyze endpoint (for comparison)
echo -e "\n[4/5] Testing direct SQL analysis..."
curl -s -X POST "$SCHEMINT_URL/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "CREATE TABLE test_orders (order_id INT, total FLOAT, customer VARCHAR(100));",
    "database_type": "mysql"
  }' | python3 -m json.tool

# 5. Test webhook endpoints (validation only)
echo -e "\n[5/5] Testing webhook validation..."
echo "GitHub webhook (missing repo - should fail):"
curl -s -X POST "$SCHEMINT_URL/api/v1/ci/webhook/github" \
  -H "Content-Type: application/json" \
  -d '{"action": "opened"}' | python3 -m json.tool

echo -e "\n=============================================="
echo "E2E TEST COMPLETE"
echo "=============================================="
```

Make it executable and run:

```bash
chmod +x test_e2e.sh
./test_e2e.sh
```

---

### Troubleshooting

| Issue | Solution |
|-------|----------|
| "DATABASE_URL is required" | Set `export DATABASE_URL=postgresql://...` |
| "Repository or refs not found" | Check GitHub token has `repo` scope |
| "Connection refused" | Ensure Schemint server is running on port 8000 |
| "No SQL files detected" | Check file paths match supported patterns |
| Empty `sql_changes` | Files might be deleted or have no SQL content |
| "status: pass" with no findings | See debugging guide below |

---

### Debugging "No Findings" Issues

If your CI integration returns `"status": "pass"` with an empty findings list when it should detect issues, follow these steps:

#### Step 1: Enable Verbose Logging

Set the logging level to DEBUG:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or in your app configuration:

```python
# In main.py or settings
import logging
logging.getLogger("schemint.ci").setLevel(logging.DEBUG)
```

#### Step 2: Check Log Output

The DiffExtractor and CIIngestHandler now log detailed information:

```
schemint.ci.diff_extractor - INFO - Extracting diff for org/repo: main..feature-branch
schemint.ci.diff_extractor - INFO - Got 3 files in diff
schemint.ci.diff_extractor - INFO - Detected 2 SQL files out of 3
schemint.ci.diff_extractor - INFO - [SQL] migrations/001_bad.sql (type=migration, pattern=migrations/**/*.sql)
schemint.ci.ingest - INFO - Analyzing diff: 2 SQL files
schemint.ci.ingest - INFO - Got content for migrations/001_bad.sql: 185 chars
schemint.ci.ingest - INFO - Dangerous pattern: blocking_migration - ADD COLUMN with DEFAULT on 'USERS'
```

#### Step 3: Common Issues and Fixes

**Issue: Files not detected**
- Check if file paths match supported patterns (see file detection patterns table)
- Verify glob patterns work for your directory structure
- Use `is_sql_file(path)` to test individual paths

**Issue: Content is None or empty**
- Verify the Git provider is returning file content
- Check that `SQLChange.content` is preserved through the pipeline
- The DiffExtractor should log `content=True` for detected files

**Issue: No findings generated**
- Standard `RuleAnalyzer` only analyzes `CREATE TABLE` statements
- `ALTER TABLE` patterns are checked by `_check_dangerous_patterns()`
- Check logs for "Analyzing SQL content" and issue counts

#### Step 4: Run the Test Script

Use the included test script to verify the fix:

```bash
python scripts/test_ci_analysis.py
```

Expected output should show:
- Dangerous patterns being detected
- Content being preserved in SQL changes
- Findings being generated

---

## Next Phase

**Phase 3: Memory Integration** will add:
- Memory consultation in analysis pipeline
- Feedback endpoint (`POST /decisions/{id}/feedback`)
- Memory update logic
- Suppression/modification logic based on project memory
