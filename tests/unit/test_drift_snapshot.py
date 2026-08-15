"""Tests for SnapshotService — DDL capture, canonical types, schema scope."""

import pytest

from schemint.drift.models import SchemaSnapshot
from schemint.drift.snapshot import SnapshotService, _canonicalize_type, _extract_check_constraints


@pytest.fixture
def service():
    return SnapshotService()


class TestCanonicalizeType:
    """Canonical type normalization: deterministic, no inference."""

    def test_int_to_integer(self):
        assert _canonicalize_type("INT") == "integer"
        assert _canonicalize_type("int") == "integer"
        assert _canonicalize_type("INTEGER") == "integer"

    def test_varchar_with_length(self):
        assert _canonicalize_type("VARCHAR(255)") == "varchar(255)"
        assert _canonicalize_type("varchar(100)") == "varchar(100)"

    def test_decimal_with_precision(self):
        assert _canonicalize_type("DECIMAL(10,2)") == "decimal(10,2)"

    def test_bool_to_boolean(self):
        assert _canonicalize_type("BOOL") == "boolean"
        assert _canonicalize_type("BOOLEAN") == "boolean"

    def test_datetime_to_timestamp(self):
        assert _canonicalize_type("DATETIME") == "timestamp"
        assert _canonicalize_type("TIMESTAMP") == "timestamp"

    def test_longtext_to_text(self):
        assert _canonicalize_type("LONGTEXT") == "text"

    def test_unknown_type_lowercased(self):
        assert _canonicalize_type("CITEXT") == "citext"
        assert _canonicalize_type("HSTORE") == "hstore"

    def test_jsonb_preserved(self):
        assert _canonicalize_type("JSONB") == "jsonb"
        assert _canonicalize_type("JSON") == "json"

    def test_whitespace_stripped(self):
        assert _canonicalize_type("  INT  ") == "integer"


class TestCaptureFromDDL:
    def test_single_table(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(255) UNIQUE
        );
        """
        snapshot = service.capture_from_ddl(sql)

        assert isinstance(snapshot, SchemaSnapshot)
        assert snapshot.source == "ddl"
        assert snapshot.snapshot_id.startswith("ddl_")
        assert "users" in snapshot.tables

        users = snapshot.tables["users"]
        assert "id" in users.columns
        assert "name" in users.columns
        assert "email" in users.columns

    def test_multiple_tables(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100)
        );

        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT,
            total DECIMAL(10,2),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
        snapshot = service.capture_from_ddl(sql)

        assert len(snapshot.tables) == 2
        assert "users" in snapshot.tables
        assert "orders" in snapshot.tables

    def test_column_constraints_captured(self, service):
        sql = """
        CREATE TABLE products (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            sku VARCHAR(50) UNIQUE
        );
        """
        snapshot = service.capture_from_ddl(sql)
        products = snapshot.tables["products"]
        id_col = products.columns["id"]

        assert "PRIMARY KEY" in id_col.constraints
        # NOT NULL constraint on name
        name_col = products.columns["name"]
        assert name_col.nullable is False

    def test_constraints_are_sorted(self, service):
        """Constraints must be sorted for stable comparison."""
        sql = """
        CREATE TABLE t (
            id INT PRIMARY KEY NOT NULL UNIQUE
        );
        """
        snapshot = service.capture_from_ddl(sql)
        constraints = snapshot.tables["t"].columns["id"].constraints
        assert constraints == sorted(constraints)

    def test_foreign_keys_captured(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY
        );

        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
        snapshot = service.capture_from_ddl(sql)
        orders = snapshot.tables["orders"]

        assert len(orders.foreign_keys) == 1
        fk = orders.foreign_keys[0]
        assert fk.column == "user_id"
        assert fk.references_table == "users"
        assert fk.references_column == "id"

    def test_inline_foreign_key_captured_with_postgres_default_name(self, service):
        sql = """
        CREATE TABLE users (id INT PRIMARY KEY);
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT REFERENCES users(id)
        );
        """

        snapshot = service.capture_from_ddl(sql, database_type="postgresql")

        fk = snapshot.tables["orders"].foreign_keys[0]
        assert fk.name == "orders_user_id_fkey"
        assert fk.column == "user_id"
        assert fk.references_table == "users"
        assert fk.references_column == "id"

    def test_view_survives_later_unsupported_statement(self, service):
        sql = """
        CREATE TABLE users (id INT PRIMARY KEY, email TEXT);
        CREATE VIEW user_emails AS SELECT email FROM users;
        REFRESH MATERIALIZED VIEW cached_users;
        """

        snapshot = service.capture_from_ddl(sql, database_type="postgresql")

        assert snapshot.views["user_emails"].source_tables == ["users"]

    def test_indexes_captured(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            email VARCHAR(255),
            UNIQUE INDEX idx_email (email)
        );
        """
        snapshot = service.capture_from_ddl(sql)
        users = snapshot.tables["users"]

        assert len(users.indexes) >= 1

    def test_primary_key_captured(self, service):
        sql = """
        CREATE TABLE users (
            id INT,
            tenant_id INT,
            PRIMARY KEY (id, tenant_id)
        );
        """
        snapshot = service.capture_from_ddl(sql)
        users = snapshot.tables["users"]

        assert "id" in users.primary_key
        assert "tenant_id" in users.primary_key

    def test_nullable_column(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            bio TEXT
        );
        """
        snapshot = service.capture_from_ddl(sql)
        bio = snapshot.tables["users"].columns["bio"]
        assert bio.nullable is True

    def test_not_null_column(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        );
        """
        snapshot = service.capture_from_ddl(sql)
        name_col = snapshot.tables["users"].columns["name"]
        assert name_col.nullable is False

    def test_default_value_captured(self, service):
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            status VARCHAR(20) DEFAULT 'active'
        );
        """
        snapshot = service.capture_from_ddl(sql)
        status = snapshot.tables["users"].columns["status"]
        assert status.default == "'active'"

    def test_database_type_passed_through(self, service):
        sql = "CREATE TABLE t (id INT PRIMARY KEY);"
        snapshot = service.capture_from_ddl(sql, database_type="mysql")
        assert snapshot.database_type == "mysql"

    def test_snapshot_id_format(self, service):
        sql = "CREATE TABLE t (id INT PRIMARY KEY);"
        snapshot = service.capture_from_ddl(sql)
        assert snapshot.snapshot_id.startswith("ddl_public_")
        # Format: ddl_{schema}_{YYYYMMDD}_{HHMMSS}
        parts = snapshot.snapshot_id.split("_")
        assert len(parts) == 4

    def test_snapshot_id_includes_schema_name(self, service):
        sql = "CREATE TABLE t (id INT PRIMARY KEY);"
        snapshot = service.capture_from_ddl(sql, schema_name="analytics")
        assert snapshot.snapshot_id.startswith("ddl_analytics_")

    def test_invalid_sql_raises(self, service):
        with pytest.raises(Exception, match=r".+"):
            service.capture_from_ddl("NOT VALID SQL")

    def test_empty_sql_raises(self, service):
        with pytest.raises(Exception, match=r".+"):
            service.capture_from_ddl("")

    def test_canonical_types_applied(self, service):
        """Types must be canonicalized in DDL snapshots."""
        sql = """
        CREATE TABLE t (
            id INT PRIMARY KEY,
            name VARCHAR(100),
            amount DECIMAL(10,2),
            active BOOLEAN
        );
        """
        snapshot = service.capture_from_ddl(sql)
        cols = snapshot.tables["t"].columns

        assert cols["id"].type == "integer"
        assert cols["name"].type == "varchar(100)"
        assert cols["active"].type == "boolean"

    def test_schema_name_defaults_to_public(self, service):
        sql = "CREATE TABLE t (id INT PRIMARY KEY);"
        snapshot = service.capture_from_ddl(sql)
        assert snapshot.schema_name == "public"

    def test_schema_name_configurable(self, service):
        sql = "CREATE TABLE t (id INT PRIMARY KEY);"
        snapshot = service.capture_from_ddl(sql, schema_name="analytics")
        assert snapshot.schema_name == "analytics"

    def test_column_ordering_preserved(self, service):
        """Column order must match DDL declaration order."""
        sql = """
        CREATE TABLE t (
            z_col INT,
            a_col INT,
            m_col INT
        );
        """
        snapshot = service.capture_from_ddl(sql)
        col_names = list(snapshot.tables["t"].columns.keys())
        assert col_names == ["z_col", "a_col", "m_col"]


# =========================================================================
# Enhanced snapshot tests (merged from test_enhanced_snapshot.py)
# =========================================================================


class TestExtractCheckConstraints:
    """Test CHECK constraint extraction from DDL text."""

    def test_simple_check(self):
        sql = """
        CREATE TABLE users (
            age INTEGER NOT NULL,
            CHECK (age > 0)
        );
        """
        result = _extract_check_constraints(sql)
        assert "users" in result
        assert len(result["users"]) == 1
        assert "age > 0" in result["users"][0]

    def test_inline_check(self):
        sql = """
        CREATE TABLE products (
            price DECIMAL(10,2) CHECK (price >= 0),
            name VARCHAR(255) NOT NULL
        );
        """
        result = _extract_check_constraints(sql)
        assert "products" in result
        assert any("price >= 0" in c for c in result["products"])

    def test_multiple_checks(self):
        sql = """
        CREATE TABLE employees (
            age INTEGER NOT NULL,
            salary DECIMAL(10,2),
            CHECK (age >= 18),
            CHECK (salary > 0)
        );
        """
        result = _extract_check_constraints(sql)
        assert "employees" in result
        assert len(result["employees"]) == 2

    def test_no_checks(self):
        sql = """
        CREATE TABLE simple (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255)
        );
        """
        result = _extract_check_constraints(sql)
        assert "simple" not in result

    def test_named_check_constraint(self):
        sql = """
        CREATE TABLE orders (
            amount DECIMAL(10,2),
            CONSTRAINT chk_positive_amount CHECK (amount > 0)
        );
        """
        result = _extract_check_constraints(sql)
        assert "orders" in result
        assert any("amount > 0" in c for c in result["orders"])

    def test_check_with_nested_parens(self):
        sql = """
        CREATE TABLE users (
            status VARCHAR(20),
            CHECK (status IN ('active', 'inactive', 'suspended'))
        );
        """
        result = _extract_check_constraints(sql)
        assert "users" in result
        assert any("status" in c for c in result["users"])

    def test_multiple_tables(self):
        sql = """
        CREATE TABLE t1 (
            x INTEGER,
            CHECK (x > 0)
        );
        CREATE TABLE t2 (
            y INTEGER,
            CHECK (y < 100)
        );
        """
        result = _extract_check_constraints(sql)
        assert "t1" in result
        assert "t2" in result

    def test_if_not_exists(self):
        sql = """
        CREATE TABLE IF NOT EXISTS guarded (
            val INTEGER,
            CHECK (val >= 0)
        );
        """
        result = _extract_check_constraints(sql)
        assert "guarded" in result


class TestSnapshotCheckConstraints:
    """CHECK constraints attached to column snapshots via DDL capture."""

    def test_check_attached_to_column(self):
        sql = """
        CREATE TABLE users (
            age INTEGER NOT NULL,
            name VARCHAR(255),
            CHECK (age > 0)
        );
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)

        age_col = snapshot.tables["users"].columns["age"]
        check_constraints = [c for c in age_col.constraints if c.startswith("CHECK")]
        assert len(check_constraints) >= 1
        assert any("age > 0" in c for c in check_constraints)

    def test_check_not_attached_to_unrelated_column(self):
        sql = """
        CREATE TABLE users (
            age INTEGER NOT NULL,
            name VARCHAR(255),
            CHECK (age > 0)
        );
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)

        name_col = snapshot.tables["users"].columns["name"]
        check_constraints = [c for c in name_col.constraints if c.startswith("CHECK")]
        assert len(check_constraints) == 0

    def test_multiple_checks_on_different_columns(self):
        sql = """
        CREATE TABLE products (
            price DECIMAL(10,2) NOT NULL,
            quantity INTEGER NOT NULL,
            CHECK (price >= 0),
            CHECK (quantity >= 0)
        );
        """
        service = SnapshotService()
        snapshot = service.capture_from_ddl(sql)

        price_col = snapshot.tables["products"].columns["price"]
        qty_col = snapshot.tables["products"].columns["quantity"]

        price_checks = [c for c in price_col.constraints if c.startswith("CHECK")]
        qty_checks = [c for c in qty_col.constraints if c.startswith("CHECK")]

        assert len(price_checks) >= 1
        assert len(qty_checks) >= 1
