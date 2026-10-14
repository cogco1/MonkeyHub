"""Read explicitly selected Claude Code session JSONL into content-free usage events.

A selected session file is read with the subagent records Claude Code keeps beside
it, ``<session>/subagents/agent-*.jsonl``; nothing else is discovered. Every file
must lie inside the Claude home (``CLAUDE_CONFIG_DIR``, default ``~/.claude``).

Only ``assistant`` rows that carry ``message.usage`` count. Claude Code writes one
row per content block, so a message's rows repeat its id with counts that grow as
the stream completes: each count is the maximum any of its rows reports, and the
message is one model call. Message text, tool inputs and results, the working
directory and every other field are never copied; identifiers are copied only
when they are bounded codes.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Iterable, Iterator, Mapping

from .pricing import billing_plan
from .usage import TokenUsage, UsageEvent

AGENT = "claude-code"
PROVIDER = "claude"
# Claude Code's own notices (an API error, an interrupted turn) are written as
# assistant messages of this model. They are not provider calls.
_LOCAL_MODEL = "<synthetic>"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,159}")
_COUNTS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
_NESTED = ("cache_creation", "output_tokens_details")
# A field whose rows disagree names no tier, so no standard plan can be claimed.
_MIXED = "mixed"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").resolve()


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def tokens(usage: Mapping[str, object]) -> TokenUsage | None:
    """Claude reports uncached input beside its cache buckets; Monitor's input includes both."""
    if not isinstance(usage, Mapping):
        return None
    ordinary, write, read = (_count(usage.get(name)) for name in
                             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    total = ordinary + write + read if None not in (ordinary, write, read) else None
    cache, details = usage.get("cache_creation"), usage.get("output_tokens_details")
    hour = _count(cache.get("ephemeral_1h_input_tokens")) if isinstance(cache, Mapping) else None
    if write == 0:
        hour = 0  # No cache write has no one-hour part either, as Hub and Codex read it.
    thinking = _count(details.get("thinking_tokens")) if isinstance(details, Mapping) else None
    return TokenUsage(input_tokens=total, output_tokens=_count(usage.get("output_tokens")),
                      cached_input_tokens=read, cache_write_input_tokens=write,
                      cache_write_1h_input_tokens=hour, reasoning_output_tokens=thinking)


def _merge(held: dict, usage: Mapping[str, object]) -> None:
    """Counts are cumulative within one message: the larger report is the later one."""
    for name, value in usage.items():
        if name in _COUNTS:
            if _count(value) is not None and (held.get(name) is None or value > held[name]):
                held[name] = value
        elif name in _NESTED:
            if isinstance(value, Mapping):
                bucket = held.setdefault(name, {})
                for key, count in value.items():
                    if _count(count) is not None and (bucket.get(key) is None or count > bucket[key]):
                        bucket[key] = count
        elif isinstance(value, str) or value is None:
            if value is not None and held.get(name, value) != value:
                held[name] = _MIXED
            elif value is not None:
                held[name] = value


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _code(value: object, pattern=_ID) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _files(path: Path, home: Path, notices: set[str]) -> list[Path]:
    """The selected file and, for a session file, its own subagents; all inside home."""
    path = path.resolve()
    if path.suffix != ".jsonl" or not path.is_relative_to(home):
        notices.add("指定的 Claude Code 会话不在 Claude home 内或不是 .jsonl；已跳过，未读取。")
        return []
    if not path.is_file():
        notices.add("指定的 Claude Code 会话暂不可读；已跳过。")
        return []
    files = [path]
    if path.parent.name != "subagents":
        directory = path.with_suffix("") / "subagents"
        if directory.is_dir():
            for child in sorted(directory.glob("agent-*.jsonl")):
                child = child.resolve()
                if child.is_file() and child.is_relative_to(home):
                    files.append(child)
    return files


def _rows(path: Path, notices: set[str]) -> Iterator[dict]:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    if line.endswith("\n"):
                        notices.add("Claude Code 会话中有无法解析的行；已跳过这些行。")
                        continue
                    notices.add("Claude Code 会话末尾有不完整的半行（可能仍在写入）；已跳过。")
                    break
                if isinstance(row, dict):
                    yield row
    except (OSError, UnicodeError):
        notices.add("指定的 Claude Code 会话暂不可读；已跳过。")


def iter_claude_code_events(paths: Iterable[str | Path], *, warnings: list[str] | None = None,
                            home: Path | None = None) -> Iterator[UsageEvent]:
    """One UsageEvent per model message, deduplicated by ``message.id`` across the selection.

    A message copied into another selected session (a resumed or forked one) is
    the same call: it keeps the first selected session and the maximum of every
    count. Started and ended times are the first and last time a row of the
    message was written, not the model's latency. Bad rows and refused paths
    become warnings; they never stop the other files.
    """
    notices: set[str] = set()
    home = (home or claude_home()).resolve()
    messages: dict[str, dict] = {}
    files = []
    for value in paths:
        for path in _files(Path(value), home, notices):
            if path not in files:
                files.append(path)
    for path in files:
        prompts: dict[str, tuple[str | None, str | None]] = {}
        for row in _rows(path, notices):
            uuid = _code(row.get("uuid"))
            if uuid is not None:
                prompts[uuid] = (_code(row.get("parentUuid")), _code(row.get("promptId")))
            if row.get("type") != "assistant":
                continue
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                notices.add("部分 Claude Code 回复缺少 message.usage；未计入。")
                continue
            if message.get("model") == _LOCAL_MODEL:
                continue
            identifier = _code(message.get("id"))
            if identifier is None:
                notices.add("部分 Claude Code 回复缺少可用的 message.id，无法去重；未计入。")
                continue
            parsed = _time(row.get("timestamp"))
            if parsed is None:
                notices.add("部分 Claude Code 回复缺少带时区的时间戳；未计入。")
                continue
            held = messages.get(identifier)
            if held is None:
                turn, seen, parent = None, set(), _code(row.get("parentUuid"))
                while parent in prompts and parent not in seen:
                    seen.add(parent)
                    parent, turn = prompts[parent]
                    if turn is not None:
                        break
                agent = _code(row.get("agentId"))
                held = messages[identifier] = {
                    "usage": {}, "model": None, "first": None, "last": None, "turn": turn,
                    "session": _code(row.get("sessionId")), "agent": agent,
                }
            _merge(held["usage"], message["usage"])
            held["model"] = held["model"] or _code(message.get("model"), _MODEL)
            # Keep the recorder's own text for the earliest and latest row.
            stamp = (parsed, row["timestamp"])
            if held["first"] is None or parsed < held["first"][0]:
                held["first"] = stamp
            if held["last"] is None or parsed >= held["last"][0]:
                held["last"] = stamp
    events = []
    for identifier, held in messages.items():
        try:
            usage = tokens(held["usage"])
        except ValueError:
            notices.add("部分 Claude Code 回复的计数自相矛盾；未计入。")
            continue
        plan = billing_plan(PROVIDER, held["usage"])
        session, agent = held["session"], held["agent"]
        events.append(UsageEvent(
            event_id=f"{AGENT}:{identifier}", source=AGENT, provider=PROVIDER, model=held["model"] or "unknown",
            phase="agent", status="observed", started_at=held["first"][1], ended_at=held["last"][1],
            tokens=usage, billing_mode="subscription_equivalent", timing_scope="unknown", model_call=True,
            source_ref=f"{AGENT}:{session}" if session else None,
            session_id=agent or session, parent_session_id=session if agent else None,
            turn_id=held["turn"], details={} if plan is None else {"billing_plan": plan},
        ))
    if warnings is not None:
        warnings.extend(sorted(notice for notice in notices if notice not in warnings))
    yield from sorted(events, key=lambda event: _time(event.started_at))
