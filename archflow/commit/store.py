"""P1 in-memory state store with one atomic compare-and-swap primitive."""

from __future__ import annotations

from threading import Lock

from archflow.state import CanonicalState, StateRef


class StaleStateError(RuntimeError):
    """The requested base is no longer canonical."""


class InMemoryStateStore:
    """Exposes reads publicly; mutation remains a committer-internal seam."""

    def __init__(self, initial: CanonicalState) -> None:
        self._state = initial
        self._lock = Lock()

    def read(self) -> CanonicalState:
        with self._lock:
            return self._state

    def _compare_and_swap(
        self, expected: StateRef, replacement: CanonicalState
    ) -> None:
        with self._lock:
            if self._state.ref != expected:
                raise StaleStateError(
                    f"expected {expected!r}; canonical is {self._state.ref!r}"
                )
            if replacement.ref.project_id != expected.project_id:
                raise ValueError("replacement project_id differs from canonical project")
            if replacement.ref.version != expected.version + 1:
                raise ValueError("replacement must advance exactly one version")
            self._state = replacement
