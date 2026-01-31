"""Project context models for schema-aware analysis."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ColumnMetadata(BaseModel):
    """Metadata for a single column in the project schema."""

    name: str = Field(..., description="Column name")
    data_type: str = Field(..., description="Column data type")
    description: str | None = Field(None, description="Column purpose/description")
    nullable: bool = Field(True, description="Whether column allows NULL")
    default: str | None = Field(None, description="Default value")

    # Deprecation tracking
    deprecated: bool = Field(False, description="Whether column is deprecated")
    deprecated_reason: str | None = Field(None, description="Why the column is deprecated")
    deprecated_since: str | None = Field(None, description="Version when deprecated")
    renamed_to: str | None = Field(None, description="New column name if renamed")

    # Rename tracking (for new columns that replaced old ones)
    renamed_from: str | None = Field(None, description="Old column name if this is a rename")

    # Additional metadata
    pii: bool = Field(False, description="Whether column contains PII")
    indexed: bool = Field(False, description="Whether column has an index")
    foreign_key_to: str | None = Field(None, description="Referenced table.column if FK")


class TableMetadata(BaseModel):
    """Metadata for a single table in the project schema."""

    name: str = Field(..., description="Table name")
    description: str | None = Field(None, description="Table purpose/description")
    columns: list[ColumnMetadata] = Field(default_factory=list, description="Column metadata")

    # Deprecation tracking
    deprecated: bool = Field(False, description="Whether table is deprecated")
    deprecated_reason: str | None = Field(None, description="Why the table is deprecated")
    deprecated_since: str | None = Field(None, description="Version when deprecated")
    renamed_to: str | None = Field(None, description="New table name if renamed")
    renamed_from: str | None = Field(None, description="Old table name if this is a rename")

    # Additional metadata
    primary_key: list[str] = Field(default_factory=list, description="Primary key columns")
    indexes: list[str] = Field(default_factory=list, description="Index names")
    estimated_rows: int | None = Field(None, description="Estimated row count")

    def get_column(self, name: str) -> ColumnMetadata | None:
        """Get column by name (case-insensitive)."""
        for col in self.columns:
            if col.name.lower() == name.lower():
                return col
        return None

    def get_deprecated_columns(self) -> list[ColumnMetadata]:
        """Get all deprecated columns."""
        return [col for col in self.columns if col.deprecated]

    def get_renamed_columns(self) -> list[ColumnMetadata]:
        """Get all columns that were renamed from something else."""
        return [col for col in self.columns if col.renamed_from]


class SchemaMetadata(BaseModel):
    """Complete schema metadata for a project."""

    tables: list[TableMetadata] = Field(default_factory=list, description="All tables")
    database_type: str = Field("mysql", description="Database type")
    version: str | None = Field(None, description="Schema version")

    def get_table(self, name: str) -> TableMetadata | None:
        """Get table by name (case-insensitive)."""
        for table in self.tables:
            if table.name.lower() == name.lower():
                return table
        return None

    def get_deprecated_tables(self) -> list[TableMetadata]:
        """Get all deprecated tables."""
        return [table for table in self.tables if table.deprecated]

    def get_all_deprecated_columns(self) -> list[tuple[str, ColumnMetadata]]:
        """Get all deprecated columns with their table names."""
        result = []
        for table in self.tables:
            for col in table.get_deprecated_columns():
                result.append((table.name, col))
        return result


class MigrationAction(str, Enum):
    """Types of migration actions."""

    CREATE_TABLE = "create_table"
    DROP_TABLE = "drop_table"
    ALTER_TABLE = "alter_table"
    ADD_COLUMN = "add_column"
    DROP_COLUMN = "drop_column"
    RENAME_COLUMN = "rename_column"
    MODIFY_COLUMN = "modify_column"
    ADD_INDEX = "add_index"
    DROP_INDEX = "drop_index"
    ADD_CONSTRAINT = "add_constraint"
    DROP_CONSTRAINT = "drop_constraint"
    RENAME_TABLE = "rename_table"
    OTHER = "other"


class MigrationInfo(BaseModel):
    """Information about a single migration."""

    version: str = Field(..., description="Migration version/identifier")
    description: str = Field("", description="Migration description")
    timestamp: datetime | None = Field(None, description="When migration was created")
    file_path: str | None = Field(None, description="Path to migration file")

    # Actions in this migration
    actions: list[MigrationAction] = Field(default_factory=list, description="Actions performed")
    tables_affected: list[str] = Field(default_factory=list, description="Tables modified")

    # Deprecation/rename tracking
    deprecated_tables: list[str] = Field(default_factory=list, description="Tables deprecated in this migration")
    deprecated_columns: list[str] = Field(
        default_factory=list,
        description="Columns deprecated (format: table.column)"
    )
    renamed_tables: dict[str, str] = Field(
        default_factory=dict,
        description="Table renames (old_name -> new_name)"
    )
    renamed_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Column renames (table.old_col -> table.new_col)"
    )

    # Raw SQL if available
    up_sql: str | None = Field(None, description="UP migration SQL")
    down_sql: str | None = Field(None, description="DOWN migration SQL")


class ProjectConventions(BaseModel):
    """Project-specific SQL conventions."""

    # Naming conventions
    naming_conventions: dict[str, str] = Field(
        default_factory=dict,
        description="Naming rules (e.g., table_case: snake_case)"
    )

    # Required elements
    required_columns: list[str] = Field(
        default_factory=list,
        description="Columns required in all tables (e.g., created_at, updated_at)"
    )
    required_indexes: list[str] = Field(
        default_factory=list,
        description="Columns that must be indexed"
    )

    # Forbidden elements
    forbidden_column_names: list[str] = Field(
        default_factory=list,
        description="Column names that should not be used"
    )
    forbidden_data_types: list[str] = Field(
        default_factory=list,
        description="Data types that should not be used"
    )

    # Preferred patterns
    preferred_types: dict[str, str] = Field(
        default_factory=dict,
        description="Preferred types for use cases (e.g., money: DECIMAL(19,4))"
    )
    preferred_id_type: str = Field("BIGINT", description="Preferred type for ID columns")
    preferred_timestamp_type: str = Field("DATETIME", description="Preferred type for timestamps")

    # Foreign key conventions
    fk_naming_pattern: str | None = Field(
        None,
        description="FK naming pattern (e.g., fk_{table}_{column})"
    )
    require_fk_indexes: bool = Field(True, description="Require indexes on FK columns")
    require_cascade_actions: bool = Field(False, description="Require ON DELETE/UPDATE actions")

    # Soft delete
    require_soft_delete: bool = Field(False, description="Require soft delete columns")
    soft_delete_column: str = Field("deleted_at", description="Name of soft delete column")

    # Multi-tenancy
    require_tenant_column: bool = Field(False, description="Require tenant isolation column")
    tenant_column_name: str = Field("tenant_id", description="Name of tenant column")


class ProjectContext(BaseModel):
    """Complete project context for schema-aware analysis."""

    project_name: str = Field(..., description="Project name")
    description: str | None = Field(None, description="Project description")

    # Schema information
    schema_metadata: SchemaMetadata | None = Field(
        None,
        description="Current schema metadata"
    )

    # Migration history
    migrations: list[MigrationInfo] = Field(
        default_factory=list,
        description="Migration history (oldest to newest)"
    )

    # Conventions
    conventions: ProjectConventions | None = Field(
        None,
        description="Project-specific conventions"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional project metadata"
    )

    def get_deprecated_elements(self) -> dict[str, list[str]]:
        """Get all deprecated tables and columns."""
        result: dict[str, list[str]] = {"tables": [], "columns": []}

        if self.schema_metadata:
            # Deprecated tables
            for table in self.schema_metadata.get_deprecated_tables():
                result["tables"].append(table.name)

            # Deprecated columns
            for table_name, col in self.schema_metadata.get_all_deprecated_columns():
                result["columns"].append(f"{table_name}.{col.name}")

        return result

    def get_column_rename_map(self) -> dict[str, str]:
        """Get mapping of old column names to new names."""
        result: dict[str, str] = {}

        if self.schema_metadata:
            for table in self.schema_metadata.tables:
                for col in table.columns:
                    if col.renamed_from:
                        old_name = f"{table.name}.{col.renamed_from}"
                        new_name = f"{table.name}.{col.name}"
                        result[old_name] = new_name

        # Also include from migrations
        for migration in self.migrations:
            result.update(migration.renamed_columns)

        return result

    def check_deprecated_usage(self, table_name: str, column_name: str | None = None) -> dict | None:
        """Check if a table or column is deprecated."""
        if not self.schema_metadata:
            return None

        table = self.schema_metadata.get_table(table_name)
        if not table:
            return None

        # Check table deprecation
        if table.deprecated and column_name is None:
            return {
                "type": "table",
                "name": table_name,
                "reason": table.deprecated_reason,
                "since": table.deprecated_since,
                "renamed_to": table.renamed_to,
            }

        # Check column deprecation
        if column_name:
            col = table.get_column(column_name)
            if col and col.deprecated:
                return {
                    "type": "column",
                    "table": table_name,
                    "name": column_name,
                    "reason": col.deprecated_reason,
                    "since": col.deprecated_since,
                    "renamed_to": col.renamed_to,
                }

        return None
