"""Rule-based schema analyzer."""

from __future__ import annotations

from schemint.models.issue import Issue, IssueCategory, IssueSeverity
from schemint.models.schema import ParsedSchema, Table

# Reserved SQL words that shouldn't be used as column names
RESERVED_WORDS = frozenset([
    "add", "all", "alter", "and", "as", "asc", "between", "by", "case", "check",
    "column", "constraint", "create", "database", "default", "delete", "desc",
    "distinct", "drop", "exists", "foreign", "from", "group", "having", "in",
    "index", "inner", "insert", "into", "is", "join", "key", "left", "like",
    "limit", "not", "null", "on", "or", "order", "outer", "primary", "references",
    "right", "select", "set", "table", "then", "to", "union", "unique", "update",
    "user", "values", "when", "where", "password", "type", "status", "data",
])

# Words that suggest a foreign key relationship
FK_INDICATOR_WORDS = frozenset([
    "user", "customer", "product", "order", "category", "post", "comment",
    "author", "parent", "owner", "creator", "manager", "employee", "company",
    "project", "task", "item", "account", "profile", "group", "team", "member",
])

# Words that suggest money/currency columns
MONEY_WORDS = frozenset([
    "price", "cost", "amount", "total", "balance", "salary", "fee", "payment",
    "charge", "discount", "tax", "revenue", "profit", "budget", "rate",
])

# Words that suggest date/time columns
DATE_WORDS = frozenset([
    "date", "time", "created", "updated", "deleted", "modified", "timestamp",
    "_at", "started", "ended", "expired", "published", "scheduled",
])


