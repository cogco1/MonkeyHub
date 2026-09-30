"""The Agent's judgments the chat binds to the user's own message and words (#294 Q3).

Retained feedback and project memory, a closed loop's admission and a Continue
are sent in the Agent's name but bound to the last user message it was given,
with the user's exact words where they carry the decision. A provider only
points at those words; the chat reads them itself.
"""

from __future__ import annotations

from .. import projects
from . import skill_plugins, studio_tool, transport

from ..models import HubFailure


# Besides retained feedback, the Agent's judgments Hub binds to the user's own
# message (#294 Q3): a closed loop's admission, and a Continue on the user's words.
_BOUND_WORDS = {("POST", "/api/admissions"), ("PUT", "/api/working-draft")}


def _binds_words(method: str, path: str) -> bool:
    """Whether this request is a judgment Hub binds to the user's own message."""
    return (method == "POST" and _is_feedback(path)) or (method, path) in _BOUND_WORDS


def _user_message(chat_id: str, session: dict, purpose: str) -> dict:
    """The user message the Agent is acting on: the last one it has been given.

    An interjection still waiting for the Agent's next step (#301) is nothing it
    could have acted on yet, so it binds no judgment until it is delivered.
    """
    message = next((row for row in reversed(session.get("messages", [])) if row.get("role") == "user"
                    and row.get("interjection") in (None, "delivered", "restarted")), None)
    if session.get("id") != chat_id or not message or not message.get("id"):
        raise HubFailure(409, "CHAT_FEEDBACK_SOURCE", f"{purpose} needs this conversation's current user message.")
    return message


def _user_words(message: dict, quote: str | None, purpose: str) -> str:
    """The user's own words in that message: all of it, or one exact passage it holds once.

    Hub extracts them itself; a provider only points at them, so it can neither
    invent nor rewrite what the user said.
    """
    wording = message.get("content") or ""
    if not wording.strip():
        raise HubFailure(409, "CHAT_FEEDBACK_SOURCE", f"{purpose} needs the user's own words, and the current user message has none.")
    if quote is not None:
        if not isinstance(quote, str) or not quote.strip():
            raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote must select a nonempty exact passage from this user message.")
        start = wording.find(quote)
        if start < 0 or wording.find(quote, start + 1) >= 0:
            raise HubFailure(422, "CHAT_FEEDBACK_QUOTE", "feedbackQuote must occur exactly once, unchanged, in this user message. Include enough surrounding words to identify it.")
        wording = wording[start:start + len(quote)]
    if len(wording) > 2000:
        raise HubFailure(422, "CHAT_FEEDBACK_TOO_LONG", "Select the exact passage of the user's words with feedbackQuote beside method/path/body (at most 2000 characters); the user need not repeat the message.")
    return wording


def _admission_body(chat_id: str, session: dict, body: dict, quote: str | None = None) -> dict:
    """Close one chat task's loop in the Agent's name, bound to the message it answers.

    The task is always ``hub-chat``. The user's words are bound only where they
    carry the decision: a selected passage, or the message itself for a
    rejection, since the Agent rejects only on what the user said (Q3).
    """
    if {"messageSource", "message_source", "rawLanguage", "raw_language"}.intersection(body):
        raise HubFailure(422, "CHAT_ADMISSION_INVALID", "The chat fills messageSource and rawLanguage from this user turn; never supply them.")
    task = body.get("task", {"kind": "hub-chat"})
    if not isinstance(task, dict) or task.get("kind", "hub-chat") != "hub-chat":
        raise HubFailure(422, "CHAT_ADMISSION_INVALID", "A chat closes its own task as kind hub-chat; the ui and retroactive kinds are a person's act.")
    message = _user_message(chat_id, session, "An admission")
    rejects = any(isinstance(row, dict) and row.get("outcome") == "rejected" for row in body.get("results") or ())
    bound = {**body, "projectId": session["projectId"], "task": {**task, "kind": "hub-chat"},
             "messageSource": {"sessionId": chat_id, "messageId": message["id"]}}
    if quote is not None or rejects:
        bound["rawLanguage"] = _user_words(message, quote, "A rejection" if rejects else "An admission")
    return bound


def _continue_body(chat_id: str, session: dict, body: dict, quote: str | None = None) -> dict:
    """Move the Working Head only on the user's own words, bound to their message (Q3).

    The Runtime still owns the exact-run check, the position's CAS and the
    retained event; this adapter refuses a Continue no user message asks for.
    """
    if set(body) - {"projectId", "runId", "baseRevisionSha256", "branchId"}:
        raise HubFailure(422, "CHAT_CONTINUE_INVALID", "A Continue from chat takes runId, baseRevisionSha256 and optional branchId; the chat binds the user's message and words.")
    if not isinstance(body.get("runId"), str) or not body["runId"]:
        raise HubFailure(422, "CHAT_CONTINUE_INVALID", "Name the result to continue on; returning to the default is the architect's own action.")
    message = _user_message(chat_id, session, "A Continue")
    return {**body, "projectId": session["projectId"], "rawLanguage": _user_words(message, quote, "A Continue"),
            "messageSource": {"sessionId": chat_id, "messageId": message["id"]}}


