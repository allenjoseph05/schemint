# Schemint Agentic AI — Implementation Status

## Part 1: AgentAnalyzer (DONE)

Rewrote `ClaudeAnalyzer` from fragile raw-JSON prompting to production-ready agent with tool use.

| Change | File |
|--------|------|
| `DOMAIN` IssueCategory added | `src/schemint/models/issue.py` |
| Tiered model settings (`claude_model_simple`, `claude_model_complex`) | `src/schemint/config.py` |
| Full rewrite: tool use, system prompt, compression, model selection | `src/schemint/services/claude.py` |
| AI category mapping updated, `fix_description`/`reasoning` handling | `src/schemint/core/analyzer/analyzer.py` |
| 15 unit tests (all passing) | `tests/unit/test_agent_analyzer.py` |

## Part 2: Memory-Enriched Prompts (DONE)

Wired memory store retrieval into the analysis pipeline so Claude receives project memory as context, respects suppressed findings, and returns AI-computed scores.

| Change | File |
|--------|------|
| `build_memory_context()` function, `memory_context` param in analyze methods | `src/schemint/services/claude.py` |
| `project_id` param, `_resolve_project_id()`, `_retrieve_memory()`, suppression logic, AI score override | `src/schemint/core/analyzer/analyzer.py` |
| `project_id` query param on `/analysis` and `/analysis/quick` | `src/schemint/api/v1/analysis.py` |
| Memory context in API call test | `tests/unit/test_agent_analyzer.py` |
| 12 new tests: memory context, suppression, AI scores, graceful fallback | `tests/unit/test_memory_enriched.py` |

### Key Design Decisions

- **Memory retrieval is optional** — if `project_id` is not provided or DB is not configured, analysis works exactly as before
- **Memory retrieval happens in `analyzer.py`, not `claude.py`** — analyzer orchestrates; ClaudeAnalyzer just receives pre-formatted data
- **AI scores override deterministic scores** — when Claude has memory context, it produces more nuanced scores
- **Suppression is double-layered** — Claude sees memory in the prompt (prompt-level), and we also filter responses against the suppressed list (code-level safety net)

## Part 3: CI Pipeline AI Integration + Architecture Documentation (DONE)

Wired `use_ai` through the CI pipeline so CI analysis can use memory-enriched AI, and created comprehensive architecture documentation.

| Change | File |
|--------|------|
| `use_ai` field on `CIIngestRequest` model | `src/schemint/ci/models.py` |
| `use_ai` + `project_id` passed through `_analyze_diff()` to `analyze_sql()` | `src/schemint/ci/ingest.py` |
| `use_ai` query param on `/ci/ingest`, API key validation | `src/schemint/api/v1/ci.py` |
| AI badge in summary, AI summary section, AI score averaging | `src/schemint/ci/report_builder.py` |
| 10 unit tests for CI+AI flow | `tests/unit/test_ci_ai_integration.py` |
| Comprehensive architecture documentation | `docs/architecture.md` |

### Key Design Decisions

- **`use_ai` defaults to `False`** — Backward compatible. Existing CI integrations unchanged.
- **Query param override** — `?use_ai=true` lets CI configs enable AI without changing JSON body.
- **Double-layered memory** — CI-level suppression (deterministic) + AI-level memory (prompt enrichment) serve different purposes and both remain active.
- **AI badge** — Reports clearly indicate when AI was used for transparency.
- **Score averaging** — Multi-file AI scores are averaged across files.

## Part 4: Multi-Turn Agentic Analyzer (DONE)

Replaced one-shot AI analysis with a multi-turn agentic analyzer that uses tool-use to selectively investigate schemas.

| Change | File |
|--------|------|
| Pre-analysis engine (topology, patterns, stats, risks) | `src/schemint/core/analyzer/pre_analysis.py` |
| Multi-turn agentic analyzer with tool loop | `src/schemint/services/agent.py` |
| Agent as primary AI path, ClaudeAnalyzer as fallback | `src/schemint/core/analyzer/analyzer.py` |
| Scoring rubric added to system prompt | `src/schemint/services/claude.py` |
| `claude_max_agent_turns` setting | `src/schemint/config.py` |
| 15 pre-analysis tests | `tests/unit/test_pre_analysis.py` |
| 10 agent tests | `tests/unit/test_agent.py` |
| AI agent improvements roadmap | `docs/ai_agent_improvements.md` |

### Key Design Decisions

- **Agent decides investigation depth** — Simple schemas get 2 turns; complex schemas get 5-8 selective turns
- **Lightweight initial message** — Only table names + column counts; full details come via `inspect_table` tool calls
- **Pre-analysis powers agent tools** — Topology, patterns, statistics, risk signals computed in pure Python (zero LLM tokens)
- **ClaudeAnalyzer stays as fallback** — If agent loop fails, gracefully falls back to one-shot analysis
- **Scoring rubric** — Both agent and one-shot prompts include explicit scoring rules for consistent, explainable scores

## Part 5: Caching Layer + Prompt Injection Protection (Planned)

- Add response caching (hash-based) to avoid re-analyzing identical schemas
- Add prompt injection detection for user-provided SQL content
- Rate limiting per project

## Part 6: Streaming + Async (Planned)

- Convert to true async with `anthropic.AsyncAnthropic`
- Streaming support for long-running analyses

## Part 7: Specialist Agents (Future)

- Per-dimension specialist agents (security agent, performance agent, etc.)
- Agent routing based on schema characteristics
- Consensus scoring across multiple specialists
