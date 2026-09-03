"""Open read-only registry for building-scoped retrieval providers."""

from __future__ import annotations

from dataclasses import dataclass

from archive.archflow.adapters.cli_retrieval import (
    CliProviderSpec,
    CliRetrievalAdapter,
    missing_provider_receipt,
)
from archive.archflow.ports.retrieval import RetrievalQuery, RetrievalReceipt


@dataclass(frozen=True, slots=True)
class RetrievalCapability:
    provider_id: str
    description: str
    topics: frozenset[str]
    side_effects: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id must be non-empty text")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be non-empty text")
        if not isinstance(self.topics, frozenset) or not self.topics:
            raise ValueError("topics must be a non-empty frozenset")
        if any(not isinstance(item, str) or not item.strip() for item in self.topics):
            raise ValueError("topics must contain non-empty text")
        if self.side_effects is not False:
            raise ValueError("retrieval capabilities must be read-only")


class RetrievalCapabilityRegistry:
    """Discovers providers but never chooses a fallback or writes state."""

    def __init__(self) -> None:
        self._capabilities: dict[str, RetrievalCapability] = {}
        self._adapters: dict[str, CliRetrievalAdapter] = {}

    def register(
        self,
        capability: RetrievalCapability,
        adapter: CliRetrievalAdapter,
    ) -> None:
        if not isinstance(capability, RetrievalCapability):
            raise TypeError("capability must be a RetrievalCapability")
        if not isinstance(adapter, CliRetrievalAdapter):
            raise TypeError("adapter must be a CliRetrievalAdapter")
        if capability.provider_id != adapter.spec.provider_id:
            raise ValueError("capability and adapter provider ids differ")
        if capability.provider_id in self._capabilities:
            raise ValueError(f"duplicate retrieval provider: {capability.provider_id}")
        self._capabilities[capability.provider_id] = capability
        self._adapters[capability.provider_id] = adapter

    def discover(self, *, topics: frozenset[str]) -> tuple[RetrievalCapability, ...]:
        if not isinstance(topics, frozenset):
            raise TypeError("topics must be a frozenset")
        matches = (
            item
            for item in self._capabilities.values()
            if item.topics & topics
        )
        return tuple(sorted(matches, key=lambda item: item.provider_id))

    def invoke(
        self,
        provider_id: str,
        query: RetrievalQuery,
    ) -> RetrievalReceipt:
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            return missing_provider_receipt(query, provider_id)
        return adapter.retrieve(query)

    def provider_spec(self, provider_id: str) -> CliProviderSpec:
        return self._adapters[provider_id].spec
