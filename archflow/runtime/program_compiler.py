"""Compatibility facade for canonical design-program compilation.

New code imports :mod:`archflow.compilers.program`.  This module preserves the
legacy runtime path without defining parallel contracts or behavior.
"""

from archflow.compilers.program import (
    CompiledDesignProgram,
    ProgramAssumptionProposal,
    ProgramCompilationError,
    ProgramCompilationReceipt,
    ProgramMetricApplicabilityBinding,
    ProgramNodeProposal,
    ProgramProposalBundle,
    ProgramRangeProposal,
    ProgramRelationshipProposal,
    ProgramScenarioProposal,
    compile_design_program,
    maximum_footprint_constraint_refs,
)

__all__ = [
    "ProgramCompilationError",
    "ProgramAssumptionProposal",
    "ProgramNodeProposal",
    "ProgramRangeProposal",
    "ProgramRelationshipProposal",
    "ProgramScenarioProposal",
    "ProgramMetricApplicabilityBinding",
    "ProgramProposalBundle",
    "ProgramCompilationReceipt",
    "CompiledDesignProgram",
    "compile_design_program",
    "maximum_footprint_constraint_refs",
]
