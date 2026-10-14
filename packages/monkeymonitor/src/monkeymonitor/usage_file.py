"""Read explicitly selected UsageEvent@1 files: the events another machine or agent reports.

Two forms are read. A document is what ``python -m monkeymonitor report`` writes:
``{"schema": "UsageEvent@1", "events": [...]}``. A line file holds one UsageEvent@1
object per line, so an agent can append as it goes; an unfinished last line is
skipped. Either way each object is exactly ``UsageEvent.to_dict()`` and passes the
same validation, so a file cannot carry prompts, responses or free-form details.

A file reports, it does not overrule. Hub and Studio rows are refused: they are
written on this machine as they happen and a copy cannot stand in for them. An
event keeps the agent source it records (``usage-file`` for an agent with no
reader of its own). Its billing plan is kept only when its recorded connection
gives that plan. Event ids that differ between copies are refused outright; the
caller refuses ids it already holds (``AgentSource.existing_ids``).
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from .pricing import billing_plan
from .usage import HOST_SOURCES, TokenUsage, UsageEvent

AGENT = "usage-file"
SCHEMA = "UsageEvent@1"


def tokens(value: Mapping[str, object]) -> TokenUsage | None:
    """A file already counts in Monitor's inclusive terms."""
    return TokenUsage.from_dict(value) if isinstance(value, Mapping) else None


def _records(path: Path, notices: list[str]) -> Iterator[object]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        _notice(notices, "指定的 UsageEvent 文件暂不可读；已跳过。")
        return
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict) and "schema" in document:
        if document.get("schema") != SCHEMA or not isinstance(document.get("events"), list):
            _notice(notices, f"指定的文件不是 {SCHEMA} 文档；已整体跳过。")
            return
        yield from document["events"]
        return
    for line in text.splitlines(keepends=True):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            if line.endswith(("\n", "\r")):
                _notice(notices, "UsageEvent 文件中有无法解析的行；已跳过这些行。")
            else:
                _notice(notices, "UsageEvent 文件末尾有不完整的半行（可能仍在写入）；已跳过。")


def _notice(notices: list[str], text: str) -> None:
    if text not in notices:
        notices.append(text)


def iter_usage_file_events(paths: Iterable[str | Path], *, warnings: list[str] | None = None) -> Iterator[UsageEvent]:
    notices: list[str] = []
    held: dict[str, tuple[UsageEvent, str]] = {}
    clashing: set[str] = set()
    invalid = host = unpriced = 0
    for path in dict.fromkeys(Path(value).resolve() for value in paths):
        for record in _records(path, notices):
            try:
                if not isinstance(record, dict):
                    raise ValueError("a UsageEvent@1 record is an object")
                event = UsageEvent.from_dict(record)
            except (ValueError, TypeError, KeyError):
                invalid += 1
                continue
            if event.source in HOST_SOURCES:
                host += 1
                continue
            plan = event.details.get("billing_plan")
            if plan is not None and plan != billing_plan(event.provider):
                unpriced += 1
                event = replace(event, details={key: value for key, value in event.details.items() if key != "billing_plan"},
                                rate_snapshot=None, rate_match_status=None)
            identity = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
            previous = held.get(event.event_id)
            if previous is None:
                held[event.event_id] = (event, identity)
            elif previous[1] != identity:
                clashing.add(event.event_id)
    if invalid:
        _notice(notices, f"UsageEvent 文件中有 {invalid} 条记录不符合 {SCHEMA}；未计入。")
    if host:
        _notice(notices, f"UsageEvent 文件中有 {host} 条 hub/studio 记录，已拒收：文件不能冒充 Hub 或 Studio 的直接记录。")
    if unpriced:
        _notice(notices, f"UsageEvent 文件中有 {unpriced} 条记录的计价档位与其连接不符；已去掉档位，费用保持未知。")
    if clashing:
        _notice(notices, f"UsageEvent 文件中有 {len(clashing)} 个 event_id 在不同副本里内容不同，已全部拒收。")
    if warnings is not None:
        warnings.extend(notice for notice in notices if notice not in warnings)
    for identifier, (event, _) in held.items():
        if identifier not in clashing:
            yield event
