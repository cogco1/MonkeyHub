"""Provider-agnostic detection for project-bound work artifact updates.

The watcher deliberately knows nothing about Codex, Claude, Rhino, Blender or
MonkeyBoard.  Producers write files; the runtime supplies explicit bindings;
this module turns a stable, validated content change into one normalized event.

It is intentionally polling-friendly.  A host may call ``scan`` from a native
filesystem watcher after a debounce or from a bounded polling loop.  Repeated
low-level notifications are coalesced because an event is emitted only after
an unchanged stat sample and a new content digest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal


ArtifactState = Literal["working", "candidate", "accepted"]


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    """One file that the project runtime has explicitly made observable."""

    project_id: str
    source_id: str
    artifact_id: str
    path: Path
    label: str
    state: ArtifactState = "working"


@dataclass(frozen=True, slots=True)
class ArtifactUpdated:
    """Objective evidence that one bound artifact advanced to new bytes."""

    project_id: str
    source_id: str
    artifact_id: str
    label: str
    path: Path
    state: ArtifactState
    previous_sha256: str
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _Observed:
    size: int
    mtime_ns: int
    sha256: str | None


Validator = Callable[[ArtifactBinding, bytes], bool]


def _default_validator(binding: ArtifactBinding, data: bytes) -> bool:
    """Accept non-empty bytes; type-specific owners may provide stricter checks."""

    del binding
    return bool(data)


class ArtifactWatcher:
    """Coalesce noisy file events into stable bound-artifact revisions.

    A file needs two consecutive scans with the same ``(size, mtime_ns)`` before
    it is read.  The first stable digest establishes a baseline and does not
    notify.  Later stable digest changes emit exactly one ``ArtifactUpdated``.

    Missing, unreadable, invalid and unbound files never emit.  Removing a
    binding also removes its remembered baseline, so a later re-bind starts
    from observation rather than fabricating an update.
    """

    def __init__(self, *, validator: Validator | None = None) -> None:
        self._validator = validator or _default_validator
        self._observed: dict[tuple[str, str, str], _Observed] = {}

    @staticmethod
    def _key(binding: ArtifactBinding) -> tuple[str, str, str]:
        return (binding.project_id, binding.source_id, binding.artifact_id)

    def forget(self, binding: ArtifactBinding) -> None:
        self._observed.pop(self._key(binding), None)

    def scan(self, bindings: Iterable[ArtifactBinding]) -> tuple[ArtifactUpdated, ...]:
        """Inspect only explicitly supplied bindings and return logical updates."""

        updates: list[ArtifactUpdated] = []
        active: set[tuple[str, str, str]] = set()

        for binding in bindings:
            key = self._key(binding)
            active.add(key)
            path = Path(binding.path)
            try:
                stat = path.stat()
            except OSError:
                # Keep the previous accepted digest. A transient missing file is
                # common during atomic-save/rename sequences and is not a delete.
                continue
            if not path.is_file():
                continue

            previous = self._observed.get(key)
            sample = (stat.st_size, stat.st_mtime_ns)
            if previous is None:
                self._observed[key] = _Observed(*sample, None)
                continue
            if (previous.size, previous.mtime_ns) != sample:
                self._observed[key] = _Observed(*sample, previous.sha256)
                continue

            try:
                data = path.read_bytes()
            except OSError:
                continue
            # A writer can still swap the file between stat and read. Re-stat
            # before accepting the bytes and wait for another stable sample if
            # identity changed under us.
            try:
                after = path.stat()
            except OSError:
                continue
            if (after.st_size, after.st_mtime_ns) != sample:
                self._observed[key] = _Observed(after.st_size, after.st_mtime_ns, previous.sha256)
                continue
            if not self._validator(binding, data):
                continue

            digest = hashlib.sha256(data).hexdigest()
            if previous.sha256 is None:
                self._observed[key] = _Observed(*sample, digest)
                continue
            if previous.sha256 == digest:
                # Preserve the digest while accepting the new stat identity;
                # touching a file must not create a project-visible update.
                self._observed[key] = _Observed(*sample, digest)
                continue

            self._observed[key] = _Observed(*sample, digest)
            updates.append(
                ArtifactUpdated(
                    project_id=binding.project_id,
                    source_id=binding.source_id,
                    artifact_id=binding.artifact_id,
                    label=binding.label,
                    path=path,
                    state=binding.state,
                    previous_sha256=previous.sha256,
                    sha256=digest,
                    size=after.st_size,
                    mtime_ns=after.st_mtime_ns,
                )
            )

        # Bindings are runtime-owned. Once a binding disappears it cannot keep
        # an implicit watcher identity alive in this process.
        for key in tuple(self._observed):
            if key not in active:
                self._observed.pop(key, None)
        return tuple(updates)
