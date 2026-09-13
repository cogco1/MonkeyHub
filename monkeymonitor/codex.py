"""Read explicitly selected Codex JSONL files into content-free usage events.

Supported rows are session_meta, turn_context and selected event_msg metadata. The
token_count info contains total_token_usage and/or last_token_usage. Repeated
cumulative snapshots are not new calls. A discontinuity uses the reported last
call, if available, and marks its status: it does not invent an interval total.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import closing
from datetime import datetime, timezone
import heapq
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator, Mapping

from .usage import TokenUsage, UsageEvent


def bound_codex_sources(bindings: object, home: Path, warnings: list[str]) -> tuple[dict[Path, str], dict[str, str]]:
    """Resolve only Hub-named sessions through Codex's read-only native index."""
    if not isinstance(bindings, list) or any(
        not isinstance(row, dict) or set(row) != {"projectId", "sessionId"}
        or any(not isinstance(row[key], str) or not row[key].strip() for key in ("projectId", "sessionId"))
        for row in bindings
    ):
        raise ValueError("Hub usage bindings must contain projectId and sessionId")
    projects: dict[str, str] = {}
    conflicts = set()
    for row in bindings:
        session, project = row["sessionId"], row["projectId"]
        if session in projects and projects[session] != project:
            conflicts.add(session)
        projects[session] = project
    for session in conflicts:
        projects.pop(session)
    if conflicts:
        warnings.append("Hub 的同一 Codex 会话绑定了多个项目；已跳过冲突来源。")
    if not projects:
        return {}, {}
    home = home.resolve()
    paths: dict[Path, str] = {}
    missing = invalid = False
    try:
        with closing(sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=1)) as database:
            for session in projects:
                row = database.execute("SELECT rollout_path FROM threads WHERE id = ?", (session,)).fetchone()
                if row is None:
                    missing = True
                    continue
                value = row[0]
                path = Path(value) if isinstance(value, str) and value else None
                if path is None or not path.is_absolute() or path.suffix != ".jsonl":
                    invalid = True
                    continue
                path = path.resolve()
                if not path.is_relative_to(home) or not path.is_file():
                    invalid = True
                    continue
                if path in paths and paths[path] != session:
                    invalid = True
                    paths[path] = ""  # Neither binding owns this conflicting path.
                else:
                    paths[path] = session
    except (sqlite3.Error, OSError, ValueError):
        warnings.append("Codex 原生会话索引暂不可读；已有诊断和手选来源仍保留。")
        return {}, {}
    if missing:
        warnings.append("部分 Hub 会话尚未在 Codex 原生索引中找到；未猜测日志来源。")
    if invalid:
        warnings.append("部分 Hub 会话的 Codex 日志路径不可用或冲突；已跳过。")
    paths = {path: session for path, session in paths.items() if session}
    return paths, {session: projects[session] for session in paths.values()}


def _tokens(value: object) -> TokenUsage | None:
    if not isinstance(value, Mapping):
        return None
    # Codex does not currently publish a separate one-hour write count. Zero
    # total writes establishes a zero subset; otherwise its split is unknown.
    write = value.get("cache_write_input_tokens")
    return TokenUsage(
        input_tokens=value.get("input_tokens"),
        output_tokens=value.get("output_tokens"),
        cached_input_tokens=value.get("cached_input_tokens"),
        cache_write_input_tokens=write,
        cache_write_1h_input_tokens=value.get("cache_write_1h_input_tokens", 0 if write == 0 else None),
        reasoning_output_tokens=value.get("reasoning_output_tokens"),
    )


def _unknown() -> TokenUsage:
    return TokenUsage(None, None, None, None, None, None)


def _difference(current: TokenUsage, previous: TokenUsage) -> TokenUsage | None:
    values: dict[str, int | None] = {}
    for name, count in current.to_dict().items():
        old = getattr(previous, name)
        if count is None or old is None:
            values[name] = None
        elif count < old:
            return None
        else:
            values[name] = count - old
    try:
        return TokenUsage(**values)
    except ValueError:
        # Delayed subset updates cannot establish a coherent interval either.
        return None


