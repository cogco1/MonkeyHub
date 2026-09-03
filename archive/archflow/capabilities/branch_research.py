"""Compatibility facade for branch-conditioned research contracts.

The canonical implementation lives in :mod:`archive.archflow.research.branch`.
"""

from archive.archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchHardFeasibilityAssessment,
    BranchPrecedentAdoption,
    BranchPrecedentQuery,
    BranchResearchError,
    BranchResearchProfile,
    BranchResearchScope,
    BranchScorecard,
    BranchScoreCriterion,
    BranchSelectionDecision,
    BranchSelectionMode,
    BranchSelectionRules,
    BranchSelectionStatus,
    DecisionResearchNeed,
    HardFeasibilityStatus,
    _apply_branch_selection,
    _compile_branch_research_scope,
    bind_branch_adoption,
    bind_branch_snapshot,
    compile_branch_query,
    evaluate_branch_selection,
    require_branch_source_url,
    require_record_payload,
)

__all__ = [
    "BranchEvidenceSnapshot",
    "BranchHardFeasibilityAssessment",
    "BranchPrecedentAdoption",
    "BranchPrecedentQuery",
    "BranchResearchError",
    "BranchResearchProfile",
    "BranchResearchScope",
    "BranchScorecard",
    "BranchScoreCriterion",
    "BranchSelectionDecision",
    "BranchSelectionMode",
    "BranchSelectionRules",
    "BranchSelectionStatus",
    "DecisionResearchNeed",
    "HardFeasibilityStatus",
    "bind_branch_adoption",
    "bind_branch_snapshot",
    "compile_branch_query",
    "evaluate_branch_selection",
    "require_branch_source_url",
    "require_record_payload",
]
