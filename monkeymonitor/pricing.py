"""Caller-supplied USD prices; no model aliases, bundled prices or billing claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .usage import TokenUsage


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a non-negative decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a non-negative decimal string") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal string")
    return result


@dataclass(frozen=True, slots=True)
class RateCard:
    provider: str
    model: str
    input: str | None = None
    cached_input: str | None = None
    cache_write_input: str | None = None
    cache_write_1h_input: str | None = None
    output: str | None = None
    source_url: str | None = None
    effective_date: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be an exact non-empty identifier")
        for name in ("input", "cached_input", "cache_write_input", "cache_write_1h_input", "output"):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RateCard:
        return cls(**dict(value))


def quote(usage: TokenUsage, rate: RateCard | None) -> dict[str, object]:
    """Price disjoint token buckets; unknown totals never become a zero quote.

    Cache writes contain their one-hour subset. Reasoning already belongs to
    output_tokens and therefore is never charged a second time. A subtotal
    includes only components whose count and rate are both known.
    """
    if rate is None:
        return {"currency": "USD", "amount_usd": None,
                "known_subtotal_usd": "0", "missing": ["rate_card"]}
    ordinary = None
    if all(value is not None for value in (
        usage.input_tokens, usage.cached_input_tokens, usage.cache_write_input_tokens,
    )):
        ordinary = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_input_tokens
    short_write = None
    if usage.cache_write_input_tokens is not None and usage.cache_write_1h_input_tokens is not None:
        short_write = usage.cache_write_input_tokens - usage.cache_write_1h_input_tokens
    components = (
        ("input", ordinary),
        ("cached_input", usage.cached_input_tokens),
        ("cache_write_input", short_write),
        ("cache_write_1h_input", usage.cache_write_1h_input_tokens),
        ("output", usage.output_tokens),
    )
    missing: list[str] = []
    subtotal = Decimal(0)
    for name, count in components:
        price = getattr(rate, name)
        if count is None:
            missing.append(f"tokens.{name}")
        elif count != 0:
            if price is None:
                missing.append(f"rate.{name}")
            else:
                subtotal += Decimal(count) * Decimal(price) / Decimal(1_000_000)
    amount = format(subtotal, "f")
    return {"currency": "USD", "amount_usd": None if missing else amount,
            "known_subtotal_usd": amount, "missing": missing}
