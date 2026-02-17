"""Comprehensive tests for Phase C: Tier 2 snapshot models, diff logic, risk classification.

Covers:
    - ExtensionSnapshot, PermissionSnapshot, PolicySnapshot models
    - PartitionInfo, MaterializedViewSnapshot, ColumnStatistics models
    - Extension/Permission/Policy/Partition/MatView diff detection
    - Risk classification for all 13 new change types
    - Combined diff with Phase B objects
    - SchemaSnapshot integration with all new fields
"""

from datetime import datetime, timezone

from schemint.drift.change_classifier import classify_change
from schemint.drift.differ import SchemaDiffer
from schemint.drift.models import (
    ColumnSnapshot,
    ColumnStatistics,
    ExtensionSnapshot,
    MaterializedViewSnapshot,
    PartitionInfo,
    PermissionSnapshot,
    PolicySnapshot,
    SchemaChangeEvent,
    SchemaSnapshot,
    TableSnapshot,
)

# =========================================================================
# Helper: minimal snapshot factory
# =========================================================================


def _make_snapshot(**overrides) -> SchemaSnapshot:
    """Create a minimal SchemaSnapshot with optional overrides."""
    defaults = {
        "snapshot_id": "test",
        "captured_at": datetime.now(timezone.utc),
        "source": "ddl",
        "database_type": "postgresql",
        "schema_name": "public",
        "tables": {},
    }
    defaults.update(overrides)
    return SchemaSnapshot(**defaults)


def _make_table(table_name: str, col_defs: dict[str, str] | None = None) -> TableSnapshot:
    """Create a minimal TableSnapshot with columns."""
    columns = {}
    for col_name, col_type in (col_defs or {}).items():
        columns[col_name] = ColumnSnapshot(name=col_name, type=col_type)
    return TableSnapshot(name=table_name, columns=columns)


# =========================================================================
# Model Validation Tests
# =========================================================================


class TestExtensionSnapshotModel:
    def test_defaults(self):
        ext = ExtensionSnapshot(name="pg_trgm")
        assert ext.name == "pg_trgm"
        assert ext.version == ""
        assert ext.installed_schema == "public"

    def test_full(self):
        ext = ExtensionSnapshot(
            name="postgis",
            version="3.4.0",
            installed_schema="extensions",
        )
        assert ext.version == "3.4.0"
        assert ext.installed_schema == "extensions"


class TestPermissionSnapshotModel:
    def test_required_fields(self):
        perm = PermissionSnapshot(
            table_name="users",
            grantee="app_role",
            privilege_type="SELECT",
        )
        assert perm.table_name == "users"
        assert perm.grantee == "app_role"
        assert perm.privilege_type == "SELECT"
        assert perm.is_grantable is False

    def test_grantable(self):
        perm = PermissionSnapshot(
            table_name="users",
            grantee="admin",
            privilege_type="ALL",
            is_grantable=True,
        )
        assert perm.is_grantable is True


class TestPolicySnapshotModel:
    def test_defaults(self):
        pol = PolicySnapshot(name="tenant_isolation", table="orders")
        assert pol.command == "ALL"
        assert pol.permissive is True
        assert pol.roles == []
        assert pol.qual_expression is None
        assert pol.with_check_expression is None

    def test_full(self):
        pol = PolicySnapshot(
            name="tenant_select",
            table="orders",
            command="SELECT",
            permissive=False,
            roles=["app_user", "admin"],
            qual_expression="tenant_id = current_setting('app.tenant_id')::int",
            with_check_expression="tenant_id = current_setting('app.tenant_id')::int",
        )
        assert pol.command == "SELECT"
        assert pol.permissive is False
        assert len(pol.roles) == 2
        assert pol.qual_expression is not None


class TestPartitionInfoModel:
    def test_defaults(self):
        part = PartitionInfo(partition_name="orders_2024_01", parent_table="orders")
        assert part.partition_bound == ""

    def test_full(self):
        part = PartitionInfo(
            partition_name="orders_2024_01",
            parent_table="orders",
            partition_bound="FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')",
        )
        assert "2024-01-01" in part.partition_bound


