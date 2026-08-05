"""Tests for named application probes used by the eval oracle."""

from __future__ import annotations

import pytest

from evals.oracle.health import parse_probe_queries

pytestmark = pytest.mark.unit


def test_named_and_default_probes_are_split() -> None:
    probes = parse_probe_queries(
        """
        -- name: visible_accounts
        SELECT count(*) FROM accounts;

        SELECT 1;
        """
    )

    assert [(probe.name, probe.sql) for probe in probes] == [
        ("visible_accounts", "SELECT count(*) FROM accounts"),
        ("probe_002", "SELECT 1"),
    ]


def test_empty_probe_file_returns_no_queries() -> None:
    assert parse_probe_queries(" -- comments only") == []


def test_duplicate_probe_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        parse_probe_queries(
            """
            -- name: contract
            SELECT 1;
            -- name: contract
            SELECT 2;
            """
        )
