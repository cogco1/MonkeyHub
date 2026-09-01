"""External-system adapters."""

from archflow.adapters.fake_voxel import FakeVoxelAdapter
from archflow.adapters.minecraft_mcp import (
    MinecraftMcpAdapter,
    MinecraftMcpConfig,
    MinecraftMcpFailure,
)
from archflow.adapters.pascal_execution import (
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
