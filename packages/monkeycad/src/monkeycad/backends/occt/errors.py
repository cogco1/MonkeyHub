"""The errors the in-process OCCT backend raises."""

from __future__ import annotations


class OcctBackendError(ValueError):
    """The OCCT backend could not do what was asked."""


class OcctUnavailableError(OcctBackendError):
    """The optional ``cadquery-ocp`` binding is not installed."""


class OcctCapabilityError(OcctBackendError):
    """The program names an operation this backend does not realize.

    Raised before any file is written; the caller reports it as a typed
    capability failure rather than silently substituting another executor.
    """

    def __init__(self, op_id: str, kind: str, reason: str) -> None:
        super().__init__(f"{op_id} ({kind}): {reason}")
        self.op_id = op_id
        self.kind = kind
        self.reason = reason


class OcctBuildError(OcctBackendError):
    """OCCT could not build, write or read a shape it was asked for."""


__all__ = [
    "OcctBackendError",
    "OcctBuildError",
    "OcctCapabilityError",
    "OcctUnavailableError",
]
