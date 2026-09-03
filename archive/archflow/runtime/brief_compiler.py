"""Compatibility facade for canonical design-brief compilation.

New code imports :mod:`archive.archflow.compilers.brief`.  This module preserves the
legacy runtime path without defining parallel contracts or behavior.
"""

from archive.archflow.compilers.brief import (
    BriefCompilationReceipt,
    BriefIntentObservation,
    BriefObservation,
    CompiledDesignBrief,
    compile_design_brief,
)

__all__ = [
    "BriefObservation",
    "BriefIntentObservation",
    "BriefCompilationReceipt",
    "CompiledDesignBrief",
    "compile_design_brief",
]
