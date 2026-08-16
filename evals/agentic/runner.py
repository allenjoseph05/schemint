"""Resumable, metered execution of the real tool-using schema agent."""

from __future__ import annotations

import time
from dataclasses import dataclass

from evals.agentic.adapter import AgentAnalyzerEvalAdapter
from evals.agentic.models import AgentEvalAnalysis
from evals.agentic.scorer import score_agent_analysis
from evals.agentic.store import AgentEvalStore
from evals.agentic.suites import AgentSuiteDefinition
from evals.core.metering import meter


@dataclass(frozen=True)
class AgentRunSummary:
    attempted: int
    completed: int
    skipped: int
    errors: int
    cost_usd: float
    budget_exhausted: bool


def run_agent_evaluations(
    suites: list[AgentSuiteDefinition],
    *,
    trials: int,
    budget_usd: float,
    store: AgentEvalStore,
    force: bool = False,
    adapter: AgentAnalyzerEvalAdapter | None = None,
) -> AgentRunSummary:
    """Run real Anthropic calls; callers must explicitly supply a positive cap."""
    if trials < 1:
        raise ValueError("trials must be at least one")
    if budget_usd <= 0:
        raise ValueError("a positive budget_usd is mandatory for agent evaluation")
    selected_adapter = adapter or AgentAnalyzerEvalAdapter()
    completed = skipped = errors = 0
    total_cost = 0.0
    exhausted = False

    for suite in suites:
        for trial in range(trials):
            config = selected_adapter.config(suite, trial)
            if not force and store.has_success(
                suite.record.task.id, config.config_hash(), config.trial
            ):
                skipped += 1
                continue
            if total_cost >= budget_usd:
                exhausted = True
                break

            started = time.perf_counter()
            with meter() as usage:
                try:
                    analysis = selected_adapter.analyze(suite)
                except Exception as exc:
                    analysis = AgentEvalAnalysis(error=f"{type(exc).__name__}: {exc}")
            elapsed = round((time.perf_counter() - started) * 1000)
            analysis = analysis.model_copy(
                update={
                    "tokens_in": usage.input_tokens,
                    "tokens_out": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "llm_calls": usage.calls,
                    "cost_usd": usage.cost_usd,
                    "latency_ms": elapsed,
                }
            )
            score = score_agent_analysis(suite, analysis, config)
            store.record(
                suite.record.task.id,
                suite.record.task.category,
                config,
                analysis,
                score,
            )
            completed += 1
            errors += analysis.error is not None
            total_cost += analysis.cost_usd
            if total_cost > budget_usd:
                exhausted = True
                break
        if exhausted:
            break

    return AgentRunSummary(
        attempted=len(suites) * trials,
        completed=completed,
        skipped=skipped,
        errors=errors,
        cost_usd=total_cost,
        budget_exhausted=exhausted,
    )
