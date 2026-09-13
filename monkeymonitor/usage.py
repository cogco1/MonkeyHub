"""Portable usage values. Input and output totals include their named subsets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from copy import deepcopy
from datetime import datetime
import math
import re
from typing import Mapping


def _count(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


_DETAIL_KEYS = {
    "input_identity", "input_object_ids", "recomputed_object_ids", "reused_object_ids", "emitted_object_ids",
    "execution_path", "scope", "cache_status", "cache_reason", "cache_checks", "comparison_refs",
    "output_refs", "executed_stages", "input_equivalent", "reuse_opportunity", "request_kind",
    "model_inference_ms", "active_wait_ms", "between_actions_ms", "unattributed_ms", "input_bytes", "output_bytes",
    "retry_attempt", "retry_reason", "wait_reason", "http_status", "duplicate_status", "comparison_event_id",
    "duplicate_reason", "opportunity_refs", "stable_input_parts",
    "context_budget", "task_type", "success", "validator_pass", "validator_scope", "escalation",
    "tool_name", "blocking", "first_token_ms", "provider_timing_basis", "billing_plan", "native_turn_id",
}
_IDENTITY_KEYS = {
    "context_digest", "prompt_sha256", "provider_fingerprint", "program_digest", "source_program_digest",
    "step_sha256", "source_step_sha256", "asset_sha256", "view_recipe", "backend", "backend_version",
    "source_execution_ref", "source_ref", "source_stage_ref", "producer_code", "references", "model",
    "provider", "format", "representation", "record_digest", "state_digest", "seat_id",
}
_LIST_DETAILS = {
    "input_object_ids", "recomputed_object_ids", "reused_object_ids", "emitted_object_ids", "comparison_refs",
    "output_refs", "executed_stages", "opportunity_refs", "stable_input_parts",
}
_COUNT_DETAILS = {"model_inference_ms", "active_wait_ms", "between_actions_ms", "unattributed_ms", "input_bytes",
                  "output_bytes", "retry_attempt", "http_status", "first_token_ms"}
_CONTEXT_SECTIONS = {"intent", "system", "schema", "state", "preferences", "dependencies", "overhead"}
_CONTEXT_CONTRIBUTORS = _CONTEXT_SECTIONS | {
    "all_components", "type_registry", "relations", "parameters", "component_definitions",
    "levels", "grids", "readings", "entities", "constraints",
}


def _context_budget(value: object) -> None:
    """The only allowed context report contains bounded labels and numeric estimates."""

    keys = {
        "basis", "estimator", "section_tokens", "estimated_input_tokens", "budget_tokens",
        "exceeded", "expected_max_output_tokens", "image_count", "image_tokens",
        "provider_overhead_included", "largest_contributors",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("unsupported context budget field")
    if value["basis"] != "text_estimate":
        raise ValueError("context budget is an estimate, never provider usage")
    if not isinstance(value["estimator"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value["estimator"]):
        raise ValueError("context estimator must be a bounded identifier")
    sections = value["section_tokens"]
    if not isinstance(sections, Mapping) or set(sections) != _CONTEXT_SECTIONS:
        raise ValueError("unsupported context section")
    for name, count in sections.items():
        _count(count, name)
        if count is None:
            raise ValueError("context section estimate must be known")
    for name in ("estimated_input_tokens", "budget_tokens", "expected_max_output_tokens", "image_count"):
        _count(value[name], name)
        if name != "expected_max_output_tokens" and value[name] is None:
            raise ValueError("context budget count must be known")
    if value["estimated_input_tokens"] != sum(sections.values()):
        raise ValueError("context total must sum disjoint text sections")
    if type(value["exceeded"]) is not bool or value["exceeded"] != (value["estimated_input_tokens"] > value["budget_tokens"]):
        raise ValueError("context exceeded flag must reflect its estimate")
    if value["provider_overhead_included"] is not False or value["image_tokens"] is not None:
        raise ValueError("text estimates cannot claim provider framing or image usage")
    largest = value["largest_contributors"]
    if not isinstance(largest, (list, tuple)) or len(largest) > 5:
        raise ValueError("context report must have at most five contributors")
    names = set()
    for entry in largest:
        if not isinstance(entry, Mapping) or set(entry) != {"name", "estimated_tokens"}:
            raise ValueError("unsupported context contributor field")
        name = entry["name"]
        if not isinstance(name, str) or name not in _CONTEXT_CONTRIBUTORS or name in names:
            raise ValueError("unsupported or duplicate context contributor")
        names.add(name)
        _count(entry["estimated_tokens"], "estimated_tokens")
        if entry["estimated_tokens"] is None:
            raise ValueError("context contributor estimate must be known")


def diagnostic_details(value: Mapping[str, object]) -> dict[str, object]:
    """A closed metadata vocabulary: no prompt, response, tool body or arbitrary attributes."""

    if not isinstance(value, Mapping) or set(value) - _DETAIL_KEYS:
        raise ValueError("unsupported diagnostic detail field")
    result = deepcopy(dict(value))
    for name, detail in result.items():
        if name in _COUNT_DETAILS:
            _count(detail, name)
        elif name == "context_budget":
            _context_budget(detail)
        elif name in {"success", "validator_pass", "escalation", "blocking"}:
            if detail is not None and type(detail) is not bool:
                raise ValueError(f"{name} must be bool or None")
        elif name == "task_type":
            if not isinstance(detail, str) or detail not in {"scalar", "component", "design", "scalar_edit", "component_edit", "design_edit", "unknown"}:
                raise ValueError("unsupported context task type")
        elif name == "validator_scope":
            if detail is not None and detail != "request_output":
                raise ValueError("unsupported model validator scope")
        elif name in _LIST_DETAILS:
            if not isinstance(detail, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in detail):
                raise ValueError(f"{name} must contain identifier strings")
        elif name == "input_identity":
            if not isinstance(detail, Mapping) or set(detail) - _IDENTITY_KEYS:
                raise ValueError("unsupported diagnostic input identity")
            for key, item in detail.items():
                if key == "view_recipe":
                    # The existing numeric view recipe is an input, never a free-text prompt.
                    def numeric_recipe(part):
                        if part is None or type(part) is bool or type(part) is int:
                            return True
                        if type(part) is float:
                            return math.isfinite(part)
                        if isinstance(part, str):
                            return len(part) <= 160 and bool(re.fullmatch(r"[\w.:-]+", part))
                        if isinstance(part, (list, tuple)):
                            return all(numeric_recipe(child) for child in part)
                        return isinstance(part, Mapping) and all(isinstance(k, str) and numeric_recipe(v) for k, v in part.items())
                    if not numeric_recipe(item):
                        raise ValueError("view_recipe must be the structured drawing input")
                elif item is not None and not isinstance(item, str):
                    raise ValueError("input identities must be text or None")
                elif key.endswith(("_digest", "_sha256", "_fingerprint")) and item is not None and not re.fullmatch(r"[0-9a-f]{64}", item):
                    raise ValueError("input digest must be SHA-256")
        elif name == "cache_checks":
            if not isinstance(detail, Mapping) or any(not isinstance(key, str) or
                    not (type(item) is bool or isinstance(item, str) and re.fullmatch(r"[a-z0-9_.:-]+", item))
                    for key, item in detail.items()):
                raise ValueError("cache checks must contain comparison codes")
        elif name == "input_equivalent":
            if type(detail) is not bool:
                raise ValueError("input_equivalent must be bool")
        elif detail is not None and (not isinstance(detail, str) or not detail.strip()):
            raise ValueError(f"{name} must be a diagnostic code or reference")
    return result


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
    ended_at: str | None = None
    timing_scope: str = "unknown"
    model_call: bool | None = None
    source_ref: str | None = None
    related_event_id: str | None = None
    session_id: str | None = None
    parent_session_id: str | None = None
    turn_id: str | None = None
    operation_id: str | None = None
    parent_event_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)
    rate_snapshot: Mapping[str, object] | None = None
    rate_match_status: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "provider", "model", "phase", "status", "started_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if self.source not in {"studio", "codex", "hub"}:
            raise ValueError("source must be studio, codex or hub")
        if self.billing_mode not in {"api_estimate", "subscription_equivalent", "unknown"}:
            raise ValueError("unsupported billing_mode")
        started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        if self.ended_at is not None:
            if not isinstance(self.ended_at, str):
                raise ValueError("ended_at must be ISO-8601 text or None")
            ended = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
            try:
                if ended < started:
                    raise ValueError("ended_at precedes started_at")
            except TypeError as exc:
                raise ValueError("start and end timestamps must use compatible time zones") from exc
        if self.timing_scope not in {"unknown", "model_call", "service", "client_wait", "agent_turn", "interaction"}:
            raise ValueError("unsupported timing_scope")
        if self.model_call is not None and type(self.model_call) is not bool:
            raise ValueError("model_call must be bool or None")
        _count(self.duration_ms, "duration_ms")
        if not isinstance(self.tokens, TokenUsage):
            raise TypeError("tokens must be TokenUsage")
        if self.model_call is False and any(value is not None for value in self.tokens.to_dict().values()):
            raise ValueError("a phase without a model call has no provider token counters")
        for name in ("project_id", "run_id", "source_ref", "related_event_id", "session_id", "parent_session_id", "turn_id", "operation_id", "parent_event_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty text or None")
        object.__setattr__(self, "details", diagnostic_details(self.details))
        if self.rate_match_status not in {None, "matched", "missing_identity", "not_found", "ambiguous"}:
            raise ValueError("unsupported rate match status")
        if self.rate_snapshot is not None:
            from .pricing import RateCard, match_rate
            rate = RateCard.from_dict(self.rate_snapshot)
            if (rate.provider, rate.model, rate.billing_plan) != (self.provider, self.model, self.details.get("billing_plan")):
                raise ValueError("rate snapshot must match the exact event billing identity")
            if self.rate_match_status != "matched":
                raise ValueError("a rate snapshot requires a matched status")
            if match_rate(self.provider, self.model, self.details.get("billing_plan"), self.started_at, (rate,))[1] != "matched":
                raise ValueError("a rate snapshot requires a dated public source for this billing identity")
            object.__setattr__(self, "rate_snapshot", rate.to_dict())
        elif self.rate_match_status == "matched":
            raise ValueError("a matched rate requires a snapshot")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> UsageEvent:
        payload = dict(value)
        payload["tokens"] = TokenUsage.from_dict(payload["tokens"])
        return cls(**payload)
