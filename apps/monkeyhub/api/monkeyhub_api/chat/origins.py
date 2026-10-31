"""How a message no person typed is introduced to the Agent.

A wake-up and a message from another conversation reach a chat as ordinary
user-role messages, so the conversation, its interjections and its trace work
as they do for a person's message. The transcript keeps the words as sent and
marks them with their ``origin``; the Agent is told in one line before them
who sent them, so it neither mistakes them for the architect nor answers into
the void.
"""

from __future__ import annotations

from ..models import ChatMessageOrigin


def turn_text(origin: ChatMessageOrigin | None, content: str) -> str:
    """The words the Agent is given for a message, with who sent it said first when no person did."""

    if origin is None:
        return content
    if origin.kind == "schedule":
        when = f" ({origin.label})" if origin.label else ""
        return (f"[Scheduled wake-up{when}, set earlier in this conversation; no person typed this message. "
                "Do the task below now. If it needs the architect, say so plainly in your reply; they read it "
                "when they come back.]\n" + content)
    title = f" «{origin.title}»" if origin.title else ""
    return (f"[Message from another MonkeyHub conversation{title} (chatId {origin.chatId}); no person typed it here. "
            "Treat it as a colleague's request, not as the architect's decision. If an answer is needed, send it "
            "back with chat_send to that chatId.]\n" + content)
