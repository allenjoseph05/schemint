"""CHECK constraint extraction from DDL — extracted from snapshot.py.

Uses regex on raw DDL since sqlparse doesn't expose CHECK bodies.
"""

from __future__ import annotations

import re


def extract_check_constraints(sql: str) -> dict[str, list[str]]:
    """Extract CHECK constraints from DDL that the parser skips.

    Returns a dict of {lowercase_table_name: [check_expression, ...]}.
    Handles both inline column CHECK and table-level CHECK constraints.
    """
    result: dict[str, list[str]] = {}

    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\(",
        re.IGNORECASE,
    )

    for match in table_pattern.finditer(sql):
        table_name = match.group(1).lower()
        start = match.end()

        # Find the matching closing paren for this CREATE TABLE
        depth = 1
        pos = start
        while pos < len(sql) and depth > 0:
            if sql[pos] == "(":
                depth += 1
            elif sql[pos] == ")":
                depth -= 1
            pos += 1

        table_body = sql[start:pos - 1] if depth == 0 else sql[start:]

        # Extract CHECK(...) expressions from the table body
        check_pattern = re.compile(r"CHECK\s*\(", re.IGNORECASE)
        checks: list[str] = []

        for check_match in check_pattern.finditer(table_body):
            check_start = check_match.end()
            paren_depth = 1
            check_pos = check_start
            while check_pos < len(table_body) and paren_depth > 0:
                if table_body[check_pos] == "(":
                    paren_depth += 1
                elif table_body[check_pos] == ")":
                    paren_depth -= 1
                check_pos += 1

            if paren_depth == 0:
                check_expr = table_body[check_start:check_pos - 1].strip()
                if check_expr:
                    checks.append(check_expr)

        if checks:
            result[table_name] = checks

    return result
