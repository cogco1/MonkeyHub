"""The one error body the Studio API returns: ``{code, detail}``, plus
``question`` and ``acceptedForms`` when a human has to answer something."""

from __future__ import annotations

from typing import Mapping


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


class BlockedNeedsHuman(StudioError):
    """The work cannot proceed until a human answers a specific question."""

    def __init__(
        self,
        detail: str,
        question: str,
        accepted_forms: tuple[str, ...] = (),
        pending: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(422, "BLOCKED_NEEDS_HUMAN", detail)
        self.question = question
        self.accepted_forms = accepted_forms
        # The structured state a clarification keeps, when the intent
        # resolver opened one: the client resumes it by its token.
        self.pending = pending

    def body(self) -> dict[str, object]:
        body = super().body()
        body["question"] = self.question
        if self.accepted_forms:
            body["acceptedForms"] = list(self.accepted_forms)
        if self.pending is not None:
            body["pending"] = dict(self.pending)
        return body
