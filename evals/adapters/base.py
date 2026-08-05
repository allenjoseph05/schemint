"""Shared adapter contract and registry."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from evals.core.models import EvalAnalysis, RunConfig, Truth
from evals.core.suites import SuiteDefinition

ADAPTER_NAMES = ("rules_only", "sandbox_copilot", "drift_pipeline", "naive_llm")


class EvalAdapter(ABC):
    """A stable interface around one production analysis path."""

    name: str
    version = "1"

    @abstractmethod
    def analyze(self, suite: SuiteDefinition) -> EvalAnalysis:
        """Analyze one migration task."""

    @abstractmethod
    def config(self, truth: Truth, trial: int) -> RunConfig:
        """Describe every setting that can affect this adapter's answer."""


def prompt_hash(*prompts: str) -> str:
    """Return a short stable hash for a collection of prompts."""
    digest = hashlib.sha256()
    for prompt in prompts:
        digest.update(prompt.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def get_adapter(name: str) -> EvalAdapter:
    """Construct an adapter by CLI name without importing every SDK eagerly."""
    if name == "rules_only":
        from evals.adapters.rules_only import RulesOnlyAdapter

        return RulesOnlyAdapter()
    if name == "sandbox_copilot":
        from evals.adapters.sandbox_copilot import SandboxCopilotAdapter

        return SandboxCopilotAdapter()
    if name == "drift_pipeline":
        from evals.adapters.drift_pipeline import DriftPipelineAdapter

        return DriftPipelineAdapter()
    if name == "naive_llm":
        from evals.adapters.naive_llm import NaiveLLMAdapter

        return NaiveLLMAdapter()
    raise ValueError(f"Unknown adapter {name!r}; choose from {', '.join(ADAPTER_NAMES)}")