class RuleAnalyzer:
    """Analyzes schema using predefined rules."""

    def analyze(self, schema: ParsedSchema) -> tuple[list[Issue], list[str]]:
        """
        Analyze schema and return issues and good practices.

        Returns:
            Tuple of (issues list, good practices list)
        """
        issues: list[Issue] = []
        good_practices: list[str] = []

        for table in schema.tables:
            # Run all checks
            issues.extend(self._check_primary_key(table))
            issues.extend(self._check_timestamps(table))
            issues.extend(self._check_columns(table))
            issues.extend(self._check_foreign_keys(table))
            issues.extend(self._check_naming(table))

            # Collect good practices
            good_practices.extend(self._find_good_practices(table))

        return issues, good_practices

    def _check_primary_key(self, table: Table) -> list[Issue]:
        """Check for primary key issues."""
        issues = []

        if not table.has_primary_key():
            issues.append(
                Issue(
                    severity=IssueSeverity.CRITICAL,
                    category=IssueCategory.MISSING_PRIMARY_KEY,
                    title=f"Missing Primary Key on '{table.name}'",
                    description=(
                        f"Table '{table.name}' has no primary key defined. "
                        "This makes it impossible to uniquely identify rows."
                    ),
                    table_name=table.name,
                    impact=(
                        "Without a primary key, you cannot reliably update or delete "
                        "specific records. ORMs and frameworks may not work correctly."
                    ),
                    example=(
                        "Imagine trying to delete a user's duplicate record - without "
                        "a primary key, you might delete the wrong one or all of them."
                    ),
                    fix_description="Add a primary key column",
                    fix_script=f"ALTER TABLE {table.name} ADD PRIMARY KEY (id);",
                )
            )

        return issues

    def _check_timestamps(self, table: Table) -> list[Issue]:
        """Check for timestamp columns."""
        issues = []

        if not table.has_timestamps():
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MISSING_TIMESTAMPS,
                    title=f"Missing Timestamps on '{table.name}'",
                    description=(
                        f"Table '{table.name}' has no created_at or updated_at columns. "
                        "This makes it hard to track when records were created or modified."
                    ),
                    table_name=table.name,
                    impact=(
                        "You won't be able to sort by creation date, audit changes, "
                        "or debug issues that depend on knowing when data changed."
                    ),
                    fix_description="Add timestamp columns",
                    fix_script=(
                        f"ALTER TABLE {table.name} ADD COLUMN created_at TIMESTAMP "
                        f"DEFAULT CURRENT_TIMESTAMP;\n"
                        f"ALTER TABLE {table.name} ADD COLUMN updated_at TIMESTAMP "
                        f"DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;"
                    ),
                )
            )

        return issues

    def _check_columns(self, table: Table) -> list[Issue]:
        """Check individual columns for issues."""
        issues = []

        for col in table.columns:
            col_type = str(col.data_type).upper()
            col_name_lower = col.name.lower()

            # Check FLOAT for money
            if "FLOAT" in col_type or "DOUBLE" in col_type:
                if any(word in col_name_lower for word in MONEY_WORDS):
                    issues.append(
                        Issue(
                            severity=IssueSeverity.CRITICAL,
                            category=IssueCategory.WRONG_DATA_TYPE,
                            title=f"FLOAT used for money column '{col.name}'",
                            description=(
                                f"Column '{table.name}.{col.name}' uses {col_type} which "
                                "causes rounding errors with monetary values."
                            ),
                            table_name=table.name,
                            column_name=col.name,
                            impact=(
                                "FLOAT arithmetic is imprecise. 0.1 + 0.2 might equal "
                                "0.30000000000000004. Over millions of transactions, "
                                "errors accumulate to significant amounts."
                            ),
                            example="Your accounting reports won't balance correctly.",
                            fix_description="Use DECIMAL for exact precision",
                            fix_script=(
                                f"ALTER TABLE {table.name} MODIFY COLUMN {col.name} "
                                f"DECIMAL(10,2) NOT NULL;"
                            ),
                        )
                    )

            # Check VARCHAR for dates
            if "VARCHAR" in col_type or "CHAR" in col_type or "TEXT" in col_type:
                if any(word in col_name_lower for word in DATE_WORDS):
                    issues.append(
                        Issue(
                            severity=IssueSeverity.WARNING,
                            category=IssueCategory.WRONG_DATA_TYPE,
                            title=f"String used for date column '{col.name}'",
                            description=(
                                f"Column '{table.name}.{col.name}' appears to store dates "
                                f"but uses {col_type}. Use TIMESTAMP or DATE instead."
                            ),
                            table_name=table.name,
                            column_name=col.name,
                            impact=(
                                "You can't sort by date correctly, do date arithmetic, "
                                "or prevent invalid dates from being stored."
                            ),
                            fix_description="Change to TIMESTAMP or DATE type",
                            fix_script=(
                                f"ALTER TABLE {table.name} MODIFY COLUMN {col.name} TIMESTAMP;"
                            ),
                        )
                    )

            # Check for reserved words
            if col_name_lower in RESERVED_WORDS:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.RESERVED_WORD,
                        title=f"Reserved word used as column name '{col.name}'",
                        description=(
                            f"Column '{table.name}.{col.name}' uses a SQL reserved word. "
                            "This may cause issues in queries and requires quoting."
                        ),
                        table_name=table.name,
                        column_name=col.name,
                        impact=(
                            "Queries will fail or require backticks/quotes around the "
                            "column name, making code more error-prone."
                        ),
                        fix_description=f"Consider renaming to '{col.name}_value' or similar",
                    )
                )

        return issues

    def _check_foreign_keys(self, table: Table) -> list[Issue]:
        """Check foreign key related issues."""
        issues = []

        # Check for columns that look like FKs but aren't defined as such
        for col in table.columns:
            col_name_lower = col.name.lower()

            # Check if column looks like a FK but isn't
            if col_name_lower in FK_INDICATOR_WORDS:
                issues.append(
                    Issue(
                        severity=IssueSeverity.SUGGESTION,
                        category=IssueCategory.NAMING_CONVENTION,
                        title=f"Foreign key column should end with '_id'",
                        description=(
                            f"Column '{table.name}.{col.name}' appears to be a foreign key "
                            f"but doesn't end with '_id'. Consider renaming to '{col.name}_id'."
                        ),
                        table_name=table.name,
                        column_name=col.name,
                        fix_description=f"Rename to {col.name}_id",
                    )
                )

        # Check existing foreign keys
        for fk in table.foreign_keys:
            # Check for missing ON DELETE action
            if not fk.on_delete:
                issues.append(
                    Issue(
                        severity=IssueSeverity.SUGGESTION,
                        category=IssueCategory.MISSING_CASCADE,
                        title=f"No ON DELETE action for foreign key",
                        description=(
                            f"Foreign key '{table.name}.{fk.column}' -> "
                            f"'{fk.references_table}.{fk.references_column}' has no "
                            "ON DELETE action defined."
                        ),
                        table_name=table.name,
                        column_name=fk.column,
                        impact=(
                            "Deleting referenced rows will fail or leave orphaned data "
                            "depending on database defaults."
                        ),
                        fix_description="Add ON DELETE CASCADE or ON DELETE SET NULL",
                    )
                )

            # Check for missing index on FK column
            fk_indexed = any(fk.column in idx.columns for idx in table.indexes)
            fk_is_pk = fk.column in table.primary_key
            if not fk_indexed and not fk_is_pk:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_INDEX,
                        title=f"Missing index on foreign key '{fk.column}'",
                        description=(
                            f"Foreign key '{table.name}.{fk.column}' has no index. "
                            "This will cause slow JOIN operations."
                        ),
                        table_name=table.name,
                        column_name=fk.column,
                        impact="JOINs on this column will scan the entire table.",
                        fix_script=(
                            f"CREATE INDEX idx_{table.name}_{fk.column} "
                            f"ON {table.name}({fk.column});"
                        ),
                    )
                )

        return issues

    def _check_naming(self, table: Table) -> list[Issue]:
        """Check naming conventions."""
        issues = []

        # Check table name
        if table.name.upper() in RESERVED_WORDS:
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.RESERVED_WORD,
                    title=f"Reserved word used as table name '{table.name}'",
                    description=(
                        f"Table '{table.name}' uses a SQL reserved word as its name."
                    ),
                    table_name=table.name,
                    impact="Queries require quoting the table name, error-prone.",
                )
            )

        return issues

    def _find_good_practices(self, table: Table) -> list[str]:
        """Identify good practices in the schema."""
        practices = []

        if table.has_primary_key():
            practices.append(f"✓ '{table.name}' has a primary key")

        if table.has_timestamps():
            practices.append(f"✓ '{table.name}' has timestamp columns")

        if table.foreign_keys:
            practices.append(
                f"✓ '{table.name}' has {len(table.foreign_keys)} foreign key(s) defined"
            )

        if table.indexes:
            practices.append(f"✓ '{table.name}' has {len(table.indexes)} index(es)")

        return practices
