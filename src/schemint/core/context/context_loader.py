"""Context loader for ingesting project schema metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemint.core.context.migration_parser import parse_migrations
from schemint.core.context.models import (
    ColumnMetadata,
    MigrationInfo,
    ProjectContext,
    ProjectConventions,
    SchemaMetadata,
    TableMetadata,
)

# Try to import YAML support
try:
    import yaml  # type: ignore[import-untyped]
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


class ContextLoader:
    """Loads project context from various sources."""

    def __init__(self) -> None:
        self._context: ProjectContext | None = None

    @property
    def context(self) -> ProjectContext | None:
        """Get the loaded context."""
        return self._context

    def load_from_file(self, file_path: str | Path) -> ProjectContext:
        """
        Load project context from a JSON or YAML file.

        Args:
            file_path: Path to context file (.json or .yaml/.yml)

        Returns:
            ProjectContext object
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Context file not found: {path}")

        content = path.read_text(encoding="utf-8")

        if path.suffix in (".yaml", ".yml"):
            if not YAML_AVAILABLE:
                raise RuntimeError(
                    "PyYAML not installed. Install with: pip install pyyaml"
                )
            data = yaml.safe_load(content)
        elif path.suffix == ".json":
            data = json.loads(content)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

        self._context = self._parse_context_data(data)
        return self._context

    def load_from_dict(self, data: dict[str, Any]) -> ProjectContext:
        """
        Load project context from a dictionary.

        Args:
            data: Context data dictionary

        Returns:
            ProjectContext object
        """
        self._context = self._parse_context_data(data)
        return self._context

    def load_from_directory(
        self,
        project_dir: str | Path,
        context_file: str = "schemint.yaml",
        migrations_dir: str = "migrations",
    ) -> ProjectContext:
        """
        Load project context from a project directory.

        Looks for:
        - schemint.yaml or schemint.json in project root
        - migrations/ directory for schema evolution history

        Args:
            project_dir: Path to project root
            context_file: Name of context file (default: schemint.yaml)
            migrations_dir: Name of migrations directory (default: migrations)

        Returns:
            ProjectContext object
        """
        project_path = Path(project_dir)

        # Try to load context file
        context_data: dict[str, Any] = {}

        # Try YAML first, then JSON
        for filename in [context_file, "schemint.json", "schemint.yaml", "schemint.yml"]:
            context_path = project_path / filename
            if context_path.exists():
                try:
                    content = context_path.read_text(encoding="utf-8")
                    if filename.endswith((".yaml", ".yml")):
                        if YAML_AVAILABLE:
                            context_data = yaml.safe_load(content) or {}
                    else:
                        context_data = json.loads(content)
                    break
                except Exception:
                    continue

        # Parse migrations if directory exists
        migrations_path = project_path / migrations_dir
        migrations: list[MigrationInfo] = []
        if migrations_path.exists() and migrations_path.is_dir():
            migrations = parse_migrations(migrations_path)

        # Build context
        context = self._parse_context_data(context_data)

        # Add migrations
        context.migrations.extend(migrations)

        # Enrich schema metadata from migrations
        if migrations and context.schema_metadata:
            self._enrich_from_migrations(context)

        self._context = context
        return self._context

    def _parse_context_data(self, data: dict[str, Any]) -> ProjectContext:
        """Parse raw context data into ProjectContext."""
        # Parse schema metadata
        schema_metadata = None
        if "schema" in data or "schema_metadata" in data:
            schema_data = data.get("schema") or data.get("schema_metadata", {})
            schema_metadata = self._parse_schema_metadata(schema_data)

        # Parse conventions
        conventions = None
        if "conventions" in data:
            conventions = self._parse_conventions(data["conventions"])

        # Parse migrations if included in file
        migrations = []
        if "migrations" in data:
            for m in data["migrations"]:
                migrations.append(MigrationInfo(**m))

        return ProjectContext(
            project_name=data.get("project_name", data.get("name", "Unknown Project")),
            description=data.get("description"),
            schema_metadata=schema_metadata,
            migrations=migrations,
            conventions=conventions,
            metadata=data.get("metadata", {}),
        )

    def _parse_schema_metadata(self, data: dict[str, Any]) -> SchemaMetadata:
        """Parse schema metadata from dictionary."""
        tables = []

        for table_data in data.get("tables", []):
            columns = []
            for col_data in table_data.get("columns", []):
                columns.append(ColumnMetadata(
                    name=col_data["name"],
                    data_type=col_data.get("type", col_data.get("data_type", "UNKNOWN")),
                    description=col_data.get("description"),
                    nullable=col_data.get("nullable", True),
                    default=col_data.get("default"),
                    deprecated=col_data.get("deprecated", False),
                    deprecated_reason=col_data.get("deprecated_reason"),
                    deprecated_since=col_data.get("deprecated_since"),
                    renamed_to=col_data.get("renamed_to"),
                    renamed_from=col_data.get("renamed_from"),
                    pii=col_data.get("pii", False),
                    indexed=col_data.get("indexed", False),
                    foreign_key_to=col_data.get("foreign_key_to"),
                ))

            tables.append(TableMetadata(
                name=table_data["name"],
                description=table_data.get("description"),
                columns=columns,
                deprecated=table_data.get("deprecated", False),
                deprecated_reason=table_data.get("deprecated_reason"),
                deprecated_since=table_data.get("deprecated_since"),
                renamed_to=table_data.get("renamed_to"),
                renamed_from=table_data.get("renamed_from"),
                primary_key=table_data.get("primary_key", []),
                indexes=table_data.get("indexes", []),
                estimated_rows=table_data.get("estimated_rows"),
            ))

        return SchemaMetadata(
            tables=tables,
            database_type=data.get("database_type", "mysql"),
            version=data.get("version"),
        )

    def _parse_conventions(self, data: dict[str, Any]) -> ProjectConventions:
        """Parse conventions from dictionary."""
        return ProjectConventions(
            naming_conventions=data.get("naming_conventions", data.get("naming", {})),
            required_columns=data.get("required_columns", []),
            required_indexes=data.get("required_indexes", []),
            forbidden_column_names=data.get("forbidden_column_names", data.get("forbidden_columns", [])),
            forbidden_data_types=data.get("forbidden_data_types", data.get("forbidden_types", [])),
            preferred_types=data.get("preferred_types", {}),
            preferred_id_type=data.get("preferred_id_type", "BIGINT"),
            preferred_timestamp_type=data.get("preferred_timestamp_type", "DATETIME"),
            fk_naming_pattern=data.get("fk_naming_pattern"),
            require_fk_indexes=data.get("require_fk_indexes", True),
            require_cascade_actions=data.get("require_cascade_actions", False),
            require_soft_delete=data.get("require_soft_delete", False),
            soft_delete_column=data.get("soft_delete_column", "deleted_at"),
            require_tenant_column=data.get("require_tenant_column", False),
            tenant_column_name=data.get("tenant_column_name", "tenant_id"),
        )

    def _enrich_from_migrations(self, context: ProjectContext) -> None:
        """Enrich schema metadata with information from migrations."""
        if not context.schema_metadata:
            return

        # Build rename maps from migrations
        column_renames: dict[str, str] = {}
        table_renames: dict[str, str] = {}

        for migration in context.migrations:
            column_renames.update(migration.renamed_columns)
            table_renames.update(migration.renamed_tables)

        # Update schema metadata with rename information
        for table in context.schema_metadata.tables:
            # Check if table was renamed from something
            for old_name, new_name in table_renames.items():
                if new_name == table.name.lower():
                    table.renamed_from = old_name

            # Check columns
            for col in table.columns:
                col_key = f"{table.name.lower()}.{col.name.lower()}"
                for old_key, new_key in column_renames.items():
                    if new_key == col_key:
                        # This column was renamed from something
                        old_col_name = old_key.split(".")[-1]
                        col.renamed_from = old_col_name


def load_context(source: str | Path | dict[str, Any]) -> ProjectContext:
    """
    Convenience function to load project context.

    Args:
        source: File path, directory path, or dictionary

    Returns:
        ProjectContext object
    """
    loader = ContextLoader()

    if isinstance(source, dict):
        return loader.load_from_dict(source)

    path = Path(source)

    if path.is_dir():
        return loader.load_from_directory(path)
    return loader.load_from_file(path)
