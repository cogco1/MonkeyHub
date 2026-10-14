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
from typing import Callable, Iterator, Literal, Mapping

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


def call_id(source: str, event_id: str) -> str | None:
    """The native call an agent event names, where its source declares how to read it."""
    declared = AGENT_SOURCES.get(source)
    return declared.call_id(event_id) if declared is not None and declared.call_id is not None else None
