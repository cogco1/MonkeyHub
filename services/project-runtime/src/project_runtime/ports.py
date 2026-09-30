"""Reserved application ports for the ArchFlow Studio API (P108).

There are deliberately no implementations here. In particular, no port can
waive validation or write canonical state directly.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class IntentProvider(Protocol):
    """Translate a user utterance into a proposal candidate, never a commit."""

    def propose(
        self,
        *,
        session_ref: str,
        message: str,
        context_refs: Sequence[str],
    ) -> Mapping[str, Any]: ...


class RetrievalProvider(Protocol):
    """Return evidence references scoped to a stage and query."""

    def retrieve(
        self,
        *,
        query: str,
        stage: int,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...


class PreviewBackend(Protocol):
    """Execute a compiled proposal in a speculative backend only."""

    def preview(
        self,
        *,
        compiled_program: Mapping[str, Any],
        expected_base: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ViewerAssetProvider(Protocol):
    """Resolve a detached candidate artifact for read-only viewing."""

    def resolve(self, *, artifact_ref: str) -> Mapping[str, Any]: ...


class HumanReviewPort(Protocol):
    """Record a human review decision; issuing remains a kernel concern."""

    def record_review(
        self,
        *,
        proposal_ref: str,
        reviewer_id: str,
        disposition: str,
    ) -> Mapping[str, Any]: ...


class StudioEventSink(Protocol):
    """Publish bounded progress events for SSE/WebSocket transports."""

    def publish(self, *, event: Mapping[str, Any]) -> None: ...

