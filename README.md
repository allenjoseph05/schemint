# Schemint

<div align="center">

**AI-powered database schema linter and analyzer with project context awareness**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

---

## Table of Contents

1. [What is Schemint?](#what-is-schemint)
2. [How Does It Work?](#how-does-it-work)
3. [Architecture Overview](#architecture-overview)
4. [Project Structure](#project-structure)
5. [Getting Started](#getting-started)
6. [Core Concepts](#core-concepts)
7. [API Reference](#api-reference)
8. [Configuration](#configuration)
9. [Examples](#examples)
10. [How to Extend](#how-to-extend)
11. [Troubleshooting](#troubleshooting)

---

## What is Schemint?

Schemint is a **database schema analyzer** that helps you write better SQL. Think of it as a "spell checker" for your database tables.

### What Problems Does It Solve?

When you create database tables, you might make mistakes like:

```sql
-- BAD: Common mistakes developers make
CREATE TABLE orders (
    id INT,                    -- Missing PRIMARY KEY!
    total FLOAT,               -- FLOAT loses precision for money!
    created VARCHAR(100)       -- Using VARCHAR for dates!
);
```

Schemint catches these issues and tells you:
- **What's wrong** (missing primary key)
- **Why it matters** (can't uniquely identify rows)
- **How to fix it** (provides SQL fix script)

### What Makes Schemint Special?

Unlike simple linters, Schemint understands **your project's context**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     SAME SQL QUERY                               │
│                                                                  │
│   CREATE TABLE payments (amount FLOAT, created VARCHAR(50));    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     ┌────────────────┐ ┌────────────┐ ┌─────────────┐
     │  E-Commerce    │ │   Blog     │ │ No Context  │
     │   Context      │ │  Context   │ │             │
     └────────────────┘ └────────────┘ └─────────────┘
              │               │               │
              ▼               ▼               ▼
     ┌────────────────┐ ┌────────────┐ ┌─────────────┐
     │ 5 Issues Found │ │ 2 Issues   │ │ 3 Issues    │
     │ - FLOAT=CRITICAL│ │ - FLOAT=warn│ │ - Basic     │
     │ - Need soft del │ │            │ │   checks    │
     │ - Need audit cols│ │            │ │             │
     │ Score: 45/100  │ │ Score: 78  │ │ Score: 65   │
     └────────────────┘ └────────────┘ └─────────────┘
```

**The same SQL gets different feedback based on your project's requirements!**

---

## How Does It Work?

Schemint has **4 main engines** that work together:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            YOUR SQL INPUT                                 │
│                                                                          │
│    CREATE TABLE users (id INT, email VARCHAR(255), created_at DATE);    │
│                                                                          │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     1. QUERY INTROSPECTION ENGINE                         │
│                        (SQL Parser)                                       │
│                                                                          │
│   Breaks down your SQL into structured data:                             │
│   - Table name: "users"                                                  │
│   - Columns: [{name: "id", type: "INT"}, ...]                           │
│   - Primary keys, foreign keys, indexes                                  │
│                                                                          │
│   File: src/schemint/core/parser/sql_parser.py                          │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    2. DETERMINISTIC POLICY ENGINE                         │
│                        (Rule-Based Analyzer)                              │
│                                                                          │
│   Checks fixed rules that ALWAYS apply:                                  │
│   ✗ Missing primary key → CRITICAL                                       │
│   ✗ FLOAT for money columns → CRITICAL                                   │
│   ✗ Reserved words as names → WARNING                                    │
│   ✓ Has timestamps → GOOD PRACTICE                                       │
│                                                                          │
│   File: src/schemint/core/analyzer/rule_analyzer.py                      │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    3. CONTEXTUAL REASONING ENGINE                         │
│                        (Project Context + AI)                             │
│                                                                          │
│   Uses YOUR project's context to find issues:                            │
│                                                                          │
│   Project Context (from schemint.yaml):                                  │
│   ┌─────────────────────────────────────────────┐                        │
│   │ - Schema metadata (what tables exist)       │                        │
│   │ - Deprecated columns (don't use these!)     │                        │
│   │ - Conventions (require created_at column)   │                        │
│   │ - Migration history (what changed when)     │                        │
│   └─────────────────────────────────────────────┘                        │
│                                                                          │
│   + Claude AI for deeper semantic analysis                               │
│                                                                          │
│   Files: src/schemint/core/context/*.py                                  │
│          src/schemint/services/claude.py                                 │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       4. DECISION AGGREGATOR                              │
│                        (Main Analyzer)                                    │
│                                                                          │
│   Combines all findings:                                                 │
│   - Deduplicates issues                                                  │
│   - Calculates final score (0-100)                                       │
│   - Generates fix scripts                                                │
│   - Produces final report                                                │
│                                                                          │
│   File: src/schemint/core/analyzer/analyzer.py                           │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          ANALYSIS RESULT                                  │
│                                                                          │
│   {                                                                      │
│     "score": 75,                                                         │
│     "grade": "C",                                                        │
│     "issues": [                                                          │
│       {"severity": "critical", "title": "Missing primary key", ...}     │
│     ],                                                                   │
│     "good_practices": ["Has timestamps", ...],                           │
│     "fix_script": "ALTER TABLE users ADD PRIMARY KEY (id);"             │
│   }                                                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLIENT                                      │
│                   (Web UI, CLI, CI/CD Pipeline)                         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ HTTP Requests
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           API LAYER                                      │
│                         (FastAPI)                                        │
│                                                                         │
│  ┌─────────────┐  ┌──────────────────┐  ┌─────────────────────────┐    │
│  │ POST        │  │ POST             │  │ POST                    │    │
│  │ /analyze    │  │ /analyze/quick   │  │ /analyze/with-context   │    │
│  └─────────────┘  └──────────────────┘  └─────────────────────────┘    │
│                                                                         │
│  File: src/schemint/api/v1/analysis.py                                 │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           CORE LAYER                                     │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      ANALYZER (Orchestrator)                      │  │
│  │                                                                    │  │
│  │   Coordinates all analysis engines and produces final result      │  │
│  │   File: src/schemint/core/analyzer/analyzer.py                    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                 │                                       │
│         ┌───────────────────────┼───────────────────────┐              │
│         ▼                       ▼                       ▼              │
│  ┌─────────────┐    ┌───────────────────┐    ┌─────────────────────┐  │
│  │   PARSER    │    │  RULE ANALYZER    │    │  CONTEXT ENGINE     │  │
│  │             │    │                   │    │                     │  │
│  │ Parses SQL  │    │ Fixed rules like  │    │ Project-specific:   │  │
│  │ into AST    │    │ "need primary key"│    │ - Conventions       │  │
│  │             │    │                   │    │ - Deprecations      │  │
│  │ sql_parser  │    │ rule_analyzer.py  │    │ - Schema metadata   │  │
│  │ .py         │    │                   │    │                     │  │
│  └─────────────┘    └───────────────────┘    └─────────────────────┘  │
│                                                        │               │
└────────────────────────────────────────────────────────┼───────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SERVICES LAYER                                   │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      CLAUDE AI SERVICE                            │  │
│  │                                                                    │  │
│  │   Sends schema + context to Claude API for deep analysis          │  │
│  │   File: src/schemint/services/claude.py                           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Diagram

```
┌─────────┐     ┌─────────┐     ┌─────────────┐     ┌──────────────┐
│   SQL   │────▶│ Parser  │────▶│ParsedSchema │────▶│   Analyzer   │
│  String │     │         │     │  (Tables,   │     │              │
│         │     │         │     │  Columns)   │     │              │
└─────────┘     └─────────┘     └─────────────┘     └──────┬───────┘
                                                           │
                                       ┌───────────────────┼───────────────────┐
                                       ▼                   ▼                   ▼
                               ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
                               │Rule Analyzer  │   │Context Checker│   │Claude AI      │
                               │               │   │               │   │               │
                               │Returns Issues │   │Returns Issues │   │Returns Issues │
                               └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
                                       │                   │                   │
                                       └───────────────────┼───────────────────┘
                                                           │
                                                           ▼
                                                   ┌───────────────┐
                                                   │ Merge Issues  │
                                                   │ Calculate     │
                                                   │ Score         │
                                                   └───────┬───────┘
                                                           │
                                                           ▼
                                                   ┌───────────────┐
                                                   │AnalysisResult │
                                                   │ - Score: 75   │
                                                   │ - Issues: [...│
                                                   │ - Fix scripts │
                                                   └───────────────┘
```

---

## Project Structure

Here's every file in the project with explanations:

```
schemint/
│
├── src/schemint/                    # 📁 Main source code
│   │
│   ├── __init__.py                  # Package marker
│   │
│   ├── main.py                      # 🚀 APPLICATION ENTRY POINT
│   │                                #    - Creates FastAPI app
│   │                                #    - Mounts all routes
│   │                                #    - Configures CORS, middleware
│   │                                #
│   │                                #    TO MODIFY: Add new middleware,
│   │                                #    change app settings
│   │
│   ├── config.py                    # ⚙️ CONFIGURATION SETTINGS
│   │                                #    - Reads from .env file
│   │                                #    - API keys (CLAUDE_API_KEY)
│   │                                #    - Server settings (HOST, PORT)
│   │                                #
│   │                                #    TO MODIFY: Add new environment
│   │                                #    variables or settings
│   │
│   ├── api/                         # 🌐 REST API LAYER
│   │   │                            #    All HTTP endpoints live here
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   └── v1/                      # API version 1
│   │       │
│   │       ├── __init__.py          # Route aggregator - combines all routes
│   │       │
│   │       ├── analysis.py          # 📍 MAIN ANALYSIS ENDPOINTS
│   │       │                        #
│   │       │                        #    POST /api/v1/analyze
│   │       │                        #      → Basic schema analysis
│   │       │                        #
│   │       │                        #    POST /api/v1/analyze/quick
│   │       │                        #      → Quick pass/fail for CI/CD
│   │       │                        #
│   │       │                        #    POST /api/v1/analyze/with-context
│   │       │                        #      → Analysis with project context
│   │       │                        #
│   │       │                        #    POST /api/v1/validate-context
│   │       │                        #      → Validate context config
│   │       │                        #
│   │       │                        #    TO MODIFY: Add new API endpoints
│   │       │
│   │       └── health.py            # Health check endpoint
│   │                                #    GET /api/v1/health
│   │
│   ├── core/                        # 🧠 BUSINESS LOGIC (THE BRAIN)
│   │   │                            #    All analysis logic lives here
│   │   │
│   │   ├── __init__.py              # Exports main functions:
│   │   │                            #    analyze_sql, parse_sql
│   │   │
│   │   ├── parser/                  # 📄 SQL PARSER
│   │   │   │                        #    Converts SQL text → data structures
│   │   │   │
│   │   │   ├── __init__.py
│   │   │   │
│   │   │   └── sql_parser.py        # THE SQL PARSER
│   │   │                            #
│   │   │                            #    Function: parse_sql(sql, db_type)
│   │   │                            #
│   │   │                            #    Takes: "CREATE TABLE users (id INT)"
│   │   │                            #    Returns: ParsedSchema object with:
│   │   │                            #      - tables: [Table objects]
│   │   │                            #      - columns: [Column objects]
│   │   │                            #      - foreign keys, indexes, etc.
│   │   │                            #
│   │   │                            #    Uses 'sqlparse' library internally
│   │   │                            #
│   │   │                            #    TO MODIFY: Support new SQL syntax,
│   │   │                            #    new data types, new databases
│   │   │
│   │   ├── analyzer/                # 🔍 ANALYSIS ENGINES
│   │   │   │
│   │   │   ├── __init__.py
│   │   │   │
│   │   │   ├── analyzer.py          # 🎯 MAIN ORCHESTRATOR
│   │   │   │                        #
│   │   │   │                        #    THIS IS THE CENTRAL FILE!
│   │   │   │                        #
│   │   │   │                        #    Functions:
│   │   │   │                        #    - analyze_sql(sql, context, use_ai)
│   │   │   │                        #    - analyze_schema(schema, context)
│   │   │   │                        #
│   │   │   │                        #    What it does:
│   │   │   │                        #    1. Calls parser → get ParsedSchema
│   │   │   │                        #    2. Calls rule_analyzer → get issues
│   │   │   │                        #    3. Calls context checker → get issues
│   │   │   │                        #    4. Calls Claude AI → get issues
│   │   │   │                        #    5. Merges all issues
│   │   │   │                        #    6. Calculates score
│   │   │   │                        #    7. Returns AnalysisResult
│   │   │   │                        #
│   │   │   │                        #    TO MODIFY: Add new analysis sources,
│   │   │   │                        #    change scoring logic
│   │   │   │
│   │   │   └── rule_analyzer.py     # 📋 DETERMINISTIC RULES
│   │   │                            #
│   │   │                            #    Class: RuleAnalyzer
│   │   │                            #    Method: analyze(schema) → issues
│   │   │                            #
│   │   │                            #    Built-in rules:
│   │   │                            #    - _check_primary_key → CRITICAL
│   │   │                            #    - _check_timestamps → WARNING
│   │   │                            #    - _check_money_columns → CRITICAL
│   │   │                            #    - _check_date_columns → WARNING
│   │   │                            #    - _check_reserved_words → WARNING
│   │   │                            #    - _check_foreign_keys → SUGGESTION
│   │   │                            #
│   │   │                            #    TO MODIFY: Add new lint rules here!
│   │   │                            #    Just add a new _check_xxx method
│   │   │
│   │   └── context/                 # 🏗️ PROJECT CONTEXT SYSTEM
│   │       │                        #    Makes analysis project-aware
│   │       │
│   │       ├── __init__.py          # Exports: load_context, ProjectContext
│   │       │
│   │       ├── models.py            # 📦 DATA MODELS FOR CONTEXT
│   │       │                        #
│   │       │                        #    Classes defined here:
│   │       │                        #
│   │       │                        #    ProjectContext
│   │       │                        #    ├── project_name: str
│   │       │                        #    ├── schema_metadata: SchemaMetadata
│   │       │                        #    ├── conventions: ProjectConventions
│   │       │                        #    └── migrations: [MigrationInfo]
│   │       │                        #
│   │       │                        #    SchemaMetadata
│   │       │                        #    └── tables: [TableMetadata]
│   │       │                        #        └── columns: [ColumnMetadata]
│   │       │                        #            ├── deprecated: bool
│   │       │                        #            └── renamed_to: str
│   │       │                        #
│   │       │                        #    ProjectConventions
│   │       │                        #    ├── required_columns: [str]
│   │       │                        #    ├── forbidden_column_names: [str]
│   │       │                        #    ├── require_soft_delete: bool
│   │       │                        #    └── preferred_types: {str: str}
│   │       │                        #
│   │       │                        #    TO MODIFY: Add new context fields
│   │       │
│   │       ├── context_loader.py    # 📂 LOADS CONTEXT FROM FILES
│   │       │                        #
│   │       │                        #    Function: load_context(source)
│   │       │                        #
│   │       │                        #    Can load from:
│   │       │                        #    - Dictionary (API request)
│   │       │                        #    - JSON file
│   │       │                        #    - YAML file (schemint.yaml)
│   │       │                        #    - Directory (finds schemint.yaml)
│   │       │                        #
│   │       │                        #    TO MODIFY: Support new file formats
│   │       │
│   │       ├── migration_parser.py  # 📜 PARSES MIGRATION FILES
│   │       │                        #
│   │       │                        #    Extracts schema history from:
│   │       │                        #    - 001_create_users.sql
│   │       │                        #    - 20240101_add_column.sql
│   │       │                        #    - Rails-style migrations
│   │       │                        #
│   │       │                        #    Detects:
│   │       │                        #    - Table creates/drops
│   │       │                        #    - Column renames
│   │       │                        #    - Deprecations (from comments)
│   │       │                        #
│   │       │                        #    TO MODIFY: Support new migration formats
│   │       │
│   │       └── conventions.py       # ✅ CONVENTION CHECKER
│   │                                #
│   │                                #    Classes:
│   │                                #
│   │                                #    ConventionChecker
│   │                                #    - Enforces project conventions
│   │                                #    - Checks: naming, required columns,
│   │                                #      forbidden types, soft delete, etc.
│   │                                #
│   │                                #    DeprecationChecker
│   │                                #    - Flags deprecated column usage
│   │                                #    - Suggests renamed alternatives
│   │                                #
│   │                                #    TO MODIFY: Add new convention types
│   │
│   ├── models/                      # 📊 PYDANTIC DATA MODELS
│   │   │                            #    Define shapes of all data
│   │   │
│   │   ├── __init__.py
│   │   │
│   │   ├── schema.py                # PARSED SQL STRUCTURES
│   │   │                            #
│   │   │                            #    DataType (enum): INT, VARCHAR, etc.
│   │   │                            #    Column: name, type, nullable, etc.
│   │   │                            #    ForeignKey: column, references
│   │   │                            #    Index: columns, is_unique
│   │   │                            #    Table: name, columns, keys
│   │   │                            #    ParsedSchema: tables, db_type
│   │   │
│   │   ├── issue.py                 # ISSUE STRUCTURES
│   │   │                            #
│   │   │                            #    IssueSeverity: CRITICAL, WARNING, SUGGESTION
│   │   │                            #    IssueCategory: MISSING_PRIMARY_KEY, etc.
│   │   │                            #    Issue: severity, title, description,
│   │   │                            #           table_name, fix_script
│   │   │
│   │   └── analysis.py              # API REQUEST/RESPONSE MODELS
│   │                                #
│   │                                #    AnalysisRequest: sql, database_type
│   │                                #    AnalysisResult: score, issues, tables
│   │                                #    AnalysisScore: total, structural, etc.
│   │
│   └── services/                    # 🔌 EXTERNAL SERVICES
│       │
│       ├── __init__.py
│       │
│       └── claude.py                # 🤖 CLAUDE AI INTEGRATION
│                                    #
│                                    #    Class: ClaudeAnalyzer
│                                    #
│                                    #    Methods:
│                                    #    - analyze_sync(schema, context)
│                                    #      → sends to Claude API
│                                    #    - _build_prompt(schema, context)
│                                    #      → creates the prompt
│                                    #    - _parse_response(text)
│                                    #      → parses JSON response
│                                    #
│                                    #    TO MODIFY: Change AI prompts,
│                                    #    adjust what context is sent
│
├── tests/                           # 🧪 TEST SUITE
│   │
│   ├── unit/                        # Unit tests (fast, isolated)
│   │   ├── test_parser.py           # Tests SQL parser
│   │   ├── test_analyzer.py         # Tests analyzer rules
│   │   └── test_context_aware_analysis.py
│   │                                # Tests context features
│   │                                # Shows same SQL → different results
│   │
│   ├── integration/                 # Integration tests (API tests)
│   │
│   └── fixtures/
│       └── schemas.py               # Sample SQL for testing
│                                    # BAD_SCHEMA, GOOD_SCHEMA, SIMPLE_SCHEMA
│
├── examples/                        # 📚 EXAMPLE FILES
│   │
│   ├── ecommerce-context.yaml       # Full e-commerce context example
│   │                                # Shows all convention options
│   │
│   ├── blog-context.yaml            # Simple blog context example
│   │                                # Minimal conventions
│   │
│   └── demo_context_aware.py        # Demo script showing context differences
│                                    # Run: python examples/demo_context_aware.py
│
├── pyproject.toml                   # 📦 PROJECT CONFIGURATION
│                                    # Dependencies, build settings, tools
│
├── requirements.txt                 # 📦 PRODUCTION DEPENDENCIES
│                                    # fastapi, pydantic, sqlparse, etc.
│
├── .env.example                     # 🔐 ENVIRONMENT TEMPLATE
│                                    # Copy to .env and fill in values
│
├── .env                             # 🔐 YOUR ENVIRONMENT (git-ignored)
│                                    # CLAUDE_API_KEY goes here
│
├── Makefile                         # 🛠️ COMMON COMMANDS
│                                    # make run, make test, make lint
│
└── CLAUDE.md                        # 📖 AI ASSISTANT INSTRUCTIONS
                                     # Context for AI coding assistants
```

### Key Files Quick Reference

| What You Want To Do | File to Edit |
|---------------------|--------------|
| Add a new lint rule | `core/analyzer/rule_analyzer.py` |
| Add a new convention | `core/context/conventions.py` + `models.py` |
| Change AI prompts | `services/claude.py` |
| Add new API endpoint | `api/v1/analysis.py` |
| Add new configuration | `config.py` |
| Support new SQL syntax | `core/parser/sql_parser.py` |
| Add new issue category | `models/issue.py` |
| Change scoring logic | `core/analyzer/analyzer.py` |

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)

### Step-by-Step Installation

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/schemint.git
cd schemint

# 2. Create virtual environment
python -m venv venv

# 3. Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# 4. Install the package in development mode
pip install -e .

# 5. (Optional) Install AI support for Claude integration
pip install -e ".[ai]"

# 6. (Optional) Install development tools (testing, linting)
pip install -e ".[dev]"
```

### Configuration

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit .env file and add your Claude API key (optional)
# Get key from: https://console.anthropic.com/
```

**.env file contents:**
```env
# Required for AI features (optional without it)
CLAUDE_API_KEY=sk-ant-your-key-here

# Server settings
HOST=0.0.0.0
PORT=8000
ENV=development
DEBUG=true
```

### Running the Server

```bash
# Start the development server
uvicorn schemint.main:app --reload

# Server runs at http://localhost:8000
# Interactive API docs at http://localhost:8000/docs
```

### Quick Test

**Option 1: Use the Interactive Docs**
1. Open http://localhost:8000/docs in your browser
2. Click on `POST /api/v1/analyze`
3. Click "Try it out"
4. Paste your SQL and click "Execute"

**Option 2: Use curl**
```bash
curl -X POST "http://localhost:8000/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{"sql": "CREATE TABLE users (id INT, name VARCHAR(100));"}'
```

**Option 3: Use Python**
```python
import httpx

response = httpx.post(
    "http://localhost:8000/api/v1/analyze",
    json={"sql": "CREATE TABLE users (id INT, name VARCHAR(100));"}
)
print(response.json())
```

---

## Core Concepts

### 1. ParsedSchema

When you give Schemint SQL, it first parses it into a structured format:

```python
# Input SQL
sql = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);
"""

# Becomes ParsedSchema
{
    "tables": [
        {
            "name": "users",
            "columns": [
                {"name": "id", "data_type": "INT", "is_primary_key": True},
                {"name": "email", "data_type": "VARCHAR", "nullable": False}
            ],
            "primary_key": ["id"],
            "foreign_keys": [],
            "indexes": []
        }
    ],
    "database_type": "mysql"
}
```

### 2. Issues

Problems found during analysis:

```python
{
    "severity": "critical",       # critical | warning | suggestion
    "category": "missing_primary_key",
    "title": "Table 'orders' has no primary key",
    "description": "Every table should have a primary key...",
    "table_name": "orders",
    "column_name": None,
    "impact": "Cannot uniquely identify rows",
    "fix_script": "ALTER TABLE orders ADD PRIMARY KEY (id);"
}
```

**Severity Levels:**

| Level | Icon | Meaning | Score Impact |
|-------|------|---------|--------------|
| CRITICAL | `!!` | Will cause data corruption or failures | -15 points |
| WARNING | `!` | Performance or integrity issues | -5 points |
| SUGGESTION | `~` | Best practice recommendations | -2 points |

### 3. Project Context

Tell Schemint about YOUR project with a `schemint.yaml` file:

```yaml
project_name: "My E-Commerce App"

# Your current database schema
schema:
  tables:
    - name: orders
      columns:
        - name: legacy_status
          deprecated: true                    # Don't use this!
          deprecated_reason: "Use status_enum instead"
          renamed_to: status_enum

# Your project's SQL conventions
conventions:
  required_columns: [created_at, updated_at]  # All tables need these
  require_soft_delete: true                    # Need deleted_at column
  preferred_types:
    money: "DECIMAL(19,4)"                     # Never use FLOAT for money
```

### 4. Scoring

Analysis produces a score from 0-100:

```
Score Calculation:
  Start with 100 points
  - Each CRITICAL issue: -15 points
  - Each WARNING: -5 points
  - Each SUGGESTION: -2 points

Grades:
  A: 90-100 (Excellent)
  B: 80-89  (Good)
  C: 70-79  (Decent)
  D: 60-69  (Needs Work)
  F: 0-59   (Poor)
```

---

## API Reference

### POST /api/v1/analyze

Basic schema analysis.

**Request:**
```json
{
  "sql": "CREATE TABLE users (id INT PRIMARY KEY);",
  "database_type": "mysql"
}
```

**Query Parameters:**
- `use_ai=true` - Enable Claude AI analysis (requires CLAUDE_API_KEY)

**Response:**
```json
{
  "id": "ana_abc123",
  "score": {
    "total": 85,
    "grade": "B",
    "structural": 100,
    "performance": 80,
    "naming": 90,
    "best_practices": 70
  },
  "issues": [
    {
      "severity": "warning",
      "category": "missing_timestamps",
      "title": "Missing timestamp columns",
      "table_name": "users",
      "fix_script": "ALTER TABLE users ADD COLUMN created_at DATETIME;"
    }
  ],
  "good_practices": ["Has primary key"],
  "tables": [{"name": "users", "column_count": 1}],
  "ai_summary": "..."
}
```

### POST /api/v1/analyze/with-context

Context-aware analysis - provides project-specific feedback.

**Request:**
```json
{
  "sql": "CREATE TABLE orders (total FLOAT);",
  "database_type": "mysql",
  "project_context": {
    "project_name": "E-Commerce",
    "conventions": {
      "required_columns": ["created_at", "updated_at"],
      "require_soft_delete": true,
      "preferred_types": {"money": "DECIMAL(19,4)"}
    }
  }
}
```

### POST /api/v1/analyze/quick

Quick pass/fail check - perfect for CI/CD pipelines.

**Response:**
```json
{
  "score": 75,
  "grade": "C",
  "passed": true,
  "critical_count": 0,
  "warning_count": 2,
  "suggestion_count": 1
}
```

### POST /api/v1/validate-context

Validate your project context configuration.

**Request:** Your project context object

**Response:**
```json
{
  "project_name": "E-Commerce",
  "table_count": 5,
  "migration_count": 3,
  "deprecated_tables": [],
  "deprecated_columns": ["orders.legacy_status"],
  "has_conventions": true
}
```

---

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `CLAUDE_API_KEY` | Claude API key for AI features | None | No (AI disabled without it) |
| `CLAUDE_MODEL` | Claude model to use | `claude-sonnet-4-20250514` | No |
| `HOST` | Server host | `0.0.0.0` | No |
| `PORT` | Server port | `8000` | No |
| `ENV` | Environment | `development` | No |
| `DEBUG` | Enable debug mode | `false` | No |

### Project Context File (schemint.yaml)

Complete reference for all options:

```yaml
# ========================================
# PROJECT IDENTIFICATION
# ========================================
project_name: "My Project"          # Required
description: "What this project does"

# ========================================
# SCHEMA METADATA
# Document your current database schema
# ========================================
schema:
  database_type: mysql              # mysql, postgresql, sqlite
  version: "1.0.0"                  # Your schema version

  tables:
    - name: users
      description: "User accounts"
      primary_key: [id]
      estimated_rows: 100000        # For performance analysis

      columns:
        - name: id
          type: BIGINT
          description: "Auto-incrementing primary key"

        - name: email
          type: VARCHAR(255)
          indexed: true             # Has an index
          pii: true                 # Contains PII (personal info)

        - name: old_field
          type: VARCHAR(50)
          deprecated: true          # DEPRECATED - don't use!
          deprecated_reason: "Use new_field instead"
          deprecated_since: "v2.0"
          renamed_to: new_field     # The replacement

        - name: new_field
          type: VARCHAR(100)
          renamed_from: old_field   # This replaced old_field

# ========================================
# CONVENTIONS
# Your project's SQL rules
# ========================================
conventions:
  # Naming rules
  naming_conventions:
    case: snake_case                # snake_case, camelCase, PascalCase
    table_prefix: ""                # e.g., "tbl_"
    table_suffix: ""                # e.g., "_table"

  # Required columns (every table must have these)
  required_columns:
    - created_at
    - updated_at

  # Forbidden column names (never use these)
  forbidden_column_names:
    - type                          # Reserved word
    - data                          # Too generic

  # Forbidden data types
  forbidden_data_types:
    - FLOAT                         # Use DECIMAL for precision
    - DOUBLE

  # Preferred types for specific uses
  preferred_types:
    money: "DECIMAL(19,4)"
    percentage: "DECIMAL(5,4)"
    uuid: "CHAR(36)"

  # ID preferences
  preferred_id_type: BIGINT
  preferred_timestamp_type: DATETIME

  # Foreign key requirements
  fk_naming_pattern: "fk_{table}_{column}"
  require_fk_indexes: true          # Index FK columns
  require_cascade_actions: true     # Need ON DELETE/UPDATE

  # Soft delete
  require_soft_delete: true         # Need deleted_at column
  soft_delete_column: deleted_at

  # Multi-tenancy
  require_tenant_column: false
  tenant_column_name: tenant_id

# ========================================
# MIGRATION HISTORY (Optional)
# Can also be loaded from migrations/ folder
# ========================================
migrations:
  - version: "001"
    description: "Initial schema"
    timestamp: "2024-01-15T10:00:00Z"
    tables_affected: [users, orders]

  - version: "002"
    description: "Deprecate old_field"
    deprecated_columns:
      - users.old_field
    renamed_columns:
      users.old_field: users.new_field
```

---

## Examples

### Example 1: Basic Analysis (Python)

```python
from schemint.core.analyzer import analyze_sql

sql = """
CREATE TABLE products (
    id INT,
    name VARCHAR(100),
    price FLOAT
);
"""

result = analyze_sql(sql)

print(f"Score: {result.score.total}/100")
print(f"Grade: {result.score.grade}")
print(f"Issues: {len(result.issues)}")

for issue in result.issues:
    print(f"  [{issue.severity.value}] {issue.title}")
    if issue.fix_script:
        print(f"    Fix: {issue.fix_script}")
```

**Output:**
```
Score: 55/100
Grade: F
Issues: 3
  [critical] Table 'products' has no primary key
    Fix: ALTER TABLE products ADD PRIMARY KEY (id);
  [critical] Column 'price' uses FLOAT for money
    Fix: ALTER TABLE products MODIFY COLUMN price DECIMAL(19,4);
  [warning] Missing timestamp columns
    Fix: ALTER TABLE products ADD COLUMN created_at DATETIME;
```

### Example 2: Context-Aware Analysis

```python
from schemint.core.analyzer import analyze_sql
from schemint.core.context import load_context

# Define strict e-commerce context
context = load_context({
    "project_name": "E-Commerce Platform",
    "conventions": {
        "required_columns": ["created_at", "updated_at"],
        "require_soft_delete": True,
        "preferred_types": {"money": "DECIMAL(19,4)"}
    }
})

sql = "CREATE TABLE orders (id INT PRIMARY KEY, total FLOAT);"

# Analyze WITH context - finds more issues
result_with_context = analyze_sql(sql, project_context=context)
print(f"With Context: {len(result_with_context.issues)} issues")

# Analyze WITHOUT context - basic checks only
result_no_context = analyze_sql(sql)
print(f"No Context: {len(result_no_context.issues)} issues")
```

**Output:**
```
With Context: 5 issues
No Context: 2 issues
```

### Example 3: Using the REST API

```python
import httpx

# Basic analysis
response = httpx.post(
    "http://localhost:8000/api/v1/analyze",
    json={"sql": "CREATE TABLE users (id INT);"}
)
result = response.json()
print(f"Score: {result['score']['total']}")

# With context
response = httpx.post(
    "http://localhost:8000/api/v1/analyze/with-context",
    json={
        "sql": "CREATE TABLE users (id INT);",
        "project_context": {
            "project_name": "My App",
            "conventions": {
                "required_columns": ["created_at"]
            }
        }
    }
)
result = response.json()
print(f"Context-aware score: {result['score']['total']}")
```

### Example 4: Run the Demo Script

```bash
# Shows same SQL producing different results with different contexts
python examples/demo_context_aware.py
```

---

## How to Extend

### Adding a New Lint Rule

**File:** `src/schemint/core/analyzer/rule_analyzer.py`

```python
class RuleAnalyzer:
    def analyze(self, schema: ParsedSchema) -> tuple[list[Issue], list[str]]:
        issues = []
        good_practices = []

        for table in schema.tables:
            # Existing checks...
            issues.extend(self._check_primary_key(table))

            # ADD YOUR NEW CHECK HERE
            issues.extend(self._check_my_custom_rule(table))

        return issues, good_practices

    # ADD YOUR NEW METHOD
    def _check_my_custom_rule(self, table: Table) -> list[Issue]:
        """Check for my custom rule."""
        issues = []

        # Your logic here
        for column in table.columns:
            if column.name == "bad_name":
                issues.append(Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"Bad column name in {table.name}",
                    description="Don't use 'bad_name' as a column name",
                    table_name=table.name,
                    column_name=column.name,
                    fix_script=f"ALTER TABLE {table.name} RENAME COLUMN bad_name TO good_name;"
                ))

        return issues
```

### Adding a New Convention Type

**Step 1:** Add the field to `src/schemint/core/context/models.py`:

```python
class ProjectConventions(BaseModel):
    # Existing fields...

    # ADD YOUR NEW CONVENTION
    require_uuid_primary_keys: bool = Field(
        False,
        description="Require UUID instead of INT for primary keys"
    )
```

**Step 2:** Add the check to `src/schemint/core/context/conventions.py`:

```python
class ConventionChecker:
    def check(self, schema: ParsedSchema) -> list[Issue]:
        issues = []

        # Existing checks...

        # ADD YOUR NEW CHECK
        issues.extend(self._check_uuid_primary_keys(schema))

        return issues

    def _check_uuid_primary_keys(self, schema: ParsedSchema) -> list[Issue]:
        """Check that primary keys use UUID."""
        if not self.conventions.require_uuid_primary_keys:
            return []

        issues = []
        for table in schema.tables:
            for col in table.columns:
                if col.is_primary_key and "UUID" not in col.raw_type.upper():
                    issues.append(Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.WRONG_DATA_TYPE,
                        title=f"Primary key should use UUID",
                        description=f"Column {col.name} should be UUID type",
                        table_name=table.name,
                        column_name=col.name,
                    ))

        return issues
```

### Modifying AI Prompts

**File:** `src/schemint/services/claude.py`

```python
def _build_prompt(self, schema, app_type, project_context):
    # Modify what you send to Claude
    return f"""
    You are a database expert analyzing SQL schemas.

    # ADD YOUR CUSTOM INSTRUCTIONS
    Pay special attention to:
    - Security vulnerabilities
    - GDPR compliance
    - Your custom requirements here

    Schema to analyze:
    {self._schema_to_sql(schema)}

    Project context:
    {self._build_project_context_section(project_context)}

    Respond with JSON...
    """
```

### Adding a New API Endpoint

**File:** `src/schemint/api/v1/analysis.py`

```python
from fastapi import APIRouter

router = APIRouter()

# ADD YOUR NEW ENDPOINT
@router.post("/my-custom-endpoint")
async def my_custom_endpoint(
    request: MyRequest,
    some_option: bool = Query(False),
) -> MyResponse:
    """
    My custom endpoint description.

    Does something special with the SQL.
    """
    # Your logic here
    result = do_something(request.sql)

    return MyResponse(data=result)
```

---

## Troubleshooting

### Common Issues

**1. "CLAUDE_API_KEY not set"**
```bash
# Add to your .env file:
CLAUDE_API_KEY=sk-ant-your-key-here

# Get a key from: https://console.anthropic.com/
```

**2. "anthropic not installed"**
```bash
# Install AI dependencies
pip install anthropic

# Or install with the [ai] extra
pip install -e ".[ai]"
```

**3. "Failed to parse SQL"**
- Check your SQL syntax is valid
- Ensure using supported database type: `mysql`, `postgresql`, or `sqlite`
- Check for unsupported SQL features

**4. "Module 'schemint' not found"**
```bash
# Make sure you installed in development mode
pip install -e .

# Check you're in the right virtual environment
which python  # Should show your venv path
```

**5. Context file not loading**
- Ensure file is valid YAML/JSON
- Check file path is correct
- For YAML, install: `pip install pyyaml`

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_context_aware_analysis.py

# Run with coverage report
pytest --cov=schemint --cov-report=html

# Run only fast tests (skip slow/integration)
pytest -m "not slow"
```

### Debug Mode

```bash
# Enable debug logging
DEBUG=true uvicorn schemint.main:app --reload

# Or set in .env
DEBUG=true
```

### Getting Help

- **Check examples:** `examples/` directory has working code
- **Run demo:** `python examples/demo_context_aware.py`
- **API docs:** http://localhost:8000/docs (when server running)
- **Issues:** https://github.com/YOUR_USERNAME/schemint/issues

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests: `pytest`
5. Run linting: `ruff check .`
6. Commit: `git commit -m "Add my feature"`
7. Push: `git push origin feature/my-feature`
8. Open a Pull Request

---

## License

MIT License - see LICENSE file for details.

---

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- SQL parsing by [sqlparse](https://github.com/andialbrecht/sqlparse)
- AI powered by [Claude](https://anthropic.com/)
