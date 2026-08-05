"""Live Postgres checks for generated rollback and alternative scoring."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - checking for the supported Docker backend

import pytest

from evals.core.models import EvalAnalysis
from evals.core.suites import select_suites
from evals.oracle.postgres import ENV_REUSE_URL, postgres_fixture
from evals.scorers.alternatives import score_alternatives
from evals.scorers.rollback import score_rollback

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _postgres_available() -> bool:
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


@pytest.mark.skipif(not _postgres_available(), reason="Postgres scorer is unavailable")
def test_rollback_and_alternative_scores_execute_against_postgres() -> None:
    suite = select_suites(["add_nullable_column"])[0]
    with postgres_fixture() as pg:
        rollback = score_rollback(
            pg,
            suite,
            EvalAnalysis(
                risk="safe",
                rollback_sql="ALTER TABLE users DROP COLUMN timezone;",
            ),
            trial=0,
        )
        safe_alternative = score_alternatives(
            pg,
            suite,
            EvalAnalysis(
                risk="safe",
                alternative_sqls=["ALTER TABLE users ADD COLUMN timezone VARCHAR(64);"],
            ),
            trial=0,
        )
        unsafe_alternative = score_alternatives(
            pg,
            suite,
            EvalAnalysis(risk="breaking", alternative_sqls=["DROP TABLE users CASCADE;"]),
            trial=1,
        )

    assert rollback.executes is True
    assert rollback.restores is True
    assert safe_alternative.executes is True
    assert safe_alternative.safe is True
    assert safe_alternative.changes_schema is True
    assert unsafe_alternative.executes is True
    assert unsafe_alternative.safe is False
