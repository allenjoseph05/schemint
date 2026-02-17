"""Tests for ALTER TABLE parser — sqlglot-based change event extraction."""

from schemint.drift.alter_parser import AlterParser
from schemint.drift.differ import SchemaDiffer


class TestAlterParserBasic:
    """Basic ALTER TABLE parsing tests."""

    def setup_method(self):
        self.parser = AlterParser()

    def test_add_column(self):
        sql = "ALTER TABLE users ADD COLUMN email VARCHAR(255)"
        events = self.parser.parse(sql)
        added = [e for e in events if e.change_type == "column_added"]
        assert len(added) >= 1
        assert added[0].table == "users"

    def test_drop_column(self):
        sql = "ALTER TABLE users DROP COLUMN email"
        events = self.parser.parse(sql)
        dropped = [e for e in events if e.change_type == "column_dropped"]
        assert len(dropped) >= 1
        assert dropped[0].table == "users"

    def test_alter_column_type(self):
        sql = "ALTER TABLE users ALTER COLUMN name TYPE TEXT"
        events = self.parser.parse(sql)
        type_changes = [e for e in events if e.change_type == "column_type_change"]
        assert len(type_changes) >= 1
        assert type_changes[0].table == "users"

    def test_rename_table(self):
        sql = "ALTER TABLE users RENAME TO customers"
        events = self.parser.parse(sql)
        renamed = [e for e in events if e.change_type == "table_renamed"]
        assert len(renamed) >= 1
        assert renamed[0].table == "users"
        assert renamed[0].new_value == "customers"

    def test_empty_sql_returns_empty(self):
        events = self.parser.parse("")
        assert events == []

    def test_invalid_sql_returns_empty(self):
        events = self.parser.parse("NOT VALID SQL AT ALL ;;;")
        assert events == []

    def test_non_alter_ignored(self):
        sql = "CREATE TABLE t1 (id INT)"
        events = self.parser.parse(sql)
        assert events == []

    def test_multiple_alters(self):
        sql = """
        ALTER TABLE users ADD COLUMN age INTEGER;
        ALTER TABLE users DROP COLUMN middle_name;
        """
        events = self.parser.parse(sql)
        assert len(events) >= 2

    def test_set_not_null(self):
        sql = "ALTER TABLE users ALTER COLUMN name SET NOT NULL"
        events = self.parser.parse(sql)
        nullable = [e for e in events if e.change_type == "column_nullable_change"]
        assert len(nullable) >= 1
        assert nullable[0].new_value == "False"

    def test_drop_not_null(self):
        sql = "ALTER TABLE users ALTER COLUMN name DROP NOT NULL"
        events = self.parser.parse(sql)
        nullable = [e for e in events if e.change_type == "column_nullable_change"]
        assert len(nullable) >= 1
        assert nullable[0].new_value == "True"


class TestAlterParserDifferIntegration:
    """Test diff_from_alter integration in SchemaDiffer."""

    def test_diff_from_alter_basic(self):
        sql = """
        ALTER TABLE orders ADD COLUMN status VARCHAR(50);
        ALTER TABLE orders DROP COLUMN temp_flag;
        """
        differ = SchemaDiffer()
        result = differ.diff_from_alter(sql)

        assert result.old_snapshot_id == "alter_source"
        assert result.new_snapshot_id == "alter_target"
        assert len(result.changes) >= 2

        # All changes should have risk classification
        for change in result.changes:
            assert change.change_risk is not None

    def test_diff_from_alter_empty(self):
        differ = SchemaDiffer()
        result = differ.diff_from_alter("SELECT 1")
        assert len(result.changes) == 0
