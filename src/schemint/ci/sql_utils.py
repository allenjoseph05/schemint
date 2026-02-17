"""
SQL Parsing Utilities.

Shared SQL parsing module using sqlparse and ast.
Replaces hardcoded regex patterns with proper library-based parsing.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Any

import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DDL, DML, Keyword, Name, Punctuation

logger = logging.getLogger(__name__)


@dataclass
class SQLAnalysis:
    """Result of analyzing SQL content."""

    tables_added: list[str] = field(default_factory=list)
    tables_modified: list[str] = field(default_factory=list)
    tables_dropped: list[str] = field(default_factory=list)
    columns_added: list[str] = field(default_factory=list)
    columns_modified: list[str] = field(default_factory=list)
    columns_dropped: list[str] = field(default_factory=list)


@dataclass
class DangerousPattern:
    """A dangerous SQL pattern detected."""

    pattern_type: str  # blocking_migration, destructive_change, unsafe_migration
    severity: str  # critical, warning
    table_name: str
    column_name: str | None = None
    description: str = ""


def _strip_quotes(name: str) -> str:
    """Strip surrounding quotes/backticks/brackets from an identifier."""
    if len(name) >= 2 and ((name[0] == '"' and name[-1] == '"') or \
           (name[0] == '`' and name[-1] == '`') or \
           (name[0] == '[' and name[-1] == ']')):
        return name[1:-1]
    return name


def _extract_table_name(token: sqlparse.sql.Token) -> str:
    """
    Extract a clean table name from a token, handling schema-qualified names.

    For 'public.users', returns 'users'.
    For '"my_table"', returns 'my_table'.
    """
    name = token.get_real_name() if hasattr(token, 'get_real_name') else str(token)
    if name is None:
        name = str(token).strip()
    name = _strip_quotes(name)
    # Handle schema-qualified: if the token still contains a dot, take the last part
    if '.' in name:
        name = name.rsplit('.', 1)[-1]
        name = _strip_quotes(name)
    return name


def _get_next_meaningful(tokens: list[Any], start_idx: int) -> tuple[int, sqlparse.sql.Token | None]:
    """Get the next non-whitespace, non-comment token after start_idx."""
    for i in range(start_idx + 1, len(tokens)):
        tok = tokens[i]
        if tok.ttype not in (sqlparse.tokens.Whitespace, sqlparse.tokens.Newline,
                             sqlparse.tokens.Comment.Single, sqlparse.tokens.Comment.Multiline) and not tok.is_whitespace:
            return i, tok
    return -1, None


def analyze_sql_content(content: str) -> SQLAnalysis:
    """
    Parse SQL content with sqlparse and extract DDL operations.

    Handles:
    - CREATE TABLE (with IF NOT EXISTS, schema-qualified, quoted)
    - ALTER TABLE ... ADD COLUMN
    - DROP TABLE (with IF EXISTS)
    """
    result = SQLAnalysis()
    parsed = sqlparse.parse(content)

    for statement in parsed:
        _analyze_statement(statement, result)

    # Deduplicate
    result.tables_added = list(dict.fromkeys(result.tables_added))
    result.tables_modified = list(dict.fromkeys(result.tables_modified))
    result.tables_dropped = list(dict.fromkeys(result.tables_dropped))
    result.columns_added = list(dict.fromkeys(result.columns_added))

    return result


def _analyze_statement(statement: Statement, result: SQLAnalysis) -> None:
    """Analyze a single SQL statement."""
    # Flatten tokens to make scanning easier
    tokens: list[Any] = list(statement.flatten())  # type: ignore[no-untyped-call]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        tok_upper = str(tok).upper().strip()

        if tok.ttype is DDL:
            if tok_upper == "CREATE":
                _handle_create(tokens, i, result)
            elif tok_upper == "ALTER":
                _handle_alter(tokens, i, result)
            elif tok_upper == "DROP":
                _handle_drop(tokens, i, result)
        i += 1


def _skip_whitespace_and_keywords(tokens: list[Any], start: int, skip_words: set[str]) -> int:
    """Skip whitespace and specific keyword tokens.

    Handles sqlparse's multi-word keyword tokens like 'IF NOT EXISTS' and 'IF EXISTS'
    by checking if all words in the token are in skip_words.
    """
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok.is_whitespace or tok.ttype in (
            sqlparse.tokens.Whitespace, sqlparse.tokens.Newline,
            sqlparse.tokens.Comment.Single, sqlparse.tokens.Comment.Multiline,
        ):
            i += 1
            continue
        tok_upper = str(tok).upper().strip()
        if tok_upper in skip_words:
            i += 1
            continue
        # Handle multi-word keyword tokens like 'IF NOT EXISTS', 'IF EXISTS'
        if skip_words and tok.ttype is Keyword:
            words = tok_upper.split()
            if all(w in skip_words for w in words):
                i += 1
                continue
        break
    return i


_SQL_KEYWORDS = frozenset({
    'TABLE', 'IF', 'NOT', 'EXISTS', 'COLUMN', 'ADD', 'DROP', 'ALTER',
    'CREATE', 'SET', 'DEFAULT', 'NULL', 'PRIMARY', 'KEY', 'CONSTRAINT',
    'INDEX', 'UNIQUE', 'CHECK', 'REFERENCES', 'FOREIGN', 'CASCADE',
    'RESTRICT', 'ON', 'UPDATE', 'DELETE', 'INT', 'INTEGER', 'VARCHAR',
    'TEXT', 'BOOLEAN', 'BIGINT', 'SMALLINT', 'FLOAT', 'DOUBLE', 'DECIMAL',
    'NUMERIC', 'DATE', 'TIMESTAMP', 'SERIAL', 'RENAME', 'TO', 'SELECT',
    'INSERT', 'FROM', 'WHERE', 'VALUES', 'INTO',
})


def _is_sql_keyword(tok_str: str) -> bool:
    """Check if a token string is a SQL keyword (single or multi-word)."""
    upper = tok_str.upper().strip()
    # Single word check
    if upper in _SQL_KEYWORDS:
        return True
    # Multi-word check (e.g. 'IF NOT EXISTS', 'NOT NULL', 'IF EXISTS')
    return all(w in _SQL_KEYWORDS for w in upper.split())


def _read_identifier(tokens: list[Any], start: int) -> tuple[str, int]:
    """Read an identifier (possibly schema-qualified, possibly quoted) from flat tokens."""
    i = _skip_whitespace_and_keywords(tokens, start, set())
    if i >= len(tokens):
        return "", i

    parts: list[str] = []
    # Collect name parts separated by dots: schema.table or just table
    while i < len(tokens):
        tok = tokens[i]
        if tok.is_whitespace:
            break
        s = str(tok).strip()
        if not s:
            i += 1
            continue
        if tok.ttype is Punctuation and s == '.':
            parts.append('.')
            i += 1
            continue

        # Accept Name tokens and Literal.String.Symbol (quoted identifiers)
        is_name = tok.ttype in (Name, sqlparse.tokens.Literal.String.Symbol,
                                sqlparse.tokens.Name.Builtin)
        # Accept Keyword tokens only if they're not SQL reserved words
        # (some identifiers overlap with keywords, e.g. "status")
        is_non_reserved_keyword = (tok.ttype is Keyword and not _is_sql_keyword(s))

        if is_name or is_non_reserved_keyword:
            parts.append(_strip_quotes(s))
            i += 1
            # Check if next is a dot for schema.table
            if i < len(tokens) and str(tokens[i]).strip() == '.':
                continue
            break
        # Not an identifier token — stop
        break

    full_name = ''.join(parts)
    # For schema.table, take just the table part
    if '.' in full_name:
        full_name = full_name.rsplit('.', 1)[-1]

    return _strip_quotes(full_name), i


def _handle_create(tokens: list[Any], idx: int, result: SQLAnalysis) -> None:
    """Handle CREATE TABLE statement."""
    # Skip 'CREATE' and find 'TABLE'
    i = idx + 1
    i = _skip_whitespace_and_keywords(tokens, i, set())
    if i >= len(tokens):
        return

    tok_str = str(tokens[i]).upper().strip()
    if tok_str != "TABLE":
        return

    # Skip 'TABLE', then skip optional 'IF NOT EXISTS'
    i += 1
    i = _skip_whitespace_and_keywords(tokens, i, {"IF", "NOT", "EXISTS"})

    name, _ = _read_identifier(tokens, i)
    if name:
        result.tables_added.append(name)


def _handle_alter(tokens: list[Any], idx: int, result: SQLAnalysis) -> None:
    """Handle ALTER TABLE statement."""
    i = idx + 1
    i = _skip_whitespace_and_keywords(tokens, i, set())
    if i >= len(tokens):
        return

    tok_str = str(tokens[i]).upper().strip()
    if tok_str != "TABLE":
        return

    i += 1
    table_name, i = _read_identifier(tokens, i)
    if not table_name:
        return

    result.tables_modified.append(table_name)

    # Look for ADD COLUMN or DROP COLUMN after the table name
    while i < len(tokens):
        tok = tokens[i]
        tok_upper = str(tok).upper().strip()

        if tok_upper == "ADD":
            # Skip ADD, optional COLUMN keyword
            i += 1
            i = _skip_whitespace_and_keywords(tokens, i, {"COLUMN"})
            col_name, i = _read_identifier(tokens, i)
            if col_name:
                result.columns_added.append(f"{table_name}.{col_name}")
        elif tok_upper == "DROP":
            # Check for DROP COLUMN (not DROP TABLE etc.)
            next_i = _skip_whitespace_and_keywords(tokens, i + 1, set())
            if next_i < len(tokens) and str(tokens[next_i]).upper().strip() == "COLUMN":
                i = next_i + 1
                i = _skip_whitespace_and_keywords(tokens, i, set())
                col_name, i = _read_identifier(tokens, i)
                if col_name:
                    result.columns_dropped.append(f"{table_name}.{col_name}")
            else:
                i += 1
        else:
            i += 1


def _handle_drop(tokens: list[Any], idx: int, result: SQLAnalysis) -> None:
    """Handle DROP TABLE statement."""
    i = idx + 1
    i = _skip_whitespace_and_keywords(tokens, i, set())
    if i >= len(tokens):
        return

    tok_str = str(tokens[i]).upper().strip()
    if tok_str != "TABLE":
        return

    i += 1
    i = _skip_whitespace_and_keywords(tokens, i, {"IF", "EXISTS"})

    name, _ = _read_identifier(tokens, i)
    if name:
        result.tables_dropped.append(name)


def is_sql_content(content: str) -> bool:
    """
    Check if content contains valid SQL DDL/DML statements.

    Uses sqlparse to parse and check for statement types rather than
    simple keyword matching.
    """
    if not content or not content.strip():
        return False

    parsed = sqlparse.parse(content.strip())
    for statement in parsed:
        # Skip empty statements
        if not statement.tokens:
            continue
        stmt_str = str(statement).strip()
        if not stmt_str or stmt_str == ';':
            continue

        # Check if the statement starts with a DDL/DML keyword
        for token in statement.flatten():  # type: ignore[no-untyped-call]
            if token.is_whitespace:
                continue
            if token.ttype in (DDL, DML):
                return True
            # Some statements might be classified differently
            tok_upper = str(token).upper().strip()
            if tok_upper in ("CREATE", "ALTER", "DROP", "SELECT", "INSERT", "UPDATE", "DELETE"):
                return True
            # If first meaningful token isn't SQL, skip this statement
            break

    return False


def detect_dangerous_patterns(content: str) -> list[DangerousPattern]:
    """
    Detect dangerous SQL patterns using sqlparse token analysis.

    Detects:
    - ADD COLUMN with DEFAULT (blocking migration)
    - DROP COLUMN (destructive)
    - DROP TABLE (destructive)
    - ADD NOT NULL column without DEFAULT (unsafe migration)
    """
    patterns: list[DangerousPattern] = []
    parsed = sqlparse.parse(content)

    for statement in parsed:
        tokens: list[Any] = list(statement.flatten())  # type: ignore[no-untyped-call]
        _detect_dangerous_in_tokens(tokens, patterns)

    return patterns


def _detect_dangerous_in_tokens(tokens: list[Any], patterns: list[DangerousPattern]) -> None:
    """Detect dangerous patterns in flattened tokens."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        tok_upper = str(tok).upper().strip()

        if tok.ttype is DDL and tok_upper == "ALTER":
            i = _check_alter_dangers(tokens, i, patterns)
        elif tok.ttype is DDL and tok_upper == "DROP":
            i = _check_drop_table(tokens, i, patterns)
        else:
            i += 1


