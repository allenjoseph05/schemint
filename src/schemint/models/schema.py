"""Schema models representing parsed database structure."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DataType(str, Enum):
    """Common SQL data types."""

    # Numeric
    INT = "INT"
    BIGINT = "BIGINT"
    SMALLINT = "SMALLINT"
    TINYINT = "TINYINT"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    DECIMAL = "DECIMAL"
    NUMERIC = "NUMERIC"

    # String
    VARCHAR = "VARCHAR"
    CHAR = "CHAR"
    TEXT = "TEXT"
    LONGTEXT = "LONGTEXT"

    # Date/Time
    DATE = "DATE"
    TIME = "TIME"
    DATETIME = "DATETIME"
    TIMESTAMP = "TIMESTAMP"

    # Binary
    BLOB = "BLOB"
    BINARY = "BINARY"

    # Other
    BOOLEAN = "BOOLEAN"
    JSON = "JSON"
    UUID = "UUID"
    ENUM = "ENUM"

    # Unknown/Custom
    UNKNOWN = "UNKNOWN"


class Column(BaseModel):
    """Represents a database column."""

    name: str = Field(..., description="Column name")
    data_type: DataType = Field(..., description="Data type")
    raw_type: str = Field(..., description="Original type string from SQL")
    length: int | None = Field(None, description="Length for VARCHAR, etc.")
    precision: int | None = Field(None, description="Precision for DECIMAL")
    scale: int | None = Field(None, description="Scale for DECIMAL")
    nullable: bool = Field(True, description="Whether column allows NULL")
    default: str | None = Field(None, description="Default value")
    is_primary_key: bool = Field(False, description="Is primary key")
    is_auto_increment: bool = Field(False, description="Auto increment")
    is_unique: bool = Field(False, description="Has unique constraint")
    enum_values: list[str] | None = Field(None, description="ENUM values if applicable")


class ForeignKey(BaseModel):
    """Represents a foreign key constraint."""

    name: str | None = Field(None, description="Constraint name")
    column: str = Field(..., description="Local column name")
    references_table: str = Field(..., description="Referenced table")
    references_column: str = Field(..., description="Referenced column")
    on_delete: str | None = Field(None, description="ON DELETE action")
    on_update: str | None = Field(None, description="ON UPDATE action")


class Index(BaseModel):
    """Represents a database index."""

    name: str | None = Field(None, description="Index name")
    columns: list[str] = Field(..., description="Indexed columns")
    is_unique: bool = Field(False, description="Is unique index")
    is_primary: bool = Field(False, description="Is primary key index")


class Table(BaseModel):
    """Represents a database table."""

    name: str = Field(..., description="Table name")
    columns: list[Column] = Field(default_factory=list, description="Table columns")
    primary_key: list[str] = Field(default_factory=list, description="Primary key columns")
    foreign_keys: list[ForeignKey] = Field(default_factory=list, description="Foreign keys")
    indexes: list[Index] = Field(default_factory=list, description="Indexes")

    def get_column(self, name: str) -> Column | None:
        """Get column by name (case-insensitive)."""
        for col in self.columns:
            if col.name.lower() == name.lower():
                return col
        return None

    def has_primary_key(self) -> bool:
        """Check if table has a primary key."""
        return len(self.primary_key) > 0

    def has_column(self, name: str) -> bool:
        """Check if table has a column (case-insensitive)."""
        return self.get_column(name) is not None

    def has_timestamps(self) -> bool:
        """Check if table has created_at/updated_at columns."""
        col_names = [c.name.lower() for c in self.columns]
        has_created = any(name in col_names for name in ["created_at", "createdat", "created"])
        has_updated = any(name in col_names for name in ["updated_at", "updatedat", "updated"])
        return has_created or has_updated

    @property
    def column_names(self) -> list[str]:
        """Get list of column names."""
        return [col.name for col in self.columns]


class ParsedSchema(BaseModel):
    """Represents a fully parsed database schema."""

    tables: list[Table] = Field(default_factory=list, description="All tables")
    database_type: str = Field("mysql", description="Database type")
    raw_sql: str | None = Field(None, description="Original SQL input")

    def get_table(self, name: str) -> Table | None:
        """Get table by name (case-insensitive)."""
        for table in self.tables:
            if table.name.lower() == name.lower():
                return table
        return None

    @property
    def table_names(self) -> list[str]:
        """Get list of table names."""
        return [table.name for table in self.tables]

    @property
    def table_count(self) -> int:
        """Get number of tables."""
        return len(self.tables)