def _disagrees(left: TokenUsage, right: TokenUsage) -> bool:
    return any(value is not None and getattr(right, name) is not None
               and value != getattr(right, name)
               for name, value in left.to_dict().items())


def _add(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(**{
        name: None if value is None or getattr(right, name) is None else value + getattr(right, name)
        for name, value in left.to_dict().items()
    })


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _native_time(value: object) -> str | None:
    if type(value) is not int:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _parent(payload: Mapping) -> str | None:
    direct = _text(payload.get("parent_thread_id"))
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    return direct or (_text(spawn.get("parent_thread_id")) if isinstance(spawn, dict) else None)


@dataclass(frozen=True)
class _Row:
    session_key: str
    session_id: str | None
    parent_session_id: str | None
    turn_id: str | None
    provider: str
    model: str
    kind: str
    timestamp: str
    ordinal: int | None
    current: TokenUsage | None = None
    last: TokenUsage | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    inherited: bool = False

    def key(self) -> tuple:
        if self.kind == "token_count":
            return self.session_key, self.kind, self.timestamp, self.current or self.last
        return self.session_key, self.kind, self.turn_id, self.started_at, self.ended_at, self.timestamp


def _read_rows(path: Path, file_index: int, warnings: list[str], expected_session: str | None = None) -> Iterator[_Row]:
    session_id = parent_id = turn_id = None
    session_key = f"file-{file_index}"
    provider = model = "unknown"
    history_mode, boundary = "legacy", None
    aliases = {"turn_started": "task_started", "turn_complete": "task_complete"}
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                if not line.endswith("\n"):
                    break
                raise ValueError(f"Malformed Codex JSON on line {line_number}") from exc
            if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                continue
            payload = row["payload"]
            if row.get("type") == "session_meta":
                session_id = _text(payload.get("id"))
                if expected_session is not None and session_id != expected_session:
                    raise ValueError("Codex session identity differs from its Hub binding")
                session_key = session_id or session_key
                parent_id = _parent(payload)
                provider = _text(payload.get("model_provider")) or provider
                history_mode = payload.get("history_mode", "legacy")
                value = payload.get("subagent_history_start_ordinal")
                boundary = value if type(value) is int and value >= 0 else None
                continue
            if expected_session is not None and session_id is None:
                continue
            ordinal = row.get("ordinal")
            ordinal = ordinal if type(ordinal) is int and ordinal >= 0 else None
            inherited = history_mode == "paginated" and boundary is not None and ordinal is not None and ordinal < boundary
            if row.get("type") == "turn_context":
                model = _text(payload.get("model")) or "unknown"
                provider = _text(payload.get("model_provider")) or provider
                turn_id = _text(payload.get("turn_id")) or turn_id
                continue
            if row.get("type") != "event_msg":
                continue
            kind = aliases.get(payload.get("type"), payload.get("type"))
            if kind not in {"token_count", "task_started", "task_complete", "turn_aborted"}:
                continue
            if boundary is not None and ordinal is None:
                warning = "Codex 子代理来源缺少 recorded ordinal，继承边界无法核实；未按文件行号猜测归属。"
                if warning not in warnings:
                    warnings.append(warning)
            current = last = None
            if kind == "token_count":
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                current, last = _tokens(info.get("total_token_usage")), _tokens(info.get("last_token_usage"))
                if current is None and last is None:
                    continue
            else:
                turn_id = _text(payload.get("turn_id")) or turn_id
            started_at, ended_at = _native_time(payload.get("started_at")), _native_time(payload.get("completed_at"))
            timestamp = _text(row.get("timestamp")) or started_at or ended_at
            if timestamp is None:
                raise ValueError(f"Codex event lacks a timestamp on line {line_number}")
            duration = payload.get("duration_ms")
            yield _Row(
                session_key, session_id, parent_id, turn_id, provider, model, kind, timestamp, ordinal,
                current, last, started_at, ended_at,
                duration if type(duration) is int and duration >= 0 else None, inherited,
            )
            if kind in {"task_complete", "turn_aborted"}:
                turn_id = None
    if expected_session is not None and session_id != expected_session:
        raise ValueError("Codex session identity is unavailable")


def _merge_rows(chains: list[list[_Row]]) -> list[_Row]:
    """Merge selected copies using their recorded ordering, not physical line ids."""
    rows, edges, indegree = {}, {}, {}
    for chain in chains:
        previous, seen = None, set()
        for row in chain:
            key = row.key()
            if key not in rows:
                rows[key], edges[key], indegree[key] = row, set(), 0
            else:
                old = rows[key]
                rows[key] = replace(old, turn_id=old.turn_id or row.turn_id,
                    parent_session_id=old.parent_session_id or row.parent_session_id,
                    provider=row.provider if old.provider == "unknown" else old.provider,
                    model=row.model if old.model == "unknown" else old.model,
                    ordinal=old.ordinal if old.ordinal is not None else row.ordinal,
                    inherited=old.inherited or row.inherited,
                    current=old.current or row.current, last=old.last or row.last)
            if key in seen:
                continue
            seen.add(key)
            if previous is not None and key not in edges[previous]:
                edges[previous].add(key)
                indegree[key] += 1
            previous = key
    positions = {key: index for index, key in enumerate(rows)}
    ready = []
    for key, degree in indegree.items():
        if degree == 0:
            row = rows[key]
            heapq.heappush(ready, (row.timestamp, row.ordinal if row.ordinal is not None else -1, positions[key], key))
    result = []
    while ready:
        _, _, _, key = heapq.heappop(ready)
        result.append(rows[key])
        for successor in edges[key]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                row = rows[successor]
                heapq.heappush(ready, (row.timestamp, row.ordinal if row.ordinal is not None else -1, positions[successor], successor))
    if len(result) != len(rows):
        raise ValueError("Selected Codex copies disagree about event ordering")
    return result


def _turn_id(row: _Row, started_at: str) -> str:
    return f"codex:{row.session_key}:turn:{row.turn_id or started_at}"


def _elapsed(start: str, end: str) -> int | None:
    try:
        delta = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return round(delta.total_seconds() * 1000) if delta.total_seconds() >= 0 else None


def _session_events(rows: list[_Row], inherited_turns: set[str]) -> Iterator[UsageEvent]:
    previous = pending = last_only_key = None
    active_turn = None
    events, turns = [], {}
    for row in rows:
        inherited = row.inherited or row.turn_id is not None and row.turn_id in inherited_turns
        common = dict(source="codex", provider=row.provider, model=row.model,
            billing_mode="subscription_equivalent", source_ref=f"codex:{row.session_key}",
            session_id=row.session_id, parent_session_id=row.parent_session_id, turn_id=row.turn_id)
        if row.kind != "token_count":
            if inherited:
                continue
            if row.kind == "task_started":
                started = row.started_at or row.timestamp
                key = row.turn_id or started
                active_turn = key
                turns[key] = UsageEvent(event_id=_turn_id(row, started), phase="agent_turn", status="running",
                    started_at=started, tokens=_unknown(), timing_scope="agent_turn", **common)
            else:
                key = row.turn_id or active_turn or row.started_at
                previous_turn = turns.get(key)
                started = row.started_at or (previous_turn.started_at if previous_turn else None)
                if started is None:
                    continue
                ended = row.ended_at or row.timestamp
                duration = row.duration_ms if row.duration_ms is not None else _elapsed(started, ended)
                if _elapsed(started, ended) is None:
                    ended, duration = None, None
                turns[key] = UsageEvent(event_id=_turn_id(row, started), phase="agent_turn",
                    status="aborted" if row.kind == "turn_aborted" else "completed",
                    started_at=started, ended_at=ended, duration_ms=duration, tokens=_unknown(),
                    timing_scope="agent_turn", **common)
                if key == active_turn:
                    active_turn = None
            continue
        current, last = row.current, row.last
        if current is not None:
            if current == previous:
                continue
            if previous is None:
                usage = _unknown() if pending is not None else last or current
                status = "partial_history" if pending is not None or not last or _disagrees(current, last) else "observed"
            else:
                delta = _difference(current, previous)
                if delta is not None and pending is not None:
                    delta = _difference(delta, pending)
                    if delta is not None and all(value == 0 for value in delta.to_dict().values() if value is not None) and delta.input_tokens == delta.output_tokens == 0:
                        previous, pending = current, None
                        continue
                if delta is None:
                    usage, status = last or _unknown(), "counter_discontinuity" if last else "counter_reset_unknown"
                elif last is not None and _disagrees(delta, last):
                    usage, status = last, "counter_discontinuity"
                else:
                    usage, status = delta, "observed"
            previous, pending = current, None
        elif last is not None:
            key = (row.timestamp, last)
            if key == last_only_key:
                continue
            last_only_key = key
            usage, status = last, "last_only"
            pending = last if pending is None else _add(pending, last)
        else:
            continue
        if inherited:
            continue
        counts = ",".join("?" if value is None else str(value) for value in (current or last).to_dict().values())
        events.append(UsageEvent(event_id=f"codex:{row.session_key}:token:{row.timestamp}:{counts}",
            phase="agent", status=status, started_at=row.timestamp, tokens=usage,
            model_call=True, timing_scope="model_call", **common))
    for event in events:
        turn = turns.get(event.turn_id) if event.turn_id is not None else None
        yield replace(event, related_event_id=turn.event_id) if turn is not None else event
    yield from turns.values()


def iter_codex_events(paths: Iterable[str | Path], *, warnings: list[str] | None = None,
                      expected_sessions: Mapping[Path, str] | None = None) -> Iterator[UsageEvent]:
    """Read selected metadata only, merging copies by their real session identity.

    Token timestamps never measure model-call latency. Task boundaries produce
    separate rows with unknown tokens. Missing metadata never makes two distinct
    files one session. No source paths, prompt, response or tool bodies are exported.
    Hub-resolved sources must match their expected native session metadata in
    this same read; missing or conflicting identity rejects the whole source.
    """
    notices = warnings if warnings is not None else []
    groups: dict[str, list[list[_Row]]] = {}
    for index, path in enumerate(dict.fromkeys(Path(value).resolve() for value in paths)):
        selected: list[_Row] = []
        expected = (expected_sessions or {}).get(path)
        try:
            selected.extend(_read_rows(path, index, notices, expected))
        except (OSError, ValueError, UnicodeError):
            if warnings is None:
                raise
            if expected is not None:
                selected.clear()
                notice = "Hub 绑定的 Codex 日志身份不符、暂不可读或计数无效；已跳过该来源。"
            else:
                notice = "指定的 Codex 会话暂时不可读或包含无效计数。"
            if notice not in notices:
                notices.append(notice)
        by_session: dict[str, list[_Row]] = {}
        for row in selected:
            by_session.setdefault(row.session_key, []).append(row)
        for key, chain in by_session.items():
            groups.setdefault(key, []).append(chain)
    merged = {}
    for key, chains in groups.items():
        try:
            merged[key] = _merge_rows(chains)
        except ValueError:
            notices.append("同一 Codex 会话的副本存在顺序冲突；已跳过该会话，其他明确来源仍正常读取。")
    for key, rows in merged.items():
        parent = next((row.parent_session_id for row in rows if row.parent_session_id), None)
        if parent is not None and any(row.ordinal is None and row.kind == "token_count" for row in rows):
            if parent not in merged or any(row.turn_id is None and row.kind == "token_count" for row in rows):
                notices.append("Legacy Codex 子代理的部分继承归属缺少已选父来源或 turn_id；未猜测扣除其计数。")
        inherited_turns, seen_parents = set(), {key}
        while parent in merged and parent not in seen_parents:
            seen_parents.add(parent)
            ancestors = merged[parent]
            inherited_turns.update(row.turn_id for row in ancestors if row.turn_id is not None)
            parent = next((row.parent_session_id for row in ancestors if row.parent_session_id), None)
        yield from _session_events(rows, inherited_turns)
