"""Generate committed eval truth against a real PostgreSQL instance."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from evals.core.models import Truth
from evals.core.suites import SuiteDefinition
from evals.oracle.health import capture_health, parse_probe_queries
from evals.oracle.postgres import PostgresFixture
from evals.oracle.rules import classify_truth

logger = logging.getLogger(__name__)


def generate_all(
    suites: list[SuiteDefinition],
    *,
    fixture: PostgresFixture | None = None,
    write: bool = True,
) -> list[Truth]:
    """Generate truth for suites using one server and reusable templates."""
    owned_fixture = fixture is None
    pg = fixture or PostgresFixture()
    if owned_fixture:
        pg.start()
    templates: dict[str, str] = {}
    truths: list[Truth] = []
    try:
        for suite in suites:
            template = _ensure_template(pg, suite, templates)
            truth = generate_one(pg, suite, template)
            truth.input_hash = suite.input_hash()
            truths.append(truth)
            if write:
                _write_truth(suite.expected_path, truth)
            logger.info("Generated %s: %s", suite.task.id, truth.risk)
    finally:
        if owned_fixture:
            pg.__exit__()
    return truths


def generate_one(pg: PostgresFixture, suite: SuiteDefinition, template: str) -> Truth:
    """Generate truth for one task cloned from a prepared template database."""
    dbname = _database_name("t", suite.task.id)
    pg.create_database(dbname, template=template)
    try:
        probes = parse_probe_queries(suite.probes_sql())
        with pg.connect(dbname) as connection:
            pre_health = capture_health(connection, probes)

        from schemint.drift.snapshot_pkg.live_db_capture import LiveDBSnapshotCapture

        capture = LiveDBSnapshotCapture()
        pre_snapshot = capture.capture(pg.url_for(dbname))
        migration_error: str | None = None
        try:
            pg.apply_sql(dbname, suite.migration_sql())
        except Exception as exc:
            migration_error = str(exc)
        with pg.connect(dbname) as connection:
            post_health = capture_health(connection, probes)
        post_snapshot = capture.capture(pg.url_for(dbname))
        return classify_truth(
            task_id=suite.task.id,
            pre_health=pre_health,
            post_health=post_health,
            pre_snapshot=pre_snapshot,
            post_snapshot=post_snapshot,
            migration_error=migration_error,
        )
    finally:
        pg.drop_database(dbname)


def _ensure_template(
    pg: PostgresFixture,
    suite: SuiteDefinition,
    templates: dict[str, str],
) -> str:
    schema_sql = suite.schema_sql()
    seed_sql = suite.seed_sql()
    digest = hashlib.sha256(f"{schema_sql}\0{seed_sql}".encode()).hexdigest()[:12]
    if digest in templates:
        return templates[digest]
    template = f"tmpl_{digest}"
    pg.create_database(template)
    pg.apply_sql(template, schema_sql)
    pg.apply_sql(template, seed_sql)
    templates[digest] = template
    return template


def _database_name(prefix: str, task_id: str) -> str:
    readable = "".join(char if char.isalnum() else "_" for char in task_id.lower())[:40]
    digest = hashlib.sha256(task_id.encode()).hexdigest()[:8]
    return f"{prefix}_{readable}_{digest}"[:63]


def _write_truth(path: Path, truth: Truth) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(truth.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
