"""Throwaway PostgreSQL lifecycle for ground-truth generation.

One long-lived container per harness invocation, one *database* per task —
not one container per task. Creating a database from a pre-seeded template
takes roughly 100ms against roughly 5 seconds to boot a container, which is
what makes a 60-task sweep finish in under a minute.

Two backends:

    docker   the default. Starts postgres:16-alpine on a free ephemeral port.
    reuse    set SCHEMINT_EVAL_PG_URL and the fixture talks to that server
             instead. This is the CI path — GitHub Actions ``services:``
             already provides a Postgres, and docker-in-docker is not worth
             the trouble.

Every container carries the ``schemint-eval`` label so orphans from a crashed
run can be swept on the next startup.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess  # nosec B404 - docker CLI is the supported control path
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "postgres:16-alpine"
CONTAINER_LABEL = "schemint-eval"
ENV_REUSE_URL = "SCHEMINT_EVAL_PG_URL"
ENV_IMAGE = "SCHEMINT_EVAL_PG_IMAGE"

_ADMIN_DB = "postgres"
_ADMIN_USER = "postgres"
_ADMIN_PASSWORD = "evalharness"  # nosec B105 - throwaway container credential

_READY_TIMEOUT_S = 90.0
_READY_POLL_S = 0.5
_DOCKER_TIMEOUT_S = 120

# A labelled container younger than this is assumed to belong to a run that is
# still in flight, and is left alone. Comfortably longer than any single
# harness invocation should take.
STALE_AGE_S = 3600.0

# Postgres identifiers: lowercase, unquoted-safe, within the 63-byte limit.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class OracleError(RuntimeError):
    """Raised when the Postgres fixture cannot be established or driven."""


def _require_psycopg2() -> Any:
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - psycopg2 is a hard dep
        raise OracleError(
            "psycopg2 is required for the eval oracle. Install schemint's "
            "dependencies (pip install -e .)."
        ) from exc
    return psycopg2


def validate_identifier(name: str) -> str:
    """Reject anything that is not a plain lowercase Postgres identifier.

    Database names here are derived from task ids, which come from directory
    names on disk. They are still validated: an identifier that needs quoting
    behaves differently across the create/drop/connect paths, and a task id
    that produces one should fail loudly at authoring time.
    """
    if not _SAFE_IDENT.match(name):
        raise OracleError(
            f"Unsafe database identifier {name!r}: expected lowercase letters, "
            "digits and underscores, starting with a letter or underscore, "
            "at most 63 characters."
        )
    return name


def free_port() -> int:
    """Ask the OS for an unused TCP port.

    Inherently racy — the port is released before docker binds it. Callers
    retry on a bind failure rather than trying to hold it open.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_docker(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command and capture its output."""
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, no shell
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OracleError(
            "docker was not found on PATH. Install Docker, or point "
            f"{ENV_REUSE_URL} at an existing PostgreSQL server."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OracleError(f"docker {' '.join(args)} timed out") from exc

    if check and result.returncode != 0:
        raise OracleError(
            f"docker {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _parse_docker_timestamp(value: str) -> datetime | None:
    """Parse docker's RFC3339 ``.Created`` field.

    Docker reports nanosecond precision, which ``fromisoformat`` rejects, so
    the fraction is truncated to microseconds first.
    """
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        fraction, sign, offset = _split_offset(tail)
        text = f"{head}.{fraction[:6]}{sign}{offset}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _split_offset(tail: str) -> tuple[str, str, str]:
    """Split a fractional-seconds tail into ``(fraction, sign, offset)``."""
    for sign in ("+", "-"):
        if sign in tail:
            fraction, _, offset = tail.partition(sign)
            return fraction, sign, offset
    return tail, "", ""


def container_age_seconds(container_id: str) -> float | None:
    """Seconds since a container was created, or None if unknown."""
    result = _run_docker(["inspect", "--format", "{{.Created}}", container_id], check=False)
    if result.returncode != 0:
        return None
    created = _parse_docker_timestamp(result.stdout)
    if created is None:
        return None
    return (datetime.now(timezone.utc) - created).total_seconds()


def sweep_stale_containers(max_age_seconds: float = STALE_AGE_S) -> list[str]:
    """Remove labelled containers left behind by a crashed run.

    Age-gated on purpose. A blanket sweep would tear down a container that
    another harness run — a second developer, a parallel CI job, a fixture in
    the same test session — is actively using, and the victim would see its
    server vanish mid-run rather than an error. Only containers older than
    ``max_age_seconds`` are removed; pass ``0`` to force a full sweep.

    Returns the ids removed. Never raises: a sweep failure must not block a
    run that would otherwise work.
    """
    try:
        result = _run_docker(["ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"], check=False)
    except OracleError as exc:
        logger.debug("Container sweep skipped: %s", exc)
        return []

    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    removed: list[str] = []
    for container_id in ids:
        age = container_age_seconds(container_id)
        if age is not None and age < max_age_seconds:
            logger.debug(
                "Leaving container %s alone (age %.0fs < %.0fs) — another run may be using it",
                container_id,
                age,
                max_age_seconds,
            )
            continue
        rm = _run_docker(["rm", "-f", container_id], check=False)
        if rm.returncode == 0:
            removed.append(container_id)
        else:
            logger.warning("Could not remove stale container %s", container_id)

    if removed:
        logger.info("Swept %d stale eval container(s)", len(removed))
    return removed


class PostgresFixture:
    """A PostgreSQL server the harness can create and destroy databases on.

    Usage::

        with PostgresFixture() as pg:
            pg.create_database("tmpl_basic")
            pg.apply_sql("tmpl_basic", schema_sql)
            pg.create_database("task_1", template="tmpl_basic")
    """

    def __init__(
        self,
        image: str | None = None,
        reuse_url: str | None = None,
        start_attempts: int = 3,
    ) -> None:
        self.image = image or os.environ.get(ENV_IMAGE) or DEFAULT_IMAGE
        self._reuse_url = reuse_url or os.environ.get(ENV_REUSE_URL) or None
        self.start_attempts = start_attempts

        self.container_name: str | None = None
        self.port: int | None = None
        self._started = False
        self._owned_databases: set[str] = set()

    # ----- lifecycle -----

    @property
    def uses_docker(self) -> bool:
        return self._reuse_url is None

    @property
    def admin_url(self) -> str:
        """Connection URL for the maintenance database."""
        if self._reuse_url is not None:
            return self._reuse_url
        if self.port is None:
            raise OracleError("Fixture is not started")
        return f"postgresql://{_ADMIN_USER}:{_ADMIN_PASSWORD}@127.0.0.1:{self.port}/{_ADMIN_DB}"

    def url_for(self, dbname: str) -> str:
        """Connection URL for one database on this server.

        Rebuilt through urlsplit so a reuse URL carrying query parameters
        (``?sslmode=require``) keeps them instead of having them swallowed
        into the database name.
        """
        validate_identifier(dbname)
        parts = urlsplit(self.admin_url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))

    def start(self) -> PostgresFixture:
        """Bring the server up and wait until it accepts connections."""
        if self._started:
            return self

        if self._reuse_url is not None:
            logger.info("Using existing PostgreSQL server from %s", ENV_REUSE_URL)
            self._wait_ready()
            self._started = True
            return self

        sweep_stale_containers()

        last_error: str = ""
        for attempt in range(1, self.start_attempts + 1):
            port = free_port()
            name = f"schemint-eval-{uuid.uuid4().hex[:8]}"
            result = _run_docker(
                [
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    f"{CONTAINER_LABEL}=1",
                    "-e",
                    f"POSTGRES_PASSWORD={_ADMIN_PASSWORD}",
                    "-e",
                    f"POSTGRES_USER={_ADMIN_USER}",
                    "-e",
                    f"POSTGRES_DB={_ADMIN_DB}",
                    "-p",
                    f"{port}:5432",
                    self.image,
                ],
                check=False,
            )

            if result.returncode == 0:
                self.container_name = name
                self.port = port
                break

            # A failed `docker run` can leave the published port bound on
            # Windows; a retry on a fresh port clears it.
            last_error = result.stderr.strip() or result.stdout.strip()
            logger.warning(
                "Container start attempt %d/%d failed on port %d: %s",
                attempt,
                self.start_attempts,
                port,
                last_error,
            )
            _run_docker(["rm", "-f", name], check=False)
        else:
            raise OracleError(
                f"Could not start {self.image} after {self.start_attempts} "
                f"attempts. Last error: {last_error}"
            )

        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

        self._started = True
        logger.info("PostgreSQL ready on port %d (container %s)", self.port, name)
        return self

    def stop(self) -> None:
        """Tear the server down. Idempotent; never raises."""
        self._started = False
        if self.container_name is None:
            return
        _run_docker(["rm", "-f", self.container_name], check=False)
        logger.debug("Removed container %s", self.container_name)
        self.container_name = None
        self.port = None

    def __enter__(self) -> PostgresFixture:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        if self._reuse_url is not None:
            self.drop_owned_databases()
        else:
            self.stop()

    def _wait_ready(self) -> None:
        """Poll until the server accepts a connection, or give up."""
        psycopg2 = _require_psycopg2()
        deadline = time.monotonic() + _READY_TIMEOUT_S
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                conn = psycopg2.connect(self.admin_url, connect_timeout=3)
                conn.close()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(_READY_POLL_S)

        raise OracleError(
            f"PostgreSQL did not become ready within {_READY_TIMEOUT_S:.0f}s: {last_error}"
        )

    # ----- database management -----

    @contextmanager
    def _admin_cursor(self) -> Iterator[Any]:
        """Autocommit cursor on the maintenance database.

        CREATE/DROP DATABASE cannot run inside a transaction block.
        """
        psycopg2 = _require_psycopg2()
        conn = psycopg2.connect(self.admin_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    def create_database(self, name: str, template: str | None = None) -> str:
        """Create a database, optionally cloning a seeded template.

        Returns its connection URL. Any existing database of the same name is
        dropped first so a re-run starts clean.
        """
        from psycopg2 import sql as psql

        validate_identifier(name)
        if template is not None:
            validate_identifier(template)

        self.drop_database(name)

        statement = psql.SQL("CREATE DATABASE {}").format(psql.Identifier(name))
        if template is not None:
            statement = psql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                psql.Identifier(name), psql.Identifier(template)
            )

        with self._admin_cursor() as cur:
            cur.execute(statement)

        self._owned_databases.add(name)
        return self.url_for(name)

    def drop_database(self, name: str) -> None:
        """Drop a database, disconnecting anything still attached to it."""
        from psycopg2 import sql as psql

        validate_identifier(name)
        with self._admin_cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (name,),
            )
            try:
                cur.execute(
                    psql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        psql.Identifier(name)
                    )
                )
            except Exception:
                # WITH (FORCE) needs PostgreSQL 13+. On an older reuse target,
                # the terminate above has already cleared the connections.
                cur.execute(psql.SQL("DROP DATABASE IF EXISTS {}").format(psql.Identifier(name)))

        self._owned_databases.discard(name)

    def drop_owned_databases(self) -> None:
        """Drop every database this fixture created. Never raises.

        Only matters on the reuse backend — with docker, removing the
        container takes the data with it.
        """
        for name in sorted(self._owned_databases):
            try:
                self.drop_database(name)
            except Exception as exc:
                logger.warning("Could not drop database %s: %s", name, exc)
        self._owned_databases.clear()

    def list_databases(self) -> list[str]:
        """Non-template databases present on the server."""
        with self._admin_cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
            )
            return [row[0] for row in cur.fetchall()]

    # ----- SQL execution -----

    @contextmanager
    def connect(self, dbname: str, autocommit: bool = True) -> Iterator[Any]:
        """Open a connection to one database."""
        psycopg2 = _require_psycopg2()
        conn = psycopg2.connect(self.url_for(dbname))
        conn.autocommit = autocommit
        try:
            yield conn
        finally:
            conn.close()

    def apply_sql(self, dbname: str, sql_text: str) -> None:
        """Execute a SQL script against one database.

        Sent as a single batch so dollar-quoted function bodies and multi-line
        statements survive intact — the oracle must never re-parse SQL, since
        parsing is exactly the thing under test.
        """
        if not sql_text.strip():
            return
        with self.connect(dbname) as conn, conn.cursor() as cur:
            cur.execute(sql_text)


@contextmanager
def postgres_fixture(
    image: str | None = None, reuse_url: str | None = None
) -> Iterator[PostgresFixture]:
    """Start a fixture for the duration of the block, then tear it down."""
    fixture = PostgresFixture(image=image, reuse_url=reuse_url)
    try:
        yield fixture.start()
    finally:
        fixture.__exit__()
