"""Tests for tranche A suite discovery and committed truth validation."""

from __future__ import annotations

from collections import Counter

import pytest

from evals.core.suites import SuiteError, discover_suites, select_suites
from evals.validate_suites import validate

pytestmark = pytest.mark.unit


def test_tranche_a_has_exact_category_balance() -> None:
    suites = discover_suites()
    assert len(suites) == 30
    assert Counter(suite.task.category for suite in suites) == {"breaking": 15, "safe": 15}


def test_every_suite_resolves_existing_inputs_and_truth() -> None:
    for suite in discover_suites():
        assert suite.schema_path.is_file()
        assert suite.migration_path.is_file()
        assert suite.expected_path.is_file()
        assert len(suite.input_hash()) == 64


def test_unknown_task_selection_fails_loudly() -> None:
    with pytest.raises(SuiteError, match="Unknown task"):
        select_suites(["does_not_exist"])


def test_committed_tranche_a_passes_validation() -> None:
    assert validate() == []
