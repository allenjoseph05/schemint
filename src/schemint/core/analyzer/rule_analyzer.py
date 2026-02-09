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

# Columns ending with _id that are NOT foreign key indicators
FK_ID_EXCEPTIONS = frozenset([
    "id", "external_id", "uuid", "device_id", "session_id",
])

# Security-sensitive column names (without safe suffixes)
SECURITY_SENSITIVE_NAMES = frozenset([
    "password", "secret", "token", "api_key",
])

# Safe suffixes for security columns
SECURITY_SAFE_SUFFIXES = ("_hash", "_hashed", "_encrypted", "_digest")

# PII column name indicators
PII_INDICATORS = frozenset([
    "email", "ssn", "social_security", "phone", "phone_number",
    "address", "street_address", "date_of_birth", "dob",
    "credit_card", "card_number",
])

# PII encryption markers in column names
PII_ENCRYPTION_MARKERS = ("_encrypted", "_hash", "_hashed", "_masked", "_tokenized")

# Columns that should typically be NOT NULL
SHOULD_NOT_BE_NULL = frozenset([
    "name", "email", "username", "status", "type", "title",
])

# Soft delete column names
SOFT_DELETE_COLUMNS = frozenset([
    "deleted_at", "is_deleted", "soft_deleted",
])

# Multi-tenancy indicator columns
MULTI_TENANCY_COLUMNS = frozenset([
    "tenant_id", "organization_id", "org_id",
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
            issues.extend(self._check_missing_foreign_key(table))
            issues.extend(self._check_missing_constraint(table))
            issues.extend(self._check_inefficient_type(table))
            issues.extend(self._check_security_risk(table))
            issues.extend(self._check_pii_detected(table))
            issues.extend(self._check_soft_delete(table))
            issues.extend(self._check_missing_not_null(table))
            issues.extend(self._check_multi_tenancy(table))

            # Collect good practices
            good_practices.extend(self._find_good_practices(table))

        # Cross-table checks
        issues.extend(self._check_orphaned_foreign_keys(schema))

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

    def _check_missing_foreign_key(self, table: Table) -> list[Issue]:
        """Check for columns ending in _id that lack a FK constraint."""
        issues = []
        fk_columns = {fk.column.lower() for fk in table.foreign_keys}
        pk_set = {pk.lower() for pk in table.primary_key}

        for col in table.columns:
            col_lower = col.name.lower()
            if not col_lower.endswith("_id"):
                continue
            if col_lower in FK_ID_EXCEPTIONS:
                continue
            if col_lower in pk_set:
                continue
            if col_lower in fk_columns:
                continue
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MISSING_FOREIGN_KEY,
                    title=f"Column '{col.name}' looks like a FK but has no constraint",
                    description=(
                        f"Column '{table.name}.{col.name}' ends with '_id' suggesting a "
                        "foreign key relationship, but no FOREIGN KEY constraint is defined."
                    ),
                    table_name=table.name,
                    column_name=col.name,
                    impact="Referential integrity is not enforced by the database.",
                    fix_description="Add a FOREIGN KEY constraint",
                )
            )
        return issues

    def _check_orphaned_foreign_keys(self, schema: ParsedSchema) -> list[Issue]:
        """Check for FK references to tables not in the schema."""
        issues = []
        table_names = {t.name.lower() for t in schema.tables}

        for table in schema.tables:
            for fk in table.foreign_keys:
                if fk.references_table.lower() not in table_names:
                    issues.append(
                        Issue(
                            severity=IssueSeverity.WARNING,
                            category=IssueCategory.ORPHANED_FOREIGN_KEY,
                            title=f"FK references missing table '{fk.references_table}'",
                            description=(
                                f"Foreign key '{table.name}.{fk.column}' references "
                                f"'{fk.references_table}.{fk.references_column}' but table "
                                f"'{fk.references_table}' is not defined in this schema."
                            ),
                            table_name=table.name,
                            column_name=fk.column,
                            impact="The referenced table may not exist, causing migration failures.",
                        )
                    )
        return issues

    def _check_missing_constraint(self, table: Table) -> list[Issue]:
        """Check for columns missing common constraints."""
        issues = []
        for col in table.columns:
            col_lower = col.name.lower()

            # email without UNIQUE
            if col_lower == "email" and not col.is_unique:
                # Also check if it's in a unique index
                in_unique_idx = any(
                    col.name.lower() in [c.lower() for c in idx.columns]
                    for idx in table.indexes
                    if idx.is_unique
                )
                if not in_unique_idx:
                    issues.append(
                        Issue(
                            severity=IssueSeverity.SUGGESTION,
                            category=IssueCategory.MISSING_CONSTRAINT,
                            title=f"Email column '{col.name}' without UNIQUE constraint",
                            description=(
                                f"Column '{table.name}.{col.name}' stores email addresses "
                                "but has no UNIQUE constraint, allowing duplicate emails."
                            ),
                            table_name=table.name,
                            column_name=col.name,
                            fix_description="Add a UNIQUE constraint",
                            fix_script=(
                                f"ALTER TABLE {table.name} ADD UNIQUE ({col.name});"
                            ),
                        )
                    )

            # status/type without ENUM or CHECK
            if col_lower in ("status", "type"):
                if col.data_type.value not in ("ENUM",) and not col.enum_values:
                    issues.append(
                        Issue(
                            severity=IssueSeverity.SUGGESTION,
                            category=IssueCategory.MISSING_CONSTRAINT,
                            title=f"Column '{col.name}' without ENUM or CHECK constraint",
                            description=(
                                f"Column '{table.name}.{col.name}' likely has a finite set "
                                "of values but no ENUM type or CHECK constraint to enforce them."
                            ),
                            table_name=table.name,
                            column_name=col.name,
                            fix_description="Use ENUM type or add a CHECK constraint",
                        )
                    )
        return issues

    def _check_inefficient_type(self, table: Table) -> list[Issue]:
        """Check for inefficient data types."""
        issues = []
        for col in table.columns:
            col_lower = col.name.lower()
            col_type = col.data_type.value.upper()

            # INT for boolean-like columns (is_*, has_*)
            if (col_lower.startswith("is_") or col_lower.startswith("has_")) and col_type == "INT":
                issues.append(
                    Issue(
                        severity=IssueSeverity.SUGGESTION,
                        category=IssueCategory.INEFFICIENT_TYPE,
                        title=f"INT used for boolean column '{col.name}'",
                        description=(
                            f"Column '{table.name}.{col.name}' appears to be a boolean flag "
                            "but uses INT. BOOLEAN is more semantic and space-efficient."
                        ),
                        table_name=table.name,
                        column_name=col.name,
                        fix_description="Change to BOOLEAN type",
                        fix_script=(
                            f"ALTER TABLE {table.name} MODIFY COLUMN {col.name} BOOLEAN;"
                        ),
                    )
                )

            # TEXT for short string columns (name, status, title)
            if col_lower in ("name", "status", "title") and col_type == "TEXT":
                issues.append(
                    Issue(
                        severity=IssueSeverity.SUGGESTION,
                        category=IssueCategory.INEFFICIENT_TYPE,
                        title=f"TEXT used for short column '{col.name}'",
                        description=(
                            f"Column '{table.name}.{col.name}' likely holds short strings "
                            "but uses TEXT. VARCHAR with a length limit is more efficient."
                        ),
                        table_name=table.name,
                        column_name=col.name,
                        fix_description="Change to VARCHAR with appropriate length",
                        fix_script=(
                            f"ALTER TABLE {table.name} MODIFY COLUMN {col.name} VARCHAR(255);"
                        ),
                    )
                )
        return issues

    def _check_security_risk(self, table: Table) -> list[Issue]:
        """Check for security-sensitive columns stored in plaintext."""
        issues = []
        for col in table.columns:
            col_lower = col.name.lower()

            # Check if column name matches a sensitive pattern
            for sensitive in SECURITY_SENSITIVE_NAMES:
                if col_lower == sensitive or col_lower.endswith(f"_{sensitive}"):
                    # Check if it has a safe suffix
                    has_safe_suffix = any(
                        col_lower.endswith(suffix) for suffix in SECURITY_SAFE_SUFFIXES
                    )
                    if not has_safe_suffix:
                        issues.append(
                            Issue(
                                severity=IssueSeverity.CRITICAL,
                                category=IssueCategory.SECURITY_RISK,
                                title=f"Sensitive column '{col.name}' may store plaintext",
                                description=(
                                    f"Column '{table.name}.{col.name}' appears to store "
                                    "sensitive data without hashing or encryption. "
                                    "Consider using a hashed/encrypted variant."
                                ),
                                table_name=table.name,
                                column_name=col.name,
                                impact="Plaintext secrets can be leaked in breaches.",
                                fix_description=f"Rename to {col.name}_hash and store hashed values",
                            )
                        )
        return issues

    def _check_pii_detected(self, table: Table) -> list[Issue]:
        """Check for PII columns without encryption markers."""
        issues = []
        for col in table.columns:
            col_lower = col.name.lower()

            if col_lower in PII_INDICATORS:
                has_marker = any(
                    col_lower.endswith(m) for m in PII_ENCRYPTION_MARKERS
                )
                if not has_marker:
                    issues.append(
                        Issue(
                            severity=IssueSeverity.WARNING,
                            category=IssueCategory.PII_DETECTED,
                            title=f"PII column '{col.name}' without encryption",
                            description=(
                                f"Column '{table.name}.{col.name}' appears to contain "
                                "personally identifiable information (PII). Consider "
                                "encryption at rest or tokenization."
                            ),
                            table_name=table.name,
                            column_name=col.name,
                            impact="PII exposure in data breaches or unauthorized access.",
                            fix_description="Consider encryption at rest",
                        )
                    )
        return issues

    def _check_soft_delete(self, table: Table) -> list[Issue]:
        """Check for tables without soft delete columns."""
        issues = []
        col_names = {c.name.lower() for c in table.columns}
        has_soft_delete = bool(col_names & SOFT_DELETE_COLUMNS)

        if not has_soft_delete:
            issues.append(
                Issue(
                    severity=IssueSeverity.SUGGESTION,
                    category=IssueCategory.NO_SOFT_DELETE,
                    title=f"No soft delete on '{table.name}'",
                    description=(
                        f"Table '{table.name}' has no soft delete column "
                        "(deleted_at or is_deleted). Hard deletes lose audit history."
                    ),
                    table_name=table.name,
                    fix_description="Add a deleted_at TIMESTAMP column",
                    fix_script=(
                        f"ALTER TABLE {table.name} ADD COLUMN deleted_at TIMESTAMP NULL;"
                    ),
                )
            )
        return issues

    def _check_missing_not_null(self, table: Table) -> list[Issue]:
        """Check for commonly required columns that are nullable."""
        issues = []
        pk_set = {pk.lower() for pk in table.primary_key}

        for col in table.columns:
            col_lower = col.name.lower()
            if col_lower in pk_set:
                continue
            if col_lower in SHOULD_NOT_BE_NULL and col.nullable:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_NOT_NULL,
                        title=f"Column '{col.name}' is nullable but should not be",
                        description=(
                            f"Column '{table.name}.{col.name}' is commonly required "
                            "but allows NULL values."
                        ),
                        table_name=table.name,
                        column_name=col.name,
                        fix_description="Add NOT NULL constraint",
                        fix_script=(
                            f"ALTER TABLE {table.name} MODIFY COLUMN {col.name} "
                            f"{col.raw_type} NOT NULL;"
                        ),
                    )
                )
        return issues

    def _check_multi_tenancy(self, table: Table) -> list[Issue]:
        """Check for tables without multi-tenancy columns."""
        issues = []
        col_names = {c.name.lower() for c in table.columns}
        has_tenant = bool(col_names & MULTI_TENANCY_COLUMNS)

        if not has_tenant:
            issues.append(
                Issue(
                    severity=IssueSeverity.SUGGESTION,
                    category=IssueCategory.NO_MULTI_TENANCY,
                    title=f"No multi-tenancy on '{table.name}'",
                    description=(
                        f"Table '{table.name}' has no tenant_id or organization_id column. "
                        "If this is a SaaS app, consider multi-tenancy support."
                    ),
                    table_name=table.name,
                    fix_description="Add tenant_id column if applicable",
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
