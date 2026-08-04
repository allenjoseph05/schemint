"""Unit tests for the Postgres fixture that need no database.

Database names are derived from task directory names on disk, so identifier
validation is the boundary between the filesystem and SQL DDL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evals.oracle.postgres import (
    CONTAINER_LABEL,
    ENV_IMAGE,
    ENV_REUSE_URL,
    STALE_AGE_S,
    OracleError,
    PostgresFixture,
    _parse_docker_timestamp,
    free_port,
    validate_identifier,
)


@pytest.mark.unit
class TestValidateIdentifier:
    @pytest.mark.parametrize(
        "name", ["users", "task_1", "_tmpl", "t" * 63, "drop_column_behind_view"]
    )
    def test_accepts_plain_lowercase_identifiers(self, name):
        assert validate_identifier(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1leading_digit",
            "has-hyphen",
            "has space",
            "MixedCase",
            "has.dot",
            't" OR 1=1--',
            "t" * 64,
        ],
    )
    def test_rejects_anything_needing_quoting(self, name):
        with pytest.raises(OracleError, match="Unsafe database identifier"):
            validate_identifier(name)


@pytest.mark.unit
class TestFreePort:
    def test_returns_a_usable_high_port(self):
        port = free_port()
        assert 1024 < port <= 65535

    def test_successive_calls_differ(self):
        # Not guaranteed by the OS, but a repeat every call would mean the
        # retry-on-a-fresh-port recovery in start() cannot work.
        ports = {free_port() for _ in range(5)}
        assert len(ports) > 1


@pytest.mark.unit
class TestUrlConstruction:
    def test_docker_backend_builds_local_url(self):
        fixture = PostgresFixture(reuse_url=None)
        fixture.port = 54321
        assert fixture.url_for("task_1").endswith("@127.0.0.1:54321/task_1")

    def test_admin_url_requires_a_started_fixture(self):
        with pytest.raises(OracleError, match="not started"):
            _ = PostgresFixture(reuse_url=None).admin_url

    def test_reuse_backend_swaps_only_the_database(self):
        fixture = PostgresFixture(reuse_url="postgresql://u:p@host:5432/postgres")
        assert fixture.url_for("task_1") == "postgresql://u:p@host:5432/task_1"

    def test_reuse_backend_preserves_query_parameters(self):
        # A CI connection string may carry sslmode; it must not be swallowed
        # into the database name.
        fixture = PostgresFixture(reuse_url="postgresql://u:p@host:5432/postgres?sslmode=require")
        assert fixture.url_for("task_1") == "postgresql://u:p@host:5432/task_1?sslmode=require"

    def test_url_for_validates_the_name(self):
        fixture = PostgresFixture(reuse_url="postgresql://u:p@host:5432/postgres")
        with pytest.raises(OracleError):
            fixture.url_for("bad-name")


@pytest.mark.unit
class TestBackendSelection:
    def test_defaults_to_docker(self, monkeypatch):
        monkeypatch.delenv(ENV_REUSE_URL, raising=False)
        assert PostgresFixture().uses_docker is True

    def test_env_var_selects_reuse_backend(self, monkeypatch):
        monkeypatch.setenv(ENV_REUSE_URL, "postgresql://u:p@host:5432/postgres")
        fixture = PostgresFixture()
        assert fixture.uses_docker is False
        assert fixture.admin_url == "postgresql://u:p@host:5432/postgres"

    def test_explicit_argument_beats_env_var(self, monkeypatch):
        monkeypatch.setenv(ENV_REUSE_URL, "postgresql://u:p@env:5432/postgres")
        fixture = PostgresFixture(reuse_url="postgresql://u:p@arg:5432/postgres")
        assert "arg" in fixture.admin_url

    def test_image_is_overridable_by_env(self, monkeypatch):
        monkeypatch.setenv(ENV_IMAGE, "postgres:15-alpine")
        assert PostgresFixture().image == "postgres:15-alpine"

    def test_label_is_stable(self):
        # Orphan sweeping filters on this string; changing it strands
        # containers from older runs.
        assert CONTAINER_LABEL == "schemint-eval"


@pytest.mark.unit
class TestDockerTimestampParsing:
    """Age parsing gates the orphan sweep.

    A timestamp that fails to parse reads as "age unknown", which the sweep
    treats as stale — so a parsing regression would start tearing down
    containers belonging to concurrent runs.
    """

    def test_parses_nanosecond_precision(self):
        # Docker reports 9 fractional digits; fromisoformat accepts at most 6.
        parsed = _parse_docker_timestamp("2026-08-03T10:12:34.123456789Z")
        assert parsed is not None
        assert parsed.year == 2026
        assert parsed.microsecond == 123456

    def test_parses_without_fraction(self):
        parsed = _parse_docker_timestamp("2026-08-03T10:12:34Z")
        assert parsed is not None
        assert parsed.hour == 10

    def test_parses_numeric_offset(self):
        parsed = _parse_docker_timestamp("2026-08-03T10:12:34.123456789+02:00")
        assert parsed is not None
        assert parsed.utcoffset().total_seconds() == 7200

    def test_result_is_timezone_aware(self):
        # Compared against an aware "now"; a naive value would raise.
        parsed = _parse_docker_timestamp("2026-08-03T10:12:34.123456789Z")
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_unparseable_input_returns_none(self):
        assert _parse_docker_timestamp("not a timestamp") is None

    def test_stale_age_leaves_room_for_a_full_run(self):
        assert STALE_AGE_S >= 600


@pytest.mark.unit
class TestSweepAgeGating:
    """The sweep decides which containers to destroy — stub the CLI.

    Exercising this against real Docker would tear down whatever else is
    running, including a concurrent fixture, which is precisely the failure
    the age gate exists to prevent.
    """

    @pytest.fixture
    def docker_calls(self, monkeypatch):
        """Stub docker: one hour-old container, one created seconds ago."""
        import subprocess

        from evals.oracle import postgres

        now = datetime.now(timezone.utc)
        created = {
            "old111": (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
            "fresh22": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        }
        calls: list[list[str]] = []

        def fake_run_docker(args, check=True):
            calls.append(args)
            if args[0] == "ps":
                stdout = "old111\nfresh22\n"
            elif args[0] == "inspect":
                stdout = created[args[-1]]
            else:
                stdout = ""
            return subprocess.CompletedProcess(args, 0, stdout, "")

        monkeypatch.setattr(postgres, "_run_docker", fake_run_docker)
        return calls

    def test_default_sweep_removes_only_old_containers(self, docker_calls):
        from evals.oracle.postgres import sweep_stale_containers

        assert sweep_stale_containers() == ["old111"]
        removed = [args[-1] for args in docker_calls if args[0] == "rm"]
        assert removed == ["old111"]

    @pytest.mark.usefixtures("docker_calls")
    def test_zero_age_forces_a_full_sweep(self):
        from evals.oracle.postgres import sweep_stale_containers

        assert sweep_stale_containers(max_age_seconds=0) == ["old111", "fresh22"]

    def test_unknown_age_is_treated_as_stale(self, monkeypatch):
        # An unreadable timestamp must not leave orphans accumulating forever.
        import subprocess

        from evals.oracle import postgres

        def fake_run_docker(args, check=True):
            if args[0] == "ps":
                return subprocess.CompletedProcess(args, 0, "mystery\n", "")
            if args[0] == "inspect":
                return subprocess.CompletedProcess(args, 1, "", "no such object")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(postgres, "_run_docker", fake_run_docker)
        assert postgres.sweep_stale_containers() == ["mystery"]

    def test_sweep_never_raises_when_docker_is_missing(self, monkeypatch):
        from evals.oracle import postgres

        def boom(args, check=True):
            raise OracleError("docker was not found on PATH")

        monkeypatch.setattr(postgres, "_run_docker", boom)
        assert postgres.sweep_stale_containers() == []
