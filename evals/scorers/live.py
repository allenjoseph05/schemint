"""Lazy lifecycle wrapper for Phase 4 live artifact scorers."""

from __future__ import annotations

from evals.core.models import EvalAnalysis, ScoreRow
from evals.core.suites import SuiteDefinition
from evals.oracle.postgres import PostgresFixture
from evals.scorers.alternatives import score_alternatives
from evals.scorers.rollback import score_rollback


class LiveArtifactScorer:
    """Own one Postgres fixture and start it only when generated SQL exists."""

    def __init__(self, fixture: PostgresFixture | None = None) -> None:
        self.fixture = fixture
        self._owns_fixture = fixture is None
        self._started = fixture is not None

    def score(
        self,
        suite: SuiteDefinition,
        analysis: EvalAnalysis,
        score: ScoreRow,
    ) -> ScoreRow:
        if not analysis.rollback_sql and not analysis.alternative_sqls:
            return score
        pg = self._fixture()
        rollback = score_rollback(pg, suite, analysis, trial=score.trial)
        alternatives = score_alternatives(pg, suite, analysis, trial=score.trial)
        return score.model_copy(
            update={
                "rollback_executes": rollback.executes,
                "rollback_restores": rollback.restores,
                "alternative_executes": alternatives.executes,
                "alternative_safe": alternatives.safe,
                "alternative_changes_schema": alternatives.changes_schema,
            }
        )

    def close(self) -> None:
        if self._owns_fixture and self.fixture is not None and self._started:
            self.fixture.__exit__()
            self._started = False

    def _fixture(self) -> PostgresFixture:
        if self.fixture is None:
            self.fixture = PostgresFixture()
        if not self._started:
            self.fixture.start()
            self._started = True
        return self.fixture
