"""Execute and objectively grade AI-generated rollback SQL."""

from __future__ import annotations

from dataclasses import dataclass

from evals.core.models import EvalAnalysis
from evals.core.suites import SuiteDefinition
from evals.oracle.postgres import PostgresFixture
from evals.scorers.live_common import (
    capture_state,
    database_name,
    health_restored,
    prepare_database,
    schema_restored,
)


@dataclass(frozen=True)
class RollbackScore:
    executes: bool | None
    restores: bool | None


def score_rollback(
    pg: PostgresFixture,
    suite: SuiteDefinition,
    analysis: EvalAnalysis,
    *,
    trial: int,
) -> RollbackScore:
    """Apply migration then rollback in an isolated database."""
    if not analysis.rollback_sql:
        return RollbackScore(executes=None, restores=None)
    dbname = database_name("rollback", suite.task.id, trial)
    prepare_database(pg, dbname, suite)
    try:
        before_snapshot, before_health = capture_state(pg, dbname, suite)
        try:
            pg.apply_sql(dbname, suite.migration_sql())
        except Exception:
            return RollbackScore(executes=None, restores=None)
        try:
            pg.apply_sql(dbname, analysis.rollback_sql)
        except Exception:
            return RollbackScore(executes=False, restores=False)
        after_snapshot, after_health = capture_state(pg, dbname, suite)
        restored = schema_restored(before_snapshot, after_snapshot) and health_restored(
            before_health, after_health
        )
        return RollbackScore(executes=True, restores=restored)
    finally:
        pg.drop_database(dbname)
