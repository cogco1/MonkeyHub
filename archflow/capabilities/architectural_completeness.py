"""Compatibility facade for stage-completeness contracts.

The canonical implementation lives in
``archflow.validation.stage_completeness``.  This module intentionally owns no
implementation so existing imports keep class and function identity.
"""

from archflow.validation.stage_completeness import (
    ArchitecturalCompletenessError,
    ArchitecturalCompletenessReceipt,
    ArchitecturalCompletenessStatus,
    DecisionFamilyCoverage,
    ParameterBasisKind,
    ParameterEvidence,
    ParameterEvidenceIssue,
    ParameterEvidenceIssueReason,
    ParameterGranularity,
    StageDecisionRequirements,
    compile_architectural_completeness,
)

__all__ = [
    "ArchitecturalCompletenessError",
    "ArchitecturalCompletenessReceipt",
    "ArchitecturalCompletenessStatus",
    "DecisionFamilyCoverage",
    "ParameterBasisKind",
    "ParameterEvidence",
    "ParameterEvidenceIssue",
    "ParameterEvidenceIssueReason",
    "ParameterGranularity",
    "StageDecisionRequirements",
    "compile_architectural_completeness",
]
