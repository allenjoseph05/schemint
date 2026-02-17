"""
Tests for CI SQL Utilities.
"""

from schemint.ci.sql_utils import (
    analyze_sql_content,
    detect_dangerous_patterns,
    is_sql_content,
    parse_alembic_migration,
    parse_sqlalchemy_models,
)


class TestAnalyzeSQLContent:
    """Tests for analyze_sql_content."""

    def test_create_table(self):
        result = analyze_sql_content("CREATE TABLE users (id INT PRIMARY KEY);")
        assert "users" in result.tables_added

    def test_create_table_if_not_exists(self):
        result = analyze_sql_content("CREATE TABLE IF NOT EXISTS users (id INT);")
        assert "users" in result.tables_added

    def test_create_table_schema_qualified(self):
        result = analyze_sql_content("CREATE TABLE public.users (id INT);")
        assert "users" in result.tables_added

    def test_create_table_quoted(self):
        result = analyze_sql_content('CREATE TABLE "users" (id INT);')
        assert "users" in result.tables_added

    def test_create_table_backtick_quoted(self):
        result = analyze_sql_content("CREATE TABLE `users` (id INT);")
        assert "users" in result.tables_added

    def test_alter_table(self):
        result = analyze_sql_content("ALTER TABLE users ADD COLUMN email VARCHAR(255);")
        assert "users" in result.tables_modified
        assert "users.email" in result.columns_added

    def test_alter_table_schema_qualified(self):
        result = analyze_sql_content("ALTER TABLE public.orders ADD COLUMN status INT;")
        assert "orders" in result.tables_modified

    def test_drop_table(self):
        result = analyze_sql_content("DROP TABLE users;")
        assert "users" in result.tables_dropped

    def test_drop_table_if_exists(self):
        result = analyze_sql_content("DROP TABLE IF EXISTS legacy_users;")
        assert "legacy_users" in result.tables_dropped

    def test_drop_table_schema_qualified(self):
        result = analyze_sql_content("DROP TABLE IF EXISTS public.old_table;")
        assert "old_table" in result.tables_dropped

    def test_multiple_statements(self):
        sql = """
        CREATE TABLE users (id INT);
        CREATE TABLE orders (id INT);
        ALTER TABLE users ADD COLUMN name VARCHAR(255);
        DROP TABLE IF EXISTS legacy;
        """
        result = analyze_sql_content(sql)
        assert "users" in result.tables_added
        assert "orders" in result.tables_added
        assert "users" in result.tables_modified
        assert "legacy" in result.tables_dropped

    def test_multiline_create(self):
        sql = """
        CREATE TABLE
            users
        (
            id INT PRIMARY KEY,
            name VARCHAR(255)
        );
        """
        result = analyze_sql_content(sql)
        assert "users" in result.tables_added

    def test_alter_drop_column(self):
        result = analyze_sql_content("ALTER TABLE users DROP COLUMN email;")
        assert "users" in result.tables_modified
        assert "users.email" in result.columns_dropped

    def test_empty_content(self):
        result = analyze_sql_content("")
        assert result.tables_added == []
        assert result.tables_modified == []
        assert result.tables_dropped == []

    def test_deduplication(self):
        sql = """
        CREATE TABLE users (id INT);
        CREATE TABLE users (id INT);
        """
        result = analyze_sql_content(sql)
        assert result.tables_added.count("users") == 1


class TestIsSQLContent:
    """Tests for is_sql_content."""

    def test_create_table(self):
        assert is_sql_content("CREATE TABLE users (id INT);") is True

    def test_alter_table(self):
        assert is_sql_content("ALTER TABLE users ADD COLUMN x INT;") is True

    def test_drop_table(self):
        assert is_sql_content("DROP TABLE users;") is True

    def test_select(self):
        assert is_sql_content("SELECT * FROM users;") is True

    def test_insert(self):
        assert is_sql_content("INSERT INTO users (name) VALUES ('test');") is True

    def test_update(self):
        assert is_sql_content("UPDATE users SET name='foo';") is True

    def test_delete(self):
        assert is_sql_content("DELETE FROM users WHERE id=1;") is True

    def test_not_sql(self):
        assert is_sql_content("print('hello world')") is False

    def test_empty(self):
        assert is_sql_content("") is False

    def test_whitespace(self):
        assert is_sql_content("   \n  ") is False

    def test_python_code(self):
        assert is_sql_content("class User(Base):\n    pass") is False