# The two judgments a chat saves from the user's words: avoid/keep feedback
# (decisions) and project memory (a locator, a source policy or a recipe,
# studio.memory).
_FEEDBACK_PATHS = ("/api/decisions", "/api/memory")


def _is_feedback(path: str) -> bool:
    # Reading which memory some words are about writes nothing and binds nothing.
    return path.startswith(_FEEDBACK_PATHS) and path not in studio_tool._POST_READS


def _recipe_value(hub: str, value) -> dict:
    """A recipe's value with its skill pinned to the configured library's current version.

    The agent names the skill as it sees it; the Hub reads the library's
    current index through the library's own Runtime and fills the exact
    version. The chat project's Studio never reads the library.
    """
    if not isinstance(value, dict):
        raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "A recipe's value is {task, skill, note}.")
    library = skill_plugins.read_library(transport._request_json(hub, "/api/settings/apps").get("libraryDir"), hub)
    return {**value, "skill": skill_plugins.pin(library, value.get("skill"))}


def _feedback_body(hub: str, base: str, chat_id: str, session: dict, path: str, body: dict,
                   quote: str | None = None) -> dict:
    """Bind ordinary feedback to real user words, not a model's claimed authorship.

    The Runtime still owns the decision contract, source validation, CAS and
    authorization. This adapter only narrows what a chat can ask it to write.
    """
    message = _user_message(chat_id, session, "Feedback")
    wording = _user_words(message, quote, "Feedback")
    provenance = {"sessionId": chat_id, "messageId": message["id"]}
    reserved = {"rawLanguage", "raw_language", "messageSource", "message_source", "sourceKind", "source_kind"}
    if path == "/api/decisions":
        if reserved.intersection(body) or body.get("disposition") not in {"avoid", "keep"}:
            raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Chat can save only avoid/keep feedback. Its words, message source and agent attribution are filled from this user turn.")
        return {**body, "projectId": session["projectId"], "rawLanguage": wording,
                "messageSource": provenance, "sourceKind": "agent"}
    if path == "/api/memory":
        if reserved.intersection(body) or body.get("kind") not in {"locator", "source_policy", "recipe"}:
            raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Chat can save a locator, a source policy or a recipe. Its words, message source and agent attribution are filled from this user turn.")
        if body["kind"] == "recipe":
            body = {**body, "value": _recipe_value(hub, body.get("value"))}
        return {**body, "projectId": session["projectId"], "rawLanguage": wording,
                "messageSource": provenance, "sourceKind": "agent"}
    if set(body) - {"projectId", "expectedRevisionRef", "action"} or body.get("action") != "revoke":
        raise HubFailure(422, "CHAT_FEEDBACK_INVALID", "Chat can only revoke what it retained from a user's words (avoid/keep feedback or project memory); the reason and message source come from this user turn.")
    history = transport._request_json(base, path.removesuffix("/revisions"))
    revisions = history.get("revisions", [])
    latest = revisions[-1] if revisions else {}
    memory = path.startswith("/api/memory/")
    # A memory item keeps the user's words in its provenance.
    words = (latest.get("provenance") or {}) if memory else latest
    origin = words.get("messageSource") or {}
    if (history.get("projectId") != session["projectId"] or words.get("sourceKind") != "agent"
            or (not memory and latest.get("disposition") not in {"avoid", "keep"})
            or not origin.get("sessionId") or not origin.get("messageId")):
        raise HubFailure(403, "CHAT_FEEDBACK_UNAVAILABLE", "This record is not ordinary feedback saved from a user chat message.")
    original = transport._request_json(hub, f"/api/chat/sessions/{projects._identifier(origin['sessionId'])}")
    original_message = next((row for row in original.get("messages", [])
                             if row.get("id") == origin["messageId"] and row.get("role") == "user"), None)
    original_text = (original_message or {}).get("content", "")
    original_words = words.get("rawLanguage", "")
    start = original_text.find(original_words)
    if (original.get("id") != origin["sessionId"] or original.get("projectId") != session["projectId"]
            or original.get("projectDir") != session["projectDir"] or not original_message
            or not original_words or start < 0 or original_text.find(original_words, start + 1) >= 0):
        raise HubFailure(403, "CHAT_FEEDBACK_SOURCE", "The original feedback message does not match this project's retained judgment.")
    return {**body, "projectId": session["projectId"], "reason": wording,
            "revisionMessageSource": provenance}
