"""Unit tests for SQL parser."""

import pytest

from schemint.core.parser import SQLParserError, parse_sql
from schemint.models.schema import DataType
from tests.fixtures.schemas import BAD_SCHEMA, GOOD_SCHEMA


class TestSQLParser:
    """Tests for SQLParser class."""

    def test_parse_simple_table(self):
        """Test parsing a simple CREATE TABLE statement."""
        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = parse_sql(sql)

        assert len(result.tables) == 1
        assert result.tables[0].name == "users"
        assert len(result.tables[0].columns) == 2

    def test_parse_with_primary_key(self):
        """Test parsing table with primary key."""
        sql = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100));"
        result = parse_sql(sql)

        assert result.tables[0].primary_key == ["id"]
        assert result.tables[0].columns[0].is_primary_key

    def test_parse_with_auto_increment(self):
        """Test parsing table with auto increment."""
        sql = "CREATE TABLE users (id INT PRIMARY KEY AUTO_INCREMENT);"
        result = parse_sql(sql)

        assert result.tables[0].columns[0].is_auto_increment

    def test_parse_with_foreign_key(self):
        """Test parsing table with foreign key."""
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
        result = parse_sql(sql)

        assert len(result.tables[0].foreign_keys) == 1
        fk = result.tables[0].foreign_keys[0]
        assert fk.column == "user_id"
        assert fk.references_table == "users"
        assert fk.references_column == "id"

    def test_parse_with_on_delete_cascade(self):
        """Test parsing FK with ON DELETE CASCADE."""
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
        result = parse_sql(sql)

        assert result.tables[0].foreign_keys[0].on_delete == "CASCADE"

    def test_parse_varchar_with_length(self):
        """Test parsing VARCHAR with length."""
        sql = "CREATE TABLE users (name VARCHAR(255));"
        result = parse_sql(sql)

        col = result.tables[0].columns[0]
        assert col.data_type == DataType.VARCHAR
        assert col.length == 255

    def test_parse_decimal_with_precision(self):
        """Test parsing DECIMAL with precision and scale."""
        sql = "CREATE TABLE products (price DECIMAL(10,2));"
        result = parse_sql(sql)

        col = result.tables[0].columns[0]
        assert col.data_type == DataType.DECIMAL
        assert col.precision == 10
        assert col.scale == 2

    def test_parse_not_null(self):
        """Test parsing NOT NULL constraint."""
        sql = "CREATE TABLE users (email VARCHAR(100) NOT NULL);"
        result = parse_sql(sql)

        assert not result.tables[0].columns[0].nullable

    def test_parse_default_value(self):
        """Test parsing DEFAULT value."""
        sql = "CREATE TABLE orders (status VARCHAR(20) DEFAULT 'pending');"
        result = parse_sql(sql)

        assert result.tables[0].columns[0].default == "'pending'"

    def test_parse_multiple_tables(self):
        """Test parsing multiple tables."""
        result = parse_sql(GOOD_SCHEMA)

        assert len(result.tables) == 2
        assert result.table_names == ["users", "orders"]

    def test_parse_bad_schema(self):
        """Test parsing schema with issues (should still parse)."""
        result = parse_sql(BAD_SCHEMA)

        assert len(result.tables) == 3
        # Bad schema has no primary keys
        for table in result.tables:
            assert not table.has_primary_key()

    def test_empty_sql_raises_error(self):
        """Test that empty SQL raises error."""
        with pytest.raises(SQLParserError):
            parse_sql("")

    def test_invalid_sql_raises_error(self):
        """Test that invalid SQL raises error."""
        with pytest.raises(SQLParserError):
            parse_sql("SELECT * FROM users;")

    def test_whitespace_only_raises_error(self):
        """Test that whitespace-only SQL raises error."""
        with pytest.raises(SQLParserError):
            parse_sql("   \n\t  ")


class TestTableMethods:
    """Tests for Table model methods."""

    def test_has_primary_key(self):
        """Test has_primary_key method."""
        sql = "CREATE TABLE users (id INT PRIMARY KEY);"
        result = parse_sql(sql)
        assert result.tables[0].has_primary_key()

        sql = "CREATE TABLE users (id INT);"
        result = parse_sql(sql)
        assert not result.tables[0].has_primary_key()

    def test_has_timestamps(self):
        """Test has_timestamps method."""
        sql = "CREATE TABLE users (id INT, created_at TIMESTAMP);"
        result = parse_sql(sql)
        assert result.tables[0].has_timestamps()

        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = parse_sql(sql)
        assert not result.tables[0].has_timestamps()

    def test_get_column(self):
        """Test get_column method."""
        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = parse_sql(sql)
        table = result.tables[0]

        col = table.get_column("name")
        assert col is not None
        assert col.name == "name"

        col = table.get_column("nonexistent")
        assert col is None

    def test_column_names(self):
        """Test column_names property."""
        sql = "CREATE TABLE users (id INT, name VARCHAR(100), email VARCHAR(255));"
        result = parse_sql(sql)

        assert result.tables[0].column_names == ["id", "name", "email"]
