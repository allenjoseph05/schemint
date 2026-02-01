# Schemint Evolution Plan: CI-Native Memory-Backed Architecture

## Executive Summary

This document outlines the phased evolution of Schemint from a stateless API-driven MVP to a CI-native, memory-backed SQL governance system. The new architecture:

1. **Triggers from CI events** (not user-submitted SQL)
2. **Analyzes only diffs** (changed SQL/migrations/ORM schemas)
3. **Maintains project memory** (learns from feedback over time)
4. **Reduces API surface** to CI ingestion, feedback, and inspection

---

## Table of Contents

1. [Current State Analysis](#current-state-analysis)
2. [Target Architecture](#target-architecture)
3. [Project Memory Store Design](#project-memory-store-design)
4. [Phased Implementation Plan](#phased-implementation-plan)
5. [API Surface Changes](#api-surface-changes)
6. [CI Integration Patterns](#ci-integration-patterns)
7. [Migration Strategy](#migration-strategy)

---

## Current State Analysis

### What We Have Today

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT ARCHITECTURE                          │
│                    (Stateless, API-Driven)                       │
└─────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Client     │────▶│  POST /analyze  │────▶│   SQL Parser    │
│ (Manual SQL) │     │                 │     │   (REUSE)       │
└──────────────┘     └─────────────────┘     └────────┬────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │  Rule Analyzer  │
                                             │    (REUSE)      │
                                             └────────┬────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │  Claude AI      │
                                             │  (Stateless)    │
                                             └────────┬────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │ Analysis Result │
                                             │  (Ephemeral)    │
                                             └─────────────────┘
```

### Current Components (To Reuse)

| Component | Location | Reuse Strategy |
|-----------|----------|----------------|
| SQL Parser | `core/parser/sql_parser.py` | Keep as-is, add diff parsing |
| Rule Analyzer | `core/analyzer/rule_analyzer.py` | Keep as-is, add memory consultation |
| Schema Models | `models/schema.py` | Extend for diff representation |
| Issue Models | `models/issue.py` | Add feedback/resolution fields |
| Context Models | `core/context/models.py` | Extend for memory integration |

### Current Limitations

1. **Stateless**: No memory of past analyses or decisions
2. **Manual Trigger**: Requires user to submit SQL
3. **Full Schema Analysis**: Analyzes everything, not just changes
4. **No Feedback Loop**: Findings cannot be accepted/rejected
5. **No Project Identity**: Each request is isolated

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TARGET ARCHITECTURE                                  │
│                    (CI-Native, Memory-Backed)                                │
└─────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────┐
│                              CI/CD SYSTEMS                                  │
│                                                                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │   GitHub    │  │   GitLab    │  │   Jenkins   │  │   Other     │       │
│  │   Actions   │  │     CI      │  │             │  │             │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│         │                │                │                │               │
│         └────────────────┴────────────────┴────────────────┘               │
│                                   │                                        │
│                          Webhook / API Call                                │
│                                   │                                        │
└───────────────────────────────────┼────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         SCHEMINT CI GATEWAY                                │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                    POST /ci/ingest                                   │ │
│  │                                                                      │ │
│  │  Input:                                                              │ │
│  │  {                                                                   │ │
│  │    "project_id": "org/repo",                                        │ │
│  │    "event_type": "pull_request | push | migration | pre_deploy",    │ │
│  │    "ref": "refs/pull/123/head" | "abc123" (commit SHA),             │ │
│  │    "base_ref": "main" (for diff calculation),                       │ │
│  │    "provider": "github | gitlab | bitbucket",                       │ │
│  │    "auth_token": "..." (for repo access)                            │ │
│  │  }                                                                   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                   │                                       │
└───────────────────────────────────┼───────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         DIFF EXTRACTION ENGINE                             │
│                                                                           │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                 │
│  │  Git Diff     │  │  SQL File     │  │  ORM Schema   │                 │
│  │  Fetcher      │  │  Detector     │  │  Detector     │                 │
│  │               │  │               │  │               │                 │
│  │ - Clone/fetch │  │ - *.sql       │  │ - Prisma      │                 │
│  │ - Get diff    │  │ - migrations/ │  │ - SQLAlchemy  │                 │
│  │ - Filter      │  │ - schema/     │  │ - TypeORM     │                 │
│  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘                 │
│          │                  │                  │                          │
│          └──────────────────┴──────────────────┘                          │
│                             │                                             │
│                             ▼                                             │
│                    ┌─────────────────┐                                    │
│                    │  SchemaDiff     │                                    │
│                    │  {              │                                    │
│                    │    added: [...] │                                    │
│                    │    modified:[..]│                                    │
│                    │    removed: [..]│                                    │
│                    │  }              │                                    │
│                    └────────┬────────┘                                    │
│                             │                                             │
└─────────────────────────────┼─────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         ANALYSIS PIPELINE                                  │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                    1. DETERMINISTIC LAYER                            │ │
│  │                       (Existing, Reused)                             │ │
│  │                                                                      │ │
│  │  ┌─────────────────┐     ┌─────────────────┐                        │ │
│  │  │   SQL Parser    │────▶│  Rule Analyzer  │                        │ │
│  │  │  (sql_parser.py)│     │(rule_analyzer.py│                        │ │
│  │  └─────────────────┘     └────────┬────────┘                        │ │
│  │                                   │                                  │ │
│  │                          Raw Findings                                │ │
│  │                                   │                                  │ │
│  └───────────────────────────────────┼──────────────────────────────────┘ │
│                                      │                                    │
│                                      ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                    2. MEMORY CONSULTATION                            │ │
│  │                       (NEW)                                          │ │
│  │                                                                      │ │
│  │  For each finding:                                                   │ │
│  │  ┌─────────────────────────────────────────────────────────────┐    │ │
│  │  │  Query Project Memory Store:                                 │    │ │
│  │  │                                                              │    │ │
│  │  │  - Is this pattern marked "known-safe"?                     │    │ │
│  │  │  - Was identical finding previously accepted?               │    │ │
│  │  │  - Does business rule override apply?                       │    │ │
│  │  │  - Is there historical context that changes severity?       │    │ │
│  │  │                                                              │    │ │
│  │  │  If YES → Suppress or modify finding                        │    │ │
│  │  │  If NO  → Pass through to reasoning layer                   │    │ │
│  │  └─────────────────────────────────────────────────────────────┘    │ │
│  │                                   │                                  │ │
│  └───────────────────────────────────┼──────────────────────────────────┘ │
│                                      │                                    │
│                                      ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                    3. CONTEXTUAL REASONING                           │ │
│  │                       (Enhanced with Memory)                         │ │
│  │                                                                      │ │
│  │  Claude AI receives:                                                 │ │
│  │  - Current diff (not raw SQL, but parsed structure)                 │ │
│  │  - Project memory summary:                                          │ │
│  │    - Schema semantics ("orders.total is always USD")                │ │
│  │    - Historical patterns ("team prefers snake_case")                │ │
│  │    - Past decisions ("FLOAT for prices accepted in legacy tables")  │ │
│  │    - Business rules ("multi-tenancy required for new tables")       │ │
│  │                                                                      │ │
│  │  Claude produces:                                                    │ │
│  │  - Context-aware findings                                           │ │
│  │  - Explanation referencing project history                          │ │
│  │                                   │                                  │ │
│  └───────────────────────────────────┼──────────────────────────────────┘ │
│                                      │                                    │
└──────────────────────────────────────┼────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                         DECISION OUTPUT                                    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  AnalysisDecision                                                    │ │
│  │  {                                                                   │ │
│  │    "decision_id": "dec_abc123",                                     │ │
│  │    "project_id": "org/repo",                                        │ │
│  │    "ref": "abc123",                                                 │ │
│  │    "status": "pass | fail | warn",                                  │ │
│  │    "findings": [                                                    │ │
│  │      {                                                              │ │
│  │        "id": "find_001",                                            │ │
│  │        "type": "missing_primary_key",                               │ │
│  │        "severity": "critical",                                      │ │
│  │        "location": {file, line, table, column},                     │ │
│  │        "memory_context": "No prior exceptions for this pattern",    │ │
│  │        "suggested_action": "block | warn | info",                   │ │
│  │        "feedback_url": "/api/v1/decisions/{id}/feedback"           │ │
│  │      }                                                              │ │
│  │    ],                                                               │ │
│  │    "memory_applied": ["rule_123", "pattern_456"],                   │ │
│  │    "check_url": "https://schemint.io/decisions/dec_abc123"         │ │
│  │  }                                                                   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │
           ┌───────────────────────────┴───────────────────────────┐
           │                                                       │
           ▼                                                       ▼
┌─────────────────────────┐                           ┌─────────────────────────┐
│   CI STATUS UPDATE      │                           │   FEEDBACK LOOP         │
│                         │                           │                         │
│  GitHub Check Run       │                           │  POST /decisions/{id}/  │
│  GitLab Pipeline Status │                           │       feedback          │
│  Jenkins Build Result   │                           │                         │
│                         │                           │  {                      │
│  ✓ Pass                 │                           │    "finding_id": "...", │
│  ✗ Fail                 │                           │    "action": "accept |  │
│  ⚠ Warning              │                           │              override", │
│                         │                           │    "reason": "...",     │
│                         │                           │    "apply_to": "once |  │
│                         │                           │         pattern | rule" │
│                         │                           │  }                      │
└─────────────────────────┘                           └───────────┬─────────────┘
                                                                  │
                                                                  ▼
                                                      ┌─────────────────────────┐
                                                      │   PROJECT MEMORY STORE  │
                                                      │                         │
                                                      │  Updates:               │
                                                      │  - Accepted findings    │
                                                      │  - New safe patterns    │
                                                      │  - Business rules       │
                                                      │  - Historical context   │
                                                      │                         │
                                                      │  (See detailed design   │
                                                      │   below)                │
                                                      └─────────────────────────┘
```

---

## Project Memory Store Design

### Core Principle: No Raw Code Storage

The memory store **NEVER** stores:
- Raw SQL statements
- Source code
- Full file contents
- Credentials or secrets

It **ONLY** stores:
- Structured conclusions
- Pattern signatures (hashes, not content)
- Semantic descriptions
- Decision metadata

### Memory Store Schema

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PROJECT MEMORY STORE                                 │
│                         (PostgreSQL / SQLite)                                │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: projects                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ external_id     VARCHAR(255) UNIQUE     -- "github:org/repo"                │
│ name            VARCHAR(255)                                                │
│ created_at      TIMESTAMP                                                   │
│ settings        JSONB                    -- Project-level settings          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 1:N
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: accepted_findings                                                     │
│                                                                             │
│ Records findings that were accepted (false positives or intentional)        │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ finding_type    VARCHAR(100)             -- "missing_primary_key"           │
│ pattern_hash    VARCHAR(64)              -- SHA256 of normalized pattern    │
│ scope           ENUM('once','pattern','rule')                               │
│ reason          TEXT                     -- Human explanation               │
│ accepted_by     VARCHAR(255)             -- User who accepted               │
│ accepted_at     TIMESTAMP                                                   │
│ expires_at      TIMESTAMP NULL           -- Optional expiration             │
│ context         JSONB                    -- Additional context              │
│                                          -- {table: "legacy_orders",        │
│                                          --  semantic: "historical data"}   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: known_safe_patterns                                                   │
│                                                                             │
│ Patterns marked as safe for this project (not findings, but patterns)       │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ pattern_type    VARCHAR(100)             -- "float_for_non_money"           │
│ pattern_hash    VARCHAR(64)              -- Normalized pattern hash         │
│ description     TEXT                     -- "FLOAT used for percentages"    │
│ created_by      VARCHAR(255)                                                │
│ created_at      TIMESTAMP                                                   │
│ examples        JSONB                    -- [{table, column, rationale}]    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: business_rules                                                        │
│                                                                             │
│ Project-specific rules that override or modify default behavior             │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ rule_type       VARCHAR(100)             -- "require_tenant_id"             │
│ rule_config     JSONB                    -- Rule-specific config            │
│ severity        ENUM('critical','warning','suggestion','ignore')            │
│ applies_to      JSONB                    -- {tables: ["*"], except: [...]}  │
│ rationale       TEXT                     -- Why this rule exists            │
│ created_by      VARCHAR(255)                                                │
│ created_at      TIMESTAMP                                                   │
│ active          BOOLEAN DEFAULT true                                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: schema_semantics                                                      │
│                                                                             │
│ Semantic meaning attached to schema elements                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ element_type    ENUM('table','column','relationship')                       │
│ element_path    VARCHAR(255)             -- "orders.total" or "users"       │
│ semantic_tags   TEXT[]                   -- ["money", "usd", "immutable"]   │
│ description     TEXT                     -- Human description               │
│ constraints     JSONB                    -- Semantic constraints            │
│ updated_at      TIMESTAMP                                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: historical_inflection_points                                         │
│                                                                             │
│ Major changes that affect how we interpret the schema                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ event_type      VARCHAR(100)             -- "legacy_migration",             │
│                                          -- "standard_change",              │
│                                          -- "exception_granted"             │
│ event_date      DATE                                                        │
│ description     TEXT                     -- What happened                   │
│ impact          JSONB                    -- How it affects analysis         │
│                                          -- {before_date: "lenient",        │
│                                          --  after_date: "strict"}          │
│ affected_tables TEXT[]                   -- Tables affected                 │
│ created_at      TIMESTAMP                                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ TABLE: analysis_history                                                      │
│                                                                             │
│ Record of all analyses (for trends, not for storing SQL)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ id              UUID PRIMARY KEY                                            │
│ project_id      UUID REFERENCES projects                                    │
│ ref             VARCHAR(255)             -- Commit SHA or PR ref            │
│ event_type      VARCHAR(50)              -- "pull_request", "push"          │
│ status          ENUM('pass','fail','warn')                                  │
│ finding_count   INTEGER                                                     │
│ findings_hash   VARCHAR(64)              -- Hash of finding types           │
│ memory_applied  JSONB                    -- Which memory items applied      │
│ duration_ms     INTEGER                                                     │
│ created_at      TIMESTAMP                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Pattern Hashing (No Raw SQL)

Instead of storing SQL, we store normalized pattern hashes:

```python
def compute_pattern_hash(finding: Finding) -> str:
    """
    Compute a hash that identifies the pattern, not the specific SQL.

    Example: Two different tables missing primary keys would have
    DIFFERENT hashes (table name matters), but the same table
    analyzed twice would have the SAME hash.
    """
    # Normalize the pattern components
    pattern_components = {
        "type": finding.category,           # "missing_primary_key"
        "table": finding.table_name,        # "orders"
        "column": finding.column_name,      # null for table-level
        "data_type": finding.data_type,     # "FLOAT" if relevant
        # DO NOT include: actual SQL, file contents, line numbers
    }

    # Create deterministic hash
    canonical = json.dumps(pattern_components, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
```

### Memory Consultation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      MEMORY CONSULTATION ALGORITHM                           │
└─────────────────────────────────────────────────────────────────────────────┘

For each finding from deterministic analysis:

1. COMPUTE pattern_hash for the finding

2. CHECK accepted_findings:
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ SELECT * FROM accepted_findings                                         │
   │ WHERE project_id = :project                                             │
   │   AND (                                                                 │
   │     (scope = 'once' AND pattern_hash = :hash)                          │
   │     OR (scope = 'pattern' AND pattern_hash = :hash)                    │
   │     OR (scope = 'rule' AND finding_type = :type)                       │
   │   )                                                                     │
   │   AND (expires_at IS NULL OR expires_at > NOW())                       │
   └─────────────────────────────────────────────────────────────────────────┘

   If FOUND → SUPPRESS finding, log "suppressed by acceptance {id}"

3. CHECK known_safe_patterns:
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ SELECT * FROM known_safe_patterns                                       │
   │ WHERE project_id = :project                                             │
   │   AND pattern_hash = :hash                                              │
   └─────────────────────────────────────────────────────────────────────────┘

   If FOUND → SUPPRESS finding, log "matches safe pattern {id}"

4. CHECK business_rules for severity override:
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ SELECT * FROM business_rules                                            │
   │ WHERE project_id = :project                                             │
   │   AND rule_type = :finding_type                                         │
   │   AND active = true                                                     │
   │   AND (                                                                 │
   │     applies_to->'tables' ? '*'                                         │
   │     OR applies_to->'tables' ? :table_name                              │
   │   )                                                                     │
   │   AND NOT (applies_to->'except' ? :table_name)                         │
   └─────────────────────────────────────────────────────────────────────────┘

   If FOUND → MODIFY severity per rule

5. CHECK historical_inflection_points:
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ SELECT * FROM historical_inflection_points                              │
   │ WHERE project_id = :project                                             │
   │   AND :table_name = ANY(affected_tables)                                │
   └─────────────────────────────────────────────────────────────────────────┘

   If FOUND → ADD context to finding explanation

6. ENRICH finding with schema_semantics:
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ SELECT * FROM schema_semantics                                          │
   │ WHERE project_id = :project                                             │
   │   AND element_path IN (:table_name, :table_name || '.' || :column_name)│
   └─────────────────────────────────────────────────────────────────────────┘

   If FOUND → ENRICH finding with semantic context

7. RETURN finding (possibly modified or suppressed)
```

### Demonstrable Learning Example

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DEMONSTRABLE LEARNING OVER TIME                           │
└─────────────────────────────────────────────────────────────────────────────┘

SCENARIO: Same SQL analyzed at different times

SQL: CREATE TABLE metrics (id INT PRIMARY KEY, value FLOAT);

────────────────────────────────────────────────────────────────────────────────
TIME T1: First Analysis (No Memory)
────────────────────────────────────────────────────────────────────────────────

Memory State: Empty

Analysis Result:
  ⚠ WARNING: Column 'value' uses FLOAT
    "FLOAT can cause precision loss. Consider DECIMAL for financial data."

    [Accept] [Override]

User Action: ACCEPTS with reason "This is for metrics, not money. FLOAT is fine."
            Scope: "pattern" (apply to similar patterns)

Memory Update:
  INSERT INTO accepted_findings (
    finding_type: "wrong_data_type_float",
    pattern_hash: sha256("metrics.value.FLOAT.non_money"),
    scope: "pattern",
    reason: "This is for metrics, not money. FLOAT is fine.",
    context: {semantic_tags: ["metrics", "non_financial"]}
  )

────────────────────────────────────────────────────────────────────────────────
TIME T2: Same SQL Analyzed Again (With Memory)
────────────────────────────────────────────────────────────────────────────────

Memory State: Has accepted_finding for this pattern

Analysis Result:
  ✓ NO WARNING for 'value' column

    Memory Applied: "Pattern previously accepted (metrics, not financial data)"

Result: Finding SUPPRESSED based on prior acceptance

────────────────────────────────────────────────────────────────────────────────
TIME T3: Different Table, Similar Pattern
────────────────────────────────────────────────────────────────────────────────

SQL: CREATE TABLE prices (id INT PRIMARY KEY, amount FLOAT);

Memory Consultation:
  - Pattern hash is DIFFERENT (different table, "amount" sounds financial)
  - No exact match in accepted_findings
  - Check schema_semantics: "amount" often tagged as "money"

Analysis Result:
  ⚠ WARNING: Column 'amount' uses FLOAT
    "FLOAT can cause precision loss. Column name 'amount' suggests financial data."

    Note: Similar pattern was accepted for 'metrics.value' but this column
    appears to be financial based on naming.

    [Accept] [Override]

Result: Finding NOT suppressed (different semantic context)
```

---

## Phased Implementation Plan

### Phase Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         IMPLEMENTATION PHASES                                │
└─────────────────────────────────────────────────────────────────────────────┘

Phase 1: Foundation (2-3 weeks)
├── Memory store schema + basic operations
├── Pattern hashing utilities
├── Project registration endpoint
└── Keep existing API working

Phase 2: CI Integration (2-3 weeks)
├── CI ingestion endpoint
├── Git diff extraction
├── SQL file detection
└── Webhook handlers (GitHub, GitLab)

Phase 3: Memory Integration (2-3 weeks)
├── Memory consultation in analysis pipeline
├── Feedback endpoint
├── Memory update logic
└── Suppression/modification logic

Phase 4: Reasoning Enhancement (2 weeks)
├── Memory-aware AI prompts
├── Historical context injection
├── Semantic enrichment
└── Learning demonstration

Phase 5: API Reduction & Hardening (1-2 weeks)
├── Deprecate old endpoints
├── Rate limiting
├── Authentication
└── Documentation

Total: ~10-13 weeks
```

### Phase 1: Foundation

**Goal**: Establish memory store infrastructure while keeping existing functionality.

```
src/schemint/
├── memory/                          # NEW: Memory store module
│   ├── __init__.py
│   ├── models.py                    # SQLAlchemy/Pydantic models
│   ├── store.py                     # MemoryStore class
│   ├── patterns.py                  # Pattern hashing utilities
│   └── migrations/                  # Alembic migrations
│       └── versions/
│           └── 001_initial.py
│
├── core/
│   └── ... (unchanged)              # Keep existing analysis working
│
└── api/
    └── v1/
        ├── analysis.py              # Keep existing (deprecated later)
        └── projects.py              # NEW: Project registration
```

**Key Deliverables**:

1. **Memory Store Models** (`memory/models.py`):
```python
class Project(Base):
    __tablename__ = "projects"
    id: UUID
    external_id: str  # "github:org/repo"
    name: str
    settings: dict

class AcceptedFinding(Base):
    __tablename__ = "accepted_findings"
    id: UUID
    project_id: UUID
    finding_type: str
    pattern_hash: str
    scope: Literal["once", "pattern", "rule"]
    reason: str
    # ... etc

# Similar for other tables
```

2. **Pattern Hashing** (`memory/patterns.py`):
```python
def compute_finding_hash(finding: Finding) -> str:
    """Compute deterministic hash for a finding pattern."""

def normalize_pattern(finding: Finding) -> dict:
    """Extract hashable pattern from finding."""
```

3. **Project Registration** (`api/v1/projects.py`):
```python
@router.post("/projects")
async def register_project(request: ProjectRegistration):
    """Register a new project for analysis."""

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    """Get project info and memory summary."""
```

### Phase 2: CI Integration

**Goal**: Add CI ingestion endpoint and diff extraction.

```
src/schemint/
├── ci/                              # NEW: CI integration module
│   ├── __init__.py
│   ├── ingest.py                    # CI event handler
│   ├── diff_extractor.py           # Git diff extraction
│   ├── file_detector.py            # SQL/ORM file detection
│   ├── providers/                   # Git provider integrations
│   │   ├── __init__.py
│   │   ├── base.py                  # Abstract provider
│   │   ├── github.py                # GitHub integration
│   │   ├── gitlab.py                # GitLab integration
│   │   └── generic.py               # Generic git
│   └── models.py                    # CI-specific models
│
└── api/
    └── v1/
        └── ci.py                    # NEW: CI endpoints
```

**Key Deliverables**:

1. **CI Ingestion Endpoint** (`api/v1/ci.py`):
```python
@router.post("/ci/ingest")
async def ingest_ci_event(request: CIIngestRequest) -> AnalysisDecision:
    """
    Main CI integration endpoint.

    Triggered by: GitHub Actions, GitLab CI, Jenkins, etc.

    Flow:
    1. Validate project registration
    2. Fetch diff from git provider
    3. Extract SQL changes
    4. Run analysis pipeline
    5. Return decision with feedback URLs
    """
```

2. **Diff Extraction** (`ci/diff_extractor.py`):
```python
class DiffExtractor:
    async def extract(
        self,
        provider: GitProvider,
        base_ref: str,
        head_ref: str,
    ) -> SchemaDiff:
        """
        Extract schema changes between two refs.

        Returns:
            SchemaDiff with added/modified/removed SQL
        """
```

3. **File Detection** (`ci/file_detector.py`):
```python
class SQLFileDetector:
    """Detect SQL-related files in a diff."""

    PATTERNS = [
        "*.sql",
        "migrations/**/*",
        "schema/**/*",
        "prisma/schema.prisma",
        "**/models.py",  # SQLAlchemy
        # ... etc
    ]
```

4. **GitHub Integration** (`ci/providers/github.py`):
```python
class GitHubProvider(GitProvider):
    async def get_diff(self, base: str, head: str) -> list[FileDiff]:
        """Get diff via GitHub API."""

    async def set_check_status(self, status: CheckStatus):
        """Update GitHub Check Run."""
```

### Phase 3: Memory Integration

**Goal**: Integrate memory consultation into analysis pipeline.

```
src/schemint/
├── core/
│   └── analyzer/
│       ├── analyzer.py              # MODIFY: Add memory consultation
│       └── memory_filter.py         # NEW: Memory-based filtering
│
├── memory/
│   ├── consultation.py              # NEW: Memory consultation logic
│   └── feedback.py                  # NEW: Feedback processing
│
└── api/
    └── v1/
        └── decisions.py             # NEW: Decision feedback endpoint
```

**Key Deliverables**:

1. **Memory Consultation** (`memory/consultation.py`):
```python
class MemoryConsultant:
    """Consults project memory to filter/modify findings."""

    async def filter_findings(
        self,
        project_id: UUID,
        findings: list[Finding],
    ) -> tuple[list[Finding], list[MemoryApplication]]:
        """
        Filter findings based on project memory.

        Returns:
            Tuple of (filtered_findings, applied_memory_items)
        """
```

2. **Modified Analyzer** (`core/analyzer/analyzer.py`):
```python
async def analyze_with_memory(
    schema: ParsedSchema,
    project_id: UUID,
    # ...
) -> AnalysisResult:
    """
    Analysis pipeline with memory consultation.

    1. Run deterministic analysis (existing)
    2. Consult project memory (NEW)
    3. Run contextual reasoning (existing, enhanced)
    4. Return result with memory context
    """
```

3. **Feedback Endpoint** (`api/v1/decisions.py`):
```python
@router.post("/decisions/{decision_id}/feedback")
async def submit_feedback(
    decision_id: str,
    feedback: FindingFeedback,
) -> FeedbackResult:
    """
    Submit feedback on a finding.

    Actions:
    - accept: Mark as acceptable (won't warn again)
    - override: Change severity/behavior

    Scope:
    - once: Just this instance
    - pattern: Similar patterns in this project
    - rule: All instances of this rule type
    """
```

### Phase 4: Reasoning Enhancement

**Goal**: Enhance AI reasoning with memory context.

**Key Deliverables**:

1. **Memory-Aware Prompts** (`services/claude.py`):
```python
def _build_memory_context_section(
    self,
    project_memory: ProjectMemorySummary,
) -> str:
    """
    Build prompt section with memory context.

    Includes:
    - Previously accepted patterns and why
    - Business rules and rationales
    - Schema semantics
    - Historical inflection points
    """
```

2. **Learning Demonstration**:
```python
class LearningDemonstrator:
    """
    Utility to demonstrate learning over time.

    Shows how identical SQL produces different results
    based on accumulated project memory.
    """

    async def compare_with_without_memory(
        self,
        sql: str,
        project_id: UUID,
    ) -> LearningComparison:
        """
        Analyze same SQL with and without memory.

        Returns comparison showing:
        - Findings without memory
        - Findings with memory
        - Which memory items applied
        - How output changed
        """
```

### Phase 5: API Reduction & Hardening

**Goal**: Finalize public API surface, remove deprecated endpoints.

**Final API Surface**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FINAL PUBLIC API                                   │
└─────────────────────────────────────────────────────────────────────────────┘

CI INGESTION (Primary Interface)
────────────────────────────────────────────────────────────────────────────────
POST /api/v1/ci/ingest
  - Receives CI events (PR, push, migration, pre-deploy)
  - Extracts diff, runs analysis, returns decision
  - Sets check status on git provider

DECISION FEEDBACK
────────────────────────────────────────────────────────────────────────────────
POST /api/v1/decisions/{id}/feedback
  - Accept or override findings
  - Updates project memory

GET /api/v1/decisions/{id}
  - Get decision details
  - Read-only

READ-ONLY INSPECTION
────────────────────────────────────────────────────────────────────────────────
GET /api/v1/projects/{id}
  - Project info and settings

GET /api/v1/projects/{id}/memory
  - Project memory summary
  - Accepted findings, safe patterns, business rules

GET /api/v1/projects/{id}/history
  - Analysis history
  - Trends over time

ADMIN (Protected)
────────────────────────────────────────────────────────────────────────────────
POST /api/v1/projects
  - Register new project

DELETE /api/v1/projects/{id}/memory/{item_id}
  - Remove memory item

DEPRECATED (Remove in v2)
────────────────────────────────────────────────────────────────────────────────
POST /api/v1/analyze              → Use /ci/ingest
POST /api/v1/analyze/quick        → Use /ci/ingest
POST /api/v1/analyze/with-context → Use /ci/ingest + project memory
```

---

## API Surface Changes

### Current API (To Deprecate)

```python
# DEPRECATE: Manual SQL submission
POST /api/v1/analyze
POST /api/v1/analyze/quick
POST /api/v1/analyze/with-context
POST /api/v1/validate-context
```

### New API (CI-Native)

```python
# PRIMARY: CI Integration
POST /api/v1/ci/ingest
{
    "project_id": "github:org/repo",
    "event_type": "pull_request",
    "ref": "refs/pull/123/head",
    "base_ref": "main",
    "provider": "github",
    "provider_token": "ghs_xxx"  # For repo access
}

# FEEDBACK: Decision Management
POST /api/v1/decisions/{decision_id}/feedback
{
    "finding_id": "find_001",
    "action": "accept",
    "reason": "This is intentional for legacy compatibility",
    "scope": "pattern"  # once | pattern | rule
}

GET /api/v1/decisions/{decision_id}

# INSPECTION: Read-Only
GET /api/v1/projects/{project_id}
GET /api/v1/projects/{project_id}/memory
GET /api/v1/projects/{project_id}/memory/accepted
GET /api/v1/projects/{project_id}/memory/patterns
GET /api/v1/projects/{project_id}/memory/rules
GET /api/v1/projects/{project_id}/history

# ADMIN: Project Management
POST /api/v1/projects
{
    "external_id": "github:org/repo",
    "name": "My Project",
    "settings": {
        "default_severity": "warning",
        "auto_block": ["missing_primary_key"]
    }
}

DELETE /api/v1/projects/{project_id}/memory/{item_id}
```

---

## CI Integration Patterns

### GitHub Actions Example

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
        uses: schemint/action@v1
        with:
          project_id: ${{ github.repository }}
          schemint_url: https://schemint.example.com
          schemint_token: ${{ secrets.SCHEMINT_TOKEN }}
```

### GitLab CI Example

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
      curl -X POST https://schemint.example.com/api/v1/ci/ingest \
        -H "Authorization: Bearer $SCHEMINT_TOKEN" \
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

### Jenkins Example

```groovy
// Jenkinsfile
pipeline {
    agent any
    stages {
        stage('Schema Analysis') {
            when {
                changeset "**/*.sql"
            }
            steps {
                script {
                    def response = httpRequest(
                        url: 'https://schemint.example.com/api/v1/ci/ingest',
                        httpMode: 'POST',
                        contentType: 'APPLICATION_JSON',
                        customHeaders: [[name: 'Authorization', value: "Bearer ${SCHEMINT_TOKEN}"]],
                        requestBody: """
                        {
                            "project_id": "jenkins:${JOB_NAME}",
                            "event_type": "push",
                            "ref": "${GIT_COMMIT}",
                            "base_ref": "${GIT_PREVIOUS_COMMIT}",
                            "provider": "generic"
                        }
                        """
                    )

                    def result = readJSON text: response.content
                    if (result.status == 'fail') {
                        error "Schema analysis failed: ${result.findings.size()} issues found"
                    }
                }
            }
        }
    }
}
```

---

## Migration Strategy

### Backward Compatibility

During transition, both APIs will be available:

```python
# api/v1/__init__.py

# New CI-native endpoints (preferred)
from schemint.api.v1.ci import router as ci_router
from schemint.api.v1.decisions import router as decisions_router
from schemint.api.v1.projects import router as projects_router

# Legacy endpoints (deprecated, will be removed in v2)
from schemint.api.v1.analysis import router as legacy_router

# Add deprecation warning to legacy endpoints
@legacy_router.post("/analyze")
async def analyze_schema(request: AnalysisRequest):
    warnings.warn(
        "POST /analyze is deprecated. Use POST /ci/ingest instead.",
        DeprecationWarning
    )
    # ... existing implementation
```

### Data Migration

For existing users, provide migration path:

```python
# scripts/migrate_to_memory.py

async def migrate_project(
    project_id: str,
    existing_context: dict,  # From schemint.yaml
):
    """
    Migrate existing project context to memory store.

    Converts:
    - Deprecated columns → accepted_findings
    - Conventions → business_rules
    - Schema metadata → schema_semantics
    """
```

---

## Summary

This evolution transforms Schemint from a stateless tool into an intelligent, learning system:

| Aspect | Current MVP | Target System |
|--------|-------------|---------------|
| Trigger | Manual API call | CI events |
| Input | Full SQL | Git diff only |
| Memory | None (stateless) | Project-scoped store |
| Learning | None | Demonstrable over time |
| API Surface | Multiple endpoints | 3 core endpoints |
| Context | Per-request | Accumulated history |

The phased approach ensures:
1. **Continuity**: Existing functionality works throughout
2. **Reuse**: SQL parser and rule analyzer unchanged
3. **Testability**: Each phase is independently testable
4. **Rollback**: Can revert to previous phase if issues
