"""SQLite storage for eval runs and scores.

Append-only by design. Re-running a configuration inserts new rows rather than
overwriting the old ones, so a regression shows up as history you can diff
instead of a baseline you have silently lost. Readers ask for the *latest* row
per ``(task, config, trial)``; nothing ever updates or deletes.

SQLite rather than Postgres because results are a local artefact — the report
and the CI gate must run without a database server, and ``results.db`` is
small enough to attach to a CI job as evidence.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.core.models import EvalAnalysis, RunConfig, ScoreRow

DEFAULT_DB_PATH = Path("evals") / "results.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    adapter       TEXT    NOT NULL,
    config_hash   TEXT    NOT NULL,
    trial         INTEGER NOT NULL,
    config_json   TEXT    NOT NULL,
    analysis_json TEXT    NOT NULL,
    error         TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_lookup
    ON runs (task_id, config_hash, trial);
CREATE INDEX IF NOT EXISTS idx_runs_adapter
    ON runs (adapter, config_hash);

CREATE TABLE IF NOT EXISTS scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER,
    task_id     TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    adapter     TEXT    NOT NULL,
    config_hash TEXT    NOT NULL,
    trial       INTEGER NOT NULL,
    score_json  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE INDEX IF NOT EXISTS idx_scores_lookup
    ON scores (adapter, config_hash);
"""


@dataclass(frozen=True)
class StoredRun:
    """One persisted adapter execution."""

    id: int
    task_id: str
    category: str
    adapter: str
    config_hash: str
    trial: int
    config: RunConfig
    analysis: EvalAnalysis
    created_at: str


class EvalStore:
    """Append-only SQLite store for runs and scores."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ----- runs -----

    def record_run(
        self,
        task_id: str,
        category: str,
        config: RunConfig,
        analysis: EvalAnalysis,
    ) -> int:
        """Insert one run. Returns the new row id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (
                    task_id, category, adapter, config_hash, trial,
                    config_json, analysis_json, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    category,
                    config.adapter,
                    config.config_hash(),
                    config.trial,
                    config.model_dump_json(),
                    analysis.model_dump_json(),
                    analysis.error,
                    _now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def has_run(self, task_id: str, config_hash: str, trial: int) -> bool:
        """Whether this exact cell has already been executed.

        Used by the runner to resume an interrupted sweep without re-paying
        for LLM calls that already succeeded.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM runs
                WHERE task_id = ? AND config_hash = ? AND trial = ?
                  AND error IS NULL
                LIMIT 1
                """,
                (task_id, config_hash, trial),
            ).fetchone()
        return row is not None

    def latest_runs(
        self,
        adapter: str | None = None,
        config_hash: str | None = None,
    ) -> list[StoredRun]:
        """Most recent run per ``(task_id, config_hash, trial)``.

        Earlier attempts stay in the table; this is what the scorers and the
        report read.
        """
        where: list[str] = []
        params: list[Any] = []
        if adapter is not None:
            where.append("adapter = ?")
            params.append(adapter)
        if config_hash is not None:
            where.append("config_hash = ?")
            params.append(config_hash)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT * FROM runs
            WHERE id IN (
                SELECT MAX(id) FROM runs
                {clause}
                GROUP BY task_id, config_hash, trial
            )
            ORDER BY task_id, config_hash, trial
        """  # nosec B608 - clause is built from literals, params are bound
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_run(row) for row in rows]

    def config_hashes(self) -> list[tuple[str, str]]:
        """Distinct ``(adapter, config_hash)`` pairs present in the store."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT adapter, config_hash FROM runs ORDER BY adapter"
            ).fetchall()
        return [(row["adapter"], row["config_hash"]) for row in rows]

    def run_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"])

    def error_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE error IS NOT NULL").fetchone()
        return int(row["n"])

    # ----- scores -----

    def record_score(self, score: ScoreRow, run_id: int | None = None) -> int:
        """Insert one scored row. Returns the new row id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scores (
                    run_id, task_id, category, adapter, config_hash, trial,
                    score_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    score.task_id,
                    score.category,
                    score.adapter,
                    score.config_hash,
                    score.trial,
                    score.model_dump_json(),
                    _now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def latest_scores(
        self,
        adapter: str | None = None,
        config_hash: str | None = None,
    ) -> list[ScoreRow]:
        """Most recent score per ``(task_id, config_hash, trial)``."""
        where: list[str] = []
        params: list[Any] = []
        if adapter is not None:
            where.append("adapter = ?")
            params.append(adapter)
        if config_hash is not None:
            where.append("config_hash = ?")
            params.append(config_hash)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT score_json FROM scores
            WHERE id IN (
                SELECT MAX(id) FROM scores
                {clause}
                GROUP BY task_id, config_hash, trial
            )
            ORDER BY task_id, config_hash, trial
        """  # nosec B608 - clause is built from literals, params are bound
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ScoreRow.model_validate_json(row["score_json"]) for row in rows]


def _row_to_run(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        id=int(row["id"]),
        task_id=row["task_id"],
        category=row["category"],
        adapter=row["adapter"],
        config_hash=row["config_hash"],
        trial=int(row["trial"]),
        config=RunConfig.model_validate(json.loads(row["config_json"])),
        analysis=EvalAnalysis.model_validate(json.loads(row["analysis_json"])),
        created_at=row["created_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