class TestDetectDangerousPatterns:
    """Tests for detect_dangerous_patterns."""

    def test_add_column_with_default(self):
        sql = "ALTER TABLE users ADD COLUMN status INT DEFAULT 0;"
        patterns = detect_dangerous_patterns(sql)
        assert len(patterns) >= 1
        blocking = [p for p in patterns if p.pattern_type == "blocking_migration"]
        assert len(blocking) == 1
        assert blocking[0].table_name == "users"
        assert blocking[0].column_name == "status"

    def test_drop_column(self):
        sql = "ALTER TABLE users DROP COLUMN email;"
        patterns = detect_dangerous_patterns(sql)
        destructive = [
            p for p in patterns if p.pattern_type == "destructive_change" and p.column_name
        ]
        assert len(destructive) == 1
        assert destructive[0].table_name == "users"
        assert destructive[0].column_name == "email"

    def test_drop_table(self):
        sql = "DROP TABLE users;"
        patterns = detect_dangerous_patterns(sql)
        destructive = [
            p for p in patterns if p.pattern_type == "destructive_change" and not p.column_name
        ]
        assert len(destructive) == 1
        assert destructive[0].table_name == "users"

    def test_drop_table_if_exists(self):
        sql = "DROP TABLE IF EXISTS legacy_table;"
        patterns = detect_dangerous_patterns(sql)
        assert len(patterns) >= 1
        assert patterns[0].table_name == "legacy_table"

    def test_not_null_without_default(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL;"
        patterns = detect_dangerous_patterns(sql)
        unsafe = [p for p in patterns if p.pattern_type == "unsafe_migration"]
        assert len(unsafe) == 1
        assert unsafe[0].table_name == "users"
        assert unsafe[0].column_name == "name"

    def test_not_null_with_default_is_blocking_not_unsafe(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT '';"
        patterns = detect_dangerous_patterns(sql)
        # Should be blocking_migration (has DEFAULT), NOT unsafe_migration
        unsafe = [p for p in patterns if p.pattern_type == "unsafe_migration"]
        assert len(unsafe) == 0
        blocking = [p for p in patterns if p.pattern_type == "blocking_migration"]
        assert len(blocking) == 1

    def test_safe_add_column(self):
        sql = "ALTER TABLE users ADD COLUMN name VARCHAR(255);"
        patterns = detect_dangerous_patterns(sql)
        # No dangerous patterns for a simple nullable ADD COLUMN
        blocking = [p for p in patterns if p.pattern_type == "blocking_migration"]
        unsafe = [p for p in patterns if p.pattern_type == "unsafe_migration"]
        assert len(blocking) == 0
        assert len(unsafe) == 0

    def test_no_dangerous_in_create(self):
        sql = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255));"
        patterns = detect_dangerous_patterns(sql)
        assert len(patterns) == 0

    def test_schema_qualified_dangerous(self):
        sql = "DROP TABLE IF EXISTS public.users;"
        patterns = detect_dangerous_patterns(sql)
        assert len(patterns) >= 1
        assert patterns[0].table_name == "users"


class TestParseAlembicMigration:
    """Tests for parse_alembic_migration."""

    def test_create_table(self):
        content = """
def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(255))
    )
"""
        result = parse_alembic_migration(content)
        assert "users" in result.tables_added

    def test_drop_table(self):
        content = """
def downgrade():
    op.drop_table('users')
"""
        result = parse_alembic_migration(content)
        assert "users" in result.tables_dropped

    def test_add_column(self):
        content = """
def upgrade():
    op.add_column('users', sa.Column('email', sa.String(255)))
"""
        result = parse_alembic_migration(content)
        assert "users.email" in result.columns_added

    def test_multiple_operations(self):
        content = """
def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Integer, primary_key=True),
    )
    op.add_column('profiles', sa.Column('bio', sa.Text))

def downgrade():
    op.drop_table('users')
"""
        result = parse_alembic_migration(content)
        assert "users" in result.tables_added
        assert "users" in result.tables_dropped
        assert "profiles.bio" in result.columns_added

    def test_invalid_python(self):
        result = parse_alembic_migration("this is not valid python {{{")
        assert result.tables_added == []

    def test_no_op_calls(self):
        content = """
def upgrade():
    print("no migration here")
"""
        result = parse_alembic_migration(content)
        assert result.tables_added == []


class TestParseSQLAlchemyModels:
    """Tests for parse_sqlalchemy_models."""

    def test_model_with_tablename(self):
        content = """
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
"""
        result = parse_sqlalchemy_models(content)
        assert "users" in result.tables_modified

    def test_model_without_tablename(self):
        content = """
class User(Base):
    id = Column(Integer, primary_key=True)
"""
        result = parse_sqlalchemy_models(content)
        assert "User" in result.tables_modified

    def test_multiple_models(self):
        content = """
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)

class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
"""
        result = parse_sqlalchemy_models(content)
        assert "users" in result.tables_modified
        assert "orders" in result.tables_modified

    def test_non_base_class_ignored(self):
        content = """
class UserMixin:
    name = 'test'

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
"""
        result = parse_sqlalchemy_models(content)
        assert "users" in result.tables_modified
        assert len(result.tables_modified) == 1

    def test_invalid_python(self):
        result = parse_sqlalchemy_models("this is not valid {{{")
        assert result.tables_modified == []
