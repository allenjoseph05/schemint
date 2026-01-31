# Contributing to Schemint

Thank you for your interest in contributing to Schemint! This document provides guidelines and instructions for contributing.

## Code of Conduct

Please be respectful and constructive in all interactions. We welcome contributors of all backgrounds and experience levels.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git

### Setup Development Environment

```bash
# Fork and clone the repository
git clone https://github.com/allenjoseph05/schemint.git
cd schemint

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies
pip install -e ".[all]"

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test file
pytest tests/unit/test_parser.py -v
```

### Code Quality

```bash
# Run linter
make lint

# Auto-fix linting issues
make lint-fix

# Run type checker
make typecheck

# Run all checks
make check
```

## How to Contribute

### Reporting Bugs

1. Check if the bug has already been reported in [Issues](https://github.com/YOUR_USERNAME/schemint/issues)
2. If not, create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - SQL schema that triggers the bug (if applicable)

### Suggesting Features

1. Check existing issues and discussions
2. Create a new issue with the `enhancement` label
3. Describe the feature and its use case

### Submitting Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Add tests for new functionality
5. Run `make check` to ensure all checks pass
6. Commit with clear messages: `git commit -m "feat: add new rule for X"`
7. Push to your fork: `git push origin feature/my-feature`
8. Open a Pull Request

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance tasks

Examples:
```
feat(rules): add check for missing NOT NULL constraints
fix(parser): handle quoted table names correctly
docs: update API documentation
test(analyzer): add tests for scoring logic
```

## Adding New Rules

To add a new schema analysis rule:

1. Add the rule logic in `src/schemint/core/analyzer/rule_analyzer.py`
2. Add the issue category in `src/schemint/models/issue.py` (if needed)
3. Add tests in `tests/unit/test_analyzer.py`
4. Update documentation

Example:

```python
# In rule_analyzer.py
def _check_my_new_rule(self, table: Table) -> list[Issue]:
    issues = []
    # Your rule logic here
    if some_condition:
        issues.append(
            Issue(
                severity=IssueSeverity.WARNING,
                category=IssueCategory.MY_CATEGORY,
                title="Issue title",
                description="Detailed explanation",
                table_name=table.name,
                fix_script="ALTER TABLE ...",
            )
        )
    return issues
```

## Project Structure

```
src/schemint/
├── api/            # FastAPI routes
├── core/           
│   ├── parser/     # SQL parsing logic
│   ├── analyzer/   # Analysis engine
│   └── rules/      # Rule definitions
├── models/         # Pydantic models
└── services/       # External services
```

## Questions?

Feel free to open an issue or discussion for any questions!