def _check_alter_dangers(tokens: list[Any], idx: int, patterns: list[DangerousPattern]) -> int:
    """Check ALTER TABLE for dangerous patterns."""
    i = idx + 1
    i = _skip_whitespace_and_keywords(tokens, i, set())
    if i >= len(tokens) or str(tokens[i]).upper().strip() != "TABLE":
        return i

    i += 1
    table_name, i = _read_identifier(tokens, i)
    if not table_name:
        return i

    # Scan for ADD or DROP operations
    while i < len(tokens):
        tok = tokens[i]
        tok_upper = str(tok).upper().strip()

        if tok_upper == "ADD":
            i += 1
            i = _skip_whitespace_and_keywords(tokens, i, {"COLUMN"})
            col_name, i = _read_identifier(tokens, i)
            if not col_name:
                continue

            # Scan rest of clause for DEFAULT and NOT NULL
            has_default = False
            has_not_null = False
            j = i
            while j < len(tokens):
                t_upper = str(tokens[j]).upper().strip()
                if t_upper in (';', ',') or (tokens[j].ttype is DDL):
                    break
                if t_upper == "DEFAULT":
                    has_default = True
                # Handle both combined 'NOT NULL' token and separate 'NOT' + 'NULL'
                if t_upper == "NOT NULL":
                    has_not_null = True
                elif t_upper == "NOT":
                    nj = _skip_whitespace_and_keywords(tokens, j + 1, set())
                    if nj < len(tokens) and str(tokens[nj]).upper().strip() == "NULL":
                        has_not_null = True
                j += 1

            if has_default:
                patterns.append(DangerousPattern(
                    pattern_type="blocking_migration",
                    severity="critical",
                    table_name=table_name,
                    column_name=col_name,
                    description=(
                        f"Adding column '{col_name}' with DEFAULT to table '{table_name}' "
                        "can cause a table lock on large tables in MySQL/PostgreSQL. "
                        "Consider adding the column without DEFAULT, then backfilling data."
                    ),
                ))

            if has_not_null and not has_default:
                patterns.append(DangerousPattern(
                    pattern_type="unsafe_migration",
                    severity="warning",
                    table_name=table_name,
                    column_name=col_name,
                    description=(
                        f"Adding NOT NULL column '{col_name}' to '{table_name}' "
                        "without a DEFAULT value will fail if the table has existing rows."
                    ),
                ))

            i = j

        elif tok_upper == "DROP":
            next_i = _skip_whitespace_and_keywords(tokens, i + 1, set())
            if next_i < len(tokens) and str(tokens[next_i]).upper().strip() == "COLUMN":
                i = next_i + 1
                i = _skip_whitespace_and_keywords(tokens, i, set())
                col_name, i = _read_identifier(tokens, i)
                if col_name:
                    patterns.append(DangerousPattern(
                        pattern_type="destructive_change",
                        severity="critical",
                        table_name=table_name,
                        column_name=col_name,
                        description=(
                            f"Dropping column '{col_name}' from table '{table_name}' "
                            "is a destructive operation that cannot be undone. "
                            "Ensure you have backups and have removed all code references first."
                        ),
                    ))
            else:
                i += 1
        elif tok_upper == ';':
            break
        else:
            i += 1

    return i


