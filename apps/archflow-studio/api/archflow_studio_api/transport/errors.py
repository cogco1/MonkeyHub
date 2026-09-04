"""The one error body the Studio API returns: ``{code, detail}``, plus
``question`` and ``acceptedForms`` when a human has to answer something, and
``outcome``, ``pendingIntent`` and ``authoredControlDraft`` when the answer
belongs to a clarification chain.

An intent ends in one of four outcomes (``application/clarification.py``). One
of them is a proposal and answers 201; the other three are refusals, and each
carries the pending intent it belongs to so the next request can continue it by
token instead of by re-sending a conversation. The three codes are distinct on
purpose: "you still have to tell me something", "this exists in the model and
has no control in the record", and "no action here can express this" are three
different problems for the person reading them.
"""

from __future__ import annotations

from typing import Mapping

# The three refusing outcomes, as wire codes. ``BLOCKED_NEEDS_HUMAN`` keeps the
# name it has had since round 1: it is still the one refusal a person answers.
BLOCKED_NEEDS_HUMAN = "BLOCKED_NEEDS_HUMAN"
MISSING_EDITABLE_CONTROL = "MISSING_EDITABLE_CONTROL"
UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
STALE_CLARIFICATION = "STALE_CLARIFICATION"


def error_sentence(exc: BaseException) -> str:
    """One exception's own sentence, without this server's filesystem in it.

    ``str(exc)`` on an ``OSError`` renders as ``[Errno 13] Permission denied:
    '<absolute path>'``. That path is the service's own layout, and a client
    asking about a project is not entitled to learn where the process keeps
    it; the exception class and the system's own message say what went wrong
    without it. Everything else already speaks in the project's own terms and
    travels verbatim.

    This is the one renderer: a second one would let one refusal publish what
    another was careful to withhold.
    """

    if isinstance(exc, OSError):
        return (
            f"{type(exc).__name__}: {exc.strerror}"
            if exc.strerror
            else type(exc).__name__
        )
    return str(exc)


class StudioError(Exception):
    """A failure the API states on the wire with a stable code."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail

    def body(self) -> dict[str, object]:
        return {"code": self.code, "detail": self.detail}


class ClarificationRefused(StudioError):
    """A refusal that says which exchange it belongs to and how it may continue.

    ``outcome`` is one of the four the resolver can reach; ``pending`` is the
    pending intent as the wire carries it, and its ``continuationToken`` is
    ``null`` on a terminal answer — which is how a client knows not to show an
    input box again. ``draft`` is present only where the answer is that a
    control has to be authored.
    """

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        outcome: str,
        pending: Mapping[str, object] | None = None,
        draft: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(status, code, detail)
        self.outcome = outcome
        self.pending = pending
        self.draft = draft

    def body(self) -> dict[str, object]:
        body = super().body()
        body["outcome"] = self.outcome
        if self.pending is not None:
            body["pendingIntent"] = dict(self.pending)
        if self.draft is not None:
            body["authoredControlDraft"] = dict(self.draft)
        return body


class BlockedNeedsHuman(ClarificationRefused):
    """The work cannot proceed until a human answers a specific question.

    The question is concrete and names things the architect can see. It never
    asks for an ``elementId``, a field name or a sentence in the grammar's own
    syntax: those are the studio's job, and a studio that asks for them is
    asking the architect to do its resolution for it.
    """

    def __init__(
        self,
        detail: str,
        question: str,
        accepted_forms: tuple[str, ...] = (),
        *,
        pending: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            422,
            BLOCKED_NEEDS_HUMAN,
            detail,
            outcome="NEEDS_CLARIFICATION",
            pending=pending,
        )
        self.question = question
        self.accepted_forms = accepted_forms

    def body(self) -> dict[str, object]:
        body = super().body()
        body["question"] = self.question
        if self.accepted_forms:
            body["acceptedForms"] = list(self.accepted_forms)
        return body


class MissingEditableControl(ClarificationRefused):
    """It is in the model and the record declares no control for it.

    Terminal, and deliberately not a question: the missing thing is a binding
    the system does not have, not an answer the architect is withholding. The
    draft says what would have to be authored and where the suggestion was read
    from; nothing is written by returning it.
    """

    def __init__(
        self,
        detail: str,
        *,
        pending: Mapping[str, object],
        draft: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            422,
            MISSING_EDITABLE_CONTROL,
            detail,
            outcome=MISSING_EDITABLE_CONTROL,
            pending=pending,
            draft=draft,
        )


class UnsupportedRequest(ClarificationRefused):
    """No action this server has can express the request, so it stops asking."""

    def __init__(self, detail: str, *, pending: Mapping[str, object]) -> None:
        super().__init__(
            422,
            UNSUPPORTED_REQUEST,
            detail,
            outcome="UNSUPPORTED",
            pending=pending,
        )
