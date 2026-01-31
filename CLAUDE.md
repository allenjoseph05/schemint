# Schemint - AI Database Schema Analyzer

## Project Overview
SQL governance and reasoning system that combines deterministic SQL parsing with AI-powered analysis.

## Tech Stack
- Python 3.10+
- FastAPI
- Pydantic
- sqlparse (SQL parsing)
- Claude API (AI analysis) - NOT Gemini

## Architecture
```
src/schemint/
├── api/v1/          # REST endpoints
├── core/
│   ├── parser/      # SQL AST parsing
│   ├── analyzer/    # Rule-based analysis
│   └── context/     # Project context (NEW)
├── models/          # Pydantic models
└── services/        # AI service (Claude)
```

## Current State
- Basic SQL parser working
- Rule-based analyzer working
- Gemini integration exists but switching to Claude

## Next Phase: Project Context Loader
Add ability to:
1. Ingest schema metadata (tables, columns, types, indexes, constraints)
2. Parse migration history from repo
3. Flag deprecated/renamed schema elements
4. Enforce project-specific conventions

## Commands
- `pip install -e .` - Install
- `uvicorn schemint.main:app --reload` - Run
- `make test` - Test

## AI Provider
Use Claude API (anthropic package), NOT Gemini.