"""Tests for DDL extraction of non-table objects.

Covers sequence, enum, function, materialized view, and extension extraction
from DDL strings, plus integration with DDLSnapshotCapture.
"""


from schemint.drift.snapshot_pkg.ddl_object_capture import (
    extract_enums_from_ddl,
    extract_extensions_from_ddl,
    extract_functions_from_ddl,
    extract_materialized_views_from_ddl,
    extract_sequences_from_ddl,
)

# =========================================================================
# Sequences
# =========================================================================


class TestExtractSequences:
    def test_basic_sequence(self):
        sql = "CREATE SEQUENCE users_id_seq;"
        result = extract_sequences_from_ddl(sql)
        assert "users_id_seq" in result
        seq = result["users_id_seq"]
        assert seq.name == "users_id_seq"
        assert seq.increment_by == 1

    def test_sequence_with_options(self):
        sql = """
        CREATE SEQUENCE orders_id_seq
            INCREMENT BY 10
            START WITH 1000
            MINVALUE 1
            MAXVALUE 999999
            CACHE 20
            CYCLE;
        """
        result = extract_sequences_from_ddl(sql)
        seq = result["orders_id_seq"]
        assert seq.increment_by == 10
        assert seq.start_value == 1000
        assert seq.max_value == 999999
        assert seq.cache_size == 20
        assert seq.cycle is True

    def test_sequence_if_not_exists(self):
        sql = "CREATE SEQUENCE IF NOT EXISTS my_seq START 5;"
        result = extract_sequences_from_ddl(sql)
        assert "my_seq" in result
        assert result["my_seq"].start_value == 5

    def test_sequence_as_type(self):
        sql = "CREATE SEQUENCE small_seq AS smallint;"
        result = extract_sequences_from_ddl(sql)
        assert result["small_seq"].data_type == "smallint"

    def test_multiple_sequences(self):
        sql = """
        CREATE SEQUENCE seq_a;
        CREATE SEQUENCE seq_b INCREMENT 5;
        """
        result = extract_sequences_from_ddl(sql)
        assert len(result) == 2
        assert "seq_a" in result
        assert "seq_b" in result

    def test_no_sequences(self):
        sql = "CREATE TABLE users (id int);"
        assert extract_sequences_from_ddl(sql) == {}

    def test_no_cycle(self):
        sql = "CREATE SEQUENCE my_seq NO CYCLE;"
        result = extract_sequences_from_ddl(sql)
        assert result["my_seq"].cycle is False

    def test_sequence_inside_comment_ignored(self):
        sql = """
        -- CREATE SEQUENCE fake_seq;
        CREATE TABLE users (id int);
        """
        result = extract_sequences_from_ddl(sql)
        assert "fake_seq" not in result

    def test_sequence_inside_block_comment_ignored(self):
        sql = """
        /* CREATE SEQUENCE fake_seq; */
        CREATE SEQUENCE real_seq;
        """
        result = extract_sequences_from_ddl(sql)
        assert "fake_seq" not in result
        assert "real_seq" in result


# =========================================================================
# Enums
# =========================================================================


class TestExtractEnums:
    def test_basic_enum(self):
        sql = "CREATE TYPE status AS ENUM ('active', 'inactive', 'pending');"
        result = extract_enums_from_ddl(sql)
        assert "status" in result
        assert result["status"].values == ["active", "inactive", "pending"]

    def test_empty_enum(self):
        sql = "CREATE TYPE empty_status AS ENUM ();"
        result = extract_enums_from_ddl(sql)
        assert "empty_status" in result
        assert result["empty_status"].values == []

    def test_multiple_enums(self):
        sql = """
        CREATE TYPE color AS ENUM ('red', 'green', 'blue');
        CREATE TYPE size AS ENUM ('small', 'medium', 'large');
        """
        result = extract_enums_from_ddl(sql)
        assert len(result) == 2

    def test_no_enums(self):
        sql = "CREATE TABLE users (id int);"
        assert extract_enums_from_ddl(sql) == {}

    def test_schema_qualified_enum(self):
        sql = "CREATE TYPE public.mood AS ENUM ('happy', 'sad');"
        result = extract_enums_from_ddl(sql)
        assert "mood" in result
        assert result["mood"].values == ["happy", "sad"]

    def test_enum_inside_comment_ignored(self):
        sql = """
        -- CREATE TYPE fake AS ENUM ('a');
        CREATE TABLE t (id int);
        """
        result = extract_enums_from_ddl(sql)
        assert "fake" not in result


# =========================================================================
# Functions
# =========================================================================


class TestExtractFunctions:
    def test_basic_function(self):
        sql = """
        CREATE FUNCTION add_one(x integer)
        RETURNS integer
        LANGUAGE sql
        AS $$ SELECT x + 1; $$;
        """
        result = extract_functions_from_ddl(sql)
        assert "add_one" in result
        fn = result["add_one"]
        assert fn.return_type == "integer"
        assert fn.language == "sql"

    def test_plpgsql_function(self):
        sql = """
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$;
        """
        result = extract_functions_from_ddl(sql)
        assert "update_timestamp" in result
        fn = result["update_timestamp"]
        assert fn.language == "plpgsql"
        assert fn.return_type == "trigger"

    def test_immutable_function(self):
        sql = """
        CREATE FUNCTION double(x integer)
        RETURNS integer
        LANGUAGE sql
        IMMUTABLE
        AS $$ SELECT x * 2; $$;
        """
        result = extract_functions_from_ddl(sql)
        assert result["double"].volatility == "immutable"

    def test_no_functions(self):
        sql = "CREATE TABLE users (id int);"
        assert extract_functions_from_ddl(sql) == {}

    def test_function_without_dollar_quoting(self):
        sql = """
        CREATE FUNCTION my_func(a int)
        RETURNS int
        LANGUAGE sql;
        """
        result = extract_functions_from_ddl(sql)
        assert "my_func" in result

    def test_function_body_not_treated_as_separate_ddl(self):
        """A CREATE TABLE inside a function body should not be extracted as a real table."""
        sql = """
        CREATE FUNCTION setup()
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        BEGIN
            CREATE SEQUENCE inner_seq;
        END;
        $$;
        """
        # The inner CREATE SEQUENCE should NOT appear as a real sequence
        seqs = extract_sequences_from_ddl(sql)
        assert "inner_seq" not in seqs


# =========================================================================
# Materialized Views
# =========================================================================


class TestExtractMaterializedViews:
    def test_basic_matview(self):
        sql = """
        CREATE MATERIALIZED VIEW active_users AS
        SELECT id, name FROM users WHERE active = true;
        """
        result = extract_materialized_views_from_ddl(sql)
        assert "active_users" in result
        mv = result["active_users"]
        assert "users" in mv.source_tables
        assert mv.is_populated is True

    def test_matview_with_no_data(self):
        sql = """
        CREATE MATERIALIZED VIEW empty_view AS
        SELECT * FROM orders WITH NO DATA;
        """
        result = extract_materialized_views_from_ddl(sql)
        assert "empty_view" in result
        assert result["empty_view"].is_populated is False

    def test_matview_with_join(self):
        sql = """
        CREATE MATERIALIZED VIEW order_details AS
        SELECT o.id, u.name
        FROM orders o
        JOIN users u ON o.user_id = u.id;
        """
        result = extract_materialized_views_from_ddl(sql)
        mv = result["order_details"]
        assert "orders" in mv.source_tables
        assert "users" in mv.source_tables

    def test_matview_if_not_exists(self):
        sql = """
        CREATE MATERIALIZED VIEW IF NOT EXISTS my_view AS
        SELECT 1 FROM dual;
        """
        result = extract_materialized_views_from_ddl(sql)
        assert "my_view" in result

    def test_no_matviews(self):
        sql = "CREATE TABLE users (id int);"
        assert extract_materialized_views_from_ddl(sql) == {}


# =========================================================================
# Extensions
# =========================================================================


class TestExtractExtensions:
    def test_basic_extension(self):
        sql = "CREATE EXTENSION pg_trgm;"
        result = extract_extensions_from_ddl(sql)
        assert "pg_trgm" in result
        assert result["pg_trgm"].installed_schema == "public"

    def test_extension_with_schema(self):
        sql = "CREATE EXTENSION hstore SCHEMA extensions;"
        result = extract_extensions_from_ddl(sql)
        assert result["hstore"].installed_schema == "extensions"

    def test_extension_with_version(self):
        sql = "CREATE EXTENSION postgis VERSION '3.4.0';"
        result = extract_extensions_from_ddl(sql)
        assert result["postgis"].version == "3.4.0"

    def test_extension_if_not_exists(self):
        sql = "CREATE EXTENSION IF NOT EXISTS uuid_ossp;"
        result = extract_extensions_from_ddl(sql)
        assert "uuid_ossp" in result

    def test_multiple_extensions(self):
        sql = """
        CREATE EXTENSION pg_trgm;
        CREATE EXTENSION hstore;
        """
        result = extract_extensions_from_ddl(sql)
        assert len(result) == 2

    def test_no_extensions(self):
        sql = "CREATE TABLE users (id int);"
        assert extract_extensions_from_ddl(sql) == {}

    def test_extension_inside_comment_ignored(self):
        sql = """
        -- CREATE EXTENSION fake_ext;
        CREATE EXTENSION real_ext;
        """
        result = extract_extensions_from_ddl(sql)
        assert "fake_ext" not in result
        assert "real_ext" in result


# =========================================================================
# DDL Capture Integration (via DDLSnapshotCapture)
# =========================================================================


class TestDDLCaptureIntegration:
    def test_capture_populates_sequences(self):
        from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

        sql = """
        CREATE SEQUENCE users_id_seq;
        CREATE TABLE users (id integer DEFAULT nextval('users_id_seq'));
        """
        snap = DDLSnapshotCapture().capture(sql)
        assert "users_id_seq" in snap.sequences

    def test_capture_populates_enums(self):
        from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

        sql = """
        CREATE TYPE status AS ENUM ('active', 'inactive');
        CREATE TABLE users (id integer);
        """
        snap = DDLSnapshotCapture().capture(sql)
        assert "status" in snap.enums

    def test_capture_populates_extensions(self):
        from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

        sql = """
        CREATE EXTENSION pg_trgm;
        CREATE TABLE users (id integer);
        """
        snap = DDLSnapshotCapture().capture(sql)
        assert "pg_trgm" in snap.extensions

    def test_capture_populates_matviews(self):
        from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

        sql = """
        CREATE TABLE users (id integer, active boolean);
        CREATE MATERIALIZED VIEW active_users AS SELECT id FROM users WHERE active = true;
        """
        snap = DDLSnapshotCapture().capture(sql)
        assert "active_users" in snap.materialized_views

    def test_empty_ddl_produces_empty_objects(self):
        from schemint.drift.snapshot_pkg.ddl_capture import DDLSnapshotCapture

        sql = "CREATE TABLE t (id int);"
        snap = DDLSnapshotCapture().capture(sql)
        assert snap.sequences == {}
        assert snap.enums == {}
        assert snap.functions == {}
        assert snap.materialized_views == {}
        assert snap.extensions == {}