class TestMaterializedViewSnapshotModel:
    def test_defaults(self):
        mv = MaterializedViewSnapshot(name="monthly_summary")
        assert mv.definition == ""
        assert mv.is_populated is True
        assert mv.tablespace is None
        assert mv.source_tables == []

    def test_full(self):
        mv = MaterializedViewSnapshot(
            name="monthly_summary",
            definition="SELECT date_trunc('month', created_at) AS month, count(*) FROM orders GROUP BY 1",
            is_populated=True,
            tablespace="fast_ssd",
            source_tables=["orders"],
        )
        assert mv.tablespace == "fast_ssd"
        assert "orders" in mv.source_tables


class TestColumnStatisticsModel:
    def test_defaults(self):
        stat = ColumnStatistics(column_name="id", table_name="users")
        assert stat.null_frac == 0.0
        assert stat.n_distinct == 0.0
        assert stat.avg_width == 0
        assert stat.correlation == 0.0

    def test_full(self):
        stat = ColumnStatistics(
            column_name="email",
            table_name="users",
            null_frac=0.02,
            n_distinct=-1.0,
            avg_width=32,
            correlation=0.95,
        )
        assert stat.n_distinct == -1.0
        assert stat.avg_width == 32


# =========================================================================
# SchemaSnapshot Integration — new fields
# =========================================================================


class TestSchemaSnapshotWithTier2:
    def test_snapshot_with_extensions(self):
        snap = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6"),
                "postgis": ExtensionSnapshot(name="postgis", version="3.4.0"),
            }
        )
        assert len(snap.extensions) == 2
        assert snap.extensions["pg_trgm"].version == "1.6"

    def test_snapshot_with_permissions(self):
        snap = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="INSERT"),
            ]
        )
        assert len(snap.permissions) == 2

    def test_snapshot_with_policies(self):
        snap = _make_snapshot(
            policies={
                "tenant_iso": PolicySnapshot(name="tenant_iso", table="orders"),
            }
        )
        assert "tenant_iso" in snap.policies

    def test_snapshot_with_partitions(self):
        snap = _make_snapshot(
            partitions={
                "orders": [
                    PartitionInfo(partition_name="orders_2024_01", parent_table="orders"),
                    PartitionInfo(partition_name="orders_2024_02", parent_table="orders"),
                ],
            }
        )
        assert len(snap.partitions["orders"]) == 2

    def test_snapshot_with_materialized_views(self):
        snap = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="SELECT count(*) FROM orders",
                ),
            }
        )
        assert snap.materialized_views["mv_summary"].definition == "SELECT count(*) FROM orders"

    def test_snapshot_with_column_statistics(self):
        snap = _make_snapshot(
            column_statistics={
                "users": [
                    ColumnStatistics(column_name="id", table_name="users", n_distinct=-1.0),
                    ColumnStatistics(column_name="email", table_name="users", null_frac=0.01),
                ],
            }
        )
        assert len(snap.column_statistics["users"]) == 2


# =========================================================================
# Extension Diff Tests
# =========================================================================


class TestExtensionDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_extension_added(self):
        old = _make_snapshot()
        new = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6"),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "extension_added"]
        assert len(events) == 1
        assert events[0].table == "pg_trgm"
        assert events[0].new_value == "1.6"

    def test_extension_dropped(self):
        old = _make_snapshot(
            extensions={
                "postgis": ExtensionSnapshot(name="postgis", version="3.4.0"),
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "extension_dropped"]
        assert len(events) == 1
        assert events[0].table == "postgis"
        assert events[0].old_value == "3.4.0"

    def test_extension_version_changed(self):
        old = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.5"),
            }
        )
        new = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6"),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "extension_version_changed"]
        assert len(events) == 1
        assert events[0].old_value == "1.5"
        assert events[0].new_value == "1.6"

    def test_extension_unchanged(self):
        exts = {"pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6")}
        old = _make_snapshot(extensions=exts)
        new = _make_snapshot(extensions=exts)
        result = self.differ.diff(old, new)
        ext_events = [e for e in result.changes if "extension" in e.change_type]
        assert len(ext_events) == 0

    def test_multiple_extension_changes(self):
        old = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.5"),
                "hstore": ExtensionSnapshot(name="hstore", version="1.8"),
            }
        )
        new = _make_snapshot(
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6"),
                "uuid-ossp": ExtensionSnapshot(name="uuid-ossp", version="1.1"),
            }
        )
        result = self.differ.diff(old, new)
        ext_events = [e for e in result.changes if "extension" in e.change_type]
        types = {e.change_type for e in ext_events}
        assert "extension_dropped" in types  # hstore
        assert "extension_added" in types  # uuid-ossp
        assert "extension_version_changed" in types  # pg_trgm


