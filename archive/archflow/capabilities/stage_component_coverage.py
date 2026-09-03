"""Compatibility facade for canonical stage component lineage validation.

New code imports :mod:`archive.archflow.validation.component_lineage`.  This module
preserves the legacy path without defining parallel contracts or behavior.
"""

from archive.archflow.validation.component_lineage import (
    ComponentCoverageSummary,
    OperationCoverage,
    OperationDisposition,
    OperationLineageResolution,
    PredecessorOperationDisposition,
    StageComponentCoverageError,
    StageComponentCoverageReceipt,
    StageComponentCoverageStatus,
    StageOperation,
    StageOperationRef,
    compile_stage_component_coverage,
)

__all__ = [
    "ComponentCoverageSummary",
    "OperationCoverage",
    "OperationDisposition",
    "OperationLineageResolution",
    "PredecessorOperationDisposition",
    "StageComponentCoverageError",
    "StageComponentCoverageReceipt",
    "StageComponentCoverageStatus",
    "StageOperation",
    "StageOperationRef",
    "compile_stage_component_coverage",
]
