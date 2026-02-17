"""Project context module for schema-aware analysis."""

from schemint.core.context.context_loader import ContextLoader, load_context
from schemint.core.context.conventions import (
    ConventionChecker,
    DeprecationChecker,
    check_conventions,
)
from schemint.core.context.migration_parser import MigrationParser, parse_migrations
from schemint.core.context.models import (
    ColumnMetadata,
    MigrationAction,
    MigrationInfo,
    ProjectContext,
    ProjectConventions,
    SchemaMetadata,
    TableMetadata,
)

__all__ = [
    # Models
    "ColumnMetadata",
    # Loaders
    "ContextLoader",
    # Checkers
    "ConventionChecker",
    "DeprecationChecker",
    "MigrationAction",
    "MigrationInfo",
    # Parsers
    "MigrationParser",
    "ProjectContext",
    "ProjectConventions",
    "SchemaMetadata",
    "TableMetadata",
    "check_conventions",
    "load_context",
    "parse_migrations",
]
