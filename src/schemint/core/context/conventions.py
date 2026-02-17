"""Convention checker for enforcing project-specific SQL rules."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from schemint.models.issue import Issue, IssueCategory, IssueSeverity

if TYPE_CHECKING:
    from schemint.core.context.models import ProjectContext, ProjectConventions
    from schemint.models.schema import Column, ParsedSchema, Table


class ConventionChecker:
    """Checks SQL schemas against project conventions."""

    def __init__(self, conventions: ProjectConventions) -> None:
        self.conventions = conventions

    def check(self, schema: ParsedSchema) -> list[Issue]:
        """
        Check schema against project conventions.

        Args:
            schema: Parsed SQL schema

        Returns:
            List of issues found
        """
        issues: list[Issue] = []

        for table in schema.tables:
            # Check table naming
            issues.extend(self._check_naming(table.name, "table"))

            # Check required columns
            issues.extend(self._check_required_columns(table))

            # Check forbidden column names
            issues.extend(self._check_forbidden_columns(table))

            # Check column naming and types
            for col in table.columns:
                issues.extend(self._check_naming(col.name, "column", table.name))
                issues.extend(self._check_forbidden_types(col, table.name))
                issues.extend(self._check_preferred_types(col, table.name))

            # Check FK conventions
            issues.extend(self._check_fk_conventions(table))

            # Check soft delete requirement
            issues.extend(self._check_soft_delete(table))

            # Check tenant column requirement
            issues.extend(self._check_tenant_column(table))

        return issues

    def _check_naming(
        self,
        name: str,
        element_type: str,
        table_name: str | None = None,
    ) -> list[Issue]:
        """Check naming convention compliance."""
        issues = []
        conv = self.conventions.naming_conventions

        # Get expected case style
        case_key = f"{element_type}_case"
        expected_case = conv.get(case_key, conv.get("case", "snake_case"))

        if expected_case == "snake_case" and not self._is_snake_case(name):
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"{element_type.capitalize()} name violates snake_case convention",
                    description=f"'{name}' should use snake_case naming",
                    table_name=table_name or name,
                    column_name=name if element_type == "column" else None,
                    impact="Inconsistent naming makes code harder to maintain",
                    fix_description=f"Rename to {self._to_snake_case(name)}",
                )
            )
        elif expected_case == "camelCase" and not self._is_camel_case(name):
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"{element_type.capitalize()} name violates camelCase convention",
                    description=f"'{name}' should use camelCase naming",
                    table_name=table_name or name,
                    column_name=name if element_type == "column" else None,
                    impact="Inconsistent naming makes code harder to maintain",
                )
            )
        elif expected_case == "PascalCase" and not self._is_pascal_case(name):
            issues.append(
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"{element_type.capitalize()} name violates PascalCase convention",
                    description=f"'{name}' should use PascalCase naming",
                    table_name=table_name or name,
                    column_name=name if element_type == "column" else None,
                    impact="Inconsistent naming makes code harder to maintain",
                )
            )

        # Check prefix/suffix conventions
        prefix_key = f"{element_type}_prefix"
        if prefix_key in conv and not name.startswith(conv[prefix_key]):
            issues.append(
                Issue(
                    severity=IssueSeverity.SUGGESTION,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"{element_type.capitalize()} missing required prefix",
                    description=f"'{name}' should start with '{conv[prefix_key]}'",
                    table_name=table_name or name,
                    column_name=name if element_type == "column" else None,
                )
            )

        suffix_key = f"{element_type}_suffix"
        if suffix_key in conv and not name.endswith(conv[suffix_key]):
            issues.append(
                Issue(
                    severity=IssueSeverity.SUGGESTION,
                    category=IssueCategory.NAMING_CONVENTION,
                    title=f"{element_type.capitalize()} missing required suffix",
                    description=f"'{name}' should end with '{conv[suffix_key]}'",
                    table_name=table_name or name,
                    column_name=name if element_type == "column" else None,
                )
            )

        return issues

    def _check_required_columns(self, table: Table) -> list[Issue]:
        """Check that required columns are present."""
        from schemint.models.schema import Table

        if not isinstance(table, Table):
            return []

        issues = []
        table_cols = [c.name.lower() for c in table.columns]

        for required in self.conventions.required_columns:
            if required.lower() not in table_cols:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_TIMESTAMPS
                        if required in ("created_at", "updated_at")
                        else IssueCategory.OTHER,
                        title=f"Missing required column: {required}",
                        description=f"Table '{table.name}' is missing required column '{required}'",
                        table_name=table.name,
                        impact="Missing required columns violate project standards",
                        fix_script=f"ALTER TABLE {table.name} ADD COLUMN {required} {self._get_column_type(required)};",
                    )
                )

        return issues

    def _check_forbidden_columns(self, table: Table) -> list[Issue]:
        """Check for forbidden column names."""
        from schemint.models.schema import Table

        if not isinstance(table, Table):
            return []

        issues = []

        for col in table.columns:
            if col.name.lower() in [f.lower() for f in self.conventions.forbidden_column_names]:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.NAMING_CONVENTION,
                        title=f"Forbidden column name: {col.name}",
                        description=f"Column '{col.name}' in table '{table.name}' uses a forbidden name",
                        table_name=table.name,
                        column_name=col.name,
                        impact="Using forbidden column names can cause issues or confusion",
                    )
                )

        return issues

    def _check_forbidden_types(self, col: Column, table_name: str) -> list[Issue]:
        """Check for forbidden data types."""
        from schemint.models.schema import Column

        if not isinstance(col, Column):
            return []

        issues = []

        for forbidden in self.conventions.forbidden_data_types:
            if forbidden.upper() in col.raw_type.upper():
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.WRONG_DATA_TYPE,
                        title=f"Forbidden data type: {forbidden}",
                        description=f"Column '{col.name}' uses forbidden type '{col.raw_type}'",
                        table_name=table_name,
                        column_name=col.name,
                        impact="Using forbidden data types violates project standards",
                    )
                )

        return issues

    def _check_preferred_types(self, col: Column, table_name: str) -> list[Issue]:
        """Check that columns use preferred types for specific purposes."""
        from schemint.models.schema import Column

        if not isinstance(col, Column):
            return []

        issues = []
        name_lower = col.name.lower()

        # Check money columns
        if any(kw in name_lower for kw in ["price", "cost", "amount", "total", "balance", "money"]):
            preferred = self.conventions.preferred_types.get("money", "DECIMAL(19,4)")
            if "DECIMAL" not in col.raw_type.upper() and "NUMERIC" not in col.raw_type.upper():
                issues.append(
                    Issue(
                        severity=IssueSeverity.CRITICAL,
                        category=IssueCategory.WRONG_DATA_TYPE,
                        title=f"Money column should use {preferred}",
                        description=f"Column '{col.name}' appears to store money but uses {col.raw_type}",
                        table_name=table_name,
                        column_name=col.name,
                        impact="Using FLOAT/DOUBLE for money causes precision loss",
                        fix_script=f"ALTER TABLE {table_name} MODIFY COLUMN {col.name} {preferred};",
                    )
                )

        # Check ID columns
        if col.name.lower() == "id" or col.name.lower().endswith("_id"):
            preferred_id = self.conventions.preferred_id_type
            if (
                col.data_type.value not in ["INT", "BIGINT"]
                and "SERIAL" not in col.raw_type.upper()
                and "UUID" not in col.raw_type.upper()
            ):
                issues.append(
                    Issue(
                        severity=IssueSeverity.SUGGESTION,
                        category=IssueCategory.WRONG_DATA_TYPE,
                        title=f"ID column should use {preferred_id}",
                        description=f"Column '{col.name}' is an ID but uses {col.raw_type}",
                        table_name=table_name,
                        column_name=col.name,
                    )
                )

        return issues

    def _check_fk_conventions(self, table: Table) -> list[Issue]:
        """Check foreign key conventions."""
        from schemint.models.schema import Table

        if not isinstance(table, Table):
            return []

        issues = []

        for fk in table.foreign_keys:
            # Check cascade actions
            if self.conventions.require_cascade_actions and not fk.on_delete:
                issues.append(
                    Issue(
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MISSING_CASCADE,
                        title="Foreign key missing ON DELETE action",
                        description=f"FK on '{fk.column}' should have ON DELETE action",
                        table_name=table.name,
                        column_name=fk.column,
                        fix_script=f"ALTER TABLE {table.name} DROP FOREIGN KEY {fk.name}; "
                        f"ALTER TABLE {table.name} ADD FOREIGN KEY ({fk.column}) "
                        f"REFERENCES {fk.references_table}({fk.references_column}) ON DELETE CASCADE;",
                    )
                )

            # Check FK naming pattern
            if self.conventions.fk_naming_pattern and fk.name:
                expected = self.conventions.fk_naming_pattern.format(
                    table=table.name,
                    column=fk.column,
                    ref_table=fk.references_table,
                )
                if fk.name != expected:
                    issues.append(
                        Issue(
                            severity=IssueSeverity.SUGGESTION,
                            category=IssueCategory.NAMING_CONVENTION,
                            title="FK name doesn't match convention",
                            description=f"FK '{fk.name}' should be named '{expected}'",
                            table_name=table.name,
                            column_name=fk.column,
                        )
                    )

        return issues

    def _check_soft_delete(self, table: Table) -> list[Issue]:
        """Check soft delete column requirement."""
        from schemint.models.schema import Table

        if not isinstance(table, Table):
            return []

        if not self.conventions.require_soft_delete:
            return []

        col_name = self.conventions.soft_delete_column
        if not table.has_column(col_name):
            return [
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NO_SOFT_DELETE,
                    title="Missing soft delete column",
                    description=f"Table '{table.name}' should have '{col_name}' column for soft deletes",
                    table_name=table.name,
                    impact="Hard deletes can cause data loss and referential integrity issues",
                    fix_script=f"ALTER TABLE {table.name} ADD COLUMN {col_name} DATETIME NULL;",
                )
            ]

        return []

    def _check_tenant_column(self, table: Table) -> list[Issue]:
        """Check tenant column requirement."""
        from schemint.models.schema import Table

        if not isinstance(table, Table):
            return []

        if not self.conventions.require_tenant_column:
            return []

        col_name = self.conventions.tenant_column_name
        if not table.has_column(col_name):
            return [
                Issue(
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.NO_MULTI_TENANCY,
                    title="Missing tenant isolation column",
                    description=f"Table '{table.name}' should have '{col_name}' for multi-tenancy",
                    table_name=table.name,
                    impact="Without tenant isolation, data leakage between tenants is possible",
                    fix_script=f"ALTER TABLE {table.name} ADD COLUMN {col_name} BIGINT NOT NULL;",
                )
            ]

        return []

    def _is_snake_case(self, name: str) -> bool:
        """Check if name is snake_case."""
        return bool(re.match(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", name))

    def _is_camel_case(self, name: str) -> bool:
        """Check if name is camelCase."""
        return bool(re.match(r"^[a-z][a-zA-Z0-9]*$", name))

    def _is_pascal_case(self, name: str) -> bool:
        """Check if name is PascalCase."""
        return bool(re.match(r"^[A-Z][a-zA-Z0-9]*$", name))

    def _to_snake_case(self, name: str) -> str:
        """Convert name to snake_case."""
        # Handle camelCase and PascalCase
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    def _get_column_type(self, col_name: str) -> str:
        """Get default column type for a required column."""
        name_lower = col_name.lower()
        if "created" in name_lower or "updated" in name_lower:
            return self.conventions.preferred_timestamp_type + " NOT NULL DEFAULT CURRENT_TIMESTAMP"
        if "deleted" in name_lower:
            return self.conventions.preferred_timestamp_type + " NULL"
        if "tenant" in name_lower:
            return self.conventions.preferred_id_type + " NOT NULL"
        return "VARCHAR(255)"


class DeprecationChecker:
    """Checks SQL for usage of deprecated schema elements."""

    def __init__(self, context: ProjectContext) -> None:
        self.context = context

    def check(self, schema: ParsedSchema) -> list[Issue]:
        """
        Check schema for deprecated element usage.

        Args:
            schema: Parsed SQL schema

        Returns:
            List of issues for deprecated usage
        """
        issues: list[Issue] = []

        if not self.context.schema_metadata:
            return issues

        deprecated = self.context.get_deprecated_elements()
        rename_map = self.context.get_column_rename_map()

        for table in schema.tables:
            # Check if table name matches a deprecated table
            if table.name.lower() in [t.lower() for t in deprecated["tables"]]:
                dep_info = self.context.check_deprecated_usage(table.name)
                issues.append(
                    Issue(
                        severity=IssueSeverity.CRITICAL,
                        category=IssueCategory.OTHER,
                        title=f"Query uses deprecated table: {table.name}",
                        description=dep_info.get("reason", "This table is deprecated")
                        if dep_info
                        else "This table is deprecated",
                        table_name=table.name,
                        impact="Using deprecated tables may cause issues in future versions",
                        fix_description=f"Use table '{dep_info.get('renamed_to')}' instead"
                        if dep_info and dep_info.get("renamed_to")
                        else "Remove usage of deprecated table",
                    )
                )

            # Check columns
            for col in table.columns:
                col_key = f"{table.name}.{col.name}".lower()

                # Check if column is deprecated
                if col_key in [c.lower() for c in deprecated["columns"]]:
                    dep_info = self.context.check_deprecated_usage(table.name, col.name)
                    new_name = dep_info.get("renamed_to") if dep_info else None

                    issues.append(
                        Issue(
                            severity=IssueSeverity.WARNING,
                            category=IssueCategory.OTHER,
                            title=f"Query uses deprecated column: {col.name}",
                            description=dep_info.get("reason", "This column is deprecated")
                            if dep_info
                            else "This column is deprecated",
                            table_name=table.name,
                            column_name=col.name,
                            impact="Using deprecated columns may cause issues in future versions",
                            fix_description=f"Use column '{new_name}' instead"
                            if new_name
                            else "Remove usage of deprecated column",
                        )
                    )

                # Check if using old name that was renamed
                for old_key, new_key in rename_map.items():
                    if col_key == old_key.lower():
                        new_col = new_key.split(".")[-1]
                        issues.append(
                            Issue(
                                severity=IssueSeverity.WARNING,
                                category=IssueCategory.NAMING_CONVENTION,
                                title=f"Using old column name: {col.name}",
                                description=f"Column was renamed from '{col.name}' to '{new_col}'",
                                table_name=table.name,
                                column_name=col.name,
                                fix_description=f"Update to use '{new_col}' instead",
                            )
                        )

        return issues


def check_conventions(
    schema: ParsedSchema,
    context: ProjectContext,
) -> list[Issue]:
    """
    Convenience function to check schema against project conventions.

    Args:
        schema: Parsed SQL schema
        context: Project context with conventions

    Returns:
        List of issues found
    """
    issues: list[Issue] = []

    # Check conventions
    if context.conventions:
        checker = ConventionChecker(context.conventions)
        issues.extend(checker.check(schema))

    # Check deprecated usage
    deprecation_checker = DeprecationChecker(context)
    issues.extend(deprecation_checker.check(schema))

    return issues