# =========================================================================
# Permission Diff Tests
# =========================================================================


class TestPermissionDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_permission_granted(self):
        old = _make_snapshot()
        new = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
            ]
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "permission_granted"]
        assert len(events) == 1
        assert events[0].table == "users"

    def test_permission_revoked(self):
        old = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
            ]
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "permission_revoked"]
        assert len(events) == 1
        assert events[0].table == "users"

    def test_permission_unchanged(self):
        perms = [
            PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
        ]
        old = _make_snapshot(permissions=perms)
        new = _make_snapshot(permissions=perms)
        result = self.differ.diff(old, new)
        perm_events = [e for e in result.changes if "permission" in e.change_type]
        assert len(perm_events) == 0

    def test_permission_identity_key(self):
        """Permission identity is (table, grantee, privilege) — same table with different grantee is different."""
        old = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
            ]
        )
        new = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="admin", privilege_type="SELECT"),
            ]
        )
        result = self.differ.diff(old, new)
        perm_events = [e for e in result.changes if "permission" in e.change_type]
        assert len(perm_events) == 2  # revoked for app, granted for admin

    def test_multiple_permissions_on_same_table(self):
        old = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="INSERT"),
            ]
        )
        new = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="DELETE"),
            ]
        )
        result = self.differ.diff(old, new)
        revoked = [e for e in result.changes if e.change_type == "permission_revoked"]
        granted = [e for e in result.changes if e.change_type == "permission_granted"]
        assert len(revoked) == 1  # INSERT revoked
        assert len(granted) == 1  # DELETE granted


# =========================================================================
# Policy Diff Tests
# =========================================================================


class TestPolicyDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_policy_added(self):
        old = _make_snapshot()
        new = _make_snapshot(
            policies={
                "tenant_iso": PolicySnapshot(name="tenant_iso", table="orders"),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_added"]
        assert len(events) == 1
        assert events[0].table == "orders"

    def test_policy_dropped(self):
        old = _make_snapshot(
            policies={
                "tenant_iso": PolicySnapshot(name="tenant_iso", table="orders"),
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_dropped"]
        assert len(events) == 1
        assert events[0].old_value == "tenant_iso"

    def test_policy_changed_command(self):
        old = _make_snapshot(
            policies={
                "tenant_iso": PolicySnapshot(name="tenant_iso", table="orders", command="ALL"),
            }
        )
        new = _make_snapshot(
            policies={
                "tenant_iso": PolicySnapshot(name="tenant_iso", table="orders", command="SELECT"),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_changed"]
        assert len(events) == 1
        assert "SELECT" in events[0].new_value

    def test_policy_changed_permissive(self):
        old = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(name="pol1", table="t", permissive=True),
            }
        )
        new = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(name="pol1", table="t", permissive=False),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_changed"]
        assert len(events) == 1

    def test_policy_changed_qual(self):
        old = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(name="pol1", table="t", qual_expression="tenant_id = 1"),
            }
        )
        new = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(
                    name="pol1", table="t", qual_expression="tenant_id = current_user"
                ),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_changed"]
        assert len(events) == 1

    def test_policy_changed_roles(self):
        old = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(name="pol1", table="t", roles=["app_user"]),
            }
        )
        new = _make_snapshot(
            policies={
                "pol1": PolicySnapshot(name="pol1", table="t", roles=["app_user", "admin"]),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_changed"]
        assert len(events) == 1

    def test_policy_unchanged(self):
        pol = {"pol1": PolicySnapshot(name="pol1", table="t", command="SELECT")}
        old = _make_snapshot(policies=pol)
        new = _make_snapshot(policies=pol)
        result = self.differ.diff(old, new)
        pol_events = [e for e in result.changes if "policy" in e.change_type]
        assert len(pol_events) == 0


# =========================================================================
# Partition Diff Tests
# =========================================================================


class TestPartitionDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_partition_added(self):
        old = _make_snapshot(
            partitions={
                "orders": [PartitionInfo(partition_name="orders_2024_01", parent_table="orders")],
            }
        )
        new = _make_snapshot(
            partitions={
                "orders": [
                    PartitionInfo(partition_name="orders_2024_01", parent_table="orders"),
                    PartitionInfo(partition_name="orders_2024_02", parent_table="orders"),
                ],
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "partition_added"]
        assert len(events) == 1
        assert events[0].new_value == "orders_2024_02"
        assert events[0].table == "orders"

    def test_partition_dropped(self):
        old = _make_snapshot(
            partitions={
                "orders": [
                    PartitionInfo(partition_name="orders_2024_01", parent_table="orders"),
                    PartitionInfo(partition_name="orders_2024_02", parent_table="orders"),
                ],
            }
        )
        new = _make_snapshot(
            partitions={
                "orders": [PartitionInfo(partition_name="orders_2024_01", parent_table="orders")],
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "partition_dropped"]
        assert len(events) == 1
        assert events[0].old_value == "orders_2024_02"

    def test_partition_table_removed_entirely(self):
        old = _make_snapshot(
            partitions={
                "orders": [PartitionInfo(partition_name="orders_2024_01", parent_table="orders")],
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "partition_dropped"]
        assert len(events) == 1

    def test_partition_table_added_entirely(self):
        old = _make_snapshot()
        new = _make_snapshot(
            partitions={
                "events": [PartitionInfo(partition_name="events_2024_01", parent_table="events")],
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "partition_added"]
        assert len(events) == 1

    def test_partitions_unchanged(self):
        parts = {
            "orders": [PartitionInfo(partition_name="orders_2024_01", parent_table="orders")],
        }
        old = _make_snapshot(partitions=parts)
        new = _make_snapshot(partitions=parts)
        result = self.differ.diff(old, new)
        part_events = [e for e in result.changes if "partition" in e.change_type]
        assert len(part_events) == 0

    def test_multiple_parent_tables(self):
        old = _make_snapshot(
            partitions={
                "orders": [PartitionInfo(partition_name="orders_q1", parent_table="orders")],
                "events": [PartitionInfo(partition_name="events_q1", parent_table="events")],
            }
        )
        new = _make_snapshot(
            partitions={
                "orders": [
                    PartitionInfo(partition_name="orders_q1", parent_table="orders"),
                    PartitionInfo(partition_name="orders_q2", parent_table="orders"),
                ],
                # events partition removed entirely
            }
        )
        result = self.differ.diff(old, new)
        added = [e for e in result.changes if e.change_type == "partition_added"]
        dropped = [e for e in result.changes if e.change_type == "partition_dropped"]
        assert len(added) == 1  # orders_q2
        assert len(dropped) == 1  # events_q1


# =========================================================================
# Materialized View Diff Tests
# =========================================================================


class TestMaterializedViewDiff:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_matview_added(self):
        old = _make_snapshot()
        new = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="SELECT count(*) FROM orders",
                ),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "matview_added"]
        assert len(events) == 1
        assert events[0].table == "mv_summary"

    def test_matview_dropped(self):
        old = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(name="mv_summary", definition="SELECT 1"),
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "matview_dropped"]
        assert len(events) == 1

    def test_matview_definition_changed(self):
        old = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="SELECT count(*) FROM orders",
                ),
            }
        )
        new = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="SELECT count(*), sum(amount) FROM orders",
                ),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "matview_definition_changed"]
        assert len(events) == 1

    def test_matview_definition_whitespace_normalized(self):
        """Whitespace and case differences should not trigger a change."""
        old = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="SELECT  count(*)  FROM  orders",
                ),
            }
        )
        new = _make_snapshot(
            materialized_views={
                "mv_summary": MaterializedViewSnapshot(
                    name="mv_summary",
                    definition="select count(*) from orders",
                ),
            }
        )
        result = self.differ.diff(old, new)
        mv_events = [e for e in result.changes if "matview" in e.change_type]
        assert len(mv_events) == 0

    def test_matview_unchanged(self):
        mvs = {
            "mv": MaterializedViewSnapshot(name="mv", definition="SELECT 1"),
        }
        old = _make_snapshot(materialized_views=mvs)
        new = _make_snapshot(materialized_views=mvs)
        result = self.differ.diff(old, new)
        mv_events = [e for e in result.changes if "matview" in e.change_type]
        assert len(mv_events) == 0


