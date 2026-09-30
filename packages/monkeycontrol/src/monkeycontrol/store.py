"""The append-only action trace, under a directory the caller supplies.

MonkeyControl writes receipts, screenshots, frames and manifests only below
that directory, and every write in this package happens in this file, which
governance/architecture_policy.json registers site by site. It owns no default
location, no project path and no canonical record.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from archflow.contracts.canonical import canonical_json

RECEIPTS = "actions.ndjson"


class ActionTraceStore:
    """One trace directory: receipts as NDJSON, everything else beside them."""

    def __init__(self, directory: Path) -> None:
        # Construction touches no disk: a runtime that refuses its first action
        # leaves no directory behind.
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def sha256(data: bytes) -> str:
        """The content identity ``save_bytes`` names a file by, for callers
        that keep their own index of sequentially named frames."""

        return hashlib.sha256(data).hexdigest()

    def read_receipts(self) -> list[dict]:
        """Every receipt appended so far; ``[]`` while the trace has none."""

        path = self._directory / RECEIPTS
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append(self, receipt: dict) -> None:
        """Add one canonical JSON line to ``actions.ndjson``."""

        path = self._directory / RECEIPTS
        self._prepare(path.parent)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(receipt) + "\n")

    def save_bytes(
        self,
        relative_dir: str,
        data: bytes,
        suffix: str,
        *,
        name: str | None = None,
    ) -> str:
        """Store bytes under ``relative_dir`` and return the path relative to
        the trace directory.

        The file is ``<sha256><suffix>``, so identical screenshots are stored
        once; a recorder that needs an ordered frame sequence passes its own
        ``name`` instead and keeps the digest from
        :meth:`ActionTraceStore.sha256`. The bytes are written under a
        temporary name and moved into place, so a reader never sees half a file.
        """

        filename = f"{self.sha256(data)}{suffix}" if name is None else name
        relative = self._relative(f"{relative_dir}/{filename}")
        destination = self._directory / relative
        self._prepare(destination.parent)
        temporary = destination.with_name(f".{destination.name}.part")
        with temporary.open("wb") as stream:
            stream.write(data)
        os.replace(temporary, destination)
        return relative.as_posix()

    def append_json(self, relative_path: str, payload: dict) -> str:
        """Add one canonical JSON line to any NDJSON file in the trace.

        :meth:`append` owns the receipt trace and nothing else; a recorder's
        frame index and timeline are journals of the same shape, written line
        by line while the recording runs, so they come through here rather
        than through a second writer outside this file.
        """

        relative = self._relative(relative_path)
        destination = self._directory / relative
        self._prepare(destination.parent)
        with destination.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(payload) + "\n")
        return relative.as_posix()

    def read_lines(self, relative_path: str) -> list[dict]:
        """Every JSON line of one NDJSON file; ``[]`` while it does not exist."""

        path = self._directory / self._relative(relative_path)
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def write_json(self, relative_path: str, payload: dict) -> None:
        """Write one canonical JSON document, replacing any earlier version."""

        relative = self._relative(relative_path)
        destination = self._directory / relative
        self._prepare(destination.parent)
        temporary = destination.with_name(f".{destination.name}.part")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(payload))
        os.replace(temporary, destination)

    def _prepare(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _relative(self, relative: str) -> PurePosixPath:
        """Refuse any path that would write outside the caller's directory."""

        cleaned = relative.replace("\\", "/")
        parts = [part for part in cleaned.split("/") if part not in ("", ".")]
        if not parts or cleaned.startswith("/") or ":" in cleaned or ".." in parts:
            raise ValueError(
                f"{relative!r} must be a relative path inside the trace directory"
            )
        return PurePosixPath(*parts)
