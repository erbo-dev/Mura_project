"""Centralized AI model pricing and deterministic cost calculation.

All financial arithmetic uses Python Decimal to prevent floating-point
imprecision on fractional-cent token pricing. If a model or provider has
no defined pricing, the calculator returns (None, None) rather than fabricating
an estimated cost.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

DEFAULT_PRICING_VERSION = "2026-09-v1"


@dataclass(frozen=True)
class ModelPricingRate:
    pricing_version: str
    input_rate_per_million: Decimal | None = None
    cached_input_rate_per_million: Decimal | None = None
    output_rate_per_million: Decimal | None = None
    audio_rate_per_second: Decimal | None = None


#: Canonical published rates (per million tokens or per audio second).
DEFAULT_PRICING_REGISTRY: dict[tuple[str, str], ModelPricingRate] = {
    # DeepSeek standard chat / flash models
    ("deepseek", "deepseek-v4-flash"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        input_rate_per_million=Decimal("0.27"),
        cached_input_rate_per_million=Decimal("0.07"),
        output_rate_per_million=Decimal("1.10"),
    ),
    ("deepseek", "deepseek-chat"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        input_rate_per_million=Decimal("0.27"),
        cached_input_rate_per_million=Decimal("0.07"),
        output_rate_per_million=Decimal("1.10"),
    ),
    ("deepseek", "deepseek-v3"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        input_rate_per_million=Decimal("0.27"),
        cached_input_rate_per_million=Decimal("0.07"),
        output_rate_per_million=Decimal("1.10"),
    ),
    # DeepSeek pro / reasoner models
    ("deepseek", "deepseek-v4-pro"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        input_rate_per_million=Decimal("0.55"),
        cached_input_rate_per_million=Decimal("0.14"),
        output_rate_per_million=Decimal("2.19"),
    ),
    ("deepseek", "deepseek-reasoner"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        input_rate_per_million=Decimal("0.55"),
        cached_input_rate_per_million=Decimal("0.14"),
        output_rate_per_million=Decimal("2.19"),
    ),
    # OpenAI Whisper models ($0.006 / minute = $0.000100 / second)
    ("whisper", "whisper-1"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        audio_rate_per_second=Decimal("0.000100"),
    ),
    ("openai", "whisper-1"): ModelPricingRate(
        pricing_version=DEFAULT_PRICING_VERSION,
        audio_rate_per_second=Decimal("0.000100"),
    ),
}

_ONE_MILLION = Decimal("1000000")
_SIX_DECIMALS = Decimal("0.000001")


def calculate_ai_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    audio_seconds: float | int | Decimal | None = None,
    pricing_registry: Mapping[tuple[str, str], ModelPricingRate] | None = None,
) -> tuple[Decimal | None, str | None]:
    """Calculate deterministic estimated cost in USD.

    Returns:
        tuple of (estimated_cost_usd, pricing_version) or (None, None) if unknown.
    """
    registry = pricing_registry if pricing_registry is not None else DEFAULT_PRICING_REGISTRY
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    rate = registry.get((normalized_provider, normalized_model))
    if rate is None:
        return None, None

    total_cost = Decimal("0")
    cost_applied = False

    # 1. Input tokens (accounting for cache hits where available)
    if input_tokens is not None and rate.input_rate_per_million is not None:
        total_input = Decimal(str(max(0, input_tokens)))
        hit_tokens = Decimal(str(max(0, cached_input_tokens or 0)))
        if hit_tokens > total_input:
            hit_tokens = total_input

        if rate.cached_input_rate_per_million is not None and hit_tokens > 0:
            miss_tokens = total_input - hit_tokens
            input_cost = (
                (miss_tokens * rate.input_rate_per_million)
                + (hit_tokens * rate.cached_input_rate_per_million)
            ) / _ONE_MILLION
        else:
            input_cost = (total_input * rate.input_rate_per_million) / _ONE_MILLION
        total_cost += input_cost
        cost_applied = True

    # 2. Output tokens
    if output_tokens is not None and rate.output_rate_per_million is not None:
        out_count = Decimal(str(max(0, output_tokens)))
        output_cost = (out_count * rate.output_rate_per_million) / _ONE_MILLION
        total_cost += output_cost
        cost_applied = True

    # 3. Audio seconds (Whisper)
    if audio_seconds is not None and rate.audio_rate_per_second is not None:
        dur = Decimal(str(max(0, float(audio_seconds))))
        audio_cost = dur * rate.audio_rate_per_second
        total_cost += audio_cost
        cost_applied = True

    if not cost_applied:
        return None, None

    return total_cost.quantize(_SIX_DECIMALS, rounding=ROUND_HALF_UP), rate.pricing_version