# =========================================================================
# Risk Classification Tests — All 13 new change types
# =========================================================================


class TestPhaseCRiskClassification:
    """Test classify_change for every Phase C change type."""

    def _event(self, change_type, **kwargs):
        defaults = {"table": "t", "detected_at": datetime.now(timezone.utc)}
        defaults.update(kwargs)
        return SchemaChangeEvent(change_type=change_type, **defaults)

    # Extension
    def test_extension_added_safe(self):
        assert classify_change(self._event("extension_added")) == "safe"

    def test_extension_dropped_breaking(self):
        assert classify_change(self._event("extension_dropped")) == "breaking"

    def test_extension_version_changed_needs_review(self):
        assert classify_change(self._event("extension_version_changed")) == "needs_review"

    # Permission
    def test_permission_granted_safe(self):
        assert classify_change(self._event("permission_granted")) == "safe"

    def test_permission_revoked_potentially_breaking(self):
        assert classify_change(self._event("permission_revoked")) == "potentially_breaking"

    # Policy
    def test_policy_added_potentially_breaking(self):
        assert classify_change(self._event("policy_added")) == "potentially_breaking"

    def test_policy_dropped_potentially_breaking(self):
        assert classify_change(self._event("policy_dropped")) == "potentially_breaking"

    def test_policy_changed_needs_review(self):
        assert classify_change(self._event("policy_changed")) == "needs_review"

    # Partition
    def test_partition_added_safe(self):
        assert classify_change(self._event("partition_added")) == "safe"

    def test_partition_dropped_breaking(self):
        assert classify_change(self._event("partition_dropped")) == "breaking"

    # Materialized View
    def test_matview_added_safe(self):
        assert classify_change(self._event("matview_added")) == "safe"

    def test_matview_dropped_breaking(self):
        assert classify_change(self._event("matview_dropped")) == "breaking"

    def test_matview_definition_changed_needs_review(self):
        assert classify_change(self._event("matview_definition_changed")) == "needs_review"


# =========================================================================
# Risk Classification on Diff Output — end-to-end
# =========================================================================


