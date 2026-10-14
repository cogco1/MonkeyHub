"""Read explicitly selected UsageEvent@1 files: the events another machine or agent reports.

Two forms are read. A document is what ``python -m monkeymonitor report`` writes:
``{"schema": "UsageEvent@1", "events": [...]}``. A line file holds one UsageEvent@1
object per line, so an agent can append as it goes; an unfinished last line is
skipped. Either way each object is exactly ``UsageEvent.to_dict()`` and passes the
same validation, and every text it carries must be a bounded code: no spaces,
paths or addresses. So a file cannot put prompts, responses or workspace text
into a report.

A file reports, it does not overrule. Hub and Studio rows are refused: they are
written on this machine as they happen and a copy cannot stand in for them. An
event keeps the agent source it records (``usage-file`` for an agent with no
reader of its own). Its billing plan is kept only when its recorded connection
gives that plan, and a recorded rate never travels with it: this machine's
catalog prices the counts. Event ids that differ between copies are refused
outright; the caller refuses ids it already holds (``AgentSource.existing_ids``).
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Iterable, Iterator, Mapping

from .pricing import billing_plan
from .usage import HOST_SOURCES, TokenUsage, UsageEvent

AGENT = "usage-file"
SCHEMA = "UsageEvent@1"
# A recorded rate never travels with an event; this machine's catalog prices it.
_RATE_FIELDS = ("rate_snapshot", "rate_match_status")
# Times are validated as ISO-8601 by UsageEvent; every other text is an id-like code.
_TIMES = ("started_at", "ended_at")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+,?=#-]{0,479}")
_ADDRESS = re.compile(r"[A-Za-z]:[/\\]|file:|https?:|//")


def tokens(value: Mapping[str, object]) -> TokenUsage | None:
    """A file already counts in Monitor's inclusive terms."""
    return TokenUsage.from_dict(value) if isinstance(value, Mapping) else None


def _bounded(value: object) -> bool:
    if isinstance(value, str):
        return bool(_CODE.fullmatch(value)) and not _ADDRESS.search(value)
    if isinstance(value, Mapping):
        return all(_bounded(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_bounded(item) for item in value)
    return True


def _records(path: Path, notices: list[str]) -> Iterator[object]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        _notice(notices, "指定的 UsageEvent 文件暂不可读；已跳过。")
        return
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
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
            record = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            if line.endswith(("\n", "\r")):
                _notice(notices, "UsageEvent 文件中有无法解析的行；已跳过这些行。")
            else:
                _notice(notices, "UsageEvent 文件末尾有不完整的半行（可能仍在写入）；已跳过。")
            continue
        yield record


def _notice(notices: list[str], text: str) -> None:
    if text not in notices:
        notices.append(text)


def iter_usage_file_events(paths: Iterable[str | Path], *, warnings: list[str] | None = None) -> Iterator[UsageEvent]:
    notices: list[str] = []
    held: dict[str, tuple[UsageEvent, str]] = {}
    clashing: set[str] = set()
    invalid = unbounded = host = unpriced = 0
    for path in dict.fromkeys(Path(value).resolve() for value in paths):
        for record in _records(path, notices):
            if not isinstance(record, dict):
                invalid += 1
                continue
            try:
                # An untrusted record: whatever fails its validation is not counted.
                event = UsageEvent.from_dict({key: value for key, value in record.items() if key not in _RATE_FIELDS})
            except Exception:
                invalid += 1
                continue
            if not _bounded({key: value for key, value in event.to_dict().items() if key not in _TIMES}):
                unbounded += 1
                continue
            if event.source in HOST_SOURCES:
                host += 1
                continue
            plan = event.details.get("billing_plan")
            if plan is not None and plan != billing_plan(event.provider):
                unpriced += 1
                event = replace(event, details={key: value for key, value in event.details.items() if key != "billing_plan"})
            identity = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
            previous = held.get(event.event_id)
            if previous is None:
                held[event.event_id] = (event, identity)
            elif previous[1] != identity:
                clashing.add(event.event_id)
    if invalid:
        _notice(notices, f"UsageEvent 文件中有 {invalid} 条记录不符合 {SCHEMA}；未计入。")
    if unbounded:
        _notice(notices, f"UsageEvent 文件中有 {unbounded} 条记录的标识字段含空格、路径、网址或过长文本；未计入。")
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
