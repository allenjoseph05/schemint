"""
SQL File Detector.

Detects SQL-related files in a diff for analysis.
"""

from dataclasses import dataclass, field

import pathspec

from schemint.ci.providers.base import DiffFile


@dataclass
class SQLFilePattern:
    """A pattern for detecting SQL-related files."""

    pattern: str
    description: str
    file_type: str  # sql, migration, orm, config


# Default patterns for SQL-related files
# Note: More specific patterns should come first to ensure correct matching
DEFAULT_PATTERNS: list[SQLFilePattern] = [
    # Migration directories (specific patterns first)
    SQLFilePattern("migrations/**/*.sql", "Migration SQL files", "migration"),
    SQLFilePattern("migrations/**/*.py", "Migration Python files", "migration"),
    SQLFilePattern("migrations/**/*.rb", "Migration Ruby files", "migration"),
    SQLFilePattern("db/migrate/**/*.rb", "Rails migrations", "migration"),
    SQLFilePattern("db/migrate/**/*.sql", "Rails SQL migrations", "migration"),
    SQLFilePattern("alembic/versions/**/*.py", "Alembic migrations", "migration"),
    SQLFilePattern("flyway/**/*.sql", "Flyway migrations", "migration"),
    SQLFilePattern("liquibase/**/*", "Liquibase migrations", "migration"),
    # Schema directories
    SQLFilePattern("schema/**/*.sql", "Schema SQL files", "sql"),
    SQLFilePattern("db/schema/**/*.sql", "Database schema files", "sql"),
    SQLFilePattern("database/**/*.sql", "Database SQL files", "sql"),
    # Direct SQL files (after specific directories)
    SQLFilePattern("**/*.sql", "SQL files", "sql"),
    # ORM schema files
    SQLFilePattern("prisma/schema.prisma", "Prisma schema", "orm"),
    SQLFilePattern("**/models.py", "SQLAlchemy models", "orm"),
    SQLFilePattern("**/models/*.py", "SQLAlchemy models", "orm"),
    SQLFilePattern("**/entities/*.ts", "TypeORM entities", "orm"),
    SQLFilePattern("**/entity/*.ts", "TypeORM entities", "orm"),
    # TypeScript/JavaScript ORMs
    SQLFilePattern("drizzle/**/*.ts", "Drizzle schema", "orm"),
    SQLFilePattern("**/schema.ts", "TypeScript schema", "orm"),
    # Go ORMs
    SQLFilePattern("**/ent/schema/*.go", "Ent schema", "orm"),
    SQLFilePattern("**/models.go", "Go models", "orm"),
]


@dataclass
class DetectedFile:
    """A detected SQL-related file."""

    path: str
    change_type: str  # added, modified, deleted
    file_type: str  # sql, migration, orm
    matched_pattern: str
    content: str | None = None


@dataclass
class DetectionResult:
    """Result of SQL file detection."""

    files: list[DetectedFile] = field(default_factory=list)
    total_files_scanned: int = 0
    sql_files_found: int = 0

    @property
    def has_sql_changes(self) -> bool:
        """Check if any SQL-related files were found."""
        return len(self.files) > 0

    def by_type(self, file_type: str) -> list[DetectedFile]:
        """Get files of a specific type."""
        return [f for f in self.files if f.file_type == file_type]


class SQLFileDetector:
    """
    Detects SQL-related files in a diff.

    Supports:
    - Direct SQL files (*.sql)
    - Migration directories (migrations/, alembic/, flyway/, etc.)
    - ORM schemas (Prisma, SQLAlchemy, TypeORM, etc.)
    - Custom patterns
    """

    def __init__(
        self,
        patterns: list[SQLFilePattern] | None = None,
        additional_patterns: list[SQLFilePattern] | None = None,
    ):
        """
        Initialize detector.

        Args:
            patterns: Custom patterns to use (replaces defaults)
            additional_patterns: Additional patterns (adds to defaults)
        """
        self.patterns = patterns or DEFAULT_PATTERNS.copy()
        if additional_patterns:
            self.patterns.extend(additional_patterns)

        # Pre-build pathspec matchers for each pattern
        self._matchers: list[
            tuple[SQLFilePattern, pathspec.PathSpec]  # type: ignore[type-arg]
        ] = []
        for pat in self.patterns:
            spec = pathspec.PathSpec.from_lines("gitignore", [pat.pattern])
            self._matchers.append((pat, spec))

    def detect(self, diff_files: list[DiffFile]) -> DetectionResult:
        """
        Detect SQL-related files in a diff.

        Args:
            diff_files: List of files from the diff

        Returns:
            Detection result with matched files
        """
        result = DetectionResult(
            total_files_scanned=len(diff_files),
        )

        for diff_file in diff_files:
            matched_pattern = self._match_patterns(diff_file.path)
            if matched_pattern:
                detected = DetectedFile(
                    path=diff_file.path,
                    change_type=diff_file.change_type,
                    file_type=matched_pattern.file_type,
                    matched_pattern=matched_pattern.pattern,
                    content=diff_file.content,
                )
                result.files.append(detected)

        result.sql_files_found = len(result.files)
        return result

    def _match_patterns(self, path: str) -> SQLFilePattern | None:
        """Check if path matches any pattern."""
        path_normalized = path.replace("\\", "/")

        for pattern, spec in self._matchers:
            if spec.match_file(path_normalized):
                return pattern

        return None

    def is_sql_file(self, path: str) -> bool:
        """Quick check if a path is a SQL-related file."""
        return self._match_patterns(path) is not None


# Module-level convenience functions
_default_detector: SQLFileDetector | None = None


def get_detector() -> SQLFileDetector:
    """Get the default file detector."""
    global _default_detector
    if _default_detector is None:
        _default_detector = SQLFileDetector()
    return _default_detector


def detect_sql_files(diff_files: list[DiffFile]) -> DetectionResult:
    """Detect SQL files using the default detector."""
    return get_detector().detect(diff_files)


def is_sql_file(path: str) -> bool:
    """Check if a path is a SQL-related file."""
    return get_detector().is_sql_file(path)
