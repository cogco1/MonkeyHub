"""Filesystem isolation metadata for speculative work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from archflow.state import CanonicalState, StateRef


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    workspace_id: str
    base: StateRef
    root: Path


class WorkspaceManager:
    """Creates branch-local directories without mutating canonical state."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def fork(self, state: CanonicalState) -> WorkspaceRef:
        workspace_id = f"w-{state.ref.version}-{uuid4().hex[:12]}"
        path = (self._root / workspace_id).resolve()
        path.relative_to(self._root)
        path.mkdir()
        return WorkspaceRef(workspace_id=workspace_id, base=state.ref, root=path)
