"""Read-only web evidence snapshots.

A snapshot retains one fetched page as content-addressed evidence: the
exact URL, retrieval time, raw-byte digest, and a bounded plain-text
extraction with its own digest. Snapshots carry no design, validation, or
adoption authority, and their text must never be fed raw into a provider
prompt: only facts quoted from a snapshot and promoted by a typed adoption
record may reach generation (see ``archflow.capabilities.precedent``).
"""

from __future__ import annotations

import hashlib
import html.parser
import re
import urllib.request
from dataclasses import dataclass

_MAX_BYTES = 4_000_000
_MAX_TEXT = 400_000
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)$"
)


class WebEvidenceError(ValueError):
    """A snapshot request or payload is invalid."""


class _TextExtractor(html.parser.HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def extract_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    text = re.sub(r"[ \t]+", " ", " ".join(parser.parts))
    return re.sub(r"\s*\n\s*", "\n", text).strip()[:_MAX_TEXT]


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


def fetch_web_evidence(
    url: str,
    *,
    retrieved_at: str,
    timeout_seconds: float = 30.0,
    user_agent: str = "ArchFlow-V4-research/0.1 (evidence retrieval)",
) -> WebEvidenceSnapshot:
    """Fetch one page and retain it as a bounded snapshot."""

    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        final_url = response.geturl()
        raw = response.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise WebEvidenceError("page exceeds the snapshot byte bound")
    text = extract_text(raw.decode("utf-8", "replace"))
    return WebEvidenceSnapshot(
        url=final_url,
        retrieved_at=retrieved_at,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        content_bytes=len(raw),
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
