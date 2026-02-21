"""Tests for drift action templates registry.

Registry composition:
  13 forward actions (original) + 5 rollback actions (internal) = 18 total.

  Rollback actions are internal — the AI planner never selects them directly.
  They are used by RollbackEngine to reverse previously executed steps.

  Rollback actions by category:
    backward_compatibility: drop_column_alias, drop_default_value, drop_migration_view
    monitor_only:           remove_monitoring_alert
    block_deploy:           unblock_deploy
"""

from schemint.drift.action_templates import (
    ACTION_REGISTRY,
    ActionTemplate,
    get_action_ids,
    get_actions_for_category,
    get_notification_action_ids,
    get_templates_for_categories,
    validate_action_id,
)

# ---------------------------------------------------------------------------
# Registry structure tests
# ---------------------------------------------------------------------------

FORWARD_ACTION_COUNT = 13
ROLLBACK_ACTION_COUNT = 5
TOTAL_ACTION_COUNT = FORWARD_ACTION_COUNT + ROLLBACK_ACTION_COUNT


class TestActionRegistry:
    def test_registry_has_correct_total(self):
        assert len(ACTION_REGISTRY) == TOTAL_ACTION_COUNT

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

    def test_backward_compatibility_has_6_templates(self):
        # 3 forward + 3 rollback (drop_column_alias, drop_default_value, drop_migration_view)
        templates = get_actions_for_category("backward_compatibility")
        assert len(templates) == 6

    def test_downstream_updates_has_3_templates(self):
        templates = get_actions_for_category("downstream_updates")
        assert len(templates) == 3

    def test_monitor_only_has_3_templates(self):
        # 2 forward + 1 rollback (remove_monitoring_alert)
        templates = get_actions_for_category("monitor_only")
        assert len(templates) == 3

    def test_block_deploy_has_3_templates(self):
        # 2 forward + 1 rollback (unblock_deploy)
        templates = get_actions_for_category("block_deploy")
        assert len(templates) == 3

    def test_notify_owner_has_3_templates(self):
        templates = get_actions_for_category("notify_owner")
        assert len(templates) == 3

    def test_enforcement_actions_require_approval(self):
        """Only the blocking enforcement actions require approval (not rollback actions)."""
        enforcement = [
            t for t in get_actions_for_category("block_deploy")
            if t.action_id in {"block_deploy", "require_migration_review"}
        ]
        assert len(enforcement) == 2
        for t in enforcement:
            assert t.requires_approval is True

    def test_enforcement_actions_not_reversible(self):
        """Only the blocking enforcement actions are irreversible."""
        enforcement = [
            t for t in get_actions_for_category("block_deploy")
            if t.action_id in {"block_deploy", "require_migration_review"}
        ]
        assert len(enforcement) == 2
        for t in enforcement:
            assert t.reversible is False

    def test_unblock_deploy_is_reversible(self):
        """unblock_deploy (rollback action) is reversible — it undoes block_deploy."""
        unblock = next(t for t in ACTION_REGISTRY if t.action_id == "unblock_deploy")
        assert unblock.reversible is True
        assert unblock.requires_approval is False

    def test_rollback_action_ids_populated(self):
        """Forward actions with rollback inverses should have rollback_action_id set."""
        assert next(t for t in ACTION_REGISTRY if t.action_id == "add_column_alias").rollback_action_id == "drop_column_alias"
        assert next(t for t in ACTION_REGISTRY if t.action_id == "add_default_value").rollback_action_id == "drop_default_value"
        assert next(t for t in ACTION_REGISTRY if t.action_id == "create_migration_view").rollback_action_id == "drop_migration_view"
        assert next(t for t in ACTION_REGISTRY if t.action_id == "add_monitoring_alert").rollback_action_id == "remove_monitoring_alert"
        assert next(t for t in ACTION_REGISTRY if t.action_id == "block_deploy").rollback_action_id == "unblock_deploy"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_get_action_ids_returns_all(self):
        ids = get_action_ids()
        assert len(ids) == TOTAL_ACTION_COUNT

    def test_get_notification_action_ids(self):
        notification_ids = get_notification_action_ids()
        # downstream_updates (3) + monitor_only (3: add, log, remove) + notify_owner (3) = 9
        assert len(notification_ids) == 9

    def test_validate_action_id_valid(self):
        assert validate_action_id("block_deploy") is True
        assert validate_action_id("notify_table_owner") is True
        assert validate_action_id("unblock_deploy") is True  # rollback action

    def test_validate_action_id_invalid(self):
        assert validate_action_id("nonexistent_action") is False
        assert validate_action_id("") is False

    def test_get_actions_for_unknown_category(self):
        assert get_actions_for_category("unknown") == []

    def test_get_templates_for_categories_single(self):
        templates = get_templates_for_categories(["block_deploy"])
        assert len(templates) == 3  # block_deploy, require_migration_review, unblock_deploy
        assert all(t.category == "block_deploy" for t in templates)

    def test_get_templates_for_categories_multiple(self):
        templates = get_templates_for_categories(["block_deploy", "notify_owner"])
        assert len(templates) == 6  # 3 + 3
        categories = {t.category for t in templates}
        assert categories == {"block_deploy", "notify_owner"}

    def test_get_templates_for_categories_empty(self):
        templates = get_templates_for_categories([])
        assert templates == []

    def test_get_templates_for_categories_filters_correctly(self):
        """Only returns templates in the allowed categories, not the full registry."""
        templates = get_templates_for_categories(["monitor_only"])
        assert len(templates) == 3  # add_monitoring_alert, log_drift_event, remove_monitoring_alert
        for t in templates:
            assert t.category == "monitor_only"
        # Verify backward_compatibility is NOT included
        ids = {t.action_id for t in templates}
        assert "add_column_alias" not in ids
