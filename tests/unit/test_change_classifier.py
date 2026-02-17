"""Tests for the change risk classifier."""

from schemint.drift.change_classifier import (
    _TYPE_TO_FAMILY,
    TYPE_FAMILIES,
    _extract_base_type,
    _extract_type_length,
    classify_change,
    classify_fk_action_change,
    classify_type_change,
)
from schemint.drift.models import SchemaChangeEvent

# =============================================================================
# Type family system tests
# =============================================================================


class TestTypeFamilies:
    """Verify type family structure is consistent."""

    def test_all_families_non_empty(self):
        for family_name, members in TYPE_FAMILIES.items():
            assert len(members) > 0, f"Family {family_name} is empty"

    def test_reverse_lookup_covers_all_types(self):
        all_types = []
        for members in TYPE_FAMILIES.values():
            all_types.extend(members)
        for t in all_types:
            assert t in _TYPE_TO_FAMILY, f"Type {t} missing from reverse lookup"

    def test_no_duplicate_types_across_families(self):
        seen: dict[str, str] = {}
        for family_name, members in TYPE_FAMILIES.items():
            for t in members:
                assert t not in seen, f"Type {t} in both {seen[t]} and {family_name}"
                seen[t] = family_name


class TestExtractBaseType:
    def test_simple_type(self):
        assert _extract_base_type("integer") == "integer"

    def test_type_with_params(self):
        assert _extract_base_type("varchar(255)") == "varchar"

    def test_type_with_precision(self):
        assert _extract_base_type("decimal(10,2)") == "decimal"

    def test_uppercase(self):
        assert _extract_base_type("VARCHAR(100)") == "varchar"


class TestExtractTypeLength:
    def test_with_length(self):
        assert _extract_type_length("varchar(255)") == 255

    def test_without_length(self):
        assert _extract_type_length("integer") is None

    def test_with_precision(self):
        assert _extract_type_length("decimal(10,2)") == 10


# =============================================================================
# Type change classification
# =============================================================================


class TestClassifyTypeChange:
    """Test type compatibility classification using family system."""

    def test_same_type_same_length_is_safe(self):
        assert classify_type_change("varchar(255)", "varchar(255)") == "safe"

    def test_same_type_wider_length_is_safe(self):
        assert classify_type_change("varchar(50)", "varchar(255)") == "safe"

    def test_same_type_narrower_length_is_breaking(self):
        assert classify_type_change("varchar(255)", "varchar(50)") == "potentially_breaking"

    def test_int_to_bigint_is_safe(self):
        """Widening within integer family."""
        assert classify_type_change("integer", "bigint") == "safe"

    def test_bigint_to_int_is_breaking(self):
        """Narrowing within integer family."""
        assert classify_type_change("bigint", "integer") == "potentially_breaking"

    def test_smallint_to_bigint_is_safe(self):
        assert classify_type_change("smallint", "bigint") == "safe"

    def test_tinyint_to_integer_is_safe(self):
        assert classify_type_change("tinyint", "integer") == "safe"

    def test_float_to_double_is_safe(self):
        assert classify_type_change("float", "double") == "safe"

    def test_double_to_float_is_breaking(self):
        assert classify_type_change("double", "float") == "potentially_breaking"

    def test_char_to_varchar_is_safe(self):
        assert classify_type_change("char", "varchar") == "safe"

    def test_varchar_to_text_is_safe(self):
        assert classify_type_change("varchar", "text") == "safe"

    def test_text_to_varchar_is_breaking(self):
        assert classify_type_change("text", "varchar") == "potentially_breaking"

    def test_integer_to_text_is_breaking(self):
        """Cross-family change."""
        assert classify_type_change("integer", "text") == "breaking"

    def test_varchar_to_integer_is_breaking(self):
        """Cross-family change."""
        assert classify_type_change("varchar", "integer") == "breaking"

    def test_boolean_to_integer_is_breaking(self):
        assert classify_type_change("boolean", "integer") == "breaking"

    def test_json_to_jsonb_is_safe(self):
        assert classify_type_change("json", "jsonb") == "safe"

    def test_unknown_type_is_needs_review(self):
        assert classify_type_change("custom_type", "integer") == "needs_review"

    def test_both_unknown_types(self):
        assert classify_type_change("custom_a", "custom_b") == "needs_review"

    def test_serial_to_bigserial_is_safe(self):
        assert classify_type_change("serial", "bigserial") == "safe"

    def test_same_base_no_length(self):
        assert classify_type_change("integer", "integer") == "safe"


