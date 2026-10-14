"""Agent usage sources: one interface, one registration point.

An agent source turns files a caller explicitly selected into content-free
``UsageEvent`` values whose ``source`` is the agent's key. ``AGENT_SOURCES`` is
the only place one is registered; ``UsageEvent`` accepts exactly its keys beside
the two host writers, ``studio`` and ``hub``, which record their own calls as
they happen. A new agent is one reader module plus one entry here.

Each entry declares:

- ``read(paths, *, warnings)``: the reader. It reads the named files only (a
  reader may also take what the selected file itself names as its own, as Claude
  Code subagent records), never scans for others, never writes, and reports a
  refused path or bad row as a warning rather than stopping the other files.
- ``tokens(native)``: its count conversion into Monitor's inclusive TokenUsage.
- ``provider``: the connection whose billing plan prices its calls, or None when
  each session or event records its own.
- ``token_authority``: who counts the tokens of a native session that the Hub
  also observed. ``agent``: this source's records replace the Hub's usage rows of
  that session (Codex: the Hub records only activity intervals for it). ``host``:
  a call the Hub counted itself is not counted again from this source (Claude
  Code: the Hub reads each message's closing usage from the stream, on the
  connection it ran). Studio's own provider calls are never the same calls as an
  agent's and are always counted.
- ``call_id(event_id)``: the native model call an event names, so a ``host``
  source is matched call by call against the Hub's own usage rows. A source
  whose events name no native call (``None``) defers for every session the Hub
  counted, and for those sessions' subagents.
- ``existing_ids``: what an event id another source already holds means.
  ``merge``: the same record read again (a native re-read). ``reject``: a copy
  that may not replace or join an existing record.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Literal, Mapping

from . import claude_code, codex, usage_file
from .usage import TokenUsage, UsageEvent


@dataclass(frozen=True)
class AgentSource:
    agent: str
    read: Callable[..., Iterator[UsageEvent]]
    tokens: Callable[[Mapping[str, object]], TokenUsage | None]
    provider: str | None
    token_authority: Literal["agent", "host"]
    existing_ids: Literal["merge", "reject"]
    call_id: Callable[[str], str | None] | None = None


AGENT_SOURCES: Mapping[str, AgentSource] = MappingProxyType({
    source.agent: source for source in (
        AgentSource(codex.AGENT, codex.iter_codex_events, codex.tokens,
                    provider=None, token_authority="agent", existing_ids="merge"),
        AgentSource(claude_code.AGENT, claude_code.iter_claude_code_events, claude_code.tokens,
                    provider=claude_code.PROVIDER, token_authority="host", existing_ids="merge",
                    call_id=claude_code.call_id),
        AgentSource(usage_file.AGENT, usage_file.iter_usage_file_events, usage_file.tokens,
                    provider=None, token_authority="host", existing_ids="reject"),
    )
})


def token_authority(source: str) -> str:
    """``host`` for Hub and Studio rows; an agent's declared authority otherwise."""
    declared = AGENT_SOURCES.get(source)
    return declared.token_authority if declared is not None else "host"


# The Hub writes one usage row per native message it counted itself, with the
# event id ``hub:turn:<turn>:usage:<message.id>`` (MonkeyHub's chat turn observer).
_HUB_CALL = ":usage:"


def defer_to_hub(rows: Iterable[Mapping[str, object]]) -> tuple[list[Mapping[str, object]], dict[str, int]]:
    """One row per model call: a ``host`` source's row for a call the Hub counted itself is left out.

    A source that names its native call (``call_id``) is matched call by call, so a
    fork's copy and a subagent's message are matched too, and a call the Hub never
    saw stays. A source that names none defers for every session the Hub counted
    and those sessions' subagents. Hub, Studio and ``agent`` sources' rows always
    stay. Returns the kept rows, in order, and how many were left out per source.
    """
    rows = list(rows)
    calls, sessions = set(), set()
    for row in rows:
        if row["source"] == "hub" and row.get("model_call") is True:
            _, marker, call = str(row["event_id"]).rpartition(_HUB_CALL)
            if marker and call:
                calls.add(call)
            if row.get("session_id"):
                sessions.add(row["session_id"])
    kept, left_out = [], {}
    for row in rows:
        declared = AGENT_SOURCES.get(row["source"])
        if declared is not None and declared.token_authority == "host" and row.get("model_call") is True:
            call = declared.call_id(row["event_id"]) if declared.call_id is not None else None
            if call is not None:
                counted = call in calls
            else:
                counted = bool(sessions & {row.get("session_id"), row.get("parent_session_id")})
            if counted:
                left_out[row["source"]] = left_out.get(row["source"], 0) + 1
                continue
        kept.append(row)
    return kept, left_out
