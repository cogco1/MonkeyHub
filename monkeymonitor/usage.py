"""Portable usage values. Input and output totals include their named subsets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Mapping


def _count(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    cache_write_1h_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            _count(getattr(self, field.name), field.name)
        if self.input_tokens is not None:
            known_write = max(self.cache_write_input_tokens or 0, self.cache_write_1h_input_tokens or 0)
            known_cache = (self.cached_input_tokens or 0) + known_write
            if known_cache > self.input_tokens:
                raise ValueError("cache read and write tokens exceed inclusive input_tokens")
        for subset, total, label in (
            (self.cache_write_1h_input_tokens, self.cache_write_input_tokens, "one-hour cache write"),
            (self.reasoning_output_tokens, self.output_tokens, "reasoning output"),
        ):
            if subset is not None and total is not None and subset > total:
                raise ValueError(f"{label} tokens exceed their inclusive total")

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TokenUsage:
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class UsageEvent:
    event_id: str
    source: str
    provider: str
    model: str
    phase: str
    status: str
    started_at: str
    tokens: TokenUsage
    billing_mode: str = "unknown"
    duration_ms: int | None = None
    project_id: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "provider", "model", "phase", "status", "started_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if self.source not in {"studio", "codex"}:
            raise ValueError("source must be studio or codex")
        if self.billing_mode not in {"api_estimate", "subscription_equivalent", "unknown"}:
            raise ValueError("unsupported billing_mode")
        datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        _count(self.duration_ms, "duration_ms")
        if not isinstance(self.tokens, TokenUsage):
            raise TypeError("tokens must be TokenUsage")
        for name in ("project_id", "run_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty text or None")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> UsageEvent:
        payload = dict(value)
        payload["tokens"] = TokenUsage.from_dict(payload["tokens"])
        return cls(**payload)
