"""Optional local diagnostic storage, never a project document writer."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from .usage import UsageEvent


class UsageLog:
    """Append metadata to an explicitly supplied noncanonical directory.

    Construction is read/write free. One Studio process owns a directory;
    MonkeyMonitor reads it. No prompt, response or credential field is accepted.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "usage.jsonl"
        self._lock = Lock()

    def append(self, event: UsageEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, allow_nan=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)

    def read(self) -> tuple[list[UsageEvent], list[str]]:
        events: dict[str, UsageEvent] = {}
        warnings = []
        if not self.path.exists():
            return [], []
        with self.path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    event = UsageEvent.from_dict(json.loads(line))
                    events[event.event_id] = event
                except (ValueError, TypeError, KeyError):
                    warnings.append(f"Studio 用量记录第 {number} 行未能读取；该行未计入。")
        return list(events.values()), warnings
