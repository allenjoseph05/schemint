"""Unit tests for schema analyzer."""

import pytest

from schemint.core.analyzer import analyze_sql
from schemint.models.issue import IssueCategory, IssueSeverity
from tests.fixtures.schemas import BAD_SCHEMA, GOOD_SCHEMA, SIMPLE_SCHEMA


class TestAnalyzer:
    """Tests for schema analyzer."""

    def test_analyze_returns_result(self):
        """Test that analyze returns an AnalysisResult."""
        result = analyze_sql(SIMPLE_SCHEMA)

        assert result is not None
        assert result.id.startswith("ana_")
        assert result.score is not None
        assert result.tables is not None

    def test_analyze_bad_schema_low_score(self):
        """Test that bad schema gets low score."""
        result = analyze_sql(BAD_SCHEMA)

        assert result.score.total < 50
        assert result.critical_count > 0

    def test_analyze_good_schema_high_score(self):
        """Test that good schema gets high score."""
        result = analyze_sql(GOOD_SCHEMA)

        assert result.score.total > 70
        assert result.critical_count == 0

    def test_detects_missing_primary_key(self):
        """Test detection of missing primary key."""
        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = analyze_sql(sql)

        pk_issues = [
            i for i in result.issues
            if i.category == IssueCategory.MISSING_PRIMARY_KEY
        ]
        assert len(pk_issues) == 1
        assert pk_issues[0].severity == IssueSeverity.CRITICAL

    def test_detects_float_for_money(self):
        """Test detection of FLOAT used for money columns."""
        sql = "CREATE TABLE products (id INT PRIMARY KEY, price FLOAT);"
        result = analyze_sql(sql)

        float_issues = [
            i for i in result.issues
            if i.category == IssueCategory.WRONG_DATA_TYPE
            and "FLOAT" in i.title
        ]
        assert len(float_issues) == 1
        assert float_issues[0].severity == IssueSeverity.CRITICAL

    def test_detects_missing_timestamps(self):
        """Test detection of missing timestamps."""
        sql = "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100));"
        result = analyze_sql(sql)

        ts_issues = [
            i for i in result.issues
            if i.category == IssueCategory.MISSING_TIMESTAMPS
        ]
        assert len(ts_issues) == 1
        assert ts_issues[0].severity == IssueSeverity.WARNING

    def test_no_timestamp_warning_when_present(self):
        """Test no timestamp warning when timestamps exist."""
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        result = analyze_sql(sql)

        ts_issues = [
            i for i in result.issues
            if i.category == IssueCategory.MISSING_TIMESTAMPS
        ]
        assert len(ts_issues) == 0

    def test_detects_reserved_word(self):
        """Test detection of reserved word as column name."""
        sql = "CREATE TABLE users (id INT PRIMARY KEY, password VARCHAR(255));"
        result = analyze_sql(sql)

        reserved_issues = [
            i for i in result.issues
            if i.category == IssueCategory.RESERVED_WORD
        ]
        assert len(reserved_issues) == 1

    def test_fix_scripts_generated(self):
        """Test that fix scripts are generated."""
        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = analyze_sql(sql)

        # Should have fix script for missing primary key
        pk_issues = [
            i for i in result.issues
            if i.category == IssueCategory.MISSING_PRIMARY_KEY
        ]
        assert pk_issues[0].fix_script is not None
        assert "ALTER TABLE" in pk_issues[0].fix_script

    def test_good_practices_found(self):
        """Test that good practices are identified."""
        result = analyze_sql(GOOD_SCHEMA)

        assert len(result.good_practices) > 0

    def test_table_summaries_created(self):
        """Test that table summaries are created."""
        result = analyze_sql(GOOD_SCHEMA)

        assert len(result.tables) == 2
        assert result.tables[0].name == "users"
        assert result.tables[0].has_primary_key

    def test_score_grade(self):
        """Test score grade calculation."""
        result = analyze_sql(GOOD_SCHEMA)
        assert result.score.grade in ["A", "B", "C", "D", "F"]

        result = analyze_sql(BAD_SCHEMA)
        assert result.score.grade in ["D", "F"]

    def test_combined_fix_script(self):
        """Test combined fix script property."""
        result = analyze_sql(BAD_SCHEMA)

        fix_script = result.fix_script
        assert isinstance(fix_script, str)
        # Should have multiple fixes combined
        if result.issues:
            assert "ALTER TABLE" in fix_script or "CREATE INDEX" in fix_script


class TestScoring:
    """Tests for scoring logic."""

    def test_perfect_score_no_issues(self):
        """Test that no issues gives high score."""
        # This schema is well-designed
        sql = """
        CREATE TABLE logs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        );
        """
        result = analyze_sql(sql)
        assert result.score.total >= 90

    def test_critical_issues_major_deduction(self):
        """Test that critical issues cause major score deduction."""
        # Missing primary key is critical
        sql = "CREATE TABLE users (id INT, name VARCHAR(100));"
        result = analyze_sql(sql)

        # Should deduct significantly
        assert result.score.total < 90

    def test_multiple_issues_compound(self):
        """Test that multiple issues compound."""
        result1 = analyze_sql("CREATE TABLE a (id INT);")
        result2 = analyze_sql(BAD_SCHEMA)  # Multiple tables with issues

        # More issues = lower score
        assert result2.score.total <= result1.score.total