class TestPhaseCRiskOnDiffOutput:
    """Verify that diff output events carry correct risk_classification."""

    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_extension_dropped_has_breaking_risk(self):
        old = _make_snapshot(
            extensions={
                "postgis": ExtensionSnapshot(name="postgis", version="3.4"),
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "extension_dropped"]
        assert len(events) == 1
        assert events[0].change_risk == "breaking"

    def test_permission_granted_has_safe_risk(self):
        old = _make_snapshot()
        new = _make_snapshot(
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
            ]
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "permission_granted"]
        assert len(events) == 1
        assert events[0].change_risk == "safe"

    def test_partition_dropped_has_breaking_risk(self):
        old = _make_snapshot(
            partitions={
                "orders": [PartitionInfo(partition_name="p1", parent_table="orders")],
            }
        )
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "partition_dropped"]
        assert len(events) == 1
        assert events[0].change_risk == "breaking"

    def test_policy_added_has_potentially_breaking_risk(self):
        old = _make_snapshot()
        new = _make_snapshot(
            policies={
                "rls1": PolicySnapshot(name="rls1", table="orders"),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_added"]
        assert len(events) == 1
        assert events[0].change_risk == "potentially_breaking"


# =========================================================================
# Combined Diff — Phase C objects mixed with table changes
# =========================================================================


class TestCombinedDiffPhaseC:
    """Verify all Phase C object diffs work alongside table/column diffs."""

    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_full_combined_diff(self):
        old = _make_snapshot(
            tables={
                "users": _make_table("users", {"id": "integer", "name": "text"}),
            },
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.5"),
            },
            permissions=[
                PermissionSnapshot(table_name="users", grantee="app", privilege_type="SELECT"),
            ],
            policies={
                "rls1": PolicySnapshot(name="rls1", table="users"),
            },
            partitions={
                "events": [PartitionInfo(partition_name="events_q1", parent_table="events")],
            },
            materialized_views={
                "mv1": MaterializedViewSnapshot(
                    name="mv1", definition="SELECT count(*) FROM users"
                ),
            },
        )
        new = _make_snapshot(
            tables={
                "users": _make_table(
                    "users", {"id": "integer", "name": "text", "email": "varchar(255)"}
                ),
            },
            extensions={
                "pg_trgm": ExtensionSnapshot(name="pg_trgm", version="1.6"),
            },
            permissions=[
                PermissionSnapshot(table_name="users", grantee="admin", privilege_type="ALL"),
            ],
            policies={},
            partitions={
                "events": [
                    PartitionInfo(partition_name="events_q1", parent_table="events"),
                    PartitionInfo(partition_name="events_q2", parent_table="events"),
                ],
            },
            materialized_views={
                "mv1": MaterializedViewSnapshot(name="mv1", definition="SELECT sum(1) FROM users"),
            },
        )
        result = self.differ.diff(old, new)

        change_types = {e.change_type for e in result.changes}

        # Table-level: column added
        assert "column_added" in change_types
        # Extension version changed
        assert "extension_version_changed" in change_types
        # Permission revoked + granted
        assert "permission_revoked" in change_types
        assert "permission_granted" in change_types
        # Policy dropped
        assert "policy_dropped" in change_types
        # Partition added
        assert "partition_added" in change_types
        # Matview definition changed
        assert "matview_definition_changed" in change_types

        # All changes should have risk classifications
        for event in result.changes:
            assert event.change_risk is not None


# =========================================================================
# Edge Cases
# =========================================================================


class TestPhaseCEdgeCases:
    def setup_method(self):
        self.differ = SchemaDiffer()

    def test_empty_extensions_both_sides(self):
        old = _make_snapshot()
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        ext_events = [e for e in result.changes if "extension" in e.change_type]
        assert len(ext_events) == 0

    def test_empty_permissions_both_sides(self):
        old = _make_snapshot()
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        perm_events = [e for e in result.changes if "permission" in e.change_type]
        assert len(perm_events) == 0

    def test_empty_policies_both_sides(self):
        old = _make_snapshot()
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        pol_events = [e for e in result.changes if "policy" in e.change_type]
        assert len(pol_events) == 0

    def test_empty_partitions_both_sides(self):
        old = _make_snapshot()
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        part_events = [e for e in result.changes if "partition" in e.change_type]
        assert len(part_events) == 0

    def test_empty_matviews_both_sides(self):
        old = _make_snapshot()
        new = _make_snapshot()
        result = self.differ.diff(old, new)
        mv_events = [e for e in result.changes if "matview" in e.change_type]
        assert len(mv_events) == 0

    def test_policy_with_check_expression_change(self):
        old = _make_snapshot(
            policies={
                "p1": PolicySnapshot(
                    name="p1",
                    table="t",
                    with_check_expression="tenant_id = 1",
                ),
            }
        )
        new = _make_snapshot(
            policies={
                "p1": PolicySnapshot(
                    name="p1",
                    table="t",
                    with_check_expression="tenant_id = 2",
                ),
            }
        )
        result = self.differ.diff(old, new)
        events = [e for e in result.changes if e.change_type == "policy_changed"]
        assert len(events) == 1
