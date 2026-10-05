"""Deletable, digest-checked sync download cache outside the project (ADR-008).

No cache byte becomes retained project content except through P036's existing
transfer validator/installer. A partial file can be resumed after process exit.
"""
from __future__ import annotations
import base64
import hashlib
from pathlib import Path
import re
import threading


class SyncDownloadCache:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    def _path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid content identity")
        return self.root / digest

    def offset(self, digest: str, size: int) -> int:
        with self._lock:
            path = self._path(digest)
            if not path.exists():
                return 0
            current = path.stat().st_size
            if current > size:
                path.unlink()
                return 0
            if current == size:
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        path.unlink()
                        return 0
            return current

    def append(self, digest: str, offset: int, content: bytes, size: int) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(digest)
            current = path.stat().st_size if path.exists() else 0
            if current != offset or offset + len(content) > size:
                raise ValueError("Sync chunk does not match its position")
            with path.open("ab") as stream:
                stream.write(content)
                stream.flush()

    def encoded(self, digest: str, size: int) -> str:
        with self._lock:
            if self.offset(digest, size) != size or not self._path(digest).exists():
                raise ValueError("Sync file is incomplete")
            return base64.b64encode(self._path(digest).read_bytes()).decode("ascii")

    def saved_initial_transfer(self, project_id: str, destination: Path) -> dict | None:
        import json
        path = self.root / "initial-transfer.json"
        with self._lock:
            if not path.exists():
                return None
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("projectId") != project_id or saved.get("destination") != str(destination.resolve()):
                raise ValueError("Initial transfer belongs to another destination")
            return saved["manifest"]

    def initial_transfer(self, manifest: dict, destination: Path) -> dict:
        """Pin fully downloaded metadata immediately before first installation.

        This is a disposable download journal, not permission or project state.
        P036 still validates every byte and refuses unrelated destination files.
        """
        import json
        import os
        import tempfile
        path = self.root / "initial-transfer.json"
        binding = {"projectId": manifest["project_id"], "destination": str(destination.resolve())}
        with self._lock:
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if any(saved.get(key) != value for key, value in binding.items()):
                    raise ValueError("Initial transfer belongs to another destination")
                return saved["manifest"]
            self.root.mkdir(parents=True, exist_ok=True)
            selected = {**manifest, "contents": {}}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump({**binding, "manifest": selected}, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return selected

    def finish_initial_transfer(self) -> None:
        with self._lock:
            (self.root / "initial-transfer.json").unlink(missing_ok=True)
