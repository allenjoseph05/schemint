"""
Tests for CI File Detector.
"""

from schemint.ci.file_detector import (
    DetectedFile,
    DetectionResult,
    SQLFileDetector,
    SQLFilePattern,
    detect_sql_files,
    is_sql_file,
)
from schemint.ci.providers.base import DiffFile


class TestSQLFileDetector:
    """Tests for SQLFileDetector."""

    def test_detect_sql_file(self):
        """Test detection of .sql files."""
        detector = SQLFileDetector()

        diff_files = [
            DiffFile(path="schema/users.sql", change_type="added"),
            DiffFile(path="readme.md", change_type="modified"),
        ]

        result = detector.detect(diff_files)

        assert result.has_sql_changes
        assert result.sql_files_found == 1
        assert result.total_files_scanned == 2
        assert result.files[0].path == "schema/users.sql"

    def test_detect_migration_files(self):
        """Test detection of migration files."""
        detector = SQLFileDetector()

        diff_files = [
            DiffFile(path="migrations/001_create_users.sql", change_type="added"),
            DiffFile(path="db/migrate/20240101_add_column.rb", change_type="added"),
            DiffFile(path="alembic/versions/abc123_create_users.py", change_type="modified"),
        ]

        result = detector.detect(diff_files)

        assert result.sql_files_found == 3
        migrations = result.by_type("migration")
        assert len(migrations) == 3
        # Verify each file is correctly typed
        paths = [f.path for f in migrations]
        assert "migrations/001_create_users.sql" in paths
        assert "db/migrate/20240101_add_column.rb" in paths
        assert "alembic/versions/abc123_create_users.py" in paths

    def test_detect_orm_files(self):
        """Test detection of ORM schema files."""
        detector = SQLFileDetector()

        diff_files = [
            DiffFile(path="prisma/schema.prisma", change_type="modified"),
            DiffFile(path="app/models.py", change_type="modified"),
            DiffFile(path="src/entities/user.ts", change_type="added"),
        ]

        result = detector.detect(diff_files)

        assert result.sql_files_found == 3
        orm_files = result.by_type("orm")
        assert len(orm_files) == 3

    def test_no_sql_files(self):
        """Test when no SQL files are in diff."""
        detector = SQLFileDetector()

        diff_files = [
            DiffFile(path="readme.md", change_type="modified"),
            DiffFile(path="src/app.ts", change_type="modified"),
            DiffFile(path="package.json", change_type="modified"),
        ]

        result = detector.detect(diff_files)

        assert not result.has_sql_changes
        assert result.sql_files_found == 0
        assert result.total_files_scanned == 3

    def test_custom_patterns(self):
        """Test using custom patterns."""
        custom_patterns = [
            SQLFilePattern("*.ddl", "DDL files", "sql"),
        ]

        detector = SQLFileDetector(patterns=custom_patterns)

        diff_files = [
            DiffFile(path="schema/tables.ddl", change_type="added"),
            DiffFile(path="schema/tables.sql", change_type="added"),
        ]

        result = detector.detect(diff_files)

        # Only .ddl should match with custom patterns (replaces defaults)
        assert result.sql_files_found == 1
        assert result.files[0].path == "schema/tables.ddl"

    def test_additional_patterns(self):
        """Test adding additional patterns to defaults."""
        additional = [
            SQLFilePattern("*.ddl", "DDL files", "sql"),
        ]

        detector = SQLFileDetector(additional_patterns=additional)

        diff_files = [
            DiffFile(path="schema/tables.ddl", change_type="added"),
            DiffFile(path="schema/tables.sql", change_type="added"),
        ]

        result = detector.detect(diff_files)

        # Both should match (defaults + additional)
        assert result.sql_files_found == 2

    def test_detected_file_includes_content(self):
        """Test that detected file includes content from diff."""
        detector = SQLFileDetector()

        diff_files = [
            DiffFile(
                path="schema/users.sql",
                change_type="added",
                content="CREATE TABLE users (id INT);",
            ),
        ]

        result = detector.detect(diff_files)

        assert result.files[0].content == "CREATE TABLE users (id INT);"


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_is_sql_file(self):
        """Test is_sql_file function."""
        assert is_sql_file("schema/users.sql") is True
        assert is_sql_file("migrations/001_init.sql") is True
        assert is_sql_file("prisma/schema.prisma") is True
        assert is_sql_file("readme.md") is False
        assert is_sql_file("app.py") is False

    def test_detect_sql_files(self):
        """Test detect_sql_files function."""
        diff_files = [
            DiffFile(path="schema/users.sql", change_type="added"),
        ]

        result = detect_sql_files(diff_files)

        assert result.has_sql_changes
        assert result.sql_files_found == 1


