"""Compatibility facade for canonical resource-policy compilation.

New code imports :mod:`archflow.compilers.resources`.  This module preserves
the legacy runtime path without defining parallel contracts or behavior.
"""

from archflow.compilers.resources import (
    BuildAssumptionProposal,
    BuildBudgetProposal,
    BuildPolicyProposal,
    CompiledBuildPolicy,
    ConstructabilityConstraintProposal,
    ProtectedBlockRuleProposal,
    ResourceAvailabilityProposal,
    ResourceCompilationError,
    ResourceCompilationReceipt,
    ResourceDemandProposal,
    StagingAssumptionProposal,
    compile_build_policy,
)

__all__ = [
    "ResourceCompilationError",
    "BuildAssumptionProposal",
    "ResourceAvailabilityProposal",
    "ResourceDemandProposal",
    "ProtectedBlockRuleProposal",
    "BuildBudgetProposal",
    "StagingAssumptionProposal",
    "ConstructabilityConstraintProposal",
    "BuildPolicyProposal",
    "ResourceCompilationReceipt",
    "CompiledBuildPolicy",
    "compile_build_policy",
]
