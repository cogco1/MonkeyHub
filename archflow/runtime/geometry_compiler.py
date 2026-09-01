"""Compatibility facade for the canonical pure geometry compiler.

New production code imports :mod:`archflow.compilers.geometry`.  These direct
re-exports preserve historical object identity for callers on the runtime path.
"""

from archflow.compilers.geometry import (
    AssetSubstitutionReceipt,
    CompiledGeometryObject,
    CompiledGeometryProgram,
    GeometryCompilationError,
    GeometryCompilationReceipt,
    GeometryCompilationResult,
    GeometryCompileStatus,
    GeometryIssue,
    GeometryIssueCode,
    compile_geometry_program,
    resolve_interface_datums,
)

__all__ = [
    "AssetSubstitutionReceipt",
    "CompiledGeometryObject",
    "CompiledGeometryProgram",
    "GeometryCompilationError",
    "GeometryCompilationReceipt",
    "GeometryCompilationResult",
    "GeometryCompileStatus",
    "GeometryIssue",
    "GeometryIssueCode",
    "compile_geometry_program",
    "resolve_interface_datums",
]
