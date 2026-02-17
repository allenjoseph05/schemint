"""
Diff Extractor.

Extracts and parses SQL changes from git diffs.
"""

import logging
import re
from dataclasses import dataclass, field

from schemint.ci.file_detector import DetectedFile, DetectionResult, SQLFileDetector
from schemint.ci.models import FileChange, SchemaDiff, SQLChange
from schemint.ci.providers.base import BaseGitProvider, DiffFile
from schemint.ci.sql_utils import (
    analyze_sql_content,
    parse_alembic_migration,
    parse_sqlalchemy_models,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtractedSQL:
    """SQL content extracted from a file."""

    file_path: str
    file_type: str  # sql, migration, orm
    statements: list[str] = field(default_factory=list)
    raw_content: str = ""

    # Parsed structure
    tables_referenced: list[str] = field(default_factory=list)
    columns_referenced: list[str] = field(default_factory=list)

    # For migrations
    migration_type: str | None = None  # up, down, forward, backward


class DiffExtractor:
    """
    Extracts schema changes from git diffs.

    Flow:
    1. Get diff from git provider
    2. Detect SQL-related files
    3. Extract and parse SQL content
    4. Return structured SchemaDiff
    """

    def __init__(
        self,
        file_detector: SQLFileDetector | None = None,
    ):
        """
        Initialize extractor.

        Args:
            file_detector: Custom file detector (uses default if None)
        """
        self.file_detector = file_detector or SQLFileDetector()

    async def extract(
        self,
        provider: BaseGitProvider,
        repo: str,
        base_ref: str,
        head_ref: str,
    ) -> SchemaDiff:
        """
        Extract schema changes between two refs.

        Args:
            provider: Git provider to use
            repo: Repository identifier
            base_ref: Base ref for comparison
            head_ref: Head ref for comparison

        Returns:
            SchemaDiff with all SQL-related changes
        """
        logger.info(f"Extracting diff for {repo}: {base_ref}..{head_ref}")

        # Get the diff from provider
        diff_files = await provider.get_diff(repo, base_ref, head_ref)

        logger.info(f"Got {len(diff_files)} files in diff")
        for df in diff_files:
            has_content = bool(df.content)
            content_len = len(df.content) if df.content else 0
            logger.debug(f"  - {df.path} ({df.change_type}): content={has_content}, len={content_len}")

        # Detect SQL-related files
        detection = self.file_detector.detect(diff_files)

        logger.info(f"Detected {detection.sql_files_found} SQL files out of {detection.total_files_scanned}")
        for detected in detection.files:
            has_content = bool(detected.content)
            content_len = len(detected.content) if detected.content else 0
            logger.info(f"  [SQL] {detected.path} (type={detected.file_type}, pattern={detected.matched_pattern})")
            logger.debug(f"         content={has_content}, len={content_len}")

        # Build the SchemaDiff
        return self._build_schema_diff(
            base_ref=base_ref,
            head_ref=head_ref,
            diff_files=diff_files,
            detection=detection,
        )

    def extract_from_diff_files(
        self,
        diff_files: list[DiffFile],
        base_ref: str = "base",
        head_ref: str = "head",
    ) -> SchemaDiff:
        """
        Extract schema changes from pre-fetched diff files.

        Useful when the provider has already fetched the diff.

        Args:
            diff_files: List of diff files
            base_ref: Base ref for reference
            head_ref: Head ref for reference

        Returns:
            SchemaDiff with all SQL-related changes
        """
        detection = self.file_detector.detect(diff_files)

        return self._build_schema_diff(
            base_ref=base_ref,
            head_ref=head_ref,
            diff_files=diff_files,
            detection=detection,
        )

    def _build_schema_diff(
        self,
        base_ref: str,
        head_ref: str,
        diff_files: list[DiffFile],
        detection: DetectionResult,
    ) -> SchemaDiff:
        """Build SchemaDiff from detection results."""
        logger.debug(f"Building SchemaDiff from {len(detection.files)} detected files")

        # Build file changes list
        file_changes = [
            FileChange(
                path=f.path,
                change_type=f.change_type,
                additions=0,  # Not available without parsing diff hunks
                deletions=0,
            )
            for f in diff_files
        ]

        # Build SQL changes
        sql_changes: list[SQLChange] = []
        tables_affected: set[str] = set()
        columns_affected: set[str] = set()

        for detected in detection.files:
            sql_change = self._parse_detected_file(detected)
            sql_changes.append(sql_change)

            has_content = bool(sql_change.content)
            content_len = len(sql_change.content) if sql_change.content else 0
            logger.info(
                f"SQLChange created: {sql_change.file_path} "
                f"(content={has_content}, len={content_len}, "
                f"tables_added={sql_change.tables_added}, "
                f"tables_modified={sql_change.tables_modified})"
            )

            # Aggregate affected elements
            tables_affected.update(sql_change.tables_added)
            tables_affected.update(sql_change.tables_modified)
            tables_affected.update(sql_change.tables_dropped)
            columns_affected.update(sql_change.columns_added)
            columns_affected.update(sql_change.columns_modified)
            columns_affected.update(sql_change.columns_dropped)

        logger.info(
            f"SchemaDiff built: {len(sql_changes)} SQL changes, "
            f"{len(tables_affected)} tables affected, "
            f"{len(columns_affected)} columns affected"
        )

        return SchemaDiff(
            ref=head_ref,
            base_ref=base_ref,
            files_changed=file_changes,
            sql_files=[f.path for f in detection.files],
            sql_changes=sql_changes,
            total_tables_affected=len(tables_affected),
            total_columns_affected=len(columns_affected),
        )

    def _parse_detected_file(self, detected: DetectedFile) -> SQLChange:
        """Parse a detected file to extract SQL changes."""
        sql_change = SQLChange(
            file_path=detected.path,
            change_type=detected.change_type,
            content=detected.content,  # Preserve content for analysis
        )

        if not detected.content:
            return sql_change

        # Parse based on file type
        if detected.file_type == "sql":
            self._parse_sql_content(detected.content, sql_change)
        elif detected.file_type == "migration":
            self._parse_migration_content(detected.content, sql_change)
        elif detected.file_type == "orm":
            self._parse_orm_content(detected.content, detected.path, sql_change)

        return sql_change

    def _parse_sql_content(self, content: str, sql_change: SQLChange) -> None:
        """Parse raw SQL content using sqlparse."""
        analysis = analyze_sql_content(content)
        sql_change.tables_added = analysis.tables_added
        sql_change.tables_modified = analysis.tables_modified
        sql_change.tables_dropped = analysis.tables_dropped
        sql_change.columns_added = analysis.columns_added
        sql_change.columns_dropped = analysis.columns_dropped

    def _parse_migration_content(self, content: str, sql_change: SQLChange) -> None:
        """Parse migration file content."""
        # First try to parse as SQL
        self._parse_sql_content(content, sql_change)

        # Alembic (Python) - use AST parser
        content_lower = content.lower()
        if "op." in content_lower:
            alembic = parse_alembic_migration(content)
            sql_change.tables_added.extend(alembic.tables_added)
            sql_change.tables_dropped.extend(alembic.tables_dropped)
            sql_change.columns_added.extend(alembic.columns_added)

        # Rails migrations (Ruby) - keep regex (no Ruby parser)
        if "create_table" in content_lower:
            matches = re.findall(r"create_table\s+[:\"](\w+)", content)
            sql_change.tables_added.extend(matches)

        if "drop_table" in content_lower:
            matches = re.findall(r"drop_table\s+[:\"](\w+)", content)
            sql_change.tables_dropped.extend(matches)

    def _parse_orm_content(
        self,
        content: str,
        path: str,
        sql_change: SQLChange,
    ) -> None:
        """Parse ORM schema content."""
        path_lower = path.lower()

        if path_lower.endswith(".prisma"):
            self._parse_prisma_content(content, sql_change)
        elif path_lower.endswith(".py"):
            self._parse_sqlalchemy_content(content, sql_change)
        elif path_lower.endswith(".ts"):
            self._parse_typeorm_content(content, sql_change)

    def _parse_prisma_content(self, content: str, sql_change: SQLChange) -> None:
        """Parse Prisma schema."""
        # Find model definitions
        matches = re.findall(r"model\s+(\w+)\s*\{", content)
        # All models in the file are considered (can't easily diff)
        sql_change.tables_modified = list(set(matches))

    def _parse_sqlalchemy_content(self, content: str, sql_change: SQLChange) -> None:
        """Parse SQLAlchemy models using AST."""
        analysis = parse_sqlalchemy_models(content)
        sql_change.tables_modified = analysis.tables_modified

    def _parse_typeorm_content(self, content: str, sql_change: SQLChange) -> None:
        """Parse TypeORM entities."""
        # Find @Entity decorators
        matches = re.findall(
            r"@Entity\s*\(\s*['\"]?(\w+)?['\"]?\s*\)",
            content,
        )
        # Also find class names after @Entity
        class_matches = re.findall(
            r"@Entity[^)]*\)\s*(?:export\s+)?class\s+(\w+)",
            content,
        )
        sql_change.tables_modified = list(set(matches + class_matches))


# Module-level convenience function
async def extract_diff(
    provider: BaseGitProvider,
    repo: str,
    base_ref: str,
    head_ref: str,
) -> SchemaDiff:
    """
    Extract schema diff using default extractor.

    Args:
        provider: Git provider
        repo: Repository
        base_ref: Base ref
        head_ref: Head ref

    Returns:
        SchemaDiff
    """
    extractor = DiffExtractor()
    return await extractor.extract(provider, repo, base_ref, head_ref)
