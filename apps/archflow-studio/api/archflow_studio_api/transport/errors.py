"""The one error body the Studio API returns: ``{code, detail}``, plus
``question`` and ``acceptedForms`` when a human has to answer something."""

from __future__ import annotations


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
    ) -> None:
        super().__init__(422, "BLOCKED_NEEDS_HUMAN", detail)
        self.question = question
        self.accepted_forms = accepted_forms

    def body(self) -> dict[str, object]:
        body = super().body()
        body["question"] = self.question
        if self.accepted_forms:
            body["acceptedForms"] = list(self.accepted_forms)
        return body
