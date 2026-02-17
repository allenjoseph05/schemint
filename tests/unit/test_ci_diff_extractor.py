"""
Tests for CI Diff Extractor.
"""


from schemint.ci.diff_extractor import DiffExtractor
from schemint.ci.providers.base import DiffFile


class TestDiffExtractor:
    """Tests for DiffExtractor."""

    def test_extract_from_sql_file(self):
        """Test extracting SQL changes from a SQL file."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/users.sql",
                change_type="added",
                content="""
                CREATE TABLE users (
                    id INT PRIMARY KEY,
                    name VARCHAR(255)
                );
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert result.total_tables_affected == 1
        assert "schema/users.sql" in result.sql_files
        assert len(result.sql_changes) == 1
        assert "users" in result.sql_changes[0].tables_added

    def test_extract_alter_table(self):
        """Test extracting ALTER TABLE statements."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="migrations/002_add_email.sql",
                change_type="added",
                content="""
                ALTER TABLE users ADD COLUMN email VARCHAR(255);
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        assert "users" in result.sql_changes[0].tables_modified

    def test_extract_drop_table(self):
        """Test extracting DROP TABLE statements."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="migrations/003_drop_legacy.sql",
                change_type="added",
                content="""
                DROP TABLE IF EXISTS legacy_users;
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        assert "legacy_users" in result.sql_changes[0].tables_dropped

    def test_extract_multiple_files(self):
        """Test extracting from multiple SQL files."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/users.sql",
                change_type="added",
                content="CREATE TABLE users (id INT);",
            ),
            DiffFile(
                path="schema/orders.sql",
                change_type="added",
                content="CREATE TABLE orders (id INT);",
            ),
            DiffFile(
                path="readme.md",
                change_type="modified",
                content="# README",
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_files) == 2
        assert result.total_tables_affected == 2

    def test_skip_deleted_files(self):
        """Test that deleted files are not analyzed for content."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/old.sql",
                change_type="deleted",
                content=None,  # Deleted files may not have content
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        # File should be detected but not analyzed for SQL
        assert len(result.sql_files) == 1
        assert len(result.sql_changes) == 1
        # Deleted files shouldn't add to tables affected
        assert result.total_tables_affected == 0

    def test_extract_alembic_migration(self):
        """Test extracting from Alembic migration."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="alembic/versions/001_create_users.py",
                change_type="added",
                content="""
def upgrade():
    op.create_table('users',
        Column('id', Integer, primary_key=True),
        Column('name', String(255))
    )
    op.add_column('profiles', Column('bio', Text))

def downgrade():
    op.drop_table('users')
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        change = result.sql_changes[0]
        assert change.file_path == "alembic/versions/001_create_users.py"
        # Note: Alembic parsing finds table operations
        assert "users" in change.tables_added
        assert "users" in change.tables_dropped  # From downgrade

    def test_extract_rails_migration(self):
        """Test extracting from Rails migration."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="db/migrate/20240101_create_users.rb",
                change_type="added",
                content="""
class CreateUsers < ActiveRecord::Migration
  def change
    create_table :users do |t|
      t.string :name
    end
  end
end
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        change = result.sql_changes[0]
        assert change.file_path == "db/migrate/20240101_create_users.rb"
        assert "users" in change.tables_added

    def test_extract_prisma_schema(self):
        """Test extracting from Prisma schema."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="prisma/schema.prisma",
                change_type="modified",
                content="""
model User {
  id    Int     @id @default(autoincrement())
  email String  @unique
  name  String?
}

model Post {
  id        Int     @id
  title     String
}
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        change = result.sql_changes[0]
        assert "User" in change.tables_modified
        assert "Post" in change.tables_modified

    def test_extract_sqlalchemy_models(self):
        """Test extracting from SQLAlchemy models."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="app/models.py",
                change_type="modified",
                content="""
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)

class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        change = result.sql_changes[0]
        assert "users" in change.tables_modified
        assert "orders" in change.tables_modified

    def test_extract_typeorm_entity(self):
        """Test extracting from TypeORM entity."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="src/entities/user.ts",
                change_type="added",
                content="""
import { Entity, PrimaryGeneratedColumn, Column } from "typeorm";

@Entity()
export class User {
    @PrimaryGeneratedColumn()
    id: number;

    @Column()
    name: string;
}
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        assert "User" in result.sql_changes[0].tables_modified

    def test_schema_diff_properties(self):
        """Test SchemaDiff properties."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(path="schema/users.sql", change_type="added", content="CREATE TABLE users (id INT);"),
            DiffFile(path="schema/orders.sql", change_type="modified", content="ALTER TABLE orders ADD status INT;"),
            DiffFile(path="readme.md", change_type="modified", content="# README"),
        ]

        result = extractor.extract_from_diff_files(
            diff_files,
            base_ref="main",
            head_ref="feature-branch",
        )

        assert result.base_ref == "main"
        assert result.ref == "feature-branch"
        assert len(result.files_changed) == 3
        assert len(result.sql_files) == 2

    def test_extract_schema_qualified_table(self):
        """Test extracting schema-qualified table names like public.users."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/init.sql",
                change_type="added",
                content="CREATE TABLE public.users (id INT PRIMARY KEY);",
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        assert "users" in result.sql_changes[0].tables_added

    def test_extract_quoted_identifiers(self):
        """Test extracting quoted table/column names."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/quoted.sql",
                change_type="added",
                content='CREATE TABLE "my_table" (id INT);',
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert len(result.sql_changes) == 1
        assert "my_table" in result.sql_changes[0].tables_added

    def test_extract_multiline_create_table(self):
        """Test extracting from multi-line CREATE TABLE."""
        extractor = DiffExtractor()

        diff_files = [
            DiffFile(
                path="schema/multiline.sql",
                change_type="added",
                content="""
CREATE TABLE
    users
(
    id INT PRIMARY KEY,
    name VARCHAR(255)
);
                """,
            ),
        ]

        result = extractor.extract_from_diff_files(diff_files)

        assert "users" in result.sql_changes[0].tables_added
