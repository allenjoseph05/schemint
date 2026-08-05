"""Deterministic migration sandbox adapter."""

from evals.adapters.base import EvalAdapter
from evals.adapters.sandbox_common import analyze_with_sandbox
from evals.core.models import EvalAnalysis, RunConfig, Truth
from evals.core.suites import SuiteDefinition


class RulesOnlyAdapter(EvalAdapter):
    """Run production migration analysis with all AI calls disabled."""

    name = "rules_only"

    def analyze(self, suite: SuiteDefinition) -> EvalAnalysis:
        return analyze_with_sandbox(suite, run_copilot=False)

    def config(self, truth: Truth, trial: int) -> RunConfig:
        return RunConfig(
            adapter=self.name,
            adapter_version=self.version,
            trial=trial,
            generator_version=truth.generator_version,
        )
