"""Live Postgres checkpoint tests for Phase 2 truth generation."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - probing for a working docker daemon

import pytest

from evals.core.suites import select_suites
from evals.oracle.generate import generate_all
from evals.oracle.postgres import ENV_REUSE_URL, postgres_fixture

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


@pytest.mark.skipif(not _postgres_available(), reason="Postgres oracle is unavailable")
def test_five_hand_checked_tasks_regenerate_expected_truth() -> None:
    ids = [
        "drop_column_behind_view",
        "add_nullable_column",
        "add_not_null_with_nulls",
        "rls_policy_filters_rows",
        "break_trigger_function",
    ]
    suites = select_suites(ids)
    with postgres_fixture() as pg:
        truths = {truth.task_id: truth for truth in generate_all(suites, fixture=pg, write=False)}

    assert truths["drop_column_behind_view"].blast_radius == ["view:user_directory"]
    assert truths["add_nullable_column"].risk == "safe"
    assert truths["add_not_null_with_nulls"].risk == "breaking"
    assert truths["rls_policy_filters_rows"].blast_radius == ["query:visible_accounts"]
    assert truths["break_trigger_function"].blast_radius == [
        "column:items.quantity",
        "trigger:items_quantity_check",
    ]
