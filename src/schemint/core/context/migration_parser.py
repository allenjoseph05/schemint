"""Migration file parser for extracting schema evolution history."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from schemint.core.context.models import MigrationAction, MigrationInfo


class MigrationParser:
    """Parses migration files to extract schema evolution history."""

    # Patterns for common migration naming conventions
    MIGRATION_PATTERNS: ClassVar[list[str]] = [
        # Numbered: 001_create_users.sql, V001__create_users.sql
        r"^(?:V)?(\d+)(?:_+|__)(.+?)\.sql$",
        # Timestamped: 20240101120000_create_users.sql
        r"^(\d{14})_(.+?)\.sql$",
        # Rails-style: 20240101120000_create_users.rb
        r"^(\d{14})_(.+?)\.rb$",
        # Alembic-style: abc123_create_users.py
        r"^([a-f0-9]+)_(.+?)\.py$",
    ]

    # SQL patterns for detecting actions
    SQL_PATTERNS: ClassVar[dict[MigrationAction, list[str]]] = {
        MigrationAction.CREATE_TABLE: [
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.DROP_TABLE: [
            r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.ALTER_TABLE: [
            r"ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.ADD_COLUMN: [
            r"ADD\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?\s+\w+",
        ],
        MigrationAction.DROP_COLUMN: [
            r"DROP\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.RENAME_COLUMN: [
            r"RENAME\s+COLUMN\s+[`\"]?(\w+)[`\"]?\s+TO\s+[`\"]?(\w+)[`\"]?",
            r"CHANGE\s+(?:COLUMN\s+)?[`\"]?(\w+)[`\"]?\s+[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.RENAME_TABLE: [
            r"RENAME\s+(?:TABLE\s+)?[`\"]?(\w+)[`\"]?\s+TO\s+[`\"]?(\w+)[`\"]?",
            r"ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?\s+RENAME\s+(?:TO\s+)?[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.ADD_INDEX: [
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+[`\"]?(\w+)[`\"]?",
            r"ADD\s+(?:UNIQUE\s+)?INDEX\s+[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.DROP_INDEX: [
            r"DROP\s+INDEX\s+[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.ADD_CONSTRAINT: [
            r"ADD\s+CONSTRAINT\s+[`\"]?(\w+)[`\"]?",
        ],
        MigrationAction.DROP_CONSTRAINT: [
            r"DROP\s+CONSTRAINT\s+[`\"]?(\w+)[`\"]?",
        ],
    }

    # Patterns for detecting deprecation comments
    DEPRECATION_PATTERNS: ClassVar[list[str]] = [
        r"--\s*@deprecated\s*:?\s*(.+)",
        r"--\s*DEPRECATED\s*:?\s*(.+)",
        r"/\*\s*@deprecated\s*:?\s*(.+?)\s*\*/",
        r"#\s*@deprecated\s*:?\s*(.+)",
    ]

    def __init__(self) -> None:
        self.migrations: list[MigrationInfo] = []

    def parse_directory(self, migrations_dir: str | Path) -> list[MigrationInfo]:
        """
        Parse all migration files in a directory.

        Args:
            migrations_dir: Path to migrations directory

        Returns:
            List of MigrationInfo objects sorted by version
        """
        migrations_path = Path(migrations_dir)
        if not migrations_path.exists():
            return []

        migrations = []

        # Find all potential migration files
        for file_path in migrations_path.iterdir():
            if file_path.is_file():
                migration = self.parse_file(file_path)
                if migration:
                    migrations.append(migration)

        # Sort by version (numeric if possible, otherwise lexicographic)
        migrations.sort(key=lambda m: self._sort_key(m.version))

        return migrations

    def parse_file(self, file_path: str | Path) -> MigrationInfo | None:
        """
        Parse a single migration file.

        Args:
            file_path: Path to migration file

        Returns:
            MigrationInfo or None if not a valid migration
        """
        path = Path(file_path)

        # Try to extract version and description from filename
        version, description = self._parse_filename(path.name)
        if not version:
            return None

        # Read file content
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None

        # Parse SQL content
        actions, tables, renamed_tables, renamed_columns = self._parse_sql_content(content)

        # Extract deprecation comments
        deprecated_tables, deprecated_columns = self._extract_deprecations(content)

        # Try to extract timestamp from version
        timestamp = self._parse_timestamp(version)

        return MigrationInfo(
            version=version,
            description=description or path.stem,
            timestamp=timestamp,
            file_path=str(path),
            actions=actions,
            tables_affected=tables,
            deprecated_tables=deprecated_tables,
            deprecated_columns=deprecated_columns,
            renamed_tables=renamed_tables,
            renamed_columns=renamed_columns,
            up_sql=content if path.suffix == ".sql" else None,
        )

    def _parse_filename(self, filename: str) -> tuple[str | None, str | None]:
        """Extract version and description from migration filename."""
        for pattern in self.MIGRATION_PATTERNS:
            match = re.match(pattern, filename, re.IGNORECASE)
            if match:
                version = match.group(1)
                description = (
                    match.group(2).replace("_", " ") if (match.lastindex or 0) >= 2 else None
                )
                return version, description

        return None, None

    def _parse_sql_content(
        self, content: str
    ) -> tuple[list[MigrationAction], list[str], dict[str, str], dict[str, str]]:
        """
        Parse SQL content to extract actions and affected tables.

        Returns:
            Tuple of (actions, tables_affected, renamed_tables, renamed_columns)
        """
        actions: list[MigrationAction] = []
        tables: set[str] = set()
        renamed_tables: dict[str, str] = {}
        renamed_columns: dict[str, str] = {}

        # Normalize content
        sql = content.upper()

        # Track current table context for column operations
        current_table: str | None = None

        for action, patterns in self.SQL_PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, sql, re.IGNORECASE):
                    if action not in actions:
                        actions.append(action)

                    # Extract table names
                    if (
                        action == MigrationAction.CREATE_TABLE
                        or action == MigrationAction.DROP_TABLE
                    ):
                        tables.add(match.group(1).lower())
                    elif action == MigrationAction.ALTER_TABLE:
                        current_table = match.group(1).lower()
                        tables.add(current_table)
                    elif action == MigrationAction.RENAME_TABLE:
                        old_name = match.group(1).lower()
                        new_name = match.group(2).lower()
                        tables.add(old_name)
                        tables.add(new_name)
                        renamed_tables[old_name] = new_name
                    elif action == MigrationAction.RENAME_COLUMN and current_table:
                        old_col = match.group(1).lower()
                        new_col = match.group(2).lower()
                        key = f"{current_table}.{old_col}"
                        value = f"{current_table}.{new_col}"
                        renamed_columns[key] = value

        return actions, list(tables), renamed_tables, renamed_columns

    def _extract_deprecations(self, content: str) -> tuple[list[str], list[str]]:
        """Extract deprecation comments from migration content."""
        deprecated_tables: list[str] = []
        deprecated_columns: list[str] = []

        for pattern in self.DEPRECATION_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE):
                comment = match.group(1).strip()

                # Try to identify what's being deprecated
                # Format: @deprecated table_name or @deprecated table.column
                if "." in comment:
                    # Column deprecation
                    parts = comment.split()
                    if parts:
                        deprecated_columns.append(parts[0])
                else:
                    # Table deprecation
                    parts = comment.split()
                    if parts:
                        deprecated_tables.append(parts[0])

        return deprecated_tables, deprecated_columns

    def _parse_timestamp(self, version: str) -> datetime | None:
        """Try to parse timestamp from version string."""
        # Try common timestamp formats
        formats = [
            "%Y%m%d%H%M%S",  # 20240101120000
            "%Y%m%d%H%M",  # 202401011200
            "%Y%m%d",  # 20240101
        ]

        for fmt in formats:
            try:
                return datetime.strptime(version, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        return None

    def _sort_key(self, version: str) -> tuple[int, str]:
        """
        Generate a sort key for migration versions.

        Returns tuple of (numeric_value, string_value) for sorting.
        """
        # Try to extract numeric prefix
        match = re.match(r"^(\d+)", version)
        if match:
            return (int(match.group(1)), version)
        return (0, version)


def parse_migrations(migrations_dir: str | Path) -> list[MigrationInfo]:
    """
    Convenience function to parse migrations from a directory.

    Args:
        migrations_dir: Path to migrations directory

    Returns:
        List of MigrationInfo objects sorted by version
    """
    parser = MigrationParser()
    return parser.parse_directory(migrations_dir)
