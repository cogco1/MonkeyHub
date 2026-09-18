"""MonkeyControl: inspectable, model-agnostic desktop automation.

This package resolves a semantic Windows UI target, shows it, acts, verifies
the caller's declared post-condition and records a ComputerActionReceipt@1
under a directory the caller supplies. It is never a writer of canonical
design state, and it owns neither the conversation nor the permission to run:
MonkeyHub composes it, passes it a policy and decides when it may act.
"""

from __future__ import annotations

from .contract import (
    Action,
    ContractError,
    TargetSpec,
    Verification,
    validate_action,
)
from .host import HostError, HostProcess
from .providers import (
    FocusError,
    PresentationProvider,
    Provider,
    ResolutionError,
    UiaProvider,
    VisualFallbackProvider,
)
from .store import ActionTraceStore
from .trace import ResolvedTarget, WindowInfo, build_receipt

__version__ = "0.1.0"

__all__ = [
    "Action",
    "ActionTraceStore",
    "ContractError",
    "FocusError",
    "HostError",
    "HostProcess",
    "PresentationProvider",
    "Provider",
    "ResolutionError",
    "ResolvedTarget",
    "TargetSpec",
    "UiaProvider",
    "Verification",
    "VisualFallbackProvider",
    "WindowInfo",
    "build_receipt",
    "validate_action",
    "__version__",
]
