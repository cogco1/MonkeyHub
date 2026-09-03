"""HTML extraction and urllib acquisition for web evidence snapshots."""

from __future__ import annotations

import hashlib
import html.parser
import re
import urllib.request

from archive.archflow.evidence.sources import WebEvidenceError, WebEvidenceSnapshot

_MAX_BYTES = 4_000_000
_MAX_TEXT = 400_000
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


__all__ = [
    "WebEvidenceError",
    "WebEvidenceSnapshot",
    "extract_text",
    "fetch_web_evidence",
]
