# Schemint CI Pipeline — How It Works

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [End-to-End Flow](#end-to-end-flow)
3. [Analysis Pipeline Detail](#analysis-pipeline-detail)
4. [Rule Checks (All 14)](#rule-checks-all-14)
5. [Scoring System](#scoring-system)
6. [Report Output](#report-output)
7. [Making CI Jobs Fail (Quality Gates)](#making-ci-jobs-fail-quality-gates)
8. [What's Missing / Next Steps](#whats-missing--next-steps)
9. [Testing Examples](#testing-examples)

---

## Architecture Overview

```
                    GitHub/GitLab/Jenkins
                           |
                    POST /api/v1/ci/ingest
                           |
                    CIIngestHandler.ingest()
                           |
           +---------------+----------------+
           |               |                |
    Git Provider     DiffExtractor    SQLFileDetector
    (GitHub/GL/      (extracts SQL    (finds *.sql,
     Generic)         changes)        migrations, ORM)
           |               |                |
           +-------+-------+----------------+
                   |
            SQL Content per file
                   |
       +-----------+-----------+
       |                       |
  Dangerous Pattern       RuleAnalyzer
  Detection (sqlparse)    (14 checks)
       |                       |
       +-------+-------+------+
               |
        AnalysisFinding[]
               |
       +-------+--------+
       |       |         |
    Scoring  Report    Annotations
    Engine   Builder   Builder
       |       |         |
       +-------+---------+
               |
        AnalysisDecision
        {status, findings, summary,
         annotations, report_score}
               |
        HTTP 200 Response
```

---

## End-to-End Flow

### Step 1: CI System Sends Request

A CI system (GitHub Actions, GitLab CI, Jenkins) sends a POST to `/api/v1/ci/ingest`:

```json
{
  "project_id": "github:acme/ecommerce",
  "event_type": "pull_request",
  "ref": "abc123",
  "base_ref": "main",
  "provider": "github",
  "provider_token": "ghp_xxxx",
  "pr_number": 42
}
```

### Step 2: Provider Fetches Diff

The handler creates a git provider (`GitHubProvider`, `GitLabProvider`, or `GenericGitProvider`) and calls:

```
provider.get_diff(repo, base_ref, head_ref) -> list[DiffFile]
```

This returns every file changed between `base_ref` and `ref`, with content.

### Step 3: SQL File Detection

`SQLFileDetector` matches file paths against patterns:

| Category | Patterns |
|----------|----------|
| SQL files | `**/*.sql`, `schema/**/*.sql`, `database/**/*.sql` |
| Migrations | `migrations/**/*.sql`, `alembic/versions/**/*.py`, `db/migrate/**/*.rb`, `flyway/**/*.sql` |
| ORM files | `prisma/schema.prisma`, `**/models.py`, `**/entities/*.ts` |

### Step 4: Content Extraction

For each detected SQL file, `DiffExtractor` parses the content:

- **Pure SQL** → Parsed with `sqlparse` to extract CREATE/ALTER/DROP TABLE statements
- **Alembic migrations** → Python AST parsing to find `op.create_table()`, `op.add_column()`, etc.
- **SQLAlchemy models** → AST parsing for `Base` subclasses with `__tablename__`
- **Prisma / TypeORM** → Regex-based extraction

### Step 5: Analysis Pipeline

For each SQL file with content, two analysis passes run:

**Pass A — Dangerous Pattern Detection** (migration safety):
- `ALTER TABLE ... ADD COLUMN ... DEFAULT` → `blocking_migration` (CRITICAL)
- `ALTER TABLE ... DROP COLUMN` → `destructive_change` (CRITICAL)
- `DROP TABLE` → `destructive_change` (CRITICAL)
- `ADD ... NOT NULL` without DEFAULT → `unsafe_migration` (WARNING)

**Pass B — Full Rule Analysis** (`analyze_sql()` → `RuleAnalyzer`):
1. Parses SQL into `ParsedSchema` (tables, columns, FKs, indexes)
2. Normalizes identifiers to lowercase
3. Runs all 14 rule checks (see below)
4. Calculates scores
5. Returns `AnalysisResult` with issues

### Step 6: Memory Suppression

Each finding is checked against the project's memory store. If a finding was previously accepted (e.g., "we know this table doesn't need a PK"), it gets `suppressed_by_memory = True` and doesn't count toward the decision status.

### Step 7: Decision Status

```python
active_findings = [f for f in findings if not f.suppressed_by_memory]

if any critical in active_findings → FAIL
elif any warning in active_findings → WARN
else → PASS
```

### Step 8: Report Generation

`CIReportBuilder` produces three outputs:

1. **`report_score`** — Score breakdown (total, structural, performance, naming, best_practices)
2. **`annotations`** — Array of `CIAnnotation` for inline PR comments
3. **`summary`** — Markdown report for CI logs

### Step 9: CI Status Update

The handler calls `provider.set_check_status()` to post the result back:
- GitHub → Check Run API
- GitLab → Commit Status API
- Generic → No-op (CI reads the HTTP response)

---

## Analysis Pipeline Detail

### Identifier Normalization

All identifiers are lowercased after parsing:
- Table names: `Users` → `users`
- Column names: `UserName` → `username`
- FK references: `REFERENCES Users(ID)` → `users.id`
- Primary keys, index columns

This ensures rule checks work consistently regardless of SQL casing.

### Rule Checks (All 14)

| # | Check | Category | Severity | What It Catches |
|---|-------|----------|----------|-----------------|
| 1 | Missing Primary Key | `missing_primary_key` | CRITICAL | Table with no PK |
| 2 | Wrong Data Type | `wrong_data_type` | CRITICAL/WARNING | FLOAT for money, VARCHAR for dates |
| 3 | Security Risk | `security_risk` | CRITICAL | `password`, `secret`, `token`, `api_key` without `_hash`/`_encrypted` suffix |
| 4 | Missing Timestamps | `missing_timestamps` | WARNING | No `created_at`/`updated_at` |
| 5 | Missing Foreign Key | `missing_foreign_key` | WARNING | `*_id` column without FK constraint (excl. PK, `external_id`, `device_id`, `session_id`) |
| 6 | Orphaned Foreign Key | `orphaned_foreign_key` | WARNING | FK references table not in schema |
| 7 | Missing Index on FK | `missing_index` | WARNING | FK column without index |
| 8 | PII Detected | `pii_detected` | WARNING | `email`, `ssn`, `phone`, `address` without encryption marker |
| 9 | Missing NOT NULL | `missing_not_null` | WARNING | `name`, `email`, `username`, `status` that are nullable |
| 10 | Reserved Word | `reserved_word` | WARNING | Column/table uses SQL reserved word |
| 11 | Missing Constraint | `missing_constraint` | SUGGESTION | `email` without UNIQUE; `status`/`type` without ENUM |
| 12 | Inefficient Type | `inefficient_type` | SUGGESTION | INT for `is_*`/`has_*` (should be BOOLEAN); TEXT for `name`/`status` (should be VARCHAR) |
| 13 | No Soft Delete | `no_soft_delete` | SUGGESTION | No `deleted_at`/`is_deleted` column |
| 14 | No Multi-Tenancy | `no_multi_tenancy` | SUGGESTION | No `tenant_id`/`organization_id` column |

Plus FK naming convention check and missing ON DELETE cascade check.

---

## Scoring System

### Total Score (0-100)

```
Start at 100
  - CRITICAL issues:   -15 pts each
  - WARNING issues:    -5 pts each
  - SUGGESTION issues: -2 pts each (CAPPED at 10 pts total)
```

The suggestion cap prevents tables from being unfairly penalized by opinionated best-practice checks like soft delete and multi-tenancy.

### Subcategory Scores

| Category | Issues Counted | Deduction |
|----------|---------------|-----------|
| Structural | missing_primary_key, missing_foreign_key, orphaned_foreign_key, missing_constraint, missing_not_null | -15 each |
| Performance | missing_index, wrong_data_type, inefficient_type | -12 each |
| Naming | naming_convention, reserved_word | -10 each |
| Best Practices | missing_timestamps, no_soft_delete, missing_cascade, no_multi_tenancy, security_risk, pii_detected | -8 each |

### Grades

| Score | Grade | Label |
|-------|-------|-------|
| 90-100 | A | Excellent |
| 80-89 | B | Good |
| 70-79 | C | Decent |
| 60-69 | D | Needs Work |
| 0-59 | F | Poor |

---

## Report Output

### Markdown Summary (in `decision.summary`)

```markdown
## Schemint Schema Analysis: FAIL (Grade: D)

Score: 55/100 | Poor

| Severity | Count |
|----------|-------|
| Critical | 2 |
| Warning | 4 |
| Suggestion | 3 |

### Critical Issues
| Location | Issue | Description |
|----------|-------|-------------|
| users.password | Sensitive column 'password' may store plaintext | Column 'users.password' appears to store sensitive data without hashing |

### Warnings
| Location | Issue | Description |
|----------|-------|-------------|
| users.email | PII column 'email' without encryption | Column 'users.email' appears to contain PII |

### Suggestions
| Location | Issue | Description |
|----------|-------|-------------|
| users | No soft delete on 'users' | Table 'users' has no soft delete column |

---
_Analysis completed in 45ms by Schemint_
```

### Annotations (in `decision.annotations`)

```json
[
  {
    "file": "migrations/001_users.sql",
    "line": null,
    "severity": "critical",
    "title": "Sensitive column 'password' may store plaintext",
    "message": "[users.password] Column 'users.password' appears to store sensitive data...",
    "category": "security_risk"
  }
]
```

### Score Breakdown (in `decision.report_score`)

```json
{
  "total": 55,
  "grade": "F",
  "label": "Poor",
  "structural": 70,
  "performance": 88,
  "naming": 80,
  "best_practices": 52
}
```

---

## Making CI Jobs Fail (Quality Gates)

### The Problem

Right now, schemint returns `status: "fail"` or `status: "warn"` in the JSON response, but the HTTP status code is always `200 OK`. CI systems (GitHub Actions, GitLab CI, Jenkins) only fail a job when:

1. The process exits with a **non-zero exit code**, or
2. The HTTP response returns a **4xx/5xx status code**

Since schemint always returns 200, the CI job always succeeds.

### How SonarQube Does It

SonarQube uses a **Quality Gate** pattern:
1. Scanner runs and uploads results (always succeeds)
2. A separate step polls the quality gate status
3. If the gate fails, the step exits with code 1

### Solution: CLI Quality Gate Script

The recommended approach is a **wrapper script** that calls the schemint API and exits with the appropriate code. This gives CI consumers full control over thresholds.

#### Option A: Shell Script for GitHub Actions / GitLab CI

```bash
#!/bin/bash
# schemint-gate.sh — Quality gate wrapper
# Usage: ./schemint-gate.sh <schemint-url> <project-id> <ref> <base-ref> <provider> [token]

SCHEMINT_URL=${1:-"http://localhost:8000"}
PROJECT_ID=$2
REF=$3
BASE_REF=${4:-"main"}
PROVIDER=${5:-"generic"}
TOKEN=$6

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${SCHEMINT_URL}/api/v1/ci/ingest" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"${PROVIDER}:${PROJECT_ID}\",
    \"event_type\": \"pull_request\",
    \"ref\": \"${REF}\",
    \"base_ref\": \"${BASE_REF}\",
    \"provider\": \"${PROVIDER}\",
    \"provider_token\": ${TOKEN:+\"$TOKEN\"}${TOKEN:-null}
  }")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ]; then
  echo "Schemint API error: HTTP $HTTP_CODE"
  echo "$BODY"
  exit 2
fi

STATUS=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
SUMMARY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary','No summary'))")

echo "$SUMMARY"

case "$STATUS" in
  "pass")
    echo "Quality Gate: PASSED"
    exit 0
    ;;
  "warn")
    echo "Quality Gate: WARNING"
    # Exit 0 for warnings (non-blocking) or exit 1 to block
    exit 0
    ;;
  "fail")
    echo "Quality Gate: FAILED"
    exit 1
    ;;
  "error")
    echo "Quality Gate: ERROR"
    exit 2
    ;;
esac
```

#### Option B: Python CLI Script (more control)

```python
#!/usr/bin/env python3
"""schemint-gate.py — Quality gate with configurable thresholds."""

import argparse
import json
import sys
import httpx

def main():
    parser = argparse.ArgumentParser(description="Schemint CI Quality Gate")
    parser.add_argument("--url", default="http://localhost:8000", help="Schemint API URL")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--provider", default="generic")
    parser.add_argument("--token", default=None)
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score to pass (0-100)")
    parser.add_argument("--max-critical", type=int, default=0, help="Max critical issues allowed")
    parser.add_argument("--max-warning", type=int, default=None, help="Max warnings allowed")
    parser.add_argument("--fail-on-warn", action="store_true", help="Fail on warnings too")
    args = parser.parse_args()

    payload = {
        "project_id": f"{args.provider}:{args.project_id}",
        "event_type": "pull_request",
        "ref": args.ref,
        "base_ref": args.base_ref,
        "provider": args.provider,
        "provider_token": args.token,
    }

    resp = httpx.post(f"{args.url}/api/v1/ci/ingest", json=payload, timeout=60)
    if resp.status_code != 200:
        print(f"API Error: {resp.status_code} — {resp.text}")
        sys.exit(2)

    decision = resp.json()

    # Print summary
    if decision.get("summary"):
        print(decision["summary"])

    status = decision["status"]
    score = decision.get("report_score", {}).get("total", 100)
    critical = decision["critical_count"]
    warning = decision["warning_count"]

    # Quality gate checks
    failed = False
    reasons = []

    if status == "fail":
        failed = True
        reasons.append(f"Status: FAIL ({critical} critical issues)")

    if args.fail_on_warn and status == "warn":
        failed = True
        reasons.append(f"Status: WARN ({warning} warnings, --fail-on-warn enabled)")

    if args.min_score > 0 and score < args.min_score:
        failed = True
        reasons.append(f"Score {score} < minimum {args.min_score}")

    if args.max_critical is not None and critical > args.max_critical:
        failed = True
        reasons.append(f"Critical count {critical} > max {args.max_critical}")

    if args.max_warning is not None and warning > args.max_warning:
        failed = True
        reasons.append(f"Warning count {warning} > max {args.max_warning}")

    if failed:
        print("\n--- QUALITY GATE: FAILED ---")
        for r in reasons:
            print(f"  - {r}")
        sys.exit(1)
    else:
        print("\n--- QUALITY GATE: PASSED ---")
        sys.exit(0)

if __name__ == "__main__":
    main()
```

#### Option C: GitHub Action Workflow Example

```yaml
name: Schema Analysis
on: [pull_request]

jobs:
  schemint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history for diff

      - name: Run Schemint Analysis
        id: analysis
        run: |
          RESPONSE=$(curl -s -X POST "${{ secrets.SCHEMINT_URL }}/api/v1/ci/ingest" \
            -H "Content-Type: application/json" \
            -d '{
              "project_id": "github:${{ github.repository }}",
              "event_type": "pull_request",
              "ref": "${{ github.event.pull_request.head.sha }}",
              "base_ref": "${{ github.event.pull_request.base.ref }}",
              "provider": "github",
              "provider_token": "${{ secrets.GITHUB_TOKEN }}",
              "pr_number": ${{ github.event.pull_request.number }}
            }')

          STATUS=$(echo "$RESPONSE" | jq -r '.status')
          SCORE=$(echo "$RESPONSE" | jq -r '.report_score.total // 100')
          SUMMARY=$(echo "$RESPONSE" | jq -r '.summary // "No summary"')

          echo "status=$STATUS" >> $GITHUB_OUTPUT
          echo "score=$SCORE" >> $GITHUB_OUTPUT

          echo "$SUMMARY" >> $GITHUB_STEP_SUMMARY

      - name: Quality Gate
        if: steps.analysis.outputs.status == 'fail'
        run: |
          echo "Schema analysis failed with critical issues!"
          exit 1

      - name: Warn on Issues
        if: steps.analysis.outputs.status == 'warn'
        run: echo "::warning::Schema analysis found warnings"
```

#### Option D: GitLab CI Example

```yaml
schemint:
  stage: test
  script:
    - |
      RESPONSE=$(curl -s -X POST "${SCHEMINT_URL}/api/v1/ci/ingest" \
        -H "Content-Type: application/json" \
        -d "{
          \"project_id\": \"gitlab:${CI_PROJECT_PATH}\",
          \"event_type\": \"pull_request\",
          \"ref\": \"${CI_COMMIT_SHA}\",
          \"base_ref\": \"${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-main}\",
          \"provider\": \"gitlab\",
          \"provider_token\": \"${GITLAB_TOKEN}\"
        }")

      STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")

      if [ "$STATUS" = "fail" ]; then
        echo "Schema analysis FAILED"
        echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',''))"
        exit 1
      fi

      echo "Schema analysis: $STATUS"
  allow_failure: false  # Set to true if you want warnings to not block
```

---

## What's Missing / Next Steps

### High Priority

| Feature | Why It Matters | Effort |
|---------|----------------|--------|
| **CLI quality gate script** (ship with schemint) | Without this, CI jobs never fail. This is the #1 gap. | Small — write `schemint-gate` CLI command |
| **Decision persistence** | `GET /ci/status/{id}` returns 404. Can't retrieve past analyses. | Medium — add DB table + CRUD |
| **Webhook signature verification** | GitHub/GitLab webhooks are unauthenticated. Anyone can trigger analysis. | Small — HMAC-SHA256 for GitHub, token for GitLab |
| **Line number tracking** | Annotations have `line: null`. Can't point to specific lines in PR diffs. | Medium — track line offsets during parsing |

### Medium Priority

| Feature | Why It Matters | Effort |
|---------|----------------|--------|
| **PR comment posting** | Schemint can generate annotations but doesn't post them as PR comments yet. | Medium — use `provider.create_comment()` or Check Run annotations |
| **Configurable rule thresholds** | Projects can't disable `no_multi_tenancy` or `no_soft_delete` checks. | Medium — project config model + rule filtering |
| **Database type detection** | Hard-coded to `mysql`. Should detect from project settings or file content. | Small |
| **AI-enhanced analysis** | `use_ai=False` in CI pipeline. Claude could provide richer explanations. | Medium — async AI call with timeout |
| **Rate limiting** | No rate limiting on endpoints. Could be abused. | Small — add FastAPI rate limiter |

### Low Priority / Future

| Feature | Why It Matters | Effort |
|---------|----------------|--------|
| **Dashboard UI** | Web interface to view analysis history, trends, configure rules | Large |
| **SARIF output** | Standard format for security tools. GitHub Code Scanning integration. | Medium |
| **Bitbucket / Azure DevOps providers** | Providers exist as enum values but not implemented. | Medium each |
| **Cross-PR trend tracking** | "Score improved by 15 points since last PR" | Medium |
| **Custom rule plugins** | Let teams define project-specific rules | Large |
| **Schema diff visualization** | Show before/after schema diagrams | Large |

### Known Issues

1. **`test_projects_api.py`** — 11 errors due to `MemoryStore(db_path=...)` API mismatch
2. **Generic provider** — requires local git repo or pre-computed diffs, no remote fetching without cloning
3. **Large file handling** — no size limits on SQL content analysis
4. **Concurrent analysis** — no request queuing or deduplication for same PR

---

## Testing Examples

See the `examples/test_schemas/` directory for ready-to-use SQL files that exercise every rule check. Run them with:

```bash
python examples/test_schemas/run_all_tests.py
```

Or test individual schemas:

```python
from schemint.core.analyzer import analyze_sql

result = analyze_sql(open("examples/test_schemas/01_perfect_schema.sql").read())
print(f"Score: {result.score.total}/100 ({result.score.grade})")
for issue in result.issues:
    print(f"  [{issue.severity.value}] {issue.title}")
```
