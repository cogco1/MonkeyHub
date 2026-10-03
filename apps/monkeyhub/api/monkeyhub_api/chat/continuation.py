"""Public native-history projection for an explicit Codex handoff.

Provider IDs identify messages. Source presentation is retained by ChatStore,
not merged using text similarity or replayed as another provider prompt.
"""
from ..models import ChatMessage, HubFailure
from . import providers


def public_history(events: list[dict], session_id: str, created_at: str, environment: dict) -> list[ChatMessage]:
    rows: dict[str, ChatMessage] = {}
    size = 0
    for event in events:
        if event.get("sessionId") != session_id:
            raise HubFailure(409, "CHAT_CONTINUATION_HISTORY", "The provider returned history for a different native session.")
        update = event.get("update", {})
        kind = update.get("sessionUpdate")
        if kind not in {"user_message_chunk", "agent_message_chunk"}:
            continue
        content = update.get("content", {})
        # Native media URLs/paths are not Hub attachment grants. Preserve the original
        # Hub media in priorMessages; importing arbitrary provider paths is not allowed.
        if content.get("type") != "text":
            continue
        identifier = update.get("messageId")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 500:
            raise HubFailure(409, "CHAT_CONTINUATION_HISTORY", "This adapter does not provide stable public message identities.")
        role = "user" if kind == "user_message_chunk" else "assistant"
        key = f"native:{session_id}:{identifier}"
        text = providers._redact(str(content.get("text", "")), environment)
        size += len(text)
        if size > 8_000_000:
            raise HubFailure(409, "CHAT_CONTINUATION_HISTORY", "This session's public history is too large to restore safely.")
        if key in rows:
            if rows[key].role != role:
                raise HubFailure(409, "CHAT_CONTINUATION_HISTORY", "The provider reused a message identity for different roles.")
            rows[key].content += text
        else:
            rows[key] = ChatMessage(id=key, role=role, content=text, createdAt=created_at)
    return list(rows.values())


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
