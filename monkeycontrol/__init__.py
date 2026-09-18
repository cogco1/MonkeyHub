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
from .overlay import PROJECTIONS, render_overlays
from .providers import (
    FocusError,
    PresentationProvider,
    Provider,
    ResolutionError,
    UiaProvider,
    VisualFallbackProvider,
)
from .record import Recorder, encode_video
from .runtime import ComputerUseRuntime, RuntimePolicy, RuntimeRefusal
from .store import ActionTraceStore
from .trace import ResolvedTarget, WindowInfo, build_receipt
from .verify import verify_action

__version__ = "0.1.0"

__all__ = [
    "Action",
    "ActionTraceStore",
    "ComputerUseRuntime",
    "ContractError",
    "FocusError",
    "HostError",
    "HostProcess",
    "PROJECTIONS",
    "PresentationProvider",
    "Provider",
    "Recorder",
    "ResolutionError",
    "ResolvedTarget",
    "RuntimePolicy",
    "RuntimeRefusal",
    "TargetSpec",
    "UiaProvider",
    "Verification",
    "VisualFallbackProvider",
    "WindowInfo",
    "build_receipt",
    "encode_video",
    "render_overlays",
    "validate_action",
    "verify_action",
    "__version__",
]
