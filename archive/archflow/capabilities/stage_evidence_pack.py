"""Compatibility facade for stage evidence-pack contracts.

The canonical implementation lives in ``archive.archflow.evidence.stage_pack``.  This
module intentionally owns no implementation so existing imports preserve
class and function identity.
"""

from archive.archflow.evidence.stage_pack import (
    STAGE_EVIDENCE_PACK_KEYS,
    CrossRunStagePackPredecessor,
    StageArtifactBinding,
    StageArtifactRole,
    StageClosureSummary,
    StageEvidenceBinding,
    StageEvidenceGap,
    StageEvidenceGapKind,
    StageEvidenceGapSeverity,
    StageEvidencePack,
    StageEvidencePackError,
    StageEvidenceRole,
    StagePackCompilationStatus,
    StagePackPredecessor,
    compile_stage_evidence_pack,
)

__all__ = [
    "STAGE_EVIDENCE_PACK_KEYS",
    "CrossRunStagePackPredecessor",
    "StageArtifactBinding",
    "StageArtifactRole",
    "StageClosureSummary",
    "StageEvidenceBinding",
    "StageEvidenceGap",
    "StageEvidenceGapKind",
    "StageEvidenceGapSeverity",
    "StageEvidencePack",
    "StageEvidencePackError",
    "StageEvidenceRole",
    "StagePackCompilationStatus",
    "StagePackPredecessor",
    "compile_stage_evidence_pack",
]