def _check_drop_table(tokens: list[Any], idx: int, patterns: list[DangerousPattern]) -> int:
    """Check for DROP TABLE."""
    i = idx + 1
    i = _skip_whitespace_and_keywords(tokens, i, set())
    if i >= len(tokens) or str(tokens[i]).upper().strip() != "TABLE":
        return i

    i += 1
    i = _skip_whitespace_and_keywords(tokens, i, {"IF", "EXISTS"})
    table_name, i = _read_identifier(tokens, i)
    if table_name:
        patterns.append(DangerousPattern(
            pattern_type="destructive_change",
            severity="critical",
            table_name=table_name,
            column_name=None,
            description=(
                f"Dropping table '{table_name}' will delete all data. "
                "Ensure you have backups and this is intentional."
            ),
        ))
    return i


def parse_alembic_migration(content: str) -> SQLAnalysis:
    """
    Parse Alembic migration file using Python AST.

    Finds op.create_table(), op.drop_table(), op.add_column() calls.
    """
    result = SQLAnalysis()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        logger.debug("Failed to parse Python content as AST")
        return result

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Check for op.xxx() calls
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue

        # Check it's called on 'op'
        if not (isinstance(func.value, ast.Name) and func.value.id == "op"):
            continue

        method = func.attr

        if method == "create_table" and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                result.tables_added.append(first_arg.value)

        elif method == "drop_table" and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                result.tables_dropped.append(first_arg.value)

        elif method == "add_column" and len(node.args) >= 2:
            table_arg = node.args[0]
            col_arg = node.args[1]
            table_name = None
            col_name = None

            if isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str):
                table_name = table_arg.value

            # The column arg is typically sa.Column('name', ...) or Column('name', ...)
            if isinstance(col_arg, ast.Call) and col_arg.args:
                first_col_arg = col_arg.args[0]
                if isinstance(first_col_arg, ast.Constant) and isinstance(first_col_arg.value, str):
                    col_name = first_col_arg.value

            if table_name and col_name:
                result.columns_added.append(f"{table_name}.{col_name}")

    return result


def parse_sqlalchemy_models(content: str) -> SQLAnalysis:
    """
    Parse SQLAlchemy model file using Python AST.

    Finds classes inheriting from Base and their __tablename__ assignments.
    """
    result = SQLAnalysis()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        logger.debug("Failed to parse Python content as AST")
        return result

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Check if any base class contains 'Base'
        inherits_base = False
        for base in node.bases:
            if (isinstance(base, ast.Name) and "Base" in base.id) or (isinstance(base, ast.Attribute) and "Base" in base.attr):
                inherits_base = True

        if not inherits_base:
            continue

        # Look for __tablename__ assignment
        tablename = None
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "__tablename__" and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                        tablename = item.value.value

        if tablename:
            result.tables_modified.append(tablename)
        else:
            # Fall back to class name if no __tablename__
            result.tables_modified.append(node.name)

    return result
