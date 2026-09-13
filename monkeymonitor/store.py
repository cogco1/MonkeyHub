"""Optional local diagnostic storage, never a project document writer."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Lock

from .pricing import load_rates, match_rate
from .usage import UsageEvent


class UsageLog:
    """Append metadata to an explicitly supplied noncanonical directory.

    Construction is read/write free. Hub and Studio share a process lock when
    appending or rotating. Monitor reads the same journal, never project state.
    """

    def __init__(self, data_dir: Path, *, max_bytes: int = 8 * 1024 * 1024, backups: int = 3) -> None:
        if type(max_bytes) is not int or max_bytes < 1 or type(backups) is not int or not 1 <= backups <= 16:
            raise ValueError("positive max_bytes and 1..16 backups are required")
        self.path = Path(data_dir) / "usage.jsonl"
        self.max_bytes, self.backups = max_bytes, backups
        self._lock = Lock()

    @contextmanager
    def _locked(self, *, write: bool):
        with self._lock:
            lock_path = self.path.with_suffix(".lock")
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                stream = lock_path.open("a+b" if write else "rb")
            except FileNotFoundError:
                # Retained pre-rotation logs have no lock file. Reading them
                # does not create diagnostic storage.
                yield
                return
            with stream:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if write else msvcrt.LK_RLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def append(self, event: UsageEvent) -> None:
        if event.model_call is True and event.rate_match_status is None:
            try:
                rates = load_rates()
            except (OSError, ValueError, TypeError, KeyError):
                rates = ()
            rate, status = match_rate(event.provider, event.model, event.details.get("billing_plan"), event.started_at, rates)
            # Unknown is a historical result too. A future catalog update must
            # not silently re-price a completed request.
            event = replace(event, rate_snapshot=rate.to_dict() if rate else None, rate_match_status=status)
        line = json.dumps(event.to_dict(), ensure_ascii=False, allow_nan=False) + "\n"
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > self.max_bytes:
            raise ValueError("diagnostic event exceeds the configured journal segment size")
        with self._locked(write=True):
            if self.path.exists() and self.path.stat().st_size + line_bytes > self.max_bytes:
                current, _ = self._read()
                cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
                active, remaining = [], self.max_bytes - line_bytes
                unfinished = sorted((row for row in current if row.status == "running" and row.event_id != event.event_id),
                                    key=lambda row: (row.phase == "hub_turn", row.started_at), reverse=True)
                for row in unfinished:
                    started = datetime.fromisoformat(row.started_at.replace("Z", "+00:00"))
                    if started.tzinfo is None or started.timestamp() < cutoff:
                        continue
                    retained = json.dumps(row.to_dict(), ensure_ascii=False, allow_nan=False) + "\n"
                    size = len(retained.encode("utf-8"))
                    if len(active) < 256 and size <= remaining:
                        active.append(retained)
                        remaining -= size
                oldest = self.path.with_name(f"usage.{self.backups}.jsonl")
                oldest.unlink(missing_ok=True)
                for index in range(self.backups - 1, 0, -1):
                    previous = self.path.with_name(f"usage.{index}.jsonl")
                    if previous.exists():
                        previous.replace(self.path.with_name(f"usage.{index + 1}.jsonl"))
                self.path.replace(self.path.with_name("usage.1.jsonl"))
                # Preserve recent unfinished roots first, with both count and
                # byte limits so a crashed writer cannot grow retention forever.
                with self.path.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.writelines(active)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)

    def read(self) -> tuple[list[UsageEvent], list[str]]:
        with self._locked(write=False):
            return self._read()

    def _read(self) -> tuple[list[UsageEvent], list[str]]:
        events: dict[str, UsageEvent] = {}
        warnings = []
        paths = [self.path.with_name(f"usage.{index}.jsonl") for index in range(self.backups, 0, -1)] + [self.path]
        if any(path.exists() for path in paths[:-1]):
            warnings.append(f"诊断日志仅保留最近 {self.backups + 1} 段；轮转保活最多 24 小时、256 个未完成阶段，且受每段字节上限约束。更早记录可能已移除。")
        for path in paths:
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        event = UsageEvent.from_dict(json.loads(line))
                        events[event.event_id] = event
                    except (ValueError, TypeError, KeyError):
                        warnings.append(f"用量记录 {path.name} 第 {number} 行未能读取；该行未计入。")
        return list(events.values()), warnings
