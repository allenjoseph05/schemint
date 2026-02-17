"""SQL Schema Parser - extracts structure from CREATE TABLE statements."""

from __future__ import annotations

import re
from typing import ClassVar

import sqlparse
from sqlparse.sql import Parenthesis, Statement

from schemint.models.schema import (
    Column,
    DataType,
    ForeignKey,
    Index,
    ParsedSchema,
    Table,
)


class SQLParserError(Exception):
    """Error during SQL parsing."""



class SQLParser:
    """Parses SQL CREATE TABLE statements into structured schema."""

    # Map SQL types to our DataType enum
    TYPE_MAP: ClassVar[dict[str, DataType]] = {
        "int": DataType.INT,
        "integer": DataType.INT,
        "bigint": DataType.BIGINT,
        "smallint": DataType.SMALLINT,
        "tinyint": DataType.TINYINT,
        "float": DataType.FLOAT,
        "double": DataType.DOUBLE,
        "decimal": DataType.DECIMAL,
        "numeric": DataType.NUMERIC,
        "varchar": DataType.VARCHAR,
        "char": DataType.CHAR,
        "text": DataType.TEXT,
        "longtext": DataType.LONGTEXT,
        "date": DataType.DATE,
        "time": DataType.TIME,
        "datetime": DataType.DATETIME,
        "timestamp": DataType.TIMESTAMP,
        "blob": DataType.BLOB,
        "binary": DataType.BINARY,
        "boolean": DataType.BOOLEAN,
        "bool": DataType.BOOLEAN,
        "json": DataType.JSON,
        "uuid": DataType.UUID,
        "enum": DataType.ENUM,
    }

    def parse(
        self, sql: str, database_type: str = "mysql", normalize_identifiers: bool = True
    ) -> ParsedSchema:
        """Parse SQL string into ParsedSchema.

        Args:
            sql: SQL CREATE TABLE statements
            database_type: Target database type
            normalize_identifiers: If True, lowercase all identifier names
        """
        if not sql or not sql.strip():
            raise SQLParserError("Empty SQL input")

        # Parse SQL into statements
        statements = sqlparse.parse(sql)

        tables = []
        for statement in statements:
            if self._is_create_table(statement):
                try:
                    table = self._parse_create_table(statement)
                    tables.append(table)
                except Exception as e:
                    # Log but continue parsing other tables
                    print(f"Warning: Could not parse table: {e}")

        if not tables:
            raise SQLParserError("No valid CREATE TABLE statements found")

        schema = ParsedSchema(
            tables=tables,
            database_type=database_type,
            raw_sql=sql,
        )

        if normalize_identifiers:
            schema = self._normalize_schema(schema)

        return schema

    def _normalize_schema(self, schema: ParsedSchema) -> ParsedSchema:
        """Lowercase all identifier names in the schema."""
        for table in schema.tables:
            table.name = table.name.lower()
            table.primary_key = [pk.lower() for pk in table.primary_key]
            for col in table.columns:
                col.name = col.name.lower()
            for fk in table.foreign_keys:
                fk.column = fk.column.lower()
                fk.references_table = fk.references_table.lower()
                fk.references_column = fk.references_column.lower()
            for idx in table.indexes:
                idx.columns = [c.lower() for c in idx.columns]
        return schema

    def _is_create_table(self, statement: Statement) -> bool:
        """Check if statement is CREATE TABLE."""
        from sqlparse import tokens as sqlparse_tokens

        # Skip whitespace and comments to find the first real tokens
        tokens = [
            t for t in statement.tokens
            if not t.is_whitespace and t.ttype not in (sqlparse_tokens.Comment.Single, sqlparse_tokens.Comment.Multiline)
            and not (hasattr(t, 'tokens') and all(
                getattr(sub, 'ttype', None) in (sqlparse_tokens.Comment.Single, sqlparse_tokens.Comment.Multiline, sqlparse_tokens.Newline, sqlparse_tokens.Whitespace)
                for sub in t.flatten()
            ))
        ]
        if len(tokens) < 2:
            return False

        first_two = " ".join(str(t).upper() for t in tokens[:2])
        return "CREATE" in first_two and "TABLE" in first_two

    def _parse_create_table(self, statement: Statement) -> Table:
        """Parse a CREATE TABLE statement."""
        # Extract table name
        table_name = self._extract_table_name(statement)

        # Extract column definitions
        columns: list[Column] = []
        primary_key: list[str] = []
        foreign_keys: list[ForeignKey] = []
        indexes: list[Index] = []

        # Find the parenthesis containing column definitions
        for token in statement.tokens:
            if isinstance(token, Parenthesis):
                col_defs = self._split_column_definitions(token)

                for col_def in col_defs:
                    col_def_clean = col_def.strip()
                    col_def_upper = col_def_clean.upper()

                    if col_def_upper.startswith("PRIMARY KEY"):
                        pk_cols = self._extract_pk_columns(col_def_clean)
                        primary_key.extend(pk_cols)

                    elif col_def_upper.startswith("FOREIGN KEY"):
                        fk = self._parse_foreign_key(col_def_clean)
                        if fk:
                            foreign_keys.append(fk)

                    elif col_def_upper.startswith(("UNIQUE", "INDEX", "KEY")):
                        idx = self._parse_index(col_def_clean)
                        if idx:
                            indexes.append(idx)

                    elif col_def_upper.startswith("CONSTRAINT"):
                        if "FOREIGN KEY" in col_def_upper:
                            fk = self._parse_foreign_key(col_def_clean)
                            if fk:
                                foreign_keys.append(fk)
                        elif "PRIMARY KEY" in col_def_upper:
                            pk_cols = self._extract_pk_columns(col_def_clean)
                            primary_key.extend(pk_cols)

                    elif col_def_clean and not col_def_upper.startswith("CHECK"):
                        column = self._parse_column(col_def_clean)
                        if column:
                            columns.append(column)
                            if column.is_primary_key:
                                primary_key.append(column.name)

        return Table(
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            foreign_keys=foreign_keys,
            indexes=indexes,
        )

    def _extract_table_name(self, statement: Statement) -> str:
        """Extract table name from CREATE TABLE statement."""
        tokens = [t for t in statement.tokens if not t.is_whitespace]

        found_table = False
        for token in tokens:
            if found_table:
                name = str(token).strip()
                name = name.strip("`\"'")
                if "." in name:
                    name = name.split(".")[-1]
                return name

            if str(token).upper() == "TABLE":
                found_table = True

        raise SQLParserError("Could not extract table name")

    def _split_column_definitions(self, parenthesis: Parenthesis) -> list[str]:
        """Split parenthesis content into individual column definitions."""
        content = str(parenthesis)[1:-1]

        definitions = []
        current: list[str] = []
        depth = 0

        for char in content:
            if char == "(":
                depth += 1
                current.append(char)
            elif char == ")":
                depth -= 1
                current.append(char)
            elif char == "," and depth == 0:
                definitions.append("".join(current).strip())
                current = []
            else:
                current.append(char)

        if current:
            definitions.append("".join(current).strip())

        return definitions

    def _parse_column(self, col_def: str) -> Column | None:
        """Parse a single column definition."""
        parts = col_def.split()
        if len(parts) < 2:
            return None

        name = parts[0].strip("`\"'")
        type_str = parts[1]

        # Check for type with length
        length = None
        precision = None
        scale = None

        match = re.match(r"(\w+)\s*\((\d+)(?:,\s*(\d+))?\)", col_def[len(name) :].strip())
        if match:
            type_str = match.group(1)
            length = int(match.group(2))
            if match.group(3):
                precision = length
                scale = int(match.group(3))
                length = None

        # Determine data type
        type_lower = type_str.lower()
        data_type = self.TYPE_MAP.get(type_lower, DataType.UNKNOWN)

        # Parse modifiers
        col_def_upper = col_def.upper()
        nullable = "NOT NULL" not in col_def_upper
        is_primary_key = "PRIMARY KEY" in col_def_upper
        is_auto_increment = "AUTO_INCREMENT" in col_def_upper or "SERIAL" in col_def_upper
        is_unique = "UNIQUE" in col_def_upper

        # Extract default value
        default = None
        default_match = re.search(r"DEFAULT\s+([^,\s]+|'[^']*')", col_def, re.IGNORECASE)
        if default_match:
            default = default_match.group(1)

        # Extract ENUM values
        enum_values = None
        if data_type == DataType.ENUM:
            enum_match = re.search(r"ENUM\s*\(([^)]+)\)", col_def, re.IGNORECASE)
            if enum_match:
                enum_str = enum_match.group(1)
                enum_values = [v.strip().strip("'\"") for v in enum_str.split(",")]

        return Column(
            name=name,
            data_type=data_type,
            raw_type=type_str + (f"({length})" if length else ""),
            length=length,
            precision=precision,
            scale=scale,
            nullable=nullable,
            default=default,
            is_primary_key=is_primary_key,
            is_auto_increment=is_auto_increment,
            is_unique=is_unique,
            enum_values=enum_values,
        )

    def _extract_pk_columns(self, pk_def: str) -> list[str]:
        """Extract column names from PRIMARY KEY definition."""
        match = re.search(r"\(([^)]+)\)", pk_def)
        if match:
            cols_str = match.group(1)
            return [c.strip().strip("`\"'") for c in cols_str.split(",")]
        return []

    def _parse_foreign_key(self, fk_def: str) -> ForeignKey | None:
        """Parse a FOREIGN KEY constraint."""
        name = None
        name_match = re.search(r"CONSTRAINT\s+[`\"]?(\w+)[`\"]?", fk_def, re.IGNORECASE)
        if name_match:
            name = name_match.group(1)

        fk_match = re.search(r"FOREIGN\s+KEY\s*\(([^)]+)\)", fk_def, re.IGNORECASE)
        if not fk_match:
            return None
        column = fk_match.group(1).strip().strip("`\"'")

        ref_match = re.search(r"REFERENCES\s+[`\"]?(\w+)[`\"]?\s*\(([^)]+)\)", fk_def, re.IGNORECASE)
        if not ref_match:
            return None

        ref_table = ref_match.group(1)
        ref_column = ref_match.group(2).strip().strip("`\"'")

        on_delete = None
        on_update = None

        delete_match = re.search(r"ON\s+DELETE\s+(\w+(?:\s+\w+)?)", fk_def, re.IGNORECASE)
        if delete_match:
            on_delete = delete_match.group(1).upper()

        update_match = re.search(r"ON\s+UPDATE\s+(\w+(?:\s+\w+)?)", fk_def, re.IGNORECASE)
        if update_match:
            on_update = update_match.group(1).upper()

        return ForeignKey(
            name=name,
            column=column,
            references_table=ref_table,
            references_column=ref_column,
            on_delete=on_delete,
            on_update=on_update,
        )

    def _parse_index(self, idx_def: str) -> Index | None:
        """Parse an INDEX or UNIQUE constraint."""
        is_unique = idx_def.upper().startswith("UNIQUE")

        name = None
        name_match = re.search(
            r"(?:UNIQUE\s+)?(?:INDEX|KEY)\s+[`\"]?(\w+)[`\"]?", idx_def, re.IGNORECASE
        )
        if name_match:
            name = name_match.group(1)

        cols_match = re.search(r"\(([^)]+)\)", idx_def)
        if not cols_match:
            return None

        columns = [c.strip().strip("`\"'") for c in cols_match.group(1).split(",")]

        return Index(
            name=name,
            columns=columns,
            is_unique=is_unique,
            is_primary=False,
        )


def parse_sql(
    sql: str, database_type: str = "mysql", normalize_identifiers: bool = True
) -> ParsedSchema:
    """Convenience function to parse SQL."""
    parser = SQLParser()
    return parser.parse(sql, database_type, normalize_identifiers=normalize_identifiers)
