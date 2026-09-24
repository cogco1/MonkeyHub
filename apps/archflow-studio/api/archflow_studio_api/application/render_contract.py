"""Image adapter values; adapters have no project writer or filesystem path.

The Runtime resolves exact registered pages before passing bytes across this
boundary. One generate call is one attempt: an uncertain response is never
automatically retried.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class RenderPageRef:
    run_id: str
    asset_sha256: str
    page_index: int = 0
    revision_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RenderImage:
    ref: RenderPageRef
    data: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class RenderOutputOptions:
    size: str = "1K"
    aspect_ratio: str = "source"


@dataclass(frozen=True, slots=True)
class RenderInput:
    request_id: str
    source: RenderImage
    references: tuple[RenderImage, ...]
    direction: str
    output: RenderOutputOptions


@dataclass(frozen=True, slots=True)
class RenderOutput:
    data: bytes
    mime_type: str
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RenderCapability:
    provider_id: str
    label: str
    model: str | None
    available: bool
    unavailable_reason: str | None = None
    execution: Literal["server-image", "browser-native", "host"] = "server-image"
    sizes: tuple[str, ...] = ("1K",)
    aspect_ratios: tuple[str, ...] = ("source", "1:1", "4:3", "3:4", "16:9", "9:16")
    max_references: int = 4


class RenderProviderError(Exception):
    """Only certainty crosses this boundary, never raw HTTP/secret text."""

    def __init__(self, outcome: Literal["failed", "unknown"], code: str = "provider_error"):
        if outcome not in ("failed", "unknown"):
            raise ValueError("A provider error must say failed or unknown.")
        self.outcome = outcome
        self.code = code if code in {
            "provider_error", "not_configured", "unsupported_input", "provider_rejected",
            "timeout", "transport_unknown", "invalid_output",
        } else "provider_error"
        super().__init__(outcome)


class ImageRenderAdapter(Protocol):
    def capability(self) -> RenderCapability: ...

    def generate(self, request: RenderInput) -> RenderOutput: ...
