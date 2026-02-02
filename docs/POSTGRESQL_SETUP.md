# PostgreSQL Setup Guide for Schemint

Schemint uses PostgreSQL for its memory store. This guide explains how to set up PostgreSQL, configure Schemint, and test the integration.

## Table of Contents

1. [Quick Start with Docker](#quick-start-with-docker)
2. [Native Installation](#native-installation)
3. [Configuration](#configuration)
4. [Testing the Setup](#testing-the-setup)
5. [API Endpoints](#api-endpoints)
6. [Viewing Data in PostgreSQL](#viewing-data-in-postgresql)
7. [What the Memory Store Does](#what-the-memory-store-does)

---

## Quick Start with Docker

The fastest way to get PostgreSQL running:

```bash
# Start PostgreSQL container
docker run -d \
  --name schemint-postgres \
  -e POSTGRES_USER=schemint \
  -e POSTGRES_PASSWORD=schemint123 \
  -e POSTGRES_DB=schemint \
  -p 5432:5432 \
  postgres:15

# Verify it's running
docker ps

# Test connection
docker exec -it schemint-postgres psql -U schemint -d schemint -c "SELECT 1"
```

---

## Native Installation

### Windows

1. Download from https://www.postgresql.org/download/windows/
2. Run installer, set password for `postgres` user
3. Open pgAdmin or psql and run:

```sql
CREATE USER schemint WITH PASSWORD 'schemint123';
CREATE DATABASE schemint OWNER schemint;
```

### macOS

```bash
brew install postgresql@15
brew services start postgresql@15
createuser schemint -P  # Enter password: schemint123
createdb schemint -O schemint
```

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE USER schemint WITH PASSWORD 'schemint123';"
sudo -u postgres psql -c "CREATE DATABASE schemint OWNER schemint;"
```

---

## Configuration

### 1. Create .env file

```bash
cp .env.example .env
```

### 2. Set DATABASE_URL

Edit `.env`:

```bash
DATABASE_URL=postgresql://schemint:schemint123@localhost:5432/schemint
```

**Connection string format:**
```
postgresql://[user]:[password]@[host]:[port]/[database]
```

### 3. Install dependencies

```bash
pip install -e ".[dev]"
```

### 4. Start the server

```bash
uvicorn schemint.main:app --reload
```

---

## Testing the Setup

### Step 1: Register a Project

```bash
curl -X POST "http://localhost:8000/api/v1/projects" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "github:myorg/myrepo",
    "name": "My Project"
  }'
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "external_id": "github:myorg/myrepo",
  "name": "My Project",
  "created_at": "2025-01-15T10:30:00+00:00",
  "settings": {}
}
```

### Step 2: Check Memory Summary

```bash
curl "http://localhost:8000/api/v1/projects/550e8400-e29b-41d4-a716-446655440000/memory"
```

### Step 3: Run SQL Analysis

```bash
curl -X POST "http://localhost:8000/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "CREATE TABLE orders (id INT, total FLOAT);"
  }'
```

### Step 4: Accept a Finding (Python)

```python
from schemint.memory import get_memory_store, FeedbackScope
from schemint.models.issue import Issue, IssueCategory, IssueSeverity

store = get_memory_store()
project = store.get_project_by_external_id("github:myorg/myrepo")

# Create finding to accept
finding = Issue(
    category=IssueCategory.WRONG_DATA_TYPE,
    severity=IssueSeverity.WARNING,
    title="FLOAT for money",
    description="FLOAT can cause precision issues",
    table_name="orders",
    column_name="total"
)

# Accept it
store.accept_finding(
    project_id=project.id,
    finding=finding,
    reason="Acceptable for this use case",
    accepted_by="developer@example.com",
    scope=FeedbackScope.PATTERN
)
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/projects` | POST | Register a new project |
| `/api/v1/projects/{id}` | GET | Get project info |
| `/api/v1/projects/{id}/memory` | GET | Get memory summary |
| `/api/v1/projects/{id}/memory/accepted` | GET | List accepted findings |
| `/api/v1/projects/{id}/memory/rules` | GET | List business rules |
| `/api/v1/analyze` | POST | Analyze SQL |

---

## Viewing Data in PostgreSQL

### Connect to Database

```bash
# Docker
docker exec -it schemint-postgres psql -U schemint -d schemint

# Native
psql -U schemint -h localhost -d schemint
```

### Useful Queries

```sql
-- List all tables
\dt

-- View projects
SELECT id, external_id, name, created_at FROM projects;

-- View accepted findings
SELECT
    af.finding_type,
    af.scope,
    af.reason,
    af.accepted_by,
    p.name as project
FROM accepted_findings af
JOIN projects p ON af.project_id = p.id;

-- View business rules
SELECT
    rule_type,
    severity,
    rationale,
    applies_to
FROM business_rules
WHERE active = true;

-- Memory summary for all projects
SELECT
    p.name,
    (SELECT COUNT(*) FROM accepted_findings WHERE project_id = p.id) as accepted,
    (SELECT COUNT(*) FROM business_rules WHERE project_id = p.id AND active = true) as rules,
    (SELECT COUNT(*) FROM analysis_history WHERE project_id = p.id) as analyses
FROM projects p;
```

---

## What the Memory Store Does

The memory store enables Schemint to **learn from your decisions**:

### Data Stored

| Table | Purpose |
|-------|---------|
| `projects` | Registered projects and settings |
| `accepted_findings` | Findings marked as acceptable (won't warn again) |
| `business_rules` | Project-specific rules (e.g., require tenant_id) |
| `schema_semantics` | Meaning attached to columns (e.g., "this is money") |
| `analysis_history` | Record of all analyses |

### How It Works

```
Week 1: Analysis finds "FLOAT for money" warning
        → Developer clicks [Accept]: "This is metrics, not money"
        → Stored in accepted_findings

Week 2: Same SQL analyzed again
        → Memory consulted: "This pattern was accepted"
        → Warning suppressed
        → Developer sees: "Pattern previously accepted"
```

### Future Phases

- **Phase 2**: CI integration - trigger analysis from GitHub/GitLab
- **Phase 3**: Memory consultation during analysis
- **Phase 4**: AI-powered reasoning with memory context
- **Phase 5**: Feedback API for accepting/rejecting findings

---

## Troubleshooting

### "DATABASE_URL is required"

Set the environment variable:
```bash
export DATABASE_URL=postgresql://schemint:schemint123@localhost:5432/schemint
```

Or add to `.env` file.

### "Connection refused"

Check PostgreSQL is running:
```bash
# Docker
docker ps | grep postgres

# Native
pg_isready -h localhost -p 5432
```

### "Authentication failed"

Verify credentials:
```bash
psql -U schemint -h localhost -d schemint
```

Reset password if needed:
```sql
ALTER USER schemint WITH PASSWORD 'newpassword';
```
