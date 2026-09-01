"""Provider-independent retrieval request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from archflow.project.refs import ProjectVersionRef, require_identifier


class RetrievalStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    EXIT_ERROR = "exit_error"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    MISSING_PROVIDER = "missing_provider"


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    query_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    query_text: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.query_id, "query_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be a ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("query and base belong to different projects")
        self.base.require_digest()
        _text(self.query_text, "query_text", maximum=8_000)
        _refs(self.evidence_refs, "evidence_refs")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "CliRetrievalQuery@1",
            "query_id": self.query_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "query_text": self.query_text,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    evidence_id: str
    project_id: str
    query_id: str
    title: str
    source_uri: str
    excerpt: str
    output_sha256: str
    epistemic_status: str = "hypothesis"

    def __post_init__(self) -> None:
        require_identifier(self.evidence_id, "evidence_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.query_id, "query_id")
        for value, field in (
            (self.title, "title"),
            (self.source_uri, "source_uri"),
            (self.excerpt, "excerpt"),
            (self.output_sha256, "output_sha256"),
        ):
            _text(value, field, maximum=20_000)
        if self.epistemic_status != "hypothesis":
            raise ValueError("retrieved evidence must remain a hypothesis")


@dataclass(frozen=True, slots=True)
class RetrievalReceipt:
    schema: str
    receipt_id: str
    status: RetrievalStatus
    query: RetrievalQuery
    provider_id: str
    provider_version: str
    provider_fingerprint: str
    command: tuple[str, ...]
    output_sha256: str | None
    results: tuple[RetrievedEvidence, ...] = ()
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.schema != "RetrievalReceipt@1":
            raise ValueError("unsupported retrieval receipt schema")
        require_identifier(self.receipt_id, "receipt_id")
        if not isinstance(self.status, RetrievalStatus):
            raise TypeError("status must be RetrievalStatus")
        if not isinstance(self.query, RetrievalQuery):
            raise TypeError("query must be RetrievalQuery")
        _text(self.provider_id, "provider_id")
        _text(self.provider_version, "provider_version")
        _text(self.provider_fingerprint, "provider_fingerprint")
        if not isinstance(self.command, tuple):
            raise TypeError("command must be a tuple")
        if self.output_sha256 is not None:
            _text(self.output_sha256, "output_sha256")
        if not isinstance(self.results, tuple):
            raise TypeError("results must be a tuple")
        if self.status is RetrievalStatus.SUCCESS:
            if self.output_sha256 is None or self.error_code is not None:
                raise ValueError("successful receipt requires output and no error")
        elif self.results or self.error_code is None:
            raise ValueError("failed receipt requires an error and no results")


def _text(value: object, field: str, *, maximum: int = 1_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > 64:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        _text(item, f"{field} item")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


__all__ = [
    "RetrievalQuery",
    "RetrievalReceipt",
    "RetrievalStatus",
    "RetrievedEvidence",
]
