"""Screen capture and the demo overlay, over the STA presentation host.

The PNG travels as bytes, not as a path: this provider writes nothing, and the
caller decides whether a capture is kept and where, through
:class:`~monkeycontrol.store.ActionTraceStore`. The overlay is click-through and
always transient by default, so nothing this package draws can swallow a click
or outlive the action it explains.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

from ..contract import ContractError
from ..host import HostError, HostProcess
from ..store import ActionTraceStore

#: What a badge may say about an action, in the colours the host knows.
BADGE_KINDS = ("info", "ok", "fail")
HIGHLIGHT_MS = 600
BADGE_MS = 900
HIGHLIGHT_COLOR = "#FF7A00"


class PresentationProvider:
    """One overlay and one screen grabber, spoken to the presentation host."""

    def __init__(self, host: HostProcess) -> None:
        self._host = host

    @property
    def host(self) -> HostProcess:
        return self._host

    def screenshot(
        self, *, bounds: tuple[int, int, int, int] | None = None
    ) -> dict:
        """Capture the screen, or one region, and return the PNG bytes.

        The host's digest is checked against the bytes that arrived, so a
        truncated capture is a refusal rather than a corrupt frame in a trace.
        """

        result = self._host.request(
            "screenshot", bounds=list(bounds) if bounds is not None else None
        )
        png = base64.b64decode(str(result.get("png_base64") or ""))
        # The store's own digest, so the name a kept capture ends up under is
        # the one checked here rather than a second opinion about the bytes.
        digest = ActionTraceStore.sha256(png)
        if digest != result.get("sha256"):
            raise HostError(
                "HOST_ERROR",
                "the screenshot that arrived is not the one the host hashed",
            )
        return {
            "png": png,
            "sha256": digest,
            "bounds": [int(item) for item in result.get("bounds") or ()],
            "bytes": len(png),
        }

    def highlight(
        self,
        bounds: Sequence[int],
        *,
        label: str,
        kind: str = "click",
        ms: int = HIGHLIGHT_MS,
        color: str = HIGHLIGHT_COLOR,
    ) -> None:
        """Outline a rectangle and name it, for ``ms`` before the action runs.

        With ``ms`` at or below zero the overlay stays up until :meth:`clear`,
        which is how a recording keeps a target marked across several frames.
        """

        self._host.request(
            "highlight",
            bounds=[int(item) for item in bounds],
            label=str(label),
            kind=str(kind),
            ms=int(ms),
            color=str(color),
        )

    def badge(self, text: str, *, ms: int = BADGE_MS, kind: str = "info") -> None:
        """Say one short thing over the screen, in the colour of the outcome."""

        if kind not in BADGE_KINDS:
            raise ContractError(
                f"a badge kind must be one of {', '.join(BADGE_KINDS)}, not {kind!r}"
            )
        self._host.request("badge", text=str(text), ms=int(ms), kind=kind)

    def clear(self) -> None:
        """Take the overlay down, whatever it was showing."""

        self._host.request("clear")
