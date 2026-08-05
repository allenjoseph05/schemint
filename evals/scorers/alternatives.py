"""Execute and objectively grade AI-generated safer alternatives."""

from __future__ import annotations

from dataclasses import dataclass

from evals.core.models import EvalAnalysis
from evals.core.suites import SuiteDefinition
from evals.oracle.postgres import PostgresFixture
from evals.scorers.fidelity import schema_facts
from evals.scorers.live_common import (
    capture_state,
    database_name,
    health_preserved,
    prepare_database,
)


@dataclass(frozen=True)
class AlternativeScore:
    executes: bool | None
    safe: bool | None
    changes_schema: bool | None


def score_alternatives(
    pg: PostgresFixture,
    suite: SuiteDefinition,
    analysis: EvalAnalysis,
    *,
    trial: int,
) -> AlternativeScore:
    """Require every returned alternative to execute and preserve health/data."""
    if not analysis.alternative_sqls:
        return AlternativeScore(executes=None, safe=None, changes_schema=None)
    executions: list[bool] = []
    safety: list[bool] = []
    changes: list[bool] = []
    for index, sql in enumerate(analysis.alternative_sqls):
        dbname = database_name("alternative", suite.task.id, trial, index)
        prepare_database(pg, dbname, suite)
        try:
            before_snapshot, before_health = capture_state(pg, dbname, suite)
            try:
                pg.apply_sql(dbname, sql)
            except Exception:
                executions.append(False)
                safety.append(False)
                changes.append(False)
                continue
            after_snapshot, after_health = capture_state(pg, dbname, suite)
            executions.append(True)
            safety.append(health_preserved(before_health, after_health))
            changes.append(schema_facts(before_snapshot) != schema_facts(after_snapshot))
        finally:
            pg.drop_database(dbname)
    return AlternativeScore(
        executes=all(executions),
        safe=all(safety),
        changes_schema=all(changes),
    )