# =============================================================================
# FK action classification
# =============================================================================


class TestClassifyFkActionChange:
    def test_cascade_to_restrict_is_breaking(self):
        assert classify_fk_action_change("CASCADE", "RESTRICT") == "potentially_breaking"

    def test_restrict_to_cascade_is_safe(self):
        assert classify_fk_action_change("RESTRICT", "CASCADE") == "safe"

    def test_no_action_to_cascade_is_safe(self):
        assert classify_fk_action_change("NO ACTION", "CASCADE") == "safe"

    def test_cascade_to_set_null_is_breaking(self):
        assert classify_fk_action_change("CASCADE", "SET NULL") == "potentially_breaking"

    def test_none_to_cascade_is_safe(self):
        """None defaults to NO ACTION (SQL standard)."""
        assert classify_fk_action_change(None, "CASCADE") == "safe"

    def test_cascade_to_none_is_breaking(self):
        assert classify_fk_action_change("CASCADE", None) == "potentially_breaking"

    def test_same_action_is_safe(self):
        assert classify_fk_action_change("CASCADE", "CASCADE") == "safe"

    def test_unknown_action_is_needs_review(self):
        assert classify_fk_action_change("CASCADE", "CUSTOM_ACTION") == "needs_review"


# =============================================================================
# Full change event classification
# =============================================================================


class TestClassifyChange:
    def test_table_dropped_is_breaking(self):
        event = SchemaChangeEvent(change_type="table_dropped", table="users")
        assert classify_change(event) == "breaking"

    def test_table_added_is_safe(self):
        event = SchemaChangeEvent(change_type="table_added", table="new_table")
        assert classify_change(event) == "safe"

    def test_column_dropped_is_breaking(self):
        event = SchemaChangeEvent(change_type="column_dropped", table="users", column="email")
        assert classify_change(event) == "breaking"

    def test_column_added_is_safe(self):
        event = SchemaChangeEvent(change_type="column_added", table="users", column="bio")
        assert classify_change(event) == "safe"

    def test_nullable_to_not_null_is_breaking(self):
        event = SchemaChangeEvent(
            change_type="column_nullable_change",
            table="users",
            column="email",
            old_value="True",
            new_value="False",
        )
        assert classify_change(event) == "potentially_breaking"

    def test_not_null_to_nullable_is_safe(self):
        event = SchemaChangeEvent(
            change_type="column_nullable_change",
            table="users",
            column="email",
            old_value="False",
            new_value="True",
        )
        assert classify_change(event) == "safe"

    def test_type_change_delegates_to_type_classifier(self):
        event = SchemaChangeEvent(
            change_type="column_type_change",
            table="users",
            column="id",
            old_value="integer",
            new_value="bigint",
        )
        assert classify_change(event) == "safe"

    def test_fk_action_change_delegates(self):
        event = SchemaChangeEvent(
            change_type="fk_action_change",
            table="orders",
            column="user_id",
            old_value="ON DELETE CASCADE",
            new_value="ON DELETE RESTRICT",
        )
        # The classifier extracts the action part
        result = classify_change(event)
        assert result in ("potentially_breaking", "needs_review")

    def test_index_added_is_safe(self):
        event = SchemaChangeEvent(change_type="index_added", table="users")
        assert classify_change(event) == "safe"

    def test_index_dropped_is_needs_review(self):
        event = SchemaChangeEvent(change_type="index_dropped", table="users")
        assert classify_change(event) == "needs_review"

    def test_fk_added_is_potentially_breaking(self):
        event = SchemaChangeEvent(change_type="fk_added", table="orders")
        assert classify_change(event) == "potentially_breaking"

    def test_default_added_is_safe(self):
        event = SchemaChangeEvent(
            change_type="column_default_change",
            table="users",
            column="status",
            old_value=None,
            new_value="'active'",
        )
        assert classify_change(event) == "safe"

    def test_default_removed_is_needs_review(self):
        event = SchemaChangeEvent(
            change_type="column_default_change",
            table="users",
            column="status",
            old_value="'active'",
            new_value=None,
        )
        assert classify_change(event) == "needs_review"

    def test_constraint_change_is_needs_review(self):
        event = SchemaChangeEvent(
            change_type="column_constraint_change",
            table="users",
            column="age",
            old_value="CHECK(age > 0)",
            new_value="CHECK(age > 18)",
        )
        assert classify_change(event) == "needs_review"
