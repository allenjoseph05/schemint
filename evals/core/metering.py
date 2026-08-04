"""Token and cost metering for adapter runs.

schemint does not record token usage anywhere — the Anthropic response is read
for content and discarded. Rather than thread a usage recorder through
``agent_brain``, ``copilot_agent`` and ``services/agent``, the harness wraps
``Messages.create`` for the duration of one adapter call and reads
``response.usage`` on the way past. Production code is untouched.

Usage arrives in four buckets and they price differently::

    input_tokens                 uncached prompt, full rate
    cache_creation_input_tokens  cache write, 1.25x input rate
    cache_read_input_tokens      cache read,  0.10x input rate
    output_tokens                completion, output rate

That matters here: ``DriftAgent`` marks its system prompt with
``cache_control``, so a naive ``input_tokens``-only sum under-reports the true
prompt size by everything the cache served.

Only the synchronous, non-streaming ``Messages.create`` path is instrumented —
that is what every schemint call site uses today. A call made through
``client.messages.stream(...)`` would go unmetered; ``unmetered_stream_calls``
records that so the shortfall is visible rather than silent.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Cache pricing multipliers, applied to the model's input rate.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


# Published rates. Keys are matched exactly first, then by longest prefix, so
# a dated snapshot id (claude-haiku-4-5-20251001) resolves to its family.
PRICING: dict[str, ModelPrice] = {
    "claude-fable-5": ModelPrice(10.00, 50.00),
    "claude-mythos-5": ModelPrice(10.00, 50.00),
    "claude-opus-5": ModelPrice(5.00, 25.00),
    "claude-opus-4-8": ModelPrice(5.00, 25.00),
    "claude-opus-4-7": ModelPrice(5.00, 25.00),
    "claude-opus-4-6": ModelPrice(5.00, 25.00),
    "claude-sonnet-5": ModelPrice(3.00, 15.00),
    "claude-sonnet-4-6": ModelPrice(3.00, 15.00),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
    # Models this project currently has configured in schemint.config. Their
    # rates are inherited from the tier rather than read off the current
    # published table — see UNVERIFIED_MODELS.
    "claude-sonnet-4-5": ModelPrice(3.00, 15.00),
    "claude-sonnet-4-": ModelPrice(3.00, 15.00),
}

# Entries in PRICING whose rate is a tier assumption, not a published figure.
# Reports footnote any cost that drew on one of these.
UNVERIFIED_MODELS: frozenset[str] = frozenset({"claude-sonnet-4-5", "claude-sonnet-4-"})

_PRICING_KEYS_BY_LENGTH = sorted(PRICING, key=len, reverse=True)


def resolve_price(model_id: str) -> tuple[ModelPrice | None, str | None]:
    """Look up a model's rate.

    Returns ``(price, matched_key)``. A model with no match returns
    ``(None, None)`` — the caller records the tokens and reports the cost as
    unknown rather than quietly charging zero.
    """
    if model_id in PRICING:
        return PRICING[model_id], model_id
    for key in _PRICING_KEYS_BY_LENGTH:
        if model_id.startswith(key):
            return PRICING[key], key
    return None, None


@dataclass
class UsageTotals:
    """Accumulated usage across every metered call in one scope."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    # Models seen with no entry in PRICING — their tokens count, their cost
    # does not.
    unpriced_models: set[str] = field(default_factory=set)
    # Models priced from an inherited tier rate rather than a published one.
    unverified_models: set[str] = field(default_factory=set)
    # Calls that went through a path this module does not instrument.
    unmetered_stream_calls: int = 0

    @property
    def total_prompt_tokens(self) -> int:
        """Full prompt size: uncached + cache writes + cache reads."""
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    @property
    def cost_is_complete(self) -> bool:
        """False when some tokens could not be priced."""
        return not self.unpriced_models and self.unmetered_stream_calls == 0

    def add_response(self, response: Any) -> None:
        """Fold one Anthropic response's usage into the totals."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        model = str(getattr(response, "model", "") or "unknown")
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

        self.calls += 1
        self.models.add(model)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_write_tokens += cache_write
        self.cache_read_tokens += cache_read

        price, matched = resolve_price(model)
        if price is None:
            self.unpriced_models.add(model)
            return
        if matched in UNVERIFIED_MODELS:
            self.unverified_models.add(model)

        per_input_token = price.input_per_mtok / 1_000_000
        per_output_token = price.output_per_mtok / 1_000_000
        self.cost_usd += (
            input_tokens * per_input_token
            + cache_write * per_input_token * CACHE_WRITE_MULTIPLIER
            + cache_read * per_input_token * CACHE_READ_MULTIPLIER
            + output_tokens * per_output_token
        )


# ---------------------------------------------------------------------------
# Patch management
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_active: list[UsageTotals] = []
_original_create: Any = None
_messages_cls: Any = None


def _load_messages_class() -> Any:
    """Return the sync ``Messages`` resource class, or None if unavailable."""
    try:
        from anthropic.resources.messages import Messages
    except Exception as exc:  # pragma: no cover - depends on install
        logger.debug("anthropic SDK not importable, metering disabled: %s", exc)
        return None
    return Messages


def _install() -> None:
    """Patch ``Messages.create``. Caller must hold the lock."""
    global _original_create, _messages_cls

    if _original_create is not None:
        return

    messages_cls = _load_messages_class()
    if messages_cls is None:
        return

    original = messages_cls.create

    def metered_create(self: Any, *args: Any, **kwargs: Any) -> Any:
        response = original(self, *args, **kwargs)
        # Streaming returns an iterator with no usage to read; count it so the
        # gap is reported rather than silently dropped.
        if kwargs.get("stream"):
            with _lock:
                for totals in _active:
                    totals.unmetered_stream_calls += 1
            return response
        with _lock:
            for totals in _active:
                try:
                    totals.add_response(response)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to meter response: %s", exc)
        return response

    messages_cls.create = metered_create  # type: ignore[method-assign]
    _original_create = original
    _messages_cls = messages_cls


def _uninstall() -> None:
    """Restore the original ``Messages.create``. Caller must hold the lock."""
    global _original_create, _messages_cls

    if _original_create is None or _messages_cls is None:
        return
    _messages_cls.create = _original_create  # type: ignore[method-assign]
    _original_create = None
    _messages_cls = None


@contextmanager
def meter() -> Iterator[UsageTotals]:
    """Accumulate Anthropic usage for the duration of the block.

    Nesting is supported — every active meter sees every call, so a per-task
    meter and a per-run budget meter can be open at once. The patch is
    installed on the first entry and removed on the last exit.

    Yields empty totals (and does nothing) when the SDK is not installed, so a
    no-LLM adapter needs no special-casing.
    """
    totals = UsageTotals()
    with _lock:
        _install()
        _active.append(totals)
    try:
        yield totals
    finally:
        with _lock:
            with suppress(ValueError):
                _active.remove(totals)
            if not _active:
                _uninstall()
