"""Tests for Anthropic token/cost metering.

The cache-aware arithmetic is the part worth pinning: DriftAgent marks its
system prompt with cache_control, so an input_tokens-only sum would
under-report every run after the first.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from evals.core import metering
from evals.core.metering import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    UsageTotals,
    meter,
    resolve_price,
)


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _Response:
    model: str
    usage: _Usage | None


@pytest.mark.unit
class TestPricing:
    def test_exact_match(self):
        price, matched = resolve_price("claude-opus-5")
        assert matched == "claude-opus-5"
        assert (price.input_per_mtok, price.output_per_mtok) == (5.00, 25.00)

    def test_dated_snapshot_resolves_by_prefix(self):
        # schemint.config ships dated ids like claude-haiku-4-5-20251001.
        price, matched = resolve_price("claude-haiku-4-5-20251001")
        assert matched == "claude-haiku-4-5"
        assert (price.input_per_mtok, price.output_per_mtok) == (1.00, 5.00)

    def test_longest_prefix_wins(self):
        # claude-sonnet-4-5-* must not fall through to the shorter
        # claude-sonnet-4- entry by accident.
        _, matched = resolve_price("claude-sonnet-4-5-20250929")
        assert matched == "claude-sonnet-4-5"

    def test_unknown_model_returns_no_price(self):
        price, matched = resolve_price("some-other-provider-model")
        assert price is None
        assert matched is None

    def test_every_configured_schemint_model_is_priced(self):
        from schemint.config import Settings

        defaults = Settings.model_fields
        configured = [
            defaults[name].default
            for name in ("claude_model", "claude_model_simple", "claude_model_complex")
        ]
        unpriced = [m for m in configured if resolve_price(m)[0] is None]
        assert unpriced == []


@pytest.mark.unit
class TestUsageTotals:
    def test_accumulates_all_four_buckets(self):
        totals = UsageTotals()
        totals.add_response(
            _Response(
                "claude-opus-5",
                _Usage(
                    input_tokens=100,
                    output_tokens=50,
                    cache_creation_input_tokens=200,
                    cache_read_input_tokens=400,
                ),
            )
        )
        assert totals.input_tokens == 100
        assert totals.output_tokens == 50
        assert totals.cache_write_tokens == 200
        assert totals.cache_read_tokens == 400
        assert totals.calls == 1

    def test_total_prompt_tokens_includes_cache(self):
        totals = UsageTotals()
        totals.add_response(
            _Response(
                "claude-opus-5",
                _Usage(
                    input_tokens=100,
                    cache_creation_input_tokens=200,
                    cache_read_input_tokens=400,
                ),
            )
        )
        assert totals.total_prompt_tokens == 700

    def test_cost_applies_cache_multipliers(self):
        totals = UsageTotals()
        totals.add_response(
            _Response(
                "claude-opus-5",
                _Usage(
                    input_tokens=1_000_000,
                    output_tokens=1_000_000,
                    cache_creation_input_tokens=1_000_000,
                    cache_read_input_tokens=1_000_000,
                ),
            )
        )
        expected = 5.00 + 5.00 * CACHE_WRITE_MULTIPLIER + 5.00 * CACHE_READ_MULTIPLIER + 25.00
        assert totals.cost_usd == pytest.approx(expected)

    def test_cache_reads_are_cheaper_than_uncached_input(self):
        cached, uncached = UsageTotals(), UsageTotals()
        cached.add_response(_Response("claude-opus-5", _Usage(cache_read_input_tokens=1_000_000)))
        uncached.add_response(_Response("claude-opus-5", _Usage(input_tokens=1_000_000)))
        assert cached.cost_usd < uncached.cost_usd

    def test_accumulates_across_calls(self):
        totals = UsageTotals()
        for _ in range(3):
            totals.add_response(_Response("claude-opus-5", _Usage(input_tokens=10)))
        assert totals.calls == 3
        assert totals.input_tokens == 30

    def test_unknown_model_records_tokens_but_no_cost(self):
        totals = UsageTotals()
        totals.add_response(_Response("mystery-model", _Usage(input_tokens=1000)))
        assert totals.input_tokens == 1000
        assert totals.cost_usd == 0.0
        assert totals.unpriced_models == {"mystery-model"}
        assert totals.cost_is_complete is False

    def test_tier_priced_model_is_flagged_unverified(self):
        totals = UsageTotals()
        totals.add_response(_Response("claude-sonnet-4-20250514", _Usage(input_tokens=1000)))
        assert totals.cost_usd > 0
        assert totals.unverified_models == {"claude-sonnet-4-20250514"}

    def test_published_model_is_not_flagged(self):
        totals = UsageTotals()
        totals.add_response(_Response("claude-opus-5", _Usage(input_tokens=1000)))
        assert totals.unverified_models == set()
        assert totals.cost_is_complete is True

    def test_response_without_usage_is_ignored(self):
        totals = UsageTotals()
        totals.add_response(_Response("claude-opus-5", None))
        assert totals.calls == 0


@pytest.mark.unit
class TestMeterPatching:
    def _messages_cls(self):
        messages_module = pytest.importorskip(
            "anthropic.resources.messages",
            reason="Anthropic SDK is an optional dependency",
        )
        return messages_module.Messages

    def test_patches_and_restores_create(self):
        messages = self._messages_cls()
        original = messages.create
        with meter():
            assert messages.create is not original
        assert messages.create is original

    def test_restores_on_exception(self):
        messages = self._messages_cls()
        original = messages.create
        with pytest.raises(RuntimeError), meter():
            raise RuntimeError("boom")
        assert messages.create is original

    def test_nested_meters_both_see_the_call(self):
        response = _Response("claude-opus-5", _Usage(input_tokens=100))
        with meter() as outer, meter() as inner:
            for totals in metering._active:
                totals.add_response(response)
        assert outer.input_tokens == 100
        assert inner.input_tokens == 100

    def test_inner_exit_leaves_outer_patch_installed(self):
        messages = self._messages_cls()
        original = messages.create
        with meter():
            with meter():
                pass
            assert messages.create is not original
        assert messages.create is original

    def test_patched_create_forwards_result_and_meters_usage(self, monkeypatch):
        messages = self._messages_cls()
        response = _Response("claude-opus-5", _Usage(input_tokens=42, output_tokens=7))

        def stub_create(_self, **_kwargs):
            return response

        monkeypatch.setattr(messages, "create", stub_create, raising=True)

        with meter() as totals:
            returned = messages.create(object(), model="claude-opus-5")

        assert returned is response
        assert totals.input_tokens == 42
        assert totals.output_tokens == 7
        assert totals.calls == 1

    def test_streaming_calls_are_counted_as_unmetered(self, monkeypatch):
        messages = self._messages_cls()

        def stub_create(_self, **_kwargs):
            return iter([])

        monkeypatch.setattr(messages, "create", stub_create, raising=True)

        with meter() as totals:
            messages.create(object(), model="claude-opus-5", stream=True)

        assert totals.calls == 0
        assert totals.unmetered_stream_calls == 1
        assert totals.cost_is_complete is False

    def test_meter_with_no_calls_yields_zeroes(self):
        with meter() as totals:
            pass
        assert totals.calls == 0
        assert totals.cost_usd == 0.0
        assert totals.cost_is_complete is True
