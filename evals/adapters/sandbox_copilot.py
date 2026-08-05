"""Migration sandbox with the production AI co-pilot enabled."""

from evals.adapters.base import EvalAdapter, prompt_hash
from evals.adapters.sandbox_common import analyze_with_sandbox
from evals.core.models import EvalAnalysis, RunConfig, Truth
from evals.core.suites import SuiteDefinition
from schemint.config import get_settings
from schemint.drift.copilot_agent import (
    ALTERNATIVES_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    ROLLBACK_SYSTEM_PROMPT,
)


class SandboxCopilotAdapter(EvalAdapter):
    """Run deterministic sandbox analysis plus alternatives, rollback, and intent."""

    name = "sandbox_copilot"

    def analyze(self, suite: SuiteDefinition) -> EvalAnalysis:
        return analyze_with_sandbox(suite, run_copilot=True)

    def config(self, truth: Truth, trial: int) -> RunConfig:
        settings = get_settings()
        return RunConfig(
            adapter=self.name,
            adapter_version=self.version,
            model_id=settings.claude_model,
            prompt_version=prompt_hash(
                ALTERNATIVES_SYSTEM_PROMPT,
                ROLLBACK_SYSTEM_PROMPT,
                INTENT_SYSTEM_PROMPT,
            ),
            temperature=settings.claude_temperature,
            trial=trial,
            generator_version=truth.generator_version,
        )
