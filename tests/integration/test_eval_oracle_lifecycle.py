"""Integration tests for the eval oracle's Postgres lifecycle.

Requires Docker (or SCHEMINT_EVAL_PG_URL pointing at a real server). These
are the Phase 1 acceptance checks: the fixture must create and clone many
databases quickly, and must leave nothing behind when it exits.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - probing for a working docker daemon
import uuid

import pytest

from evals.oracle.postgres import (
    CONTAINER_LABEL,
    ENV_REUSE_URL,
    PostgresFixture,
    postgres_fixture,
    sweep_stale_containers,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _docker_available() -> bool:
    if os.environ.get(ENV_REUSE_URL):
        return True
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, no shell
            ["docker", "info"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon unavailable and SCHEMINT_EVAL_PG_URL is not set",
)


def _running_eval_containers() -> set[str]:
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _container_exists(name_or_id: str) -> bool:
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["docker", "ps", "-aq", "--filter", f"name={name_or_id}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return bool(result.stdout.strip())


SCHEMA_SQL = """
CREATE TABLE users (
    id    SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    name  TEXT
);
CREATE TABLE orders (
    id      SERIAL PRIMARY KEY,
    user_id INT REFERENCES users (id),
    total   NUMERIC(10, 2)
);
CREATE VIEW user_summary AS
    SELECT u.id, u.email, count(o.id) AS order_count
    FROM users u
    LEFT JOIN orders o ON o.user_id = u.id
    GROUP BY u.id, u.email;
INSERT INTO users (email, name) VALUES ('a@example.com', 'A'), ('b@example.com', 'B');
"""


@pytest.fixture(scope="module")
def pg():
    """One server for the whole module — the point of the design."""
    with postgres_fixture() as fixture:
        yield fixture


@requires_docker
class TestDatabaseLifecycle:
    def test_server_accepts_connections(self, pg):
        with pg.connect("postgres") as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_create_and_drop(self, pg):
        name = f"lifecycle_{uuid.uuid4().hex[:8]}"
        pg.create_database(name)
        assert name in pg.list_databases()
        pg.drop_database(name)
        assert name not in pg.list_databases()

    def test_create_over_existing_name_resets_it(self, pg):
        name = f"reset_{uuid.uuid4().hex[:8]}"
        pg.create_database(name)
        pg.apply_sql(name, "CREATE TABLE leftover (id INT);")

        pg.create_database(name)
        with pg.connect(name) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.leftover')")
            assert cur.fetchone()[0] is None
        pg.drop_database(name)

    def test_drop_terminates_open_connections(self, pg):
        # Without pg_terminate_backend this raises "database is being
        # accessed by other users" and leaks a database into every later run.
        name = f"busy_{uuid.uuid4().hex[:8]}"
        pg.create_database(name)
        import psycopg2

        conn = psycopg2.connect(pg.url_for(name))
        try:
            pg.drop_database(name)
            assert name not in pg.list_databases()
        finally:
            conn.close()

    def test_drop_is_idempotent(self, pg):
        pg.drop_database(f"never_created_{uuid.uuid4().hex[:8]}")


@requires_docker
class TestTemplateCloning:
    def test_clone_carries_schema_and_data(self, pg):
        template = f"tmpl_{uuid.uuid4().hex[:8]}"
        pg.create_database(template)
        pg.apply_sql(template, SCHEMA_SQL)

        clone = f"clone_{uuid.uuid4().hex[:8]}"
        pg.create_database(clone, template=template)
        with pg.connect(clone) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT count(*) FROM user_summary")
            assert cur.fetchone()[0] == 2

        pg.drop_database(clone)
        pg.drop_database(template)

    def test_clones_are_isolated_from_each_other(self, pg):
        template = f"tmpl_{uuid.uuid4().hex[:8]}"
        pg.create_database(template)
        pg.apply_sql(template, SCHEMA_SQL)

        first = f"iso_a_{uuid.uuid4().hex[:8]}"
        second = f"iso_b_{uuid.uuid4().hex[:8]}"
        pg.create_database(first, template=template)
        pg.create_database(second, template=template)

        # A destructive migration in one task must not reach the next.
        pg.apply_sql(first, "ALTER TABLE users DROP COLUMN email CASCADE;")
        with pg.connect(second) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM user_summary")
            assert cur.fetchone()[0] == 2

        for name in (first, second, template):
            pg.drop_database(name)

    def test_ten_clones_from_one_template(self, pg):
        """Phase 1 acceptance: many databases on one server, all cleaned up."""
        template = f"tmpl_{uuid.uuid4().hex[:8]}"
        pg.create_database(template)
        pg.apply_sql(template, SCHEMA_SQL)

        clones = [f"acc_{i}_{uuid.uuid4().hex[:6]}" for i in range(10)]
        for name in clones:
            pg.create_database(name, template=template)

        existing = set(pg.list_databases())
        assert set(clones).issubset(existing)

        for name in clones:
            pg.drop_database(name)
        pg.drop_database(template)

        remaining = set(pg.list_databases())
        assert not (set(clones) & remaining)
        assert template not in remaining

    def test_apply_sql_keeps_dollar_quoted_bodies_intact(self, pg):
        # The oracle must never re-parse SQL — parsing is what's under test.
        name = f"dollar_{uuid.uuid4().hex[:8]}"
        pg.create_database(name)
        pg.apply_sql(
            name,
            """
            CREATE FUNCTION bump() RETURNS trigger AS $$
            BEGIN
                NEW.total := NEW.total; -- statement; with semicolons
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        )
        with pg.connect(name) as conn, conn.cursor() as cur:
            cur.execute("SELECT proname FROM pg_proc WHERE proname = 'bump'")
            assert cur.fetchone() is not None
        pg.drop_database(name)

    def test_apply_empty_sql_is_a_noop(self, pg):
        name = f"empty_{uuid.uuid4().hex[:8]}"
        pg.create_database(name)
        pg.apply_sql(name, "   \n  ")
        pg.drop_database(name)


@requires_docker
@pytest.mark.skipif(
    bool(os.environ.get(ENV_REUSE_URL)),
    reason="Container teardown only applies to the docker backend",
)
class TestContainerTeardown:
    def test_exiting_removes_the_container(self):
        """Phase 1 acceptance: nothing is left behind."""
        before = _running_eval_containers()
        with PostgresFixture() as fixture:
            name = fixture.container_name
            assert name is not None
            assert _running_eval_containers() - before
        assert _running_eval_containers() == before

    def test_stop_is_idempotent(self):
        fixture = PostgresFixture().start()
        fixture.stop()
        fixture.stop()
        assert fixture.container_name is None

    def test_default_sweep_spares_a_running_fixture(self):
        # A second run starting up must not tear down a container the first
        # run is still using — the victim would see its server vanish rather
        # than get an error.
        with PostgresFixture() as active:
            active_name = active.container_name
            assert active_name is not None
            sweep_stale_containers()
            assert _container_exists(active_name) is True

    def test_starting_a_second_fixture_spares_the_first(self, pg):
        # start() sweeps; the module-scoped fixture must survive it.
        assert pg.container_name is not None
        with PostgresFixture():
            pass
        assert _container_exists(pg.container_name) is True
        with pg.connect("postgres") as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
