"""Tests for drift API endpoints (api/v1/drift.py).

Uses FastAPI TestClient with mocked external dependencies (store, AI agents).
All I/O is mocked — no real DB or Claude API calls.
"""

# Some mocks must be configured before the nested TestClient context is entered.
# Keep that setup order explicit throughout this legacy endpoint test module.
# ruff: noqa: SIM117

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from schemint.drift.models import (
    AgentDecision,
    ContextPackage,
    DriftRunResult,
    ExecutionReport,
    RunTelemetry,
    SchemaChangeEvent,
    SchemaDiffResult,
    SchemaSnapshot,
)
from schemint.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> Any:
    return create_app()


def _make_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        snapshot_id="snap-1",
        project_id="proj-1",
        environment="default",
        source="ddl",
        tables={},
    )


def _make_decision(severity: str = "low") -> AgentDecision:
    return AgentDecision(
        severity=severity,  # type: ignore[arg-type]
        confidence_in_decision=0.9,
        requires_human_review=False,
        rationale=["test reason"],
        recommended_action_categories=["notify_owner"],
        context_quality="complete",
    )


def _make_diff(changes: list | None = None) -> SchemaDiffResult:
    return SchemaDiffResult(
        old_snapshot_id="snap-old",
        new_snapshot_id="snap-new",
        changes=changes or [],
    )


def _make_run_result(status: str = "complete") -> DriftRunResult:
    return DriftRunResult(
        run_id="run-123",
        project_id="proj-1",
        status=status,
    )


def _make_context() -> ContextPackage:
    change = SchemaChangeEvent(
        change_type="column_added",
        table="users",
        column="email",
        change_risk="safe",
    )
    return ContextPackage(schema_change=change)


def _make_execution_report() -> ExecutionReport:
    return ExecutionReport(
        execution_id="exec-1",
        overall_status="success",
        step_results=[],
    )


# ---------------------------------------------------------------------------
# Snapshot endpoints
# ---------------------------------------------------------------------------


