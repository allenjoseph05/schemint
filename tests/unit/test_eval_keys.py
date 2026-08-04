"""Tests for the blast-radius key namespace.

Both the oracle (reading pg_catalog) and the adapters (reading parsed DDL)
build keys through this module. If normalization drifts, precision and recall
silently collapse instead of failing, so these tests pin the exact strings.
"""

from __future__ import annotations

import pytest

from evals.core import keys


@pytest.mark.unit
class TestNormalizeName:
    def test_lowercases(self):
        assert keys.normalize_name("UserSummary") == "usersummary"

    def test_strips_double_quotes(self):
        assert keys.normalize_name('"Users"') == "users"

    def test_strips_public_qualifier(self):
        assert keys.normalize_name("public.user_summary") == "user_summary"

    def test_keeps_non_public_schema(self):
        assert keys.normalize_name("analytics.daily") == "analytics.daily"

    def test_normalizes_each_dotted_part(self):
        assert keys.normalize_name('"Public"."User_Summary"') == "user_summary"

    def test_strips_surrounding_whitespace(self):
        assert keys.normalize_name("  orders  ") == "orders"

    def test_catalog_and_ddl_spellings_agree(self):
        # The exact disagreement this module exists to absorb: pg_catalog
        # returns a bare lowercase name, the DDL parser may return it quoted
        # and schema-qualified.
        assert keys.normalize_name("user_summary") == keys.normalize_name('public."User_Summary"')


@pytest.mark.unit
class TestMakeKey:
    def test_builds_type_prefixed_key(self):
        assert keys.make_key("view", "user_summary") == "view:user_summary"

    def test_normalizes_the_name(self):
        assert keys.make_key("view", 'public."User_Summary"') == "view:user_summary"

    def test_rejects_unknown_object_type(self):
        with pytest.raises(keys.BlastRadiusKeyError, match="Unknown object type"):
            keys.make_key("widget", "x")

    def test_rejects_empty_name(self):
        with pytest.raises(keys.BlastRadiusKeyError, match="Empty object name"):
            keys.make_key("view", "   ")

    def test_column_key_joins_table_and_column(self):
        assert keys.make_column_key("Users", "Email") == "column:users.email"


@pytest.mark.unit
class TestParseKey:
    def test_round_trips(self):
        key = keys.make_key("foreign_key", "orders_user_id_fkey")
        assert keys.parse_key(key) == ("foreign_key", "orders_user_id_fkey")

    def test_splits_on_first_colon_only(self):
        # A stray colon must not silently reassign the object type.
        with pytest.raises(keys.BlastRadiusKeyError):
            keys.parse_key("view:a:b")

    def test_rejects_missing_separator(self):
        with pytest.raises(keys.BlastRadiusKeyError, match="expected"):
            keys.parse_key("user_summary")

    def test_rejects_unknown_type(self):
        with pytest.raises(keys.BlastRadiusKeyError, match="unknown object type"):
            keys.parse_key("widget:x")

    def test_is_valid_key(self):
        assert keys.is_valid_key("view:user_summary")
        assert not keys.is_valid_key("nonsense")


@pytest.mark.unit
class TestKeySet:
    def test_deduplicates_after_normalization(self):
        result = keys.key_set(["view:User_Summary", "view:user_summary"])
        assert result == {"view:user_summary"}

    def test_drops_malformed_keys_instead_of_raising(self):
        # A malformed key from an adapter is a scoring miss, not a crash.
        result = keys.key_set(["view:ok", "garbage", "widget:x"])
        assert result == {"view:ok"}

    def test_empty_input(self):
        assert keys.key_set([]) == set()
