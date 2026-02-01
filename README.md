# Schemint

**CI-native SQL governance system with project memory**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What is Schemint?

Schemint is a **database schema analyzer** that learns from your team's decisions. It combines deterministic SQL parsing with AI-powered analysis and maintains **project memory** to avoid repeating resolved issues.

```
┌─────────────────────────────────────────────────────────────────┐
│                    SAME SQL, DIFFERENT TIME                      │
│                                                                  │
│   CREATE TABLE metrics (id INT PRIMARY KEY, value FLOAT);       │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       Week 1 (No Memory)              Week 2 (With Memory)
       ┌─────────────────┐             ┌─────────────────┐
       │ ⚠ WARNING:      │             │ ✓ NO WARNING    │
       │ FLOAT for value │             │                 │
       │                 │             │ (Previously     │
       │ [Accept]        │  ─────────▶ │  accepted for   │
       │                 │   Feedback  │  metrics)       │
       └─────────────────┘             └─────────────────┘
```

**Key capabilities:**
- Triggers from CI events (PR, push, pre-deploy)
- Analyzes only changed SQL (not full schema)
- Maintains durable project memory
- Learns from accept/override feedback

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CI/CD SYSTEMS                                   │
│         GitHub Actions  │  GitLab CI  │  Jenkins  │  Other                  │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         POST /ci/ingest                                      │
│   { project_id, ref, base_ref, provider }                                   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Diff Extractor │    │  SQL Parser     │    │ Project Memory  │
│                 │    │  (Reused)       │    │     Store       │
│  Git diff →     │    │                 │    │                 │
│  SQL changes    │    │  SQL → AST      │    │ - Accepted      │
└────────┬────────┘    └────────┬────────┘    │ - Safe patterns │
         │                      │             │ - Business rules│
         └──────────────────────┴─────────────┤ - Semantics     │
                                              └────────┬────────┘
                                                       │
                                                       ▼
                                    ┌─────────────────────────────────┐
                                    │        Analysis Pipeline        │
                                    │                                 │
                                    │  1. Rule Analyzer (deterministic)
                                    │  2. Memory Consultation         │
                                    │  3. Claude AI (contextual)      │
                                    │  4. Score & Decision            │
                                    └────────────────┬────────────────┘
                                                     │
                                                     ▼
                                    ┌─────────────────────────────────┐
                                    │      AnalysisDecision           │
                                    │  { status, findings, feedback } │
                                    └─────────────────────────────────┘
```

---

## Project Structure

```
schemint/
├── src/schemint/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Environment configuration
│   │
│   ├── api/v1/                 # REST API endpoints
│   │   ├── analysis.py         # Current: /analyze endpoints
│   │   └── health.py           # Health check
│   │
│   ├── core/                   # Business logic
│   │   ├── parser/             # SQL parsing (sqlparse)
│   │   │   └── sql_parser.py
│   │   ├── analyzer/           # Analysis orchestration
│   │   │   ├── analyzer.py     # Main orchestrator
│   │   │   └── rule_analyzer.py # Deterministic rules
│   │   └── context/            # Project context
│   │       ├── models.py       # Context data models
│   │       ├── context_loader.py
│   │       ├── conventions.py  # Convention checker
│   │       └── migration_parser.py
│   │
│   ├── memory/                 # Project Memory Store (NEW)
│   │   ├── models.py           # Memory data models
│   │   ├── store.py            # SQLite-backed store
│   │   └── patterns.py         # Pattern hashing (no raw SQL)
│   │
│   ├── ci/                     # CI Integration (Phase 2)
│   │   ├── models.py           # CI request/response models
│   │   └── providers/          # Git provider adapters
│   │
│   ├── models/                 # Pydantic data models
│   │   ├── schema.py           # ParsedSchema, Table, Column
│   │   ├── issue.py            # Issue, IssueSeverity
│   │   └── analysis.py         # AnalysisRequest, AnalysisResult
│   │
│   └── services/
│       └── claude.py           # Claude AI integration
│
├── tests/
├── docs/
│   └── EVOLUTION_PLAN.md       # Detailed architecture plan
└── examples/
    └── ecommerce-context.yaml  # Sample project context
```

### Key Files

| Task | File |
|------|------|
| Add lint rule | `core/analyzer/rule_analyzer.py` |
| Add convention | `core/context/conventions.py` |
| Modify AI prompts | `services/claude.py` |
| Add API endpoint | `api/v1/analysis.py` |
| Modify memory store | `memory/store.py` |

---

## Getting Started

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/schemint.git
cd schemint
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -e ".[ai,dev]"
```

### Configuration

```bash
cp .env.example .env
# Edit .env and add CLAUDE_API_KEY (optional)
```

### Run Server

```bash
uvicorn schemint.main:app --reload
# Open http://localhost:8000/docs
```

### Quick Test

```bash
curl -X POST "http://localhost:8000/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{"sql": "CREATE TABLE users (id INT, name VARCHAR(100));"}'
```

---

## API Reference

### Current API (MVP)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/analyze` | Analyze SQL schema |
| `POST /api/v1/analyze/with-context` | Analyze with project context |
| `POST /api/v1/analyze/quick` | Quick pass/fail for CI |

### Target API (CI-Native)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/ci/ingest` | CI event ingestion (primary) |
| `POST /api/v1/decisions/{id}/feedback` | Accept/override findings |
| `GET /api/v1/projects/{id}/memory` | View project memory |

See [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md) for the complete migration plan.

---

## Project Memory

The memory store persists **conclusions, not code**:

```python
from schemint.memory import get_memory_store, FeedbackScope

store = get_memory_store()

# Register project
project = store.register_project(
    external_id="github:myorg/myrepo",
    name="My Project"
)

# Accept a finding (won't warn again for this pattern)
store.accept_finding(
    project_id=project.id,
    finding=some_finding,
    reason="FLOAT is acceptable for metrics, not money",
    accepted_by="alice@example.com",
    scope=FeedbackScope.PATTERN  # Apply to similar patterns
)
```

**What's stored:**
- Accepted findings (with pattern hash, not SQL)
- Known-safe patterns
- Business rules
- Schema semantics
- Historical inflection points

**What's NOT stored:**
- Raw SQL
- Source code
- File contents

---

## Evolution Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1. Foundation | ✅ Done | Memory store models and basic operations |
| 2. CI Integration | 🔲 Next | CI ingestion endpoint, diff extraction |
| 3. Memory Integration | 🔲 | Memory consultation in analysis pipeline |
| 4. Reasoning Enhancement | 🔲 | Memory-aware AI prompts |
| 5. API Reduction | 🔲 | Deprecate old endpoints, finalize |

See [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md) for detailed implementation plan.

---

## CI Integration Examples

### GitHub Actions

```yaml
- name: Schema Analysis
  uses: schemint/action@v1
  with:
    project_id: ${{ github.repository }}
    schemint_url: https://schemint.example.com
```

### GitLab CI

```yaml
schemint:
  script:
    - curl -X POST $SCHEMINT_URL/api/v1/ci/ingest
      -d '{"project_id":"gitlab:'$CI_PROJECT_PATH'","ref":"'$CI_COMMIT_SHA'"}'
```

---

## Development

```bash
# Run tests
pytest

# Run linting
ruff check .

# Run type checking
mypy src/
```

---

## License

MIT License - see LICENSE file.

---

## Links

- [Evolution Plan](docs/EVOLUTION_PLAN.md) - Detailed architecture roadmap
- [API Docs](http://localhost:8000/docs) - Interactive API (when running)
- [Examples](examples/) - Sample configurations