class TestSnapshotEndpoints:
    def test_capture_ddl_snapshot_success(self) -> None:
        app = _make_app()
        snap = _make_snapshot()
        with patch("schemint.api.v1.drift.SnapshotService") as mock_svc:
            mock_svc.return_value.capture_from_ddl.return_value = snap
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/snapshot/ddl",
                    json={"sql": "CREATE TABLE t (id INT);", "database_type": "postgresql"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] == "snap-1"

    def test_capture_ddl_snapshot_error(self) -> None:
        app = _make_app()
        with patch("schemint.api.v1.drift.SnapshotService") as mock_svc:
            mock_svc.return_value.capture_from_ddl.side_effect = ValueError("bad sql")
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/snapshot/ddl",
                    json={"sql": "INVALID", "database_type": "postgresql"},
                )
        assert resp.status_code == 400

    def test_get_latest_snapshot_success(self) -> None:
        app = _make_app()
        snap = _make_snapshot()
        mock_store = MagicMock()
        mock_store.get_latest_snapshot.return_value = snap
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/snapshot/proj-1/latest")
        assert resp.status_code == 200

    def test_get_latest_snapshot_store_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db down")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/snapshot/proj-1/latest")
        assert resp.status_code == 500

    def test_get_latest_snapshot_none(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_latest_snapshot.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/snapshot/proj-none/latest")
        assert resp.status_code == 200
        assert resp.json() is None


# ---------------------------------------------------------------------------
# Graph endpoints
# ---------------------------------------------------------------------------


class TestGraphEndpoints:
    def test_build_graph_empty_request(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_latest_snapshot.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.post("/api/v1/drift/graph/proj-1/build", json={})
        assert resp.status_code == 200

    def test_build_graph_with_sql_files(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_latest_snapshot.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/graph/proj-1/build",
                    json={"sql_files": {"schema.sql": "CREATE TABLE a (id INT);"}},
                )
        assert resp.status_code == 200

    def test_build_graph_with_view_definitions(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_latest_snapshot.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/graph/proj-1/build",
                    json={"view_definitions": {"v_users": "SELECT * FROM users"}},
                )
        assert resp.status_code == 200

    def test_get_dependency_graph_success(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_dependency_graph.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/graph/proj-1")
        assert resp.status_code == 200

    def test_get_dependency_graph_store_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("fail")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/graph/proj-1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Judge / Plan endpoints
# ---------------------------------------------------------------------------


class TestJudgePlanEndpoints:
    def test_judge_no_ai_fallback(self) -> None:
        app = _make_app()
        ctx = _make_context()
        with patch("schemint.drift.agent_brain.get_drift_agent", return_value=None):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/judge/proj-1",
                    json={"context": ctx.model_dump(mode="json")},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "severity" in data

    def test_judge_with_ai_agent(self) -> None:
        app = _make_app()
        ctx = _make_context()
        decision = _make_decision()
        mock_agent = MagicMock()
        mock_agent.judge.return_value = decision
        with patch("schemint.drift.agent_brain.get_drift_agent", return_value=mock_agent):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/judge/proj-1",
                    json={"context": ctx.model_dump(mode="json")},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] == "low"


# ---------------------------------------------------------------------------
# Execute / Verify endpoints
# ---------------------------------------------------------------------------


class TestVerifyEndpoints:
    def test_verify_execution_success(self) -> None:
        app = _make_app()
        exec_report = _make_execution_report()
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/drift/verify/proj-1",
                json={
                    "execution_report": exec_report.model_dump(mode="json"),
                    "source_requires_human_review": False,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "goal_satisfied" in data

    def test_verify_execution_with_human_review(self) -> None:
        app = _make_app()
        exec_report = _make_execution_report()
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/drift/verify/proj-1",
                json={
                    "execution_report": exec_report.model_dump(mode="json"),
                    "source_requires_human_review": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "requires_human_escalation" in data
        assert data["requires_human_escalation"] is True

    def test_verify_failed_execution(self) -> None:
        app = _make_app()
        exec_report = ExecutionReport(
            execution_id="exec-fail",
            overall_status="failed",
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/drift/verify/proj-1",
                json={
                    "execution_report": exec_report.model_dump(mode="json"),
                    "source_requires_human_review": False,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["goal_satisfied"] is False


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


class TestRunEndpoints:
    def test_run_drift_pipeline(self) -> None:
        app = _make_app()
        ctx = _make_context()
        mock_ctrl = MagicMock()
        mock_ctrl.run.return_value = _make_run_result()
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            patch("schemint.api.v1.drift._build_memory_context", return_value=None),
        ):
            with patch("schemint.api.v1.drift._write_memory_learnings"):
                with TestClient(app) as client:
                    resp = client.post(
                        "/api/v1/drift/run/proj-1",
                        json={"context": ctx.model_dump(mode="json")},
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-123"

    def test_get_drift_run_found(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_run.return_value = _make_run_result()
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/run/proj-1/run-123")
        assert resp.status_code == 200

    def test_get_drift_run_wrong_project(self) -> None:
        app = _make_app()
        result = _make_run_result()
        result.project_id = "other-project"
        mock_store = MagicMock()
        mock_store.get_drift_run.return_value = result
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/run/proj-1/run-123")
        assert resp.status_code == 404

    def test_get_drift_run_not_found(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_run.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/run/proj-1/missing")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_get_drift_run_store_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/run/proj-1/run-123")
        assert resp.status_code == 500

    def test_list_drift_runs_success(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = [_make_run_result()]
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/runs/proj-1")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_drift_runs_with_offset(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = [
            _make_run_result(),
            _make_run_result(),
        ]
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/runs/proj-1?limit=1&offset=1")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_drift_runs_store_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("fail")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/runs/proj-1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Approval endpoints
# ---------------------------------------------------------------------------


class TestApprovalEndpoints:
    def test_approve_run_success(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.return_value = _make_run_result()
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            patch("schemint.api.v1.drift._write_memory_learnings"),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/approve/run-123",
                json={"approver": "alice", "reason": "LGTM"},
            )
        assert resp.status_code == 200

    def test_approve_run_not_found(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.side_effect = ValueError("run not found")
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/approve/missing-run",
                json={"approver": "alice"},
            )
        assert resp.status_code == 404

    def test_approve_run_wrong_status(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.side_effect = ValueError("Run is not AWAITING_APPROVAL")
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/approve/run-123",
                json={"approver": "alice"},
            )
        assert resp.status_code == 409

    def test_reject_run_success(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.return_value = _make_run_result(status="escalated")
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/reject/run-123",
                json={"approver": "bob", "reason": "too risky"},
            )
        assert resp.status_code == 200

    def test_reject_run_not_found(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.side_effect = ValueError("run not found")
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/reject/missing",
                json={"approver": "bob"},
            )
        assert resp.status_code == 404

    def test_approve_run_server_error(self) -> None:
        app = _make_app()
        mock_ctrl = MagicMock()
        mock_ctrl.resume.side_effect = RuntimeError("unexpected")
        with (
            patch(
                "schemint.drift.agent_controller.build_agent_controller",
                return_value=mock_ctrl,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/approve/run-123",
                json={"approver": "alice"},
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Observability endpoints (M6)
# ---------------------------------------------------------------------------


class TestObservabilityEndpoints:
    def test_dashboard_empty_project(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = []
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/dashboard/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0

    def test_dashboard_with_runs(self) -> None:
        app = _make_app()
        run = _make_run_result()
        run.status = "complete"
        run.decision = _make_decision("high")
        run.telemetry = RunTelemetry(
            run_id="run-123",
            project_id="proj-1",
            status="complete",
            severity="high",
            total_duration_ms=1500,
        )
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = [run, _make_run_result()]
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/dashboard/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 2

    def test_dashboard_store_error_returns_empty(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/dashboard/proj-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0

    def test_metrics_empty(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = []
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "schemint_drift_runs_total" in data
        assert data["schemint_drift_runs_total"] == 0

    def test_metrics_with_runs(self) -> None:
        app = _make_app()
        run1 = _make_run_result("complete")
        run1.decision = _make_decision("critical")
        run1.telemetry = RunTelemetry(
            run_id="r1",
            project_id="p1",
            status="complete",
            total_duration_ms=2000,
        )
        run2 = _make_run_result("escalated")
        run3 = _make_run_result("failed")
        mock_store = MagicMock()
        mock_store.get_drift_runs.return_value = [run1, run2, run3]
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schemint_drift_runs_total"] == 3
        assert data["schemint_drift_runs_escalated"] == 1
        assert data["schemint_drift_runs_failed"] == 1
        assert data["schemint_drift_runs_critical_severity"] == 1

    def test_metrics_store_error_returns_zeros(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schemint_drift_runs_total"] == 0


# ---------------------------------------------------------------------------
# Copilot analyze endpoint (M4)
# ---------------------------------------------------------------------------


class TestCopilotAnalyzeEndpoint:
    def test_copilot_analyze_no_ai(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.copilot_agent.get_copilot_agent", return_value=None),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/copilot/analyze",
                json={"migration_sql": "ALTER TABLE t ADD COLUMN x INT;"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ai_available"] is False

    def test_copilot_analyze_with_context(self) -> None:
        app = _make_app()
        ctx = _make_context()
        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = []
        mock_agent.generate_rollback.return_value = None
        mock_agent.validate_intent.return_value = None
        with (
            patch("schemint.drift.copilot_agent.get_copilot_agent", return_value=mock_agent),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/copilot/analyze",
                json={
                    "migration_sql": "ALTER TABLE users ADD COLUMN email TEXT;",
                    "context": ctx.model_dump(mode="json"),
                    "generate_alternatives": True,
                    "generate_rollback": True,
                    "validate_intent": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ai_available"] is True

    def test_copilot_analyze_no_context(self) -> None:
        app = _make_app()
        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = []
        mock_agent.generate_rollback.return_value = None
        mock_agent.validate_intent.return_value = None
        with (
            patch("schemint.drift.copilot_agent.get_copilot_agent", return_value=mock_agent),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/copilot/analyze",
                json={
                    "migration_sql": "ALTER TABLE t DROP COLUMN x;",
                    "generate_alternatives": False,
                    "generate_rollback": False,
                    "validate_intent": False,
                },
            )
        assert resp.status_code == 200

    def test_copilot_analyze_with_alternatives(self) -> None:
        app = _make_app()
        ctx = _make_context()

        alt = MagicMock()
        alt.model_dump.return_value = {"safe_sql": "ALTER TABLE t ADD COLUMN x INT DEFAULT 0;"}

        rollback = MagicMock()
        rollback.is_complete = True
        rollback.model_dump.return_value = {"rollback_sql": "ALTER TABLE t DROP COLUMN x;"}

        intent = MagicMock()
        intent.model_dump.return_value = {"intent": "add_column"}

        mock_agent = MagicMock()
        mock_agent.generate_alternatives.return_value = [alt]
        mock_agent.generate_rollback.return_value = rollback
        mock_agent.validate_intent.return_value = intent

        with (
            patch("schemint.drift.copilot_agent.get_copilot_agent", return_value=mock_agent),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/v1/drift/copilot/analyze",
                json={
                    "migration_sql": "ALTER TABLE users ADD COLUMN x INT;",
                    "context": ctx.model_dump(mode="json"),
                    "generate_alternatives": True,
                    "generate_rollback": True,
                    "validate_intent": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alternatives"]) == 1
        assert data["rollback"] is not None
        assert data["intent"] is not None


# ---------------------------------------------------------------------------
# Desired state endpoints
# ---------------------------------------------------------------------------


class TestDesiredStateEndpoints:
    def test_save_desired_state_success(self) -> None:
        app = _make_app()
        snap = _make_snapshot()
        mock_store = MagicMock()
        with patch("schemint.api.v1.drift.SnapshotService") as mock_svc:
            mock_svc.return_value.capture_from_ddl.return_value = snap
            with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
                with TestClient(app) as client:
                    resp = client.post(
                        "/api/v1/drift/desired-state/proj-1",
                        json={"sql": "CREATE TABLE t (id INT);"},
                    )
        assert resp.status_code == 200

    def test_save_desired_state_error(self) -> None:
        app = _make_app()
        with patch("schemint.api.v1.drift.SnapshotService") as mock_svc:
            mock_svc.return_value.capture_from_ddl.side_effect = ValueError("bad")
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/drift/desired-state/proj-1",
                    json={"sql": "INVALID"},
                )
        assert resp.status_code == 400

    def test_get_desired_state_success(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_desired_state.return_value = None
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/desired-state/proj-1/production")
        assert resp.status_code == 200

    def test_get_desired_state_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/desired-state/proj-1/production")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Migration history endpoints
# ---------------------------------------------------------------------------


class TestMigrationHistoryEndpoints:
    def test_get_migration_history(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.get_migration_history.return_value = []
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/migrations/proj-1/default")
        assert resp.status_code == 200

    def test_get_migration_history_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/migrations/proj-1/default")
        assert resp.status_code == 500

    def test_check_migration_applied(self) -> None:
        app = _make_app()
        mock_store = MagicMock()
        mock_store.has_migration_been_applied.return_value = True
        with patch("schemint.drift.store.get_drift_store", return_value=mock_store):
            with TestClient(app) as client:
                resp = client.get("/api/v1/drift/migrations/proj-1/default/check/abc123")
        assert resp.status_code == 200
        assert resp.json()["applied"] is True

    def test_check_migration_applied_error(self) -> None:
        app = _make_app()
        with (
            patch("schemint.drift.store.get_drift_store", side_effect=RuntimeError("db")),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/drift/migrations/proj-1/default/check/abc123")
        assert resp.status_code == 500
