"""What a provider is: windows, one resolution, and what an element says.

A provider turns a declared :class:`~monkeycontrol.contract.TargetSpec` into a
:class:`~monkeycontrol.trace.ResolvedTarget` and says which backend did it. It
never decides whether an action is allowed, never writes a file and never keeps
project state; the runtime composes providers and owns both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..contract import ContractError, TargetSpec
from ..trace import ResolvedTarget, WindowInfo


class FocusError(RuntimeError):
    """Input would not have gone where the caller meant it to.

    A window that refuses to come forward, or an element that refuses focus,
    is not a detail to log: the very next keystroke would land in somebody
    else's application, so it is raised rather than returned.
    """

    code = "FOCUS_LOST"


class ResolutionError(LookupError):
    """A target named no element, or more than one; ``code`` says which.

    ``candidates`` keeps what the provider did see, so a refusal can tell the
    caller what was on screen instead of only that nothing matched.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        candidates: Sequence[Mapping] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.candidates: list[dict] = [dict(item) for item in (candidates or ())]


@runtime_checkable
class Provider(Protocol):
    """The part of a backend the runtime depends on."""

    name: str

    def windows(self, application: str, title: str | None) -> list[WindowInfo]:
        """Every top-level window of ``application`` whose title matches."""

    def resolve(self, window: WindowInfo, target: TargetSpec) -> ResolvedTarget:
        """The one element ``target`` names inside ``window``."""

    def read(self, target: ResolvedTarget) -> dict:
        """``value``, ``enabled``, ``toggled``, ``offscreen`` and ``bounds``."""


def as_list(value: object) -> list:
    """One host field that should be a list, however PowerShell rendered it.

    PowerShell unrolls a collection on its way out of a function, so a field
    that holds several values arrives as a list, one value arrives as that bare
    value, and none arrives as null or as an empty object. Reading it back here
    keeps a single-element list from being mistaken for a string, which is how
    ``"Value" in patterns`` would quietly start matching ``"RangeValue"``.
    """

    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Mapping):
        # An unrolled empty array is rendered as ``{}`` and an unrolled single
        # object as that object, so emptiness is what tells the two apart.
        return [] if not value else [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def bounds_of(value: object, what: str) -> tuple[int, int, int, int]:
    """Read ``[left, top, right, bottom]`` from a host payload."""

    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise ContractError(f"{what} must be [left, top, right, bottom] screen pixels")
    return tuple(int(item) for item in value)  # type: ignore[return-value]


def window_of(payload: Mapping) -> WindowInfo:
    """Read one window dict as a host reports it."""

    return WindowInfo(
        handle=int(payload["handle"]),
        title=str(payload.get("title") or ""),
        pid=int(payload.get("pid") or 0),
        process=str(payload.get("process") or ""),
        bounds=bounds_of(payload.get("bounds"), "a window's bounds"),
    )
