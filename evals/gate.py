"""Fail CI when evaluation results breach a committed baseline profile."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.core.aggregate import AdapterSummary, aggregate_scores
from evals.core.models import ScoreRow
from evals.core.store import DEFAULT_DB_PATH, EvalStore

DEFAULT_BASELINES_PATH = Path("evals") / "baselines.json"
_EPSILON = 1e-12


class Threshold(BaseModel):
    """One metric boundary, either absolute or relative to a baseline."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    direction: Literal["min", "max"]
    absolute: float | None = None
    baseline_delta: float | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> Threshold:
        if (self.absolute is None) == (self.baseline_delta is None):
            raise ValueError("threshold requires exactly one of absolute or baseline_delta")
        return self


class GateProfile(BaseModel):
    """Adapters, completeness requirements, and thresholds for one CI profile."""

    model_config = ConfigDict(extra="forbid")

    baseline: str
    adapters: list[str] = Field(min_length=1)
    required_tasks: int = Field(gt=0)
    required_trials: int = Field(gt=0)
    max_errors: int = Field(ge=0, default=0)
    allow_missing_baselines: bool = False
    thresholds: list[Threshold] = Field(min_length=1)


class BaselineSnapshot(BaseModel):
    """Metric values recorded for one corpus and adapter set."""

    model_config = ConfigDict(extra="forbid")

    tasks: int = Field(gt=0)
    adapters: dict[str, dict[str, float] | None]


class BaselineConfig(BaseModel):
    """Validated schema for the committed baseline file."""

    model_config = ConfigDict(extra="forbid")

    version: int
    snapshots: dict[str, BaselineSnapshot]
    profiles: dict[str, GateProfile]
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class GateCheck:
    """One rendered gate assertion."""

    adapter: str
    name: str
    actual: float | int | None
    expected: str
    passed: bool


@dataclass
class GateResult:
    """Complete result for a profile evaluation."""

    profile: str
    checks: list[GateCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def render(self) -> str:
        lines = [f"Evaluation gate: {self.profile}", ""]
        for check in self.checks:
            status = "PASS" if check.passed else "FAIL"
            actual = "n/a" if check.actual is None else _format_number(check.actual)
            lines.append(f"[{status}] {check.adapter}.{check.name}: {actual} ({check.expected})")
        if self.warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in self.warnings)
        lines.extend(["", "Gate passed." if self.passed else "Gate failed."])
        return "\n".join(lines)


def load_baselines(path: str | Path = DEFAULT_BASELINES_PATH) -> BaselineConfig:
    """Load and validate the committed gate configuration."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return BaselineConfig.model_validate(payload)


def evaluate_gate(
    profile_name: str,
    scores: dict[str, list[ScoreRow]],
    config: BaselineConfig,
) -> GateResult:
    """Evaluate one profile against already-selected adapter score rows."""
    if profile_name not in config.profiles:
        choices = ", ".join(sorted(config.profiles))
        raise ValueError(f"unknown profile {profile_name!r}; choose from {choices}")
    profile = config.profiles[profile_name]
    if profile.baseline not in config.snapshots:
        raise ValueError(f"profile {profile_name!r} names missing baseline {profile.baseline!r}")
    snapshot = config.snapshots[profile.baseline]
    if snapshot.tasks != profile.required_tasks:
        raise ValueError(
            f"profile {profile_name!r} requires {profile.required_tasks} tasks but "
            f"baseline {profile.baseline!r} records {snapshot.tasks}"
        )

    result = GateResult(profile=profile_name)
    for adapter in profile.adapters:
        rows = scores.get(adapter, [])
        if not rows:
            result.checks.append(GateCheck(adapter, "results", None, "results present", False))
            continue
        summary = aggregate_scores(rows)
        _check_completeness(result, profile, adapter, summary, len(rows))
        baseline = snapshot.adapters.get(adapter)
        for threshold in profile.thresholds:
            _check_threshold(result, profile, adapter, summary, baseline, threshold)
    return result


def scores_from_store(store: EvalStore, adapters: list[str]) -> dict[str, list[ScoreRow]]:
    """Select the newest configuration for each requested adapter."""
    selected: dict[str, list[ScoreRow]] = {}
    for adapter in adapters:
        runs = store.latest_runs(adapter=adapter)
        if not runs:
            selected[adapter] = []
            continue
        latest = max(runs, key=lambda run: (run.created_at, run.id))
        selected[adapter] = store.latest_scores(
            adapter=adapter,
            config_hash=latest.config_hash,
        )
    return selected


def _check_completeness(
    result: GateResult,
    profile: GateProfile,
    adapter: str,
    summary: AdapterSummary,
    score_cells: int,
) -> None:
    result.checks.extend(
        [
            GateCheck(
                adapter,
                "tasks",
                summary.tasks,
                f"exactly {profile.required_tasks}",
                summary.tasks == profile.required_tasks,
            ),
            GateCheck(
                adapter,
                "trials",
                summary.trials,
                f"at least {profile.required_trials}",
                summary.trials >= profile.required_trials,
            ),
            GateCheck(
                adapter,
                "cells",
                score_cells,
                f"at least {profile.required_tasks * profile.required_trials}",
                score_cells >= profile.required_tasks * profile.required_trials,
            ),
            GateCheck(
                adapter,
                "errors",
                summary.errors,
                f"at most {profile.max_errors}",
                summary.errors <= profile.max_errors,
            ),
        ]
    )


def _check_threshold(
    result: GateResult,
    profile: GateProfile,
    adapter: str,
    summary: AdapterSummary,
    baseline: dict[str, float] | None,
    threshold: Threshold,
) -> None:
    estimate = summary.metrics.get(threshold.metric)
    if estimate is None or not math.isfinite(estimate.value):
        result.checks.append(
            GateCheck(adapter, threshold.metric, None, "finite metric present", False)
        )
        return

    target = threshold.absolute
    source = "absolute"
    if threshold.baseline_delta is not None:
        if baseline is None or threshold.metric not in baseline:
            message = (
                f"{adapter}.{threshold.metric} has no committed baseline in {profile.baseline}"
            )
            if profile.allow_missing_baselines:
                result.warnings.append(f"Skipped {message}")
                return
            result.checks.append(
                GateCheck(adapter, threshold.metric, estimate.value, "baseline present", False)
            )
            return
        target = baseline[threshold.metric] + threshold.baseline_delta
        source = f"baseline {baseline[threshold.metric]:.6f}"

    if target is None:  # guarded by Threshold validation
        raise AssertionError("threshold target was not resolved")
    passed = (
        estimate.value + _EPSILON >= target
        if threshold.direction == "min"
        else estimate.value <= target + _EPSILON
    )
    operator = ">=" if threshold.direction == "min" else "<="
    result.checks.append(
        GateCheck(
            adapter,
            threshold.metric,
            estimate.value,
            f"{operator} {target:.6f} from {source}",
            passed,
        )
    )


def _format_number(value: float | int) -> str:
    return str(value) if isinstance(value, int) else f"{value:.6f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("pr", "nightly"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES_PATH)
    args = parser.parse_args()

    config = load_baselines(args.baselines)
    profile = config.profiles[args.profile]
    result = evaluate_gate(
        args.profile,
        scores_from_store(EvalStore(args.db), profile.adapters),
        config,
    )
    print(result.render())
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