class TestDetectionResult:
    """Tests for DetectionResult."""

    def test_by_type(self):
        """Test filtering by file type."""
        result = DetectionResult(
            files=[
                DetectedFile(
                    path="schema.sql",
                    change_type="added",
                    file_type="sql",
                    matched_pattern="*.sql",
                ),
                DetectedFile(
                    path="migrations/001.sql",
                    change_type="added",
                    file_type="migration",
                    matched_pattern="migrations/**/*",
                ),
                DetectedFile(
                    path="prisma/schema.prisma",
                    change_type="modified",
                    file_type="orm",
                    matched_pattern="prisma/schema.prisma",
                ),
            ]
        )

        sql_files = result.by_type("sql")
        assert len(sql_files) == 1

        migration_files = result.by_type("migration")
        assert len(migration_files) == 1

        orm_files = result.by_type("orm")
        assert len(orm_files) == 1

    def test_has_sql_changes(self):
        """Test has_sql_changes property."""
        empty_result = DetectionResult(files=[])
        assert not empty_result.has_sql_changes

        with_files = DetectionResult(
            files=[
                DetectedFile(
                    path="test.sql",
                    change_type="added",
                    file_type="sql",
                    matched_pattern="*.sql",
                )
            ]
        )
        assert with_files.has_sql_changes


class TestPathspecMatching:
    """Edge case tests for pathspec-based matching."""

    def test_deeply_nested_sql_file(self):
        """Test matching SQL files in deeply nested paths."""
        detector = SQLFileDetector()
        diff_files = [
            DiffFile(path="src/db/schema/v2/tables/users.sql", change_type="added"),
        ]
        result = detector.detect(diff_files)
        assert result.sql_files_found == 1

    def test_backslash_path_normalization(self):
        """Test that Windows-style backslash paths are normalized."""
        detector = SQLFileDetector()
        diff_files = [
            DiffFile(path="schema\\users.sql", change_type="added"),
        ]
        result = detector.detect(diff_files)
        assert result.sql_files_found == 1

    def test_nested_migration_directory(self):
        """Test deeply nested migration directories match as SQL (not migration)."""
        detector = SQLFileDetector()
        diff_files = [
            DiffFile(path="services/auth/migrations/001_init.sql", change_type="added"),
        ]
        result = detector.detect(diff_files)
        # migrations/**/*.sql only matches root-level migrations/ dir
        # This path matches **/*.sql as sql type
        assert result.sql_files_found == 1
        assert result.files[0].file_type == "sql"

    def test_root_migration_directory(self):
        """Test root-level migration directory matches as migration."""
        detector = SQLFileDetector()
        diff_files = [
            DiffFile(path="migrations/001_init.sql", change_type="added"),
        ]
        result = detector.detect(diff_files)
        assert result.sql_files_found == 1
        assert result.files[0].file_type == "migration"

    def test_alembic_deep_path(self):
        """Test alembic version file in nested path."""
        detector = SQLFileDetector()
        diff_files = [
            DiffFile(path="alembic/versions/abc123_add_users.py", change_type="added"),
        ]
        result = detector.detect(diff_files)
        assert result.sql_files_found == 1
        assert result.files[0].file_type == "migration"
