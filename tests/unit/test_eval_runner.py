"""Unit tests for resumable Phase 3 execution and reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.adapters.base import EvalAdapter
from evals.core.models import EvalAnalysis, EvalTask, RunConfig, Truth
from evals.core.runner import run_evaluations
from evals.core.store import EvalStore
from evals.core.suites import SuiteDefinition
from evals.report import render_html, render_text


class StubAdapter(EvalAdapter):
    name = "rules_only"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def analyze(self, _suite: SuiteDefinition) -> EvalAnalysis:
        if self.fail:
            raise RuntimeError("adapter exploded")
        return EvalAnalysis(risk="safe", blocked=False)

    def config(self, truth: Truth, trial: int) -> RunConfig:
        return RunConfig(
            adapter=self.name,
            trial=trial,
            generator_version=truth.generator_version,
        )


@pytest.mark.unit
def test_runner_records_errors_without_stopping(tmp_path):
    suite = _suite(tmp_path)
    store = EvalStore(tmp_path / "results.db")
    summary = run_evaluations(StubAdapter(fail=True), [suite], store=store)
    assert summary.completed == 1
    assert summary.errors == 1
    assert store.run_count() == 1
    assert store.error_count() == 1
    assert store.latest_scores()[0].errored is True


@pytest.mark.unit
def test_runner_resumes_successful_cells_and_report_has_four_columns(tmp_path):
    suite = _suite(tmp_path)
    store = EvalStore(tmp_path / "results.db")
    first = run_evaluations(StubAdapter(), [suite], store=store)
    second = run_evaluations(StubAdapter(), [suite], store=store)
    assert first.completed == 1
    assert second.completed == 0
    assert second.skipped == 1
    report = render_text(store)
    assert all(
        adapter in report
        for adapter in (
            "rules_only",
            "sandbox_copilot",
            "drift_pipeline",
            "naive_llm",
        )
    )


@pytest.mark.unit
def test_suite_hash_is_stable_across_line_endings(tmp_path):
    suite = _suite(tmp_path)
    content = suite.schema_path.read_bytes().replace(b"\r\n", b"\n")
    suite.schema_path.write_bytes(content)
    first_hash = suite.input_hash()
    suite.schema_path.write_bytes(content.replace(b"\n", b"\r\n"))
    assert suite.input_hash() == first_hash


@pytest.mark.unit
def test_html_report_is_self_contained(tmp_path):
    suite = _suite(tmp_path)
    store = EvalStore(tmp_path / "results.db")
    run_evaluations(StubAdapter(), [suite], store=store)
    report = render_html(store)
    assert report.startswith("<!doctype html>")
    assert "rules_only" in report
    assert "<style>" in report
    assert "https://" not in report


def _suite(tmp_path: Path) -> SuiteDefinition:
    directory = tmp_path / "add_note"
    directory.mkdir()
    schema = directory / "schema.sql"
    migration = directory / "migration.sql"
    meta = directory / "meta.json"
    expected = directory / "expected.json"
    schema.write_text("CREATE TABLE users (id INTEGER);\n", encoding="utf-8")
    migration.write_text("ALTER TABLE users ADD COLUMN note TEXT;\n", encoding="utf-8")
    meta.write_text(
        '{"id":"add_note","category":"safe"}\n',
        encoding="utf-8",
    )
    task = EvalTask(id="add_note", category="safe", directory=str(directory))
    suite = SuiteDefinition(
        task=task,
        directory=directory,
        schema_path=schema,
        seed_path=None,
        migration_path=migration,
        probes_path=None,
        expected_path=expected,
        meta_path=meta,
    )
    truth = Truth(
        task_id=task.id,
        generator_version="v1",
        input_hash=suite.input_hash(),
        risk="safe",
        must_block=False,
    )
    expected.write_text(json.dumps(truth.model_dump(mode="json")), encoding="utf-8")
    return suite
