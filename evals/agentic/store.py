"""Append-only SQLite evidence store for agent trajectories and scores."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from evals.agentic.models import AgentEvalAnalysis, AgentRunConfig, AgentScoreRow

DEFAULT_AGENT_DB = Path("evals") / "agent_results.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    category TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    trial INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_lookup
    ON agent_runs(task_id, config_hash, trial);

CREATE TABLE IF NOT EXISTS agent_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    category TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    trial INTEGER NOT NULL,
    score_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_scores_lookup
    ON agent_scores(config_hash, task_id, trial);
"""


class AgentEvalStore:
    def __init__(self, path: str | Path = DEFAULT_AGENT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def has_success(self, task_id: str, config_hash: str, trial: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM agent_runs
                   WHERE task_id=? AND config_hash=? AND trial=? AND error IS NULL
                   LIMIT 1""",
                (task_id, config_hash, trial),
            ).fetchone()
        return row is not None

    def record(
        self,
        task_id: str,
        category: str,
        config: AgentRunConfig,
        analysis: AgentEvalAnalysis,
        score: AgentScoreRow,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO agent_runs(
                       task_id,category,config_hash,trial,config_json,
                       analysis_json,error,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    category,
                    config.config_hash(),
                    config.trial,
                    config.model_dump_json(),
                    analysis.model_dump_json(),
                    analysis.error,
                    now,
                ),
            )
            run_id = int(cursor.lastrowid or 0)
            connection.execute(
                """INSERT INTO agent_scores(
                       run_id,task_id,category,config_hash,trial,score_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id,
                    task_id,
                    category,
                    config.config_hash(),
                    config.trial,
                    score.model_dump_json(),
                    now,
                ),
            )
        return run_id

    def latest_scores(self, config_hash: str | None = None) -> list[AgentScoreRow]:
        params: tuple[str, ...] = ()
        clause = ""
        if config_hash:
            clause = "WHERE config_hash=?"
            params = (config_hash,)
        query = f"""SELECT score_json FROM agent_scores
                    WHERE id IN (
                        SELECT MAX(id) FROM agent_scores {clause}
                        GROUP BY task_id,config_hash,trial
                    ) ORDER BY task_id,trial"""  # nosec B608 - static optional clause
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AgentScoreRow.model_validate_json(row["score_json"]) for row in rows]
