"""External-system adapters.

The Pascal bridge depends on the runtime/compiler surface and is deliberately
loaded on first use.  Keeping that optional integration lazy prevents a plain
``archflow.adapters.<submodule>`` import from pulling the runtime back into the
control plane during package initialization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from archive.archflow.adapters.fake_voxel import FakeVoxelAdapter
from archive.archflow.adapters.minecraft_mcp import (
    MinecraftMcpAdapter,
    MinecraftMcpConfig,
    MinecraftMcpFailure,
)

if TYPE_CHECKING:
    from archive.archflow.adapters.pascal_execution import (
        PascalAxisMapping,
        PascalBridgeError,
        PascalExecutionReceipt,
        PascalExecutionRequest,
        PascalExecutionStatus,
        PascalMcpAdapter,
        PascalMcpConfig,
        PascalPatchPlan,
        PascalSceneTarget,
        compile_pascal_block_patch,
    )


_PASCAL_EXPORTS = frozenset(
    {
        "PascalAxisMapping",
        "PascalBridgeError",
        "PascalExecutionReceipt",
        "PascalExecutionRequest",
        "PascalExecutionStatus",
        "PascalMcpAdapter",
        "PascalMcpConfig",
        "PascalPatchPlan",
        "PascalSceneTarget",
        "compile_pascal_block_patch",
    }
)


def __getattr__(name: str):
    if name not in _PASCAL_EXPORTS:
        raise AttributeError(name)
    from archive.archflow.adapters import pascal_execution

    value = getattr(pascal_execution, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _PASCAL_EXPORTS)

__all__ = [
    "FakeVoxelAdapter",
    "MinecraftMcpAdapter",
    "MinecraftMcpConfig",
    "MinecraftMcpFailure",
    "PascalAxisMapping",
    "PascalBridgeError",
    "PascalExecutionReceipt",
    "PascalExecutionRequest",
    "PascalExecutionStatus",
    "PascalMcpAdapter",
    "PascalMcpConfig",
    "PascalPatchPlan",
    "PascalSceneTarget",
    "compile_pascal_block_patch",
]
