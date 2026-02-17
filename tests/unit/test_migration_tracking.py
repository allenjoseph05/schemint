"""Tests for migration tracking — Task 3.

Verifies MigrationRecord model, migration_utils (checksum + ID generation),
and model serialization.
"""

from __future__ import annotations

import re

from schemint.drift.migration_utils import (
    compute_migration_checksum,
    generate_migration_id,
)
from schemint.drift.models import MigrationRecord

# =============================================================================
# MigrationRecord model
# =============================================================================


class TestMigrationRecord:
    """Tests for MigrationRecord model."""

    def test_basic_creation(self):
        record = MigrationRecord(
            migration_id="20260215_143022_add_phone",
            project_id="proj_1",
            environment="production",
            migration_type="alter_table",
            migration_sql="ALTER TABLE users ADD COLUMN phone VARCHAR(20);",
            checksum="abc123",
        )
        assert record.migration_id == "20260215_143022_add_phone"
        assert record.project_id == "proj_1"
        assert record.environment == "production"
        assert record.migration_type == "alter_table"
        assert record.success is True
        assert record.error_message is None

    def test_default_environment(self):
        record = MigrationRecord(
            migration_id="test",
            project_id="proj_1",
            migration_type="ddl_script",
            checksum="abc",
        )
        assert record.environment == "default"

    def test_failed_migration(self):
        record = MigrationRecord(
            migration_id="test",
            project_id="proj_1",
            migration_type="ddl_script",
            checksum="abc",
            success=False,
            error_message="column already exists",
        )
        assert record.success is False
        assert record.error_message == "column already exists"

    def test_rollback_type(self):
        record = MigrationRecord(
            migration_id="test",
            project_id="proj_1",
            migration_type="rollback",
            migration_sql="ALTER TABLE users DROP COLUMN phone;",
            checksum="xyz",
        )
        assert record.migration_type == "rollback"

    def test_data_migration_type(self):
        record = MigrationRecord(
            migration_id="test",
            project_id="proj_1",
            migration_type="data_migration",
            checksum="def",
        )
        assert record.migration_type == "data_migration"

    def test_serialization_round_trip(self):
        record = MigrationRecord(
            migration_id="20260215_143022_add_phone",
            project_id="proj_1",
            environment="staging",
            migration_type="alter_table",
            migration_sql="ALTER TABLE users ADD COLUMN phone TEXT;",
            checksum="abc123",
            applied_by="ci_bot",
            execution_time_ms=150,
            success=True,
        )
        data = record.model_dump()
        restored = MigrationRecord(**data)
        assert restored.migration_id == record.migration_id
        assert restored.checksum == record.checksum
        assert restored.applied_by == "ci_bot"
        assert restored.execution_time_ms == 150

    def test_optional_fields(self):
        record = MigrationRecord(
            migration_id="test",
            project_id="proj_1",
            migration_type="ddl_script",
            checksum="abc",
        )
        assert record.migration_sql is None
        assert record.applied_by is None
        assert record.execution_time_ms is None




class TestComputeMigrationChecksum:
    """Tests for compute_migration_checksum()."""

    def test_basic_checksum(self):
        checksum = compute_migration_checksum("ALTER TABLE users ADD COLUMN phone TEXT;")
        assert isinstance(checksum, str)
        assert len(checksum) == 64  # SHA256 hex

    def test_whitespace_normalization(self):
        sql1 = "ALTER TABLE users ADD COLUMN phone TEXT;"
        sql2 = "ALTER  TABLE   users\n  ADD  COLUMN  phone    TEXT;"
        assert compute_migration_checksum(sql1) == compute_migration_checksum(sql2)

    def test_case_normalization(self):
        sql1 = "ALTER TABLE users ADD COLUMN phone TEXT;"
        sql2 = "alter table users add column phone text;"
        assert compute_migration_checksum(sql1) == compute_migration_checksum(sql2)

    def test_leading_trailing_whitespace(self):
        sql1 = "ALTER TABLE users ADD COLUMN phone TEXT;"
        sql2 = "  ALTER TABLE users ADD COLUMN phone TEXT;  "
        assert compute_migration_checksum(sql1) == compute_migration_checksum(sql2)

    def test_different_sql_different_checksum(self):
        checksum1 = compute_migration_checksum("ALTER TABLE users ADD COLUMN phone TEXT;")
        checksum2 = compute_migration_checksum("ALTER TABLE users ADD COLUMN email TEXT;")
        assert checksum1 != checksum2

    def test_deterministic(self):
        sql = "CREATE TABLE orders (id INT PRIMARY KEY);"
        assert compute_migration_checksum(sql) == compute_migration_checksum(sql)

    def test_empty_string(self):
        checksum = compute_migration_checksum("")
        assert isinstance(checksum, str)
        assert len(checksum) == 64




class TestGenerateMigrationId:
    """Tests for generate_migration_id()."""

    def test_basic_generation(self):
        mid = generate_migration_id("add_phone")
        assert "add_phone" in mid
        # Expected format is YYYYMMDD_HHMMSS_prefix
        assert re.match(r"\d{8}_\d{6}_add_phone", mid)

    def test_default_prefix(self):
        mid = generate_migration_id()
        assert "migration" in mid

    def test_sanitizes_prefix(self):
        mid = generate_migration_id("Add Phone Column!")
        # Should be lowercased and special chars replaced
        assert "add_phone_column_" in mid

    def test_empty_prefix_uses_default(self):
        mid = generate_migration_id("")
        assert "migration" in mid

    def test_whitespace_prefix(self):
        mid = generate_migration_id("   ")
        assert "migration" in mid

    def test_uniqueness_over_time(self):
        """Two calls should produce different IDs (timestamp-based)."""
        # In practice they could be the same if called in same second,
        # so we just verify format is correct
        mid1 = generate_migration_id("test")
        mid2 = generate_migration_id("test")
        assert re.match(r"\d{8}_\d{6}_test", mid1)
        assert re.match(r"\d{8}_\d{6}_test", mid2)

    def test_special_characters_in_prefix(self):
        mid = generate_migration_id("add-user-table")
        assert re.match(r"\d{8}_\d{6}_add_user_table", mid)
