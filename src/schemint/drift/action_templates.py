"""Predefined action registry for schema drift remediation.

13 templates across 5 categories. Each template declares whether it is
a notification-only action (no schema mutation) vs. a structural action.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CategoryType = Literal[
    "backward_compatibility",
    "downstream_updates",
    "monitor_only",
    "block_deploy",
    "notify_owner",
]


class ActionTemplate(BaseModel):
    """A single predefined remediation action."""

    action_id: str
    category: CategoryType
    description: str
    is_notification: bool = False
    reversible: bool = True
    requires_approval: bool = False


# =============================================================================
# Action Registry — 13 templates, 5 categories
# =============================================================================

ACTION_REGISTRY: list[ActionTemplate] = [
    # --- backward_compatibility (structural) ---
    ActionTemplate(
        action_id="add_column_alias",
        category="backward_compatibility",
        description="Add a column alias or view to preserve old column name",
        reversible=True,
    ),
    ActionTemplate(
        action_id="add_default_value",
        category="backward_compatibility",
        description="Add a default value to a new NOT NULL column for backward compatibility",
        reversible=True,
    ),
    ActionTemplate(
        action_id="create_migration_view",
        category="backward_compatibility",
        description="Create a migration view that maps old schema to new schema",
        reversible=True,
    ),
    # --- downstream_updates (structural) ---
    ActionTemplate(
        action_id="update_downstream_query",
        category="downstream_updates",
        description="Flag downstream queries that reference changed columns",
        is_notification=True,
    ),
    ActionTemplate(
        action_id="update_downstream_model",
        category="downstream_updates",
        description="Flag downstream ORM models or dbt models that need updating",
        is_notification=True,
    ),
    ActionTemplate(
        action_id="regenerate_api_contract",
        category="downstream_updates",
        description="Flag API contracts (OpenAPI, GraphQL) that reference changed schema",
        is_notification=True,
    ),
    # --- monitor_only (notification) ---
    ActionTemplate(
        action_id="add_monitoring_alert",
        category="monitor_only",
        description="Add monitoring alert for schema drift on this table",
        is_notification=True,
    ),
    ActionTemplate(
        action_id="log_drift_event",
        category="monitor_only",
        description="Log schema drift event for audit trail",
        is_notification=True,
    ),
    # --- block_deploy (enforcement) ---
    ActionTemplate(
        action_id="block_deploy",
        category="block_deploy",
        description="Block deployment until schema change is reviewed",
        is_notification=False,
        reversible=False,
        requires_approval=True,
    ),
    ActionTemplate(
        action_id="require_migration_review",
        category="block_deploy",
        description="Require explicit migration review before merge",
        is_notification=False,
        reversible=False,
        requires_approval=True,
    ),
    # --- notify_owner (notification) ---
    ActionTemplate(
        action_id="notify_table_owner",
        category="notify_owner",
        description="Notify the table owner about the schema change",
        is_notification=True,
    ),
    ActionTemplate(
        action_id="notify_downstream_teams",
        category="notify_owner",
        description="Notify downstream teams that depend on changed tables",
        is_notification=True,
    ),
    ActionTemplate(
        action_id="create_review_ticket",
        category="notify_owner",
        description="Create a review ticket for the schema change",
        is_notification=True,
    ),
]

# Pre-built lookup for O(1) access
_REGISTRY_BY_ID: dict[str, ActionTemplate] = {t.action_id: t for t in ACTION_REGISTRY}
_REGISTRY_BY_CATEGORY: dict[str, list[ActionTemplate]] = {}
for _t in ACTION_REGISTRY:
    _REGISTRY_BY_CATEGORY.setdefault(_t.category, []).append(_t)


# =============================================================================
# Helper functions
# =============================================================================


def get_action_ids() -> list[str]:
    """Return all valid action IDs."""
    return list(_REGISTRY_BY_ID.keys())


def get_notification_action_ids() -> list[str]:
    """Return action IDs that are notification-only (no schema mutation)."""
    return [t.action_id for t in ACTION_REGISTRY if t.is_notification]


def get_actions_for_category(category: str) -> list[ActionTemplate]:
    """Return all templates for a given category."""
    return _REGISTRY_BY_CATEGORY.get(category, [])


def validate_action_id(action_id: str) -> bool:
    """Check whether an action_id exists in the registry."""
    return action_id in _REGISTRY_BY_ID


def get_templates_for_categories(
    categories: list[str],
) -> list[ActionTemplate]:
    """Return ONLY templates whose category is in the allowed list."""
    result: list[ActionTemplate] = []
    for cat in categories:
        result.extend(_REGISTRY_BY_CATEGORY.get(cat, []))
    return result
