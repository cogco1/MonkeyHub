"""The fallback that admits it is only a rectangle.

When no UI Automation property names the element, the caller may still act, but
only by saying exactly where and by declaring the ``visual-fallback`` backend.
Nothing here guesses: the resolution is the caller's own bounds, and the
receipt says so, so a coordinate can never be read back as a semantic match.
"""

from __future__ import annotations

from ..contract import VISUAL_FALLBACK, ContractError, TargetSpec
from ..trace import ResolvedTarget, WindowInfo
from .uia import UiaProvider

#: What a rectangle can honestly claim to be.
UNKNOWN_CONTROL = "Unknown"
ANONYMOUS = "explicit bounds"


class VisualFallbackProvider:
    """Explicit bounds as a resolution, over the same execution host."""

    name = VISUAL_FALLBACK

    def __init__(self, uia: UiaProvider) -> None:
        self._uia = uia

    @property
    def uia(self) -> UiaProvider:
        """The semantic provider whose host also carries this one's input."""

        return self._uia

    def windows(self, application: str, title: str | None = None) -> list[WindowInfo]:
        """Windows are a process fact, so the execution host answers them."""

        return self._uia.windows(application, title)

    def resolve(self, window: WindowInfo, target: TargetSpec) -> ResolvedTarget:
        """The caller's rectangle, refused unless it was declared as one."""

        if target.bounds is None:
            raise ContractError(
                "the visual fallback resolves only an explicit target.bounds"
            )
        if target.backend != VISUAL_FALLBACK:
            raise ContractError(
                f"explicit bounds are honoured only when target.backend is "
                f"{VISUAL_FALLBACK!r}: a coordinate is an execution projection"
            )
        return ResolvedTarget(
            control_type=UNKNOWN_CONTROL,
            name=target.name or ANONYMOUS,
            automation_id=None,
            class_name=None,
            bounds=tuple(target.bounds),
            runtime_id=None,
            backend=self.name,
        )

    def read(self, target: ResolvedTarget) -> dict:
        """Only what a rectangle can say: where it is, and nothing about state."""

        return {
            "value": None,
            "enabled": True,
            "toggled": None,
            "offscreen": False,
            "bounds": list(target.bounds),
        }
