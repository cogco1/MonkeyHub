"""Read explicitly selected Codex JSONL files into content-free usage events.

Supported rows are session_meta, turn_context and event_msg/token_count. The
token_count info contains total_token_usage and/or last_token_usage. Repeated
cumulative snapshots are not new calls. A discontinuity uses the reported last
call, if available, and marks its status: it does not invent an interval total.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from .usage import TokenUsage, UsageEvent


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


def iter_codex_events(paths: Iterable[str | Path]) -> Iterator[UsageEvent]:
    """Read selected files only. Never discover sessions or include their paths.

    All Codex values are subscription API-equivalent usage, not a subscription
    invoice. started_at is the token event's reported timestamp; duration is
    unknown. A partial trailing JSON row is ignored while a file is being
    appended; malformed completed rows fail with a content-free line number.
    """
    seen_files: set[Path] = set()
    seen_events: set[str] = set()
    for file_index, path_value in enumerate(paths):
        path = Path(path_value).resolve()
        if path in seen_files:
            continue
        seen_files.add(path)
        session_id = f"file-{file_index}"
        provider = "unknown"
        model = "unknown"
        previous: TokenUsage | None = None
        pending: TokenUsage | None = None
        last_only_key: tuple[str, TokenUsage] | None = None
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
                row_type = row.get("type")
                if row_type == "session_meta":
                    if isinstance(payload.get("id"), str) and payload["id"]:
                        session_id = payload["id"]
                    if isinstance(payload.get("model_provider"), str) and payload["model_provider"]:
                        provider = payload["model_provider"]
                    continue
                if row_type == "turn_context":
                    model = payload.get("model") or "unknown"
                    provider = payload.get("model_provider") or provider
                    continue
                if row_type != "event_msg" or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                current = _tokens(info.get("total_token_usage"))
                last = _tokens(info.get("last_token_usage"))
                timestamp = row.get("timestamp")
                if current is not None:
                    if current == previous:
                        # Codex also repeats totals with last_token_usage reset
                        # to zero. The unchanged cumulative value is no call.
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
                            usage = last or _unknown()
                            status = "counter_discontinuity" if last else "counter_reset_unknown"
                        elif last is not None and _disagrees(delta, last):
                            usage, status = last, "counter_discontinuity"
                        else:
                            usage, status = delta, "observed"
                    previous, pending = current, None
                elif last is not None:
                    key = (timestamp, last)
                    if key == last_only_key:
                        continue
                    last_only_key = key
                    usage, status = last, "last_only"
                    # The next cumulative interval already includes these
                    # reported calls, so subtract them before emitting a delta.
                    pending = last if pending is None else _add(pending, last)
                else:
                    continue
                event_id = f"codex:{session_id}:{line_number}"
                if event_id in seen_events:
                    continue
                event = UsageEvent(
                    event_id=event_id, source="codex", provider=provider,
                    model=model, phase="agent", status=status,
                    started_at=timestamp, tokens=usage,
                    billing_mode="subscription_equivalent",
                )
                seen_events.add(event_id)
                yield event
