"""Caller-supplied USD prices; no model aliases, bundled prices or billing claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from datetime import date
import json
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

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
    billing_plan: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be an exact non-empty identifier")
        for name in ("input", "cached_input", "cache_write_input", "cache_write_1h_input", "output"):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name)
        if self.billing_plan is not None and (not isinstance(self.billing_plan, str) or not self.billing_plan.strip()):
            raise ValueError("billing_plan must be an exact non-empty identifier")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RateCard:
        return cls(**dict(value))


def load_rates() -> tuple[RateCard, ...]:
    """The existing catalog is evidence, not a default for an unknown model/plan."""
    rows = json.loads((Path(__file__).parent / "rates.json").read_text(encoding="utf-8"))
    return tuple(RateCard.from_dict(row) for row in rows["rates"])


def match_rate(provider: str, model: str, billing_plan: str | None, started_at: str,
               rates: tuple[RateCard, ...]) -> tuple[RateCard | None, str]:
    """Match the exact billing identity and dated source; never resolve aliases."""
    if not billing_plan or provider in {"unknown", "none"} or model in {"unknown", "none"}:
        return None, "missing_identity"
    eligible = []
    for rate in rates:
        if (rate.provider, rate.model, rate.billing_plan) != (provider, model, billing_plan):
            continue
        try:
            effective = date.fromisoformat(rate.effective_date or "")
            observed = date.fromisoformat(started_at[:10])
            source = urlsplit(rate.source_url or "")
        except (ValueError, TypeError):
            continue
        if effective <= observed and source.scheme in {"http", "https"} and source.hostname and not source.username and not source.password:
            eligible.append(rate)
    if not eligible:
        return None, "not_found"
    latest = max(rate.effective_date for rate in eligible)
    matches = [rate for rate in eligible if rate.effective_date == latest]
    return (matches[0], "matched") if len(matches) == 1 else (None, "ambiguous")


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
