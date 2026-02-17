"""Tests for drift action templates registry."""


from schemint.drift.action_templates import (
    ACTION_REGISTRY,
    ActionTemplate,
    get_action_ids,
    get_actions_for_category,
    get_notification_action_ids,
    get_templates_for_categories,
    validate_action_id,
)


class TestActionRegistry:
    def test_registry_has_13_templates(self):
        assert len(ACTION_REGISTRY) == 13

    def test_all_ids_unique(self):
        ids = [t.action_id for t in ACTION_REGISTRY]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {ids}"

    def test_all_templates_are_action_template(self):
        for t in ACTION_REGISTRY:
            assert isinstance(t, ActionTemplate)

    def test_every_category_covered(self):
        categories = {t.category for t in ACTION_REGISTRY}
        expected = {
            "backward_compatibility",
            "downstream_updates",
            "monitor_only",
            "block_deploy",
            "notify_owner",
        }
        assert categories == expected

    def test_backward_compatibility_has_3_templates(self):
        templates = get_actions_for_category("backward_compatibility")
        assert len(templates) == 3

    def test_downstream_updates_has_3_templates(self):
        templates = get_actions_for_category("downstream_updates")
        assert len(templates) == 3

    def test_monitor_only_has_2_templates(self):
        templates = get_actions_for_category("monitor_only")
        assert len(templates) == 2

    def test_block_deploy_has_2_templates(self):
        templates = get_actions_for_category("block_deploy")
        assert len(templates) == 2

    def test_notify_owner_has_3_templates(self):
        templates = get_actions_for_category("notify_owner")
        assert len(templates) == 3

    def test_block_deploy_requires_approval(self):
        templates = get_actions_for_category("block_deploy")
        for t in templates:
            assert t.requires_approval is True

    def test_block_deploy_not_reversible(self):
        templates = get_actions_for_category("block_deploy")
        for t in templates:
            assert t.reversible is False


class TestHelperFunctions:
    def test_get_action_ids_returns_all(self):
        ids = get_action_ids()
        assert len(ids) == 13

    def test_get_notification_action_ids(self):
        notification_ids = get_notification_action_ids()
        # All downstream_updates, monitor_only, and notify_owner are notifications
        assert len(notification_ids) == 8  # 3 + 2 + 3

    def test_validate_action_id_valid(self):
        assert validate_action_id("block_deploy") is True
        assert validate_action_id("notify_table_owner") is True

    def test_validate_action_id_invalid(self):
        assert validate_action_id("nonexistent_action") is False
        assert validate_action_id("") is False

    def test_get_actions_for_unknown_category(self):
        assert get_actions_for_category("unknown") == []

    def test_get_templates_for_categories_single(self):
        templates = get_templates_for_categories(["block_deploy"])
        assert len(templates) == 2
        assert all(t.category == "block_deploy" for t in templates)

    def test_get_templates_for_categories_multiple(self):
        templates = get_templates_for_categories(["block_deploy", "notify_owner"])
        assert len(templates) == 5  # 2 + 3
        categories = {t.category for t in templates}
        assert categories == {"block_deploy", "notify_owner"}

    def test_get_templates_for_categories_empty(self):
        templates = get_templates_for_categories([])
        assert templates == []

    def test_get_templates_for_categories_filters_correctly(self):
        """Only returns templates in the allowed categories, not the full registry."""
        templates = get_templates_for_categories(["monitor_only"])
        assert len(templates) == 2
        for t in templates:
            assert t.category == "monitor_only"
        # Verify backward_compatibility is NOT included
        ids = {t.action_id for t in templates}
        assert "add_column_alias" not in ids
