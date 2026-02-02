# Phase 1: Foundation - Implementation Complete

## Overview

Phase 1 establishes the memory store infrastructure while keeping existing functionality working. This phase lays the groundwork for the CI-native, memory-backed architecture.

**Database Support:** SQLite (default) and PostgreSQL (production)

## Completed Deliverables

### 1. Memory Store Module (`src/schemint/memory/`)

**Files Created:**
- `__init__.py` - Module exports
- `models.py` - Pydantic data models for memory entities
- `store.py` - SQLite-backed memory store implementation
- `patterns.py` - Pattern hashing utilities

**Key Models:**

| Model | Purpose |
|-------|---------|
| `Project` | Registered project with external ID and settings |
| `AcceptedFinding` | Finding marked as false positive or intentional |
| `KnownSafePattern` | Pattern proactively marked as safe |
| `BusinessRule` | Project-specific rule overrides |
| `SchemaSemantics` | Semantic meaning attached to schema elements |
| `HistoricalInflectionPoint` | Major events affecting interpretation |
| `AnalysisHistory` | Record of analysis runs |

**Pattern Hashing:**

The memory store uses SHA256 hashes of normalized patterns instead of storing raw SQL:

```python
from schemint.memory import compute_finding_hash, normalize_pattern

# Pattern includes: category, table, column, semantic markers
# Does NOT include: raw SQL, line numbers, file paths
pattern = normalize_pattern(finding)
hash = compute_finding_hash(finding)
```

### 2. Project Registration API (`src/schemint/api/v1/projects.py`)

**Endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/projects` | POST | Register new project |
| `/api/v1/projects/{id}` | GET | Get project info |
| `/api/v1/projects/{id}/memory` | GET | Get memory summary |
| `/api/v1/projects/{id}/memory/accepted` | GET | List accepted findings |
| `/api/v1/projects/{id}/memory/rules` | GET | List business rules |
| `/api/v1/projects/{id}/memory/accepted/{id}` | DELETE | Remove accepted finding |

**Example Usage:**

```bash
# Register a project
curl -X POST "http://localhost:8000/api/v1/projects" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "github:acme/ecommerce",
    "name": "ACME E-Commerce",
    "settings": {"default_severity": "warning"}
  }'

# Get project memory summary
curl "http://localhost:8000/api/v1/projects/github:acme-ecommerce/memory"
```

### 3. CI Integration Models (`src/schemint/ci/models.py`)

Foundation models for Phase 2:

- `CIEventType` - Types of CI events (PR, push, migration, pre-deploy)
- `GitProvider` - Supported providers (GitHub, GitLab, Bitbucket, Azure DevOps)
- `CIIngestRequest` - Request for CI event ingestion
- `AnalysisDecision` - Response with findings and feedback URLs
- `SchemaDiff` - Schema changes extracted from diff

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 ARCHITECTURE                         │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌─────────────────┐     ┌─────────────────────────┐
│   Client     │────▶│  Projects API   │────▶│    Memory Store         │
│              │     │                 │     │    (SQLite)             │
└──────────────┘     └─────────────────┘     │                         │
                                              │  - projects             │
                                              │  - accepted_findings    │
┌──────────────┐     ┌─────────────────┐     │  - known_safe_patterns  │
│ Existing API │────▶│  Analysis API   │     │  - business_rules       │
│  (Unchanged) │     │  (Unchanged)    │     │  - schema_semantics     │
└──────────────┘     └─────────────────┘     │  - analysis_history     │
                                              └─────────────────────────┘
```

## Database Schema

SQLite database (`schemint_memory.db`) with tables:

```sql
-- Projects table
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    external_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}'
);

-- Accepted findings (won't warn again)
CREATE TABLE accepted_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    finding_type TEXT NOT NULL,
    pattern_hash TEXT NOT NULL,  -- SHA256, not raw SQL
    scope TEXT NOT NULL,         -- 'once', 'pattern', 'rule'
    reason TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    expires_at TEXT,
    context TEXT NOT NULL DEFAULT '{}'
);

-- Business rules (severity overrides)
CREATE TABLE business_rules (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    rule_config TEXT NOT NULL DEFAULT '{}',
    severity TEXT NOT NULL,
    applies_to TEXT NOT NULL DEFAULT '{"tables": ["*"]}',
    rationale TEXT NOT NULL,
    created_by TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

-- Plus: known_safe_patterns, schema_semantics,
--       historical_inflection_points, analysis_history
```

## Tests

### Unit Tests (`tests/unit/test_memory_store.py`)

24 tests covering:
- Project registration and retrieval
- Accepted findings (create, check, expiration, scope)
- Business rules (create, filter by table)
- Pattern hashing and matching
- Memory summary
- Schema semantics
- Analysis history

### Integration Tests (`tests/integration/test_projects_api.py`)

11 tests covering:
- HTTP endpoints for project management
- Error handling (404, validation)
- Memory endpoint responses

## Usage Examples

### Register and Use Memory Store

```python
from schemint.memory import get_memory_store, FeedbackScope

# Get the global store instance
store = get_memory_store()

# Register a project
project = store.register_project(
    external_id="github:myorg/myrepo",
    name="My Project"
)

# Accept a finding (won't warn again)
from schemint.models.issue import Issue, IssueCategory, IssueSeverity

finding = Issue(
    category=IssueCategory.WRONG_DATA_TYPE,
    severity=IssueSeverity.WARNING,
    title="FLOAT used for price",
    description="Consider DECIMAL for money",
    table_name="products",
    column_name="price"
)

store.accept_finding(
    project_id=project.id,
    finding=finding,
    reason="FLOAT is acceptable for this legacy table",
    accepted_by="developer@example.com",
    scope=FeedbackScope.PATTERN  # Apply to similar patterns
)

# Check if a finding is accepted
accepted = store.check_finding_accepted(project.id, finding)
if accepted:
    print(f"Suppressed: {accepted.reason}")
```

### Add Business Rule

```python
store.add_business_rule(
    project_id=project.id,
    rule_type="require_tenant_id",
    severity="critical",
    rationale="Multi-tenant architecture requires tenant isolation",
    created_by="architect@example.com",
    rule_config={"column_name": "tenant_id", "type": "UUID"},
    applies_to={"tables": ["*"], "except": ["schema_migrations"]}
)
```

## Files Changed

| File | Change |
|------|--------|
| `src/schemint/memory/__init__.py` | Created - module exports |
| `src/schemint/memory/models.py` | Created - Pydantic models |
| `src/schemint/memory/store.py` | Created - SQLite store |
| `src/schemint/memory/patterns.py` | Created - pattern hashing |
| `src/schemint/ci/__init__.py` | Created - CI module placeholder |
| `src/schemint/ci/models.py` | Created - CI data models |
| `src/schemint/ci/providers/__init__.py` | Created - providers placeholder |
| `src/schemint/api/v1/projects.py` | Created - Projects API |
| `src/schemint/api/v1/__init__.py` | Updated - include projects router |
| `tests/unit/test_memory_store.py` | Created - 24 unit tests |
| `tests/integration/test_projects_api.py` | Created - 11 API tests |

## Next Phase

**Phase 2: CI Integration** will add:
- `POST /api/v1/ci/ingest` endpoint
- Git diff extraction
- SQL file detection
- GitHub/GitLab webhook handlers

## Verification

Run all tests to verify Phase 1 implementation:

```bash
# Run memory store tests
pytest tests/unit/test_memory_store.py -v

# Run API tests
pytest tests/integration/test_projects_api.py -v

# Run all tests
pytest tests/ -v
```

All 80 tests should pass.
