"""Durable source-evidence contracts.

Acquisition adapters may construct these values, but the retained schema and
its validation errors belong to the evidence layer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)$"
)


class WebEvidenceError(ValueError):
    """A snapshot request or payload is invalid."""


@dataclass(frozen=True, slots=True)
class WebEvidenceSnapshot:
    """One content-addressed retained fetch of one URL."""

    url: str
    retrieved_at: str
    content_sha256: str
    content_bytes: int
    text: str
    text_sha256: str

    SCHEMA = "WebEvidenceSnapshot@1"

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.startswith(
            ("https://", "http://")
        ):
            raise WebEvidenceError("url must be an http(s) URL")
        if not isinstance(self.retrieved_at, str) or not _TIMESTAMP.match(
            self.retrieved_at
        ):
            raise WebEvidenceError("retrieved_at must be an ISO timestamp")
        for digest in (self.content_sha256, self.text_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
                raise WebEvidenceError("digests must be lowercase sha256")
        if hashlib.sha256(
            self.text.encode("utf-8")
        ).hexdigest() != self.text_sha256:
            raise WebEvidenceError("text digest does not match the text")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value) -> "WebEvidenceSnapshot":
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise WebEvidenceError("snapshot schema drifted")
        return cls(
            url=value["url"],
            retrieved_at=value["retrieved_at"],
            content_sha256=value["content_sha256"],
            content_bytes=int(value["content_bytes"]),
            text=value["text"],
            text_sha256=value["text_sha256"],
        )


__all__ = ["WebEvidenceError", "WebEvidenceSnapshot"]
