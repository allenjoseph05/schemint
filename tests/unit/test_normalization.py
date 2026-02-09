"""Unit tests for identifier normalization in the SQL parser."""

import pytest

from schemint.core.parser import parse_sql


class TestNormalization:
    """Tests for identifier normalization."""

    def test_table_names_lowercased(self):
        """Mixed case table names are lowercased."""
        sql = """
        CREATE TABLE Users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        );
        """
        schema = parse_sql(sql)
        assert schema.tables[0].name == "users"

    def test_column_names_lowercased(self):
        """Mixed case column names are lowercased."""
        sql = """
        CREATE TABLE users (
            ID INT PRIMARY KEY,
            UserName VARCHAR(100) NOT NULL
        );
        """
        schema = parse_sql(sql)
        col_names = [c.name for c in schema.tables[0].columns]
        assert "id" in col_names
        assert "username" in col_names

    def test_fk_references_lowercased(self):
        """FK target table and column names are lowercased."""
        sql = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            User_Id INT NOT NULL,
            FOREIGN KEY (User_Id) REFERENCES Users(ID) ON DELETE CASCADE
        );
        """
        schema = parse_sql(sql)
        fk = schema.tables[0].foreign_keys[0]
        assert fk.column == "user_id"
        assert fk.references_table == "users"
        assert fk.references_column == "id"

    def test_primary_key_lowercased(self):
        """Primary key entries are lowercased."""
        sql = """
        CREATE TABLE users (
            UserID INT,
            Name VARCHAR(100),
            PRIMARY KEY (UserID)
        );
        """
        schema = parse_sql(sql)
        assert schema.tables[0].primary_key == ["userid"]

    def test_index_columns_lowercased(self):
        """Index column names are lowercased."""
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            Email VARCHAR(255),
            UNIQUE INDEX idx_email (Email)
        );
        """
        schema = parse_sql(sql)
        assert schema.tables[0].indexes[0].columns == ["email"]

    def test_normalization_off_preserves_casing(self):
        """normalize_identifiers=False preserves original casing."""
        sql = """
        CREATE TABLE Users (
            ID INT PRIMARY KEY,
            UserName VARCHAR(100)
        );
        """
        schema = parse_sql(sql, normalize_identifiers=False)
        assert schema.tables[0].name == "Users"
        col_names = [c.name for c in schema.tables[0].columns]
        assert "ID" in col_names
        assert "UserName" in col_names

    def test_default_is_normalized(self):
        """Default behavior normalizes identifiers."""
        sql = """
        CREATE TABLE PRODUCTS (
            ID INT PRIMARY KEY,
            NAME VARCHAR(200)
        );
        """
        schema = parse_sql(sql)
        assert schema.tables[0].name == "products"
