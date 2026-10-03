"""Public native-history projection for an explicit Codex handoff.

Provider IDs identify messages. Source presentation is retained by ChatStore,
not merged using text similarity or replayed as another provider prompt.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from archflow.contracts.canonical import canonical_digest

from ..models import ChatMessage, HubFailure
from . import providers


_MAX_HISTORY_CHARACTERS = 8_000_000
_MAX_HISTORY_CHUNKS = 50_000
_PublicRole = Literal["user", "assistant"]


def _well_formed_id(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Native history identities cannot contain whitespace or control characters.")
    return value


class _NativeHistoryBoundaryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str = Field(min_length=1, max_length=500)
    role: _PublicRole
    contentDigest: str = Field(pattern=r"^[0-9a-f]{64}$")

    _identity = field_validator("id")(_well_formed_id)


class NativeHistoryBoundary(BaseModel):
    """The exact public-text prefix observed while Hub still owns the writer."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    sessionId: str = Field(min_length=1, max_length=200)
    generation: int = Field(ge=1)
    items: list[_NativeHistoryBoundaryItem] = Field(max_length=_MAX_HISTORY_CHUNKS)

    _identity = field_validator("sessionId")(_well_formed_id)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("A native history boundary cannot repeat a message identity.")
        return self


def _history_failure(detail: str) -> HubFailure:
    return HubFailure(409, "CHAT_CONTINUATION_HISTORY", detail)


def _raw_history(events: list[dict], session_id: str) -> list[tuple[str, _PublicRole, str]]:
    if not isinstance(session_id, str) or not session_id or len(session_id) > 200:
        raise _history_failure("The native session identity is invalid.")
    try:
        _well_formed_id(session_id)
    except ValueError as exc:
        raise _history_failure("The native session identity is invalid.") from exc
    rows: list[tuple[str, _PublicRole, list[str]]] = []
    seen: set[str] = set()
    size = 0
    chunks = 0
    for event in events:
        if not isinstance(event, dict) or event.get("sessionId") != session_id:
            raise _history_failure("The provider returned history for a different native session.")
        update = event.get("update", {})
        if not isinstance(update, dict):
            raise _history_failure("The provider returned a malformed history update.")
        kind = update.get("sessionUpdate")
        if kind is not None and not isinstance(kind, str):
            raise _history_failure("The provider returned a malformed history update kind.")
        if kind not in {"user_message_chunk", "agent_message_chunk"}:
            continue
        content = update.get("content", {})
        if not isinstance(content, dict):
            raise _history_failure("The provider returned malformed public content.")
        # Native media URLs/paths are not Hub attachment grants. Preserve the original
        # Hub media in the active timeline; importing provider paths is not allowed.
        if content.get("type") != "text":
            continue
        identifier = update.get("messageId")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 500:
            raise _history_failure("This adapter does not provide stable public message identities.")
        try:
            _well_formed_id(identifier)
        except ValueError as exc:
            raise _history_failure("The provider returned an invalid public message identity.") from exc
        text = content.get("text")
        if not isinstance(text, str):
            raise _history_failure("The provider returned non-text public content.")
        role: _PublicRole = "user" if kind == "user_message_chunk" else "assistant"
        chunks += 1
        size += len(text)
        if size > _MAX_HISTORY_CHARACTERS or chunks > _MAX_HISTORY_CHUNKS:
            raise _history_failure("This session's public history is too large to restore safely.")
        if rows and rows[-1][0] == identifier:
            if rows[-1][1] != role:
                raise _history_failure("The provider reused a message identity for different roles.")
            rows[-1][2].append(text)
        else:
            if identifier in seen:
                raise _history_failure("The provider repeated a noncontiguous public message identity.")
            seen.add(identifier)
            rows.append((identifier, role, [text]))
    return [(identifier, role, "".join(parts)) for identifier, role, parts in rows]


def release_boundary(events: list[dict], session_id: str, generation: int) -> NativeHistoryBoundary:
    """Keep identities and raw-content digests, never the Hub's provider wrappers."""
    return NativeHistoryBoundary(sessionId=session_id, generation=generation, items=[
        _NativeHistoryBoundaryItem(id=identifier, role=role, contentDigest=canonical_digest(text))
        for identifier, role, text in _raw_history(events, session_id)
    ])


def public_history(events: list[dict], session_id: str, created_at: str, environment: dict,
                   boundary: NativeHistoryBoundary | None = None) -> list[ChatMessage]:
    rows = _raw_history(events, session_id)
    offset = 0
    if boundary is not None:
        if boundary.sessionId != session_id or len(rows) < len(boundary.items):
            raise _history_failure("The provider did not preserve the released native history prefix.")
        for item, (identifier, role, text) in zip(boundary.items, rows):
            if (item.id, item.role, item.contentDigest) != (identifier, role, canonical_digest(text)):
                raise _history_failure("The provider changed or reordered the released native history prefix.")
        offset = len(boundary.items)
    return [ChatMessage(id=f"native:{session_id}:{identifier}", role=role,
                        content=providers._redact(text, environment), createdAt=created_at)
            for identifier, role, text in rows[offset:]]


def check_native_version(command: tuple[str, ...]) -> None:
    """The documented compatibility floor includes native local writer admission."""
    import re
    import subprocess

    result = subprocess.run([*command, "--version"], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    version = re.fullmatch(r"codex-cli (\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    if result.returncode or version is None or tuple(map(int, version.groups())) < (0, 153, 4):
        raise HubFailure(409, "CHAT_CONTINUATION_UNSUPPORTED", "Native handoff requires Codex CLI 0.153.4 or newer with local writer admission.")
