"""Project-scoped Pantheon Stage 0--3 relation control.

The module is deliberately an instance adapter, not framework knowledge.  It
matches an explicit Pantheon geometry-program roster, compiles typed stage
subjects and HYPOTHESIS relation proposals, and exposes deterministic checker
inputs.  It performs no I/O and never selects a persistence path.

Two independent verification views are retained:

* ``check_assembly`` receives conservative AABBs and therefore keeps curved,
  lofted, boolean, or multi-object geometry UNKNOWN instead of inventing PASS.
* the project topology checker proves only facts that are exact in the program
  contract itself: whitelisted operation parameters, contact elevations, and
  exact boolean input membership.
* the generic walking-surface checker validates the complete Stage 1+ exterior
  approach from project-operation datums and project tolerance.  Five front
  steps remain a soft Candidate, not a historical hard fact.

Only independent PASS question receipts are passed to the generic relation
promotion compiler.  FAIL or UNKNOWN leaves the proposal graph unchanged.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import ClassVar

from archflow.contracts.branch import branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archive.archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineLevel,
    StageBaselineRole,
)
from archive.archflow.control.relation_checks import (
    RELATION_VERIFICATION_CHECKERS,
    relation_subject_inventory_ref,
)
from archive.archflow.control.relation_promotion import (
    RelationPromotionResult,
    promote_verified_relation_graph,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectRoleObligation,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archive.archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringCompilation,
    RelationAuthoringContext,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationDerivationQuestion,
    RelationProposalSpec,
    RelationRuleProposalSpec,
    RelationRuleEnvelope,
    compile_relation_authoring,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelationGraph,
    ArchitecturalRelationKind,
    RelationEpistemicStatus,
    RelationParticipant,
    RelationProjection,
)
from archflow.runtime.geometry_compiler import CompiledGeometryProgram
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryProgramProposal,
    LengthUnit,
)
from archive.archflow.validation.assembly import (
    AssemblyCoverageManifest,
    AssemblyObligationDisposition,
    AssemblyProfile,
    AssemblyRelationCandidate,
    AssemblySubject,
    AssemblySubjectObligation,
    GeometryBoundsBasis,
    RelationCandidateDisposition,
    RelationshipKind,
    RelationshipRequirement,
    check_assembly,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archive.archflow.validation.relation_verification import (
    RelationQuestionVerificationProfile,
    RelationVerificationBinding,
    compile_relation_question_verification,
)
from archive.archflow.validation.spatial import AABB
from archive.archflow.validation.walking_surface_continuity import (
    WalkingSurfaceContinuityProfile,
    WalkingSurfaceCriteria,
    WalkingSurfaceEdge,
    WalkingSurfaceEdgeKind,
    WalkingSurfaceNode,
    WalkingSurfaceNodeRole,
    WalkingSurfacePathRequirement,
    check_walking_surface_continuity,
)


PANTHEON_PROJECT_ID = "pantheon-reconstruction"
PANTHEON_STAGE_IDS = tuple(f"stage-{index}" for index in range(4))
PANTHEON_TOPOLOGY_CHECKER_ID = "pantheon-program-topology-checker"
PANTHEON_TOPOLOGY_CHECKER_VERSION = "1.0.0"
PANTHEON_RELATION_EVIDENCE_CHECKER_ID = (
    "pantheon-relation-evidence-composite-checker"
)
PANTHEON_FRONT_STEPS_CANDIDATE_REF = "candidate:pantheon-front-steps-soft"
SCENARIO_GRAVITY = "scenario:pantheon-gravity"
SCENARIO_ACCESS = "scenario:pantheon-main-entry"
SCENARIO_HOST = "scenario:pantheon-hosting"


class PantheonRelationControlError(ValueError):
    """The supplied program or explicit project mapping is incomplete."""


@dataclass(frozen=True, slots=True)
class _SubjectSpec:
    component_id: str
    parent_component_id: str | None
    semantic_kind: str
    binding_ids: tuple[str, ...]
    object_ids: tuple[str, ...]
    relation_targets: tuple[tuple[StageBaselineRole, tuple[str, ...]], ...] = ()

    @property
    def identity_ref(self) -> str:
        return f"design-component:{self.component_id}"

    @property
    def targets_by_role(self) -> dict[StageBaselineRole, tuple[str, ...]]:
        return dict(self.relation_targets)


@dataclass(frozen=True, slots=True)
class _BindingSpec:
    binding_id: str
    component_id: str
    object_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _QuestionSpec:
    question_id: str
    projection: RelationProjection
    scenario_ref: str
    subject_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    relation_kinds: tuple[ArchitecturalRelationKind, ...]
    envelopes: tuple[RelationRuleEnvelope, ...]
    relations: tuple[tuple[str, ArchitecturalRelationKind, tuple[tuple[str, str], ...]], ...]
    rule_semantic_kinds: tuple[tuple[str, ArchitecturalRelationKind, str, str, int, int | None], ...]
    prompt: str

    @property
    def ref(self) -> str:
        return f"relation-question:{self.question_id}"


@dataclass(frozen=True, slots=True)
class PantheonSubjectRecordPayloads:
    """P036-ready JSON values without caller-owned record paths."""

    component_proposal_payload: dict[str, object]
    component_proposal_digest: str
    component_index_payload: dict[str, object]
    component_index_digest: str

    SCHEMA: ClassVar[str] = "PantheonSubjectRecordPayloads@1"

    def __post_init__(self) -> None:
        if canonical_digest(self.component_proposal_payload) != require_sha256(
            self.component_proposal_digest,
            "component_proposal_digest",
        ):
            raise PantheonRelationControlError(
                "component proposal semantic digest changed"
            )
        if canonical_digest(self.component_index_payload) != require_sha256(
            self.component_index_digest,
            "component_index_digest",
        ):
            raise PantheonRelationControlError(
                "component index semantic digest changed"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_proposal_payload": self.component_proposal_payload,
            "component_proposal_digest": self.component_proposal_digest,
            "component_index_payload": self.component_index_payload,
            "component_index_digest": self.component_index_digest,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class PantheonQuestionVerification:
    profile: RelationQuestionVerificationProfile
    receipt: CheckReceiptEnvelope

    SCHEMA: ClassVar[str] = "PantheonQuestionVerification@1"

    def __post_init__(self) -> None:
        if self.receipt.check_id != (
            f"relation-verification-{self.profile.question.question_id}"
        ):
            raise PantheonRelationControlError(
                "question verification receipt crossed its profile"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile": self.profile.to_dict(),
            "receipt": self.receipt.to_dict(),
            "promotion_authority": False,
            "stage_acceptance_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class PantheonRelationControlResult:
    stage: int
    program_digest: str
    subject_records: PantheonSubjectRecordPayloads
    inventory: StageSubjectInventory
    context: RelationAuthoringContext
    proposal: RelationAuthoringProposal
    compilation: RelationAuthoringCompilation
    assembly_profile: AssemblyProfile
    program_topology_receipt: CheckReceiptEnvelope
    walking_surface_profile: WalkingSurfaceContinuityProfile | None
    walking_surface_receipt: CheckReceiptEnvelope | None
    base_assembly_receipt: CheckReceiptEnvelope
    relation_verification_base_receipt: CheckReceiptEnvelope
    question_verifications: tuple[PantheonQuestionVerification, ...]
    promotion: RelationPromotionResult | None

    SCHEMA: ClassVar[str] = "PantheonRelationControlResult@1"

    def __post_init__(self) -> None:
        _stage_id(self.stage)
        require_sha256(self.program_digest, "program_digest")
        walking_expected = self.stage >= 1
        if walking_expected != (self.walking_surface_profile is not None) or (
            walking_expected != (self.walking_surface_receipt is not None)
        ):
            raise PantheonRelationControlError(
                "walking-surface evidence must exist exactly for Stage 1+"
            )
        if (
            self.walking_surface_profile is not None
            and self.walking_surface_receipt is not None
            and self.walking_surface_receipt.coverage_denominator
            != self.walking_surface_profile.checker_requirement_refs
        ):
            raise PantheonRelationControlError(
                "walking-surface receipt crossed its path denominator"
            )
        if not self.question_verifications:
            raise PantheonRelationControlError(
                "relation control requires question verifications"
            )
        statuses = {item.receipt.status for item in self.question_verifications}
        if self.promotion is not None:
            if statuses != {CheckStatus.PASS}:
                raise PantheonRelationControlError(
                    "relation promotion crossed a non-PASS receipt"
                )
            if self.promotion.proposal_graph != self.compilation.graph:
                raise PantheonRelationControlError(
                    "promotion lost the exact hypothesis graph"
                )
        elif statuses == {CheckStatus.PASS}:
            raise PantheonRelationControlError(
                "all-PASS relation control omitted deterministic promotion"
            )

    @property
    def status(self) -> CheckStatus:
        statuses = tuple(item.receipt.status for item in self.question_verifications)
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        if CheckStatus.UNKNOWN in statuses:
            return CheckStatus.UNKNOWN
        return CheckStatus.PASS

    @property
    def proposal_graph(self) -> ArchitecturalRelationGraph:
        assert self.compilation.graph is not None
        return self.compilation.graph

    @property
    def effective_graph(self) -> ArchitecturalRelationGraph:
        return self.proposal_graph if self.promotion is None else self.promotion.graph

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage": self.stage,
            "stage_id": _stage_id(self.stage),
            "status": self.status.value,
            "program_digest": self.program_digest,
            "subject_records": self.subject_records.to_dict(),
            "inventory": self.inventory.to_dict(),
            "context": self.context.to_dict(),
            "proposal": self.proposal.to_dict(),
            "compilation": self.compilation.to_dict(),
            "assembly_profile": self.assembly_profile.to_dict(),
            "program_topology_receipt": self.program_topology_receipt.to_dict(),
            "walking_surface_profile": (
                None
                if self.walking_surface_profile is None
                else self.walking_surface_profile.to_dict()
            ),
            "walking_surface_receipt": (
                None
                if self.walking_surface_receipt is None
                else self.walking_surface_receipt.to_dict()
            ),
            "base_assembly_receipt": self.base_assembly_receipt.to_dict(),
            "relation_verification_base_receipt": (
                self.relation_verification_base_receipt.to_dict()
            ),
            "question_verifications": [
                item.to_dict() for item in self.question_verifications
            ],
            "promotion": None if self.promotion is None else self.promotion.to_dict(),
            "proposal_graph": self.proposal_graph.to_dict(),
            "effective_graph": self.effective_graph.to_dict(),
            "proposal_only_when_unverified": True,
            "stage_acceptance_authority": False,
            "persistence_authority": False,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        }


def _stage_id(stage: int) -> str:
    if not isinstance(stage, int) or isinstance(stage, bool) or stage not in range(4):
        raise PantheonRelationControlError("stage must be one of 0, 1, 2, or 3")
    return PANTHEON_STAGE_IDS[stage]


def _component_ref(component_id: str) -> str:
    return f"design-component:{component_id}"


def _topology_ref(name: str) -> str:
    return f"pantheon-topology:{name}"


def _program_parts(
    program: GeometryProgramProposal | CompiledGeometryProgram,
) -> tuple[GeometryProgramProposal, str]:
    if isinstance(program, CompiledGeometryProgram):
        return program.proposal, program.program_digest
    if isinstance(program, GeometryProgramProposal):
        return program, program.proposal_digest
    raise TypeError(
        "program must be GeometryProgramProposal or CompiledGeometryProgram"
    )


def _column_keys() -> tuple[tuple[int, int], ...]:
    return tuple(
        (row, column)
        for row, count in ((0, 8), (1, 4), (2, 4))
        for column in range(count)
    )


def _coffer_keys() -> tuple[tuple[int, int], ...]:
    return tuple((ring, column) for ring in range(5) for column in range(28))


def _with_relation_targets(
    *items: tuple[StageBaselineRole, tuple[str, ...]],
) -> tuple[tuple[StageBaselineRole, tuple[str, ...]], ...]:
    return tuple(sorted(items, key=lambda item: item[0].value))


def _subject_specs(stage: int) -> tuple[_SubjectSpec, ...]:
    _stage_id(stage)
    specs = [
        _SubjectSpec("pantheon", None, "pantheon-reconstruction", (), ()),
        _SubjectSpec(
            "foundation",
            "pantheon",
            "foundation",
            (),
            ("plinth-object",),
        ),
        _SubjectSpec(
            "rotunda",
            "pantheon",
            "rotunda",
            ("rotunda-binding",),
            (
                "rotunda-floor-object",
                "drum-inner-object",
                "drum-outer-object",
                "drum-wall-object",
            ),
            _with_relation_targets(
                (
                    StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                    (_component_ref("foundation"),),
                ),
                (
                    StageBaselineRole.LOAD_PATH,
                    (_component_ref("foundation"),),
                ),
            ),
        ),
        _SubjectSpec(
            "dome",
            "rotunda",
            "dome",
            ("dome-binding",),
            ("dome-inner-object", "dome-outer-object", "dome-shell-object"),
            _with_relation_targets(
                (
                    StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                    (_component_ref("rotunda"),),
                ),
                (
                    StageBaselineRole.LOAD_PATH,
                    (_component_ref("foundation"),),
                ),
            ),
        ),
        _SubjectSpec(
            "transition",
            "pantheon",
            "transition",
            ("transition-binding",),
            (
                "transition-floor-object",
                "transition-block-object",
                "transition-box-object",
                "transition-door-object",
                "transition-hug-object",
            ),
        ),
        _SubjectSpec("portico", "pantheon", "portico", ("portico-binding",), ()),
        _SubjectSpec(
            "portico-upper",
            "portico",
            "portico-upper",
            (),
            (
                ("portico-mass-object",)
                if stage == 0
                else ("pediment-object", "portico-mass-object")
            ),
            (
                ()
                if stage == 0
                else _with_relation_targets(
                    (
                        StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                        tuple(
                            _component_ref(f"column-capital-r{row}-c{column}")
                            for row, column in _column_keys()
                        ),
                    ),
                    (
                        StageBaselineRole.LOAD_PATH,
                        (_component_ref("foundation"),),
                    ),
                )
            ),
        ),
    ]
    if stage >= 1:
        specs.extend(
            (
                _SubjectSpec(
                    "portico-floor",
                    "portico",
                    "portico-floor",
                    (),
                    ("portico-floor-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                            (_component_ref("foundation"),),
                        ),
                        (
                            StageBaselineRole.LOAD_PATH,
                            (_component_ref("foundation"),),
                        ),
                    ),
                ),
                _SubjectSpec(
                    "colonnade",
                    "portico",
                    "colonnade",
                    ("colonnade-binding",),
                    (),
                ),
                _SubjectSpec(
                    "main-entry",
                    "transition",
                    "main-entry-opening",
                    ("entry-binding",),
                    ("door-tool-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.OPENING_CLEARANCE,
                            (_component_ref("rotunda"),),
                        ),
                    ),
                ),
                _SubjectSpec(
                    "oculus",
                    "dome",
                    "oculus-opening",
                    ("oculus-binding",),
                    ("oculus-tool-object",),
                ),
                _SubjectSpec(
                    "front-steps",
                    "portico",
                    "front-steps",
                    ("front-step-binding",),
                    tuple(f"front-step-{index}-object" for index in range(5)),
                ),
                _SubjectSpec(
                    "dome-step-rings",
                    "dome",
                    "dome-step-rings",
                    ("dome-step-binding",),
                    tuple(
                        sorted(
                            object_id
                            for ring in range(7)
                            for object_id in (
                                f"dome-step-inner-{ring}-object",
                                f"dome-step-outer-{ring}-object",
                                f"dome-step-ring-{ring}-object",
                            )
                        )
                    ),
                ),
            )
        )
        for row, column in _column_keys():
            column_id = f"portico-column-r{row}-c{column}"
            shaft_id = f"column-shaft-r{row}-c{column}"
            capital_id = f"column-capital-r{row}-c{column}"
            specs.extend(
                (
                    _SubjectSpec(
                        column_id,
                        "colonnade",
                        "portico-column",
                        (),
                        (),
                    ),
                    _SubjectSpec(
                        shaft_id,
                        column_id,
                        "portico-column-shaft",
                        (),
                        (f"{shaft_id}-object",),
                        _with_relation_targets(
                            (
                                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                                (_component_ref("portico-floor"),),
                            ),
                            (
                                StageBaselineRole.LOAD_PATH,
                                (_component_ref("foundation"),),
                            ),
                        ),
                    ),
                    _SubjectSpec(
                        capital_id,
                        column_id,
                        "portico-column-capital",
                        (),
                        (f"{capital_id}-object",),
                        _with_relation_targets(
                            (
                                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                                (_component_ref(shaft_id),),
                            ),
                            (
                                StageBaselineRole.LOAD_PATH,
                                (_component_ref("foundation"),),
                            ),
                        ),
                    ),
                )
            )
    if stage >= 2:
        specs.extend(
            (
                _SubjectSpec(
                    "exedra-ring",
                    "rotunda",
                    "exedra-ring",
                    ("exedra-binding",),
                    (),
                ),
                _SubjectSpec(
                    "niche-ring",
                    "rotunda",
                    "niche-ring",
                    ("niche-binding",),
                    (),
                ),
                _SubjectSpec(
                    "aedicula-ring",
                    "rotunda",
                    "aedicula-ring",
                    ("aedicula-binding",),
                    (),
                ),
                _SubjectSpec(
                    "apse",
                    "rotunda",
                    "apse-void",
                    ("apse-binding",),
                    ("exedra-cutter-rear-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                            (_component_ref("rotunda"),),
                        ),
                    ),
                ),
            )
        )
        for direction in ("east", "west"):
            specs.append(
                _SubjectSpec(
                    f"exedra-{direction}",
                    "exedra-ring",
                    "exedra-void",
                    (),
                    (f"exedra-cutter-{direction}-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                            (_component_ref("rotunda"),),
                        ),
                    ),
                )
            )
        for direction in ("ne", "nw", "se", "sw"):
            specs.append(
                _SubjectSpec(
                    f"niche-{direction}",
                    "niche-ring",
                    "niche-void",
                    (),
                    (f"niche-cutter-{direction}-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                            (_component_ref("rotunda"),),
                        ),
                    ),
                )
            )
        for index in range(8):
            specs.append(
                _SubjectSpec(
                    f"aedicula-{index}",
                    "aedicula-ring",
                    "aedicula",
                    (),
                    (f"aedicula-{index}-object",),
                )
            )
    if stage >= 3:
        specs.append(
            _SubjectSpec(
                "coffers",
                "dome",
                "coffers",
                ("coffer-binding",),
                (),
            )
        )
        for ring, column in _coffer_keys():
            component_id = f"coffer-r{ring}-c{column}"
            specs.append(
                _SubjectSpec(
                    component_id,
                    "coffers",
                    "coffer-void",
                    (),
                    (f"coffer-cutter-r{ring}-c{column}-object",),
                    _with_relation_targets(
                        (
                            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                            (_component_ref("dome"),),
                        ),
                    ),
                )
            )
    result = tuple(sorted(specs, key=lambda item: item.component_id))
    ids = tuple(item.component_id for item in result)
    if len(ids) != len(set(ids)):
        raise AssertionError("Pantheon subject declaration repeats a component")
    return result


def _binding_specs(stage: int) -> tuple[_BindingSpec, ...]:
    specs = _subject_specs(stage)
    objects_by_binding_owner: dict[str, set[str]] = {}
    # Object ownership is declared on leaves.  Binding ownership stays on the
    # exact program component, so the join below is explicit rather than a
    # name-based classification.
    explicit: dict[str, tuple[str, tuple[str, ...]]] = {
        "dome-binding": (
            "dome",
            ("dome-inner-object", "dome-outer-object", "dome-shell-object"),
        ),
        "portico-binding": (
            "portico",
            (
                ("portico-mass-object",)
                if stage == 0
                else ("pediment-object", "portico-floor-object", "portico-mass-object")
            ),
        ),
        "rotunda-binding": (
            "rotunda",
            (
                "drum-inner-object",
                "drum-outer-object",
                "drum-wall-object",
                "plinth-object",
                "rotunda-floor-object",
            ),
        ),
        "transition-binding": (
            "transition",
            (
                "transition-block-object",
                "transition-box-object",
                "transition-door-object",
                "transition-floor-object",
                "transition-hug-object",
            ),
        ),
    }
    if stage >= 1:
        explicit.update(
            {
                "colonnade-binding": (
                    "colonnade",
                    tuple(
                        sorted(
                            object_id
                            for row, column in _column_keys()
                            for object_id in (
                                f"column-shaft-r{row}-c{column}-object",
                                f"column-capital-r{row}-c{column}-object",
                            )
                        )
                    ),
                ),
                "dome-step-binding": (
                    "dome-step-rings",
                    tuple(
                        sorted(
                            object_id
                            for ring in range(7)
                            for object_id in (
                                f"dome-step-inner-{ring}-object",
                                f"dome-step-outer-{ring}-object",
                                f"dome-step-ring-{ring}-object",
                            )
                        )
                    ),
                ),
                "entry-binding": ("main-entry", ("door-tool-object",)),
                "front-step-binding": (
                    "front-steps",
                    tuple(f"front-step-{index}-object" for index in range(5)),
                ),
                "oculus-binding": ("oculus", ("oculus-tool-object",)),
            }
        )
    if stage >= 2:
        explicit.update(
            {
                "aedicula-binding": (
                    "aedicula-ring",
                    tuple(f"aedicula-{index}-object" for index in range(8)),
                ),
                "apse-binding": ("apse", ("exedra-cutter-rear-object",)),
                "exedra-binding": (
                    "exedra-ring",
                    ("exedra-cutter-east-object", "exedra-cutter-west-object"),
                ),
                "niche-binding": (
                    "niche-ring",
                    tuple(
                        f"niche-cutter-{direction}-object"
                        for direction in ("ne", "nw", "se", "sw")
                    ),
                ),
            }
        )
    if stage >= 3:
        explicit["coffer-binding"] = (
            "coffers",
            tuple(
                f"coffer-cutter-r{ring}-c{column}-object"
                for ring, column in _coffer_keys()
            ),
        )
    declared_binding_ids = {
        binding_id for item in specs for binding_id in item.binding_ids
    }
    if declared_binding_ids != set(explicit):
        raise AssertionError("Pantheon binding owners drifted")
    for binding_id, (_, object_ids) in explicit.items():
        objects_by_binding_owner[binding_id] = set(object_ids)
    return tuple(
        _BindingSpec(
            binding_id=binding_id,
            component_id=component_id,
            object_ids=tuple(sorted(objects_by_binding_owner[binding_id])),
        )
        for binding_id, (component_id, _) in sorted(explicit.items())
    )


def _validate_program(
    stage: int,
    proposal: GeometryProgramProposal,
    branch: BranchRef,
) -> None:
    _stage_id(stage)
    if proposal.project_id != PANTHEON_PROJECT_ID:
        raise PantheonRelationControlError(
            "Pantheon relation control received another project"
        )
    if proposal.length_unit is not LengthUnit.METER:
        raise PantheonRelationControlError(
            "Pantheon relation control requires the metre geometry program"
        )
    if (
        branch.run.project_id != proposal.project_id
        or branch.run.run_id != proposal.run_id
        or branch.run.base != proposal.base
    ):
        raise PantheonRelationControlError(
            "geometry program crossed the exact stage branch"
        )
    expected = {item.binding_id: item for item in _binding_specs(stage)}
    supplied = {item.binding_id: item for item in proposal.semantic_bindings}
    if set(supplied) != set(expected):
        raise PantheonRelationControlError(
            "Pantheon semantic binding denominator changed"
        )
    for binding_id, declaration in expected.items():
        binding = supplied[binding_id]
        if (
            binding.component_id != declaration.component_id
            or binding.object_ids != declaration.object_ids
        ):
            raise PantheonRelationControlError(
                f"Pantheon binding {binding_id} crossed its explicit object mapping"
            )
    declared_objects = {
        object_id for item in _subject_specs(stage) for object_id in item.object_ids
    }
    bound_objects = {
        object_id
        for binding in proposal.semantic_bindings
        for object_id in binding.object_ids
    }
    operation_outputs = {
        object_id
        for operation in proposal.operations
        for object_id in operation.output_object_ids
    }
    if declared_objects != bound_objects or bound_objects != operation_outputs:
        raise PantheonRelationControlError(
            "Pantheon subject/object denominator is not exact"
        )


def _descendant_objects(
    specs: tuple[_SubjectSpec, ...],
) -> dict[str, tuple[str, ...]]:
    children: dict[str, list[str]] = {}
    by_id = {item.component_id: item for item in specs}
    for item in specs:
        if item.parent_component_id is not None:
            children.setdefault(item.parent_component_id, []).append(item.component_id)

    def collect(component_id: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if component_id in seen:
            raise PantheonRelationControlError("Pantheon component ancestry cycles")
        item = by_id[component_id]
        result = set(item.object_ids)
        for child_id in children.get(component_id, ()):
            result.update(collect(child_id, seen | {component_id}))
        return result

    return {
        component_id: tuple(sorted(collect(component_id)))
        for component_id in sorted(by_id)
    }


def _subject_content(
    spec: _SubjectSpec,
    *,
    effective_object_ids: tuple[str, ...],
) -> dict[str, object]:
    relation_targets = [
        {
            "role": role.value,
            "target_refs": list(target_refs),
        }
        for role, target_refs in spec.relation_targets
    ]
    return {
        "schema": "PantheonRelationSubject@1",
        "component_id": spec.component_id,
        "identity_ref": spec.identity_ref,
        "parent_component_id": spec.parent_component_id,
        "semantic_kind": spec.semantic_kind,
        "geometry_object_ids": list(spec.object_ids),
        "effective_geometry_object_ids": list(effective_object_ids),
        "binding_ids": list(spec.binding_ids),
        "required_relation_roles": relation_targets,
        "project_specific": True,
        "design_authority": False,
        "stage_acceptance_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }


def pantheon_subject_record_payloads(
    stage: int,
    program: GeometryProgramProposal | CompiledGeometryProgram,
    branch: BranchRef,
) -> PantheonSubjectRecordPayloads:
    """Return complete subject proposal/index values before P036 persistence."""

    proposal, program_digest = _program_parts(program)
    _validate_program(stage, proposal, branch)
    specs = _subject_specs(stage)
    effective = _descendant_objects(specs)
    subjects = tuple(
        _subject_content(
            spec,
            effective_object_ids=effective[spec.component_id],
        )
        for spec in specs
    )
    component_proposal_payload = {
        "schema": "PantheonRelationComponentProposal@1",
        "project_id": proposal.project_id,
        "run_id": proposal.run_id,
        "branch": branch_ref_to_dict(branch),
        "stage_id": _stage_id(stage),
        "geometry_program_digest": program_digest,
        "subjects": list(subjects),
        "subject_count": len(subjects),
        "project_specific": True,
        "design_authority": False,
        "stage_acceptance_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }
    component_proposal_digest = canonical_digest(component_proposal_payload)
    index_entries = [
        {
            **subject,
            "component_digest": canonical_digest(subject),
            "source_ref": f"geometry-program:{program_digest}",
            "derived": True,
        }
        for subject in subjects
    ]
    component_index_payload = {
        "schema": "PantheonRelationComponentIndex@1",
        "project_id": proposal.project_id,
        "run_id": proposal.run_id,
        "branch": branch_ref_to_dict(branch),
        "stage_id": _stage_id(stage),
        "component_proposal_digest": component_proposal_digest,
        "geometry_program_digest": program_digest,
        "entries": index_entries,
        "component_ids": [item.component_id for item in specs],
        "binding_ids": [item.binding_id for item in _binding_specs(stage)],
        "geometry_object_ids": sorted(
            object_id for item in specs for object_id in item.object_ids
        ),
        "derived": True,
        "regenerable": True,
        "composition_tree_authority": False,
        "design_authority": False,
        "stage_acceptance_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }
    component_index_digest = canonical_digest(component_index_payload)
    return PantheonSubjectRecordPayloads(
        component_proposal_payload=component_proposal_payload,
        component_proposal_digest=component_proposal_digest,
        component_index_payload=component_index_payload,
        component_index_digest=component_index_digest,
    )


def _role_obligations(
    spec: _SubjectSpec,
    *,
    effective_object_ids: tuple[str, ...],
    program_digest: str,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
) -> tuple[StageSubjectRoleObligation, ...]:
    explicit = spec.targets_by_role
    lineage_target = (
        f"geometry-program:{program_digest}"
        if spec.parent_component_id is None
        else _component_ref(spec.parent_component_id)
    )
    spatial_targets = tuple(
        f"geometry-object:{object_id}" for object_id in effective_object_ids
    )
    targets_by_role = {
        StageBaselineRole.COMPONENT_LINEAGE: (lineage_target,),
        StageBaselineRole.SPATIAL_ENVELOPE: spatial_targets,
        **explicit,
    }
    return tuple(
        StageSubjectRoleObligation(
            role=role,
            disposition=(
                StageSubjectDisposition.REQUIRED
                if targets_by_role.get(role)
                else StageSubjectDisposition.NOT_APPLICABLE
            ),
            target_refs=targets_by_role.get(role, ()),
            evidence_refs=evidence_refs,
            authority_refs=authority_refs,
        )
        for role in sorted(
            BASELINE_LEVEL_ROLES[StageBaselineLevel.SPATIAL],
            key=lambda item: item.value,
        )
    )


def _inventory(
    stage: int,
    proposal: GeometryProgramProposal,
    program_digest: str,
    branch: BranchRef,
    *,
    stage_subject_ref: str,
    stage_subject_digest: str,
    component_proposal_ref: ProjectRecordRef,
    component_proposal_digest: str,
    component_index_ref: ProjectRecordRef,
    component_index_digest: str,
    subject_records: PantheonSubjectRecordPayloads,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
) -> StageSubjectInventory:
    if (
        component_proposal_digest != subject_records.component_proposal_digest
        or component_index_digest != subject_records.component_index_digest
    ):
        raise PantheonRelationControlError(
            "persisted subject semantic digests crossed generated payloads"
        )
    specs = _subject_specs(stage)
    effective = _descendant_objects(specs)
    proposal_subjects = {
        item["component_id"]: item
        for item in subject_records.component_proposal_payload["subjects"]
    }
    entries = []
    for spec in specs:
        content = proposal_subjects[spec.component_id]
        entries.append(
            StageSubjectInventoryEntry(
                component_id=spec.component_id,
                identity_ref=spec.identity_ref,
                parent_component_id=spec.parent_component_id,
                semantic_kind=spec.semantic_kind,
                component_digest=canonical_digest(content),
                geometry_object_ids=spec.object_ids,
                binding_ids=spec.binding_ids,
                role_obligations=_role_obligations(
                    spec,
                    effective_object_ids=effective[spec.component_id],
                    program_digest=program_digest,
                    evidence_refs=evidence_refs,
                    authority_refs=authority_refs,
                ),
            )
        )
    return StageSubjectInventory(
        inventory_id=f"pantheon-relation-subjects-stage-{stage}",
        branch=branch,
        stage_id=_stage_id(stage),
        stage_subject_ref=stage_subject_ref,
        stage_subject_digest=stage_subject_digest,
        baseline_level=StageBaselineLevel.SPATIAL,
        component_proposal_ref=component_proposal_ref,
        component_proposal_digest=component_proposal_digest,
        component_index_ref=component_index_ref,
        component_index_digest=component_index_digest,
        entries=tuple(entries),
    )


def _support_relation(
    relation_id: str,
    supported: str,
    supporter: str,
) -> tuple[str, ArchitecturalRelationKind, tuple[tuple[str, str], ...]]:
    return (
        relation_id,
        ArchitecturalRelationKind.SUPPORT,
        (("supported", supported), ("supporter", supporter)),
    )


def _question_specs(stage: int) -> tuple[_QuestionSpec, ...]:
    _stage_id(stage)
    one_support = (
        RelationRuleEnvelope(
            relation_kind=ArchitecturalRelationKind.SUPPORT,
            subject_role="supported",
            counted_role="supporter",
            minimum_count=1,
            maximum_count=1,
        ),
    )
    specs: list[_QuestionSpec] = [
        _QuestionSpec(
            question_id="pantheon-shell-support",
            projection=RelationProjection.SUPPORT,
            scenario_ref=SCENARIO_GRAVITY,
            subject_ids=("dome", "rotunda"),
            target_ids=("foundation",),
            relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
            envelopes=one_support,
            relations=(
                _support_relation("dome-on-rotunda", "dome", "rotunda"),
                _support_relation(
                    "rotunda-on-foundation", "rotunda", "foundation"
                ),
            ),
            rule_semantic_kinds=(
                ("dome-support-rule", ArchitecturalRelationKind.SUPPORT, "dome", "supported", 1, 1),
                ("rotunda-support-rule", ArchitecturalRelationKind.SUPPORT, "rotunda", "supported", 1, 1),
            ),
            prompt=(
                "Propose the declared dome-to-drum-to-foundation support path "
                "using only the supplied Pantheon project subjects."
            ),
        )
    ]
    if stage >= 1:
        shafts = tuple(f"column-shaft-r{r}-c{c}" for r, c in _column_keys())
        capitals = tuple(f"column-capital-r{r}-c{c}" for r, c in _column_keys())
        specs.extend(
            (
                _QuestionSpec(
                    question_id="pantheon-portico-floor-support",
                    projection=RelationProjection.SUPPORT,
                    scenario_ref=SCENARIO_GRAVITY,
                    subject_ids=("portico-floor",),
                    target_ids=("foundation",),
                    relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    envelopes=one_support,
                    relations=(
                        _support_relation(
                            "portico-floor-on-foundation",
                            "portico-floor",
                            "foundation",
                        ),
                    ),
                    rule_semantic_kinds=(
                        ("portico-floor-support-rule", ArchitecturalRelationKind.SUPPORT, "portico-floor", "supported", 1, 1),
                    ),
                    prompt="Propose the declared portico-floor support edge.",
                ),
                _QuestionSpec(
                    question_id="pantheon-portico-shaft-support",
                    projection=RelationProjection.SUPPORT,
                    scenario_ref=SCENARIO_GRAVITY,
                    subject_ids=shafts,
                    target_ids=("portico-floor",),
                    relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    envelopes=one_support,
                    relations=tuple(
                        _support_relation(
                            f"{shaft_id}-on-portico-floor",
                            shaft_id,
                            "portico-floor",
                        )
                        for shaft_id in shafts
                    ),
                    rule_semantic_kinds=(
                        ("portico-shaft-support-rule", ArchitecturalRelationKind.SUPPORT, "portico-column-shaft", "supported", 1, 1),
                    ),
                    prompt="Propose one explicit floor support for every declared shaft.",
                ),
                _QuestionSpec(
                    question_id="pantheon-portico-capital-support",
                    projection=RelationProjection.SUPPORT,
                    scenario_ref=SCENARIO_GRAVITY,
                    subject_ids=capitals,
                    target_ids=shafts,
                    relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    envelopes=one_support,
                    relations=tuple(
                        _support_relation(
                            f"column-capital-r{row}-c{column}-on-shaft",
                            f"column-capital-r{row}-c{column}",
                            f"column-shaft-r{row}-c{column}",
                        )
                        for row, column in _column_keys()
                    ),
                    rule_semantic_kinds=(
                        ("portico-capital-support-rule", ArchitecturalRelationKind.SUPPORT, "portico-column-capital", "supported", 1, 1),
                    ),
                    prompt="Propose one explicit shaft support for every declared capital.",
                ),
                _QuestionSpec(
                    question_id="pantheon-portico-upper-support",
                    projection=RelationProjection.SUPPORT,
                    scenario_ref=SCENARIO_GRAVITY,
                    subject_ids=("portico-upper",),
                    target_ids=capitals,
                    relation_kinds=(ArchitecturalRelationKind.SUPPORT,),
                    envelopes=(
                        RelationRuleEnvelope(
                            relation_kind=ArchitecturalRelationKind.SUPPORT,
                            subject_role="supported",
                            counted_role="supporter",
                            minimum_count=16,
                            maximum_count=16,
                        ),
                    ),
                    relations=tuple(
                        _support_relation(
                            f"portico-upper-on-column-capital-r{row}-c{column}",
                            "portico-upper",
                            f"column-capital-r{row}-c{column}",
                        )
                        for row, column in _column_keys()
                    ),
                    rule_semantic_kinds=(
                        ("portico-upper-support-rule", ArchitecturalRelationKind.SUPPORT, "portico-upper", "supported", 16, 16),
                    ),
                    prompt="Propose all sixteen declared capital bearings for the portico upper work.",
                ),
                _QuestionSpec(
                    question_id="pantheon-main-entry-clear",
                    projection=RelationProjection.ACCESS,
                    scenario_ref=SCENARIO_ACCESS,
                    subject_ids=(
                        "front-steps",
                        "main-entry",
                        "portico-floor",
                        "transition",
                    ),
                    target_ids=(
                        "main-entry",
                        "portico-floor",
                        "rotunda",
                        "transition",
                    ),
                    relation_kinds=(ArchitecturalRelationKind.ALLOWS_PASSAGE,),
                    envelopes=(
                        RelationRuleEnvelope(
                            relation_kind=ArchitecturalRelationKind.ALLOWS_PASSAGE,
                            subject_role="from",
                            counted_role="to",
                            minimum_count=1,
                            maximum_count=1,
                        ),
                    ),
                    relations=(
                        (
                            "front-steps-allow-portico-floor",
                            ArchitecturalRelationKind.ALLOWS_PASSAGE,
                            (("from", "front-steps"), ("to", "portico-floor")),
                        ),
                        (
                            "portico-floor-allows-transition",
                            ArchitecturalRelationKind.ALLOWS_PASSAGE,
                            (("from", "portico-floor"), ("to", "transition")),
                        ),
                        (
                            "transition-allows-main-entry",
                            ArchitecturalRelationKind.ALLOWS_PASSAGE,
                            (("from", "transition"), ("to", "main-entry")),
                        ),
                        (
                            "main-entry-allows-rotunda",
                            ArchitecturalRelationKind.ALLOWS_PASSAGE,
                            (("from", "main-entry"), ("to", "rotunda")),
                        ),
                    ),
                    rule_semantic_kinds=(
                        ("front-step-access-rule", ArchitecturalRelationKind.ALLOWS_PASSAGE, "front-steps", "from", 1, 1),
                        ("portico-floor-access-rule", ArchitecturalRelationKind.ALLOWS_PASSAGE, "portico-floor", "from", 1, 1),
                        ("transition-access-rule", ArchitecturalRelationKind.ALLOWS_PASSAGE, "transition", "from", 1, 1),
                        ("main-entry-access-rule", ArchitecturalRelationKind.ALLOWS_PASSAGE, "main-entry-opening", "from", 1, 1),
                    ),
                    prompt=(
                        "Propose the complete declared exterior approach through "
                        "the soft-candidate steps, portico and transition into "
                        "the rotunda. Do not promote the candidate step count to "
                        "a historical hard fact."
                    ),
                ),
            )
        )
    if stage >= 2:
        cutter_ids = (
            "apse",
            "exedra-east",
            "exedra-west",
            "niche-ne",
            "niche-nw",
            "niche-se",
            "niche-sw",
        )
        relations = tuple(
            (
                f"rotunda-hosts-{component_id}",
                ArchitecturalRelationKind.HOSTS_VOID,
                (("host", "rotunda"), ("void", component_id)),
            )
            for component_id in cutter_ids
        )
        specs.append(
            _QuestionSpec(
                question_id="pantheon-stage-2-hosting",
                projection=RelationProjection.HOST,
                scenario_ref=SCENARIO_HOST,
                subject_ids=tuple(sorted(cutter_ids)),
                target_ids=("rotunda",),
                relation_kinds=(ArchitecturalRelationKind.HOSTS_VOID,),
                envelopes=(
                    RelationRuleEnvelope(
                        relation_kind=ArchitecturalRelationKind.HOSTS_VOID,
                        subject_role="void",
                        counted_role="host",
                        minimum_count=1,
                        maximum_count=1,
                    ),
                ),
                relations=relations,
                rule_semantic_kinds=(
                    ("apse-host-rule", ArchitecturalRelationKind.HOSTS_VOID, "apse-void", "void", 1, 1),
                    ("exedra-host-rule", ArchitecturalRelationKind.HOSTS_VOID, "exedra-void", "void", 1, 1),
                    ("niche-host-rule", ArchitecturalRelationKind.HOSTS_VOID, "niche-void", "void", 1, 1),
                ),
                prompt="Propose the seven explicit wall-void host cuts.",
            )
        )
    if stage >= 3:
        coffer_ids = tuple(
            sorted(f"coffer-r{r}-c{c}" for r, c in _coffer_keys())
        )
        specs.append(
            _QuestionSpec(
                question_id="pantheon-stage-3-coffer-hosting",
                projection=RelationProjection.HOST,
                scenario_ref=SCENARIO_HOST,
                subject_ids=coffer_ids,
                target_ids=("dome",),
                relation_kinds=(ArchitecturalRelationKind.HOSTS_VOID,),
                envelopes=(
                    RelationRuleEnvelope(
                        relation_kind=ArchitecturalRelationKind.HOSTS_VOID,
                        subject_role="void",
                        counted_role="host",
                        minimum_count=1,
                        maximum_count=1,
                    ),
                ),
                relations=tuple(
                    (
                        f"dome-hosts-{component_id}",
                        ArchitecturalRelationKind.HOSTS_VOID,
                        (("host", "dome"), ("void", component_id)),
                    )
                    for component_id in coffer_ids
                ),
                rule_semantic_kinds=(
                    ("coffer-host-rule", ArchitecturalRelationKind.HOSTS_VOID, "coffer-void", "void", 1, 1),
                ),
                prompt="Propose one explicit dome-hosted cut for each of the 140 declared coffers.",
            )
        )
    return tuple(specs)


def _context_and_proposal(
    stage: int,
    proposal: GeometryProgramProposal,
    branch: BranchRef,
    inventory: StageSubjectInventory,
    *,
    scope_digest: str,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
) -> tuple[RelationAuthoringContext, RelationAuthoringProposal]:
    question_specs = _question_specs(stage)
    questions = []
    bases = []
    relation_specs = []
    rule_specs = []
    answers = []
    nodes = tuple(
        ArchitecturalNode(
            node_ref=entry.identity_ref,
            node_kind=ArchitecturalNodeKind.COMPONENT,
            semantic_kind=entry.semantic_kind,
            stage_id=inventory.stage_id,
            source_refs=(f"stage-subject-entry:{entry.entry_digest}",),
        )
        for entry in inventory.entries
    )
    for spec in question_specs:
        policy_basis_id = f"{spec.question_id}-policy"
        topology_basis_id = f"{spec.question_id}-topology"
        questions.append(
            RelationDerivationQuestion(
                question_id=spec.question_id,
                projection=spec.projection,
                scenario_ref=spec.scenario_ref,
                subject_refs=tuple(_component_ref(item) for item in spec.subject_ids),
                target_refs=tuple(_component_ref(item) for item in spec.target_ids),
                allowed_relation_kinds=spec.relation_kinds,
                rule_envelopes=spec.envelopes,
                basis_ids=(policy_basis_id, topology_basis_id),
                prompt=spec.prompt,
            )
        )
        for basis_id, basis_use in (
            (policy_basis_id, RelationBasisUse.POLICY),
            (topology_basis_id, RelationBasisUse.TOPOLOGY),
        ):
            bases.append(
                RelationBasisBinding(
                    basis_id=basis_id,
                    basis_kind=RelationBasisKind.HUMAN,
                    basis_use=basis_use,
                    question_refs=(spec.ref,),
                    allowed_relation_kinds=spec.relation_kinds,
                    epistemic_status=RelationEpistemicStatus.DERIVED,
                    evidence_refs=evidence_refs,
                    authority_refs=authority_refs,
                    summary=(
                        "Caller-retained Pantheon project evidence and authority "
                        "bind this exact project declaration."
                    ),
                )
            )
        question_relation_ids = []
        for relation_id, kind, participants in spec.relations:
            question_relation_ids.append(relation_id)
            relation_specs.append(
                RelationProposalSpec(
                    relation_id=relation_id,
                    question_refs=(spec.ref,),
                    kind=kind,
                    participants=tuple(
                        RelationParticipant(
                            role=role,
                            node_ref=_component_ref(component_id),
                        )
                        for role, component_id in participants
                    ),
                    scenario_ref=spec.scenario_ref,
                    basis_ids=(topology_basis_id,),
                )
            )
        question_rule_ids = []
        envelope_by_kind = {
            item.relation_kind: item for item in spec.envelopes
        }
        for (
            rule_id,
            kind,
            semantic_kind,
            subject_role,
            minimum,
            maximum,
        ) in spec.rule_semantic_kinds:
            envelope = envelope_by_kind[kind]
            if (
                subject_role != envelope.subject_role
                or minimum != envelope.minimum_count
                or maximum != envelope.maximum_count
            ):
                raise AssertionError("Pantheon question rule crossed its envelope")
            question_rule_ids.append(rule_id)
            rule_specs.append(
                RelationRuleProposalSpec(
                    rule_id=rule_id,
                    question_refs=(spec.ref,),
                    node_kind=ArchitecturalNodeKind.COMPONENT,
                    semantic_kind=semantic_kind,
                    relation_kind=kind,
                    subject_role=subject_role,
                    counted_role=envelope.counted_role,
                    minimum_count=minimum,
                    maximum_count=maximum,
                    scenario_ref=spec.scenario_ref,
                    basis_ids=(policy_basis_id,),
                )
            )
        answers.append(
            RelationDerivationAnswer(
                question_ref=spec.ref,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=tuple(sorted(question_relation_ids)),
                rule_ids=tuple(sorted(question_rule_ids)),
                rationale=(
                    "The explicit Pantheon project mapping supplies the complete "
                    "question denominator; independent checks are still required."
                ),
            )
        )
    inventory_ref = relation_subject_inventory_ref(inventory)
    context = RelationAuthoringContext(
        context_id=f"pantheon-relation-control-stage-{stage}",
        branch=branch,
        stage_id=inventory.stage_id,
        state_digest=proposal.design_state_digest,
        scope_digest=scope_digest,
        stage_subject_digest=inventory.stage_subject_digest,
        subject_inventory_ref=inventory_ref,
        subject_inventory_digest=inventory.inventory_digest,
        nodes=nodes,
        questions=tuple(questions),
        bases=tuple(bases),
    )
    authored = RelationAuthoringProposal(
        context_digest=context.context_digest,
        answers=tuple(answers),
        relations=tuple(relation_specs),
        rules=tuple(rule_specs),
    )
    return context, authored


def _parameter(operation: GeometryOperation, name: str) -> object:
    for item in operation.parameters:
        if item.name == name:
            return json.loads(item.value_json)
    raise PantheonRelationControlError(
        f"operation {operation.op_id} lacks exact parameter {name}"
    )


def _operation_maps(
    proposal: GeometryProgramProposal,
) -> tuple[dict[str, GeometryOperation], dict[str, GeometryOperation]]:
    by_id = {item.op_id: item for item in proposal.operations}
    by_output: dict[str, GeometryOperation] = {}
    for operation in proposal.operations:
        for object_id in operation.output_object_ids:
            if object_id in by_output:
                raise PantheonRelationControlError(
                    "geometry object has multiple producing operations"
                )
            by_output[object_id] = operation
    return by_id, by_output


def _union_bounds(values: tuple[AABB, ...]) -> AABB:
    if not values:
        raise PantheonRelationControlError("cannot union an empty geometry set")
    return AABB(
        minimum=tuple(min(item.minimum[index] for item in values) for index in range(3)),
        maximum=tuple(max(item.maximum[index] for item in values) for index in range(3)),
    )


def _object_bounds(
    object_id: str,
    by_output: dict[str, GeometryOperation],
    cache: dict[str, tuple[AABB, GeometryBoundsBasis]],
) -> tuple[AABB, GeometryBoundsBasis]:
    if object_id in cache:
        return cache[object_id]
    operation = by_output.get(object_id)
    if operation is None:
        raise PantheonRelationControlError(
            f"declared object {object_id} has no producer"
        )
    kind = operation.kind
    if kind is GeometryOperationKind.SOLID:
        origin = tuple(float(item) for item in _parameter(operation, "origin"))
        size = tuple(float(item) for item in _parameter(operation, "size"))
        bounds = AABB(
            minimum=origin,
            maximum=tuple(origin[index] + size[index] for index in range(3)),
        )
        basis = GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID
    elif kind is GeometryOperationKind.REVOLVE:
        start = tuple(float(item) for item in _parameter(operation, "axis_start"))
        end = tuple(float(item) for item in _parameter(operation, "axis_end"))
        radius = max(
            float(_parameter(operation, "start_radius")),
            float(_parameter(operation, "end_radius")),
        )
        if start[0] != end[0] or start[2] != end[2]:
            raise PantheonRelationControlError(
                f"operation {operation.op_id} is not a declared vertical revolve"
            )
        bounds = AABB(
            minimum=(start[0] - radius, min(start[1], end[1]), start[2] - radius),
            maximum=(start[0] + radius, max(start[1], end[1]), start[2] + radius),
        )
        basis = GeometryBoundsBasis.CONSERVATIVE_ENVELOPE
    elif kind in {GeometryOperationKind.LOFT, GeometryOperationKind.EXTRUSION}:
        name = "profiles" if kind is GeometryOperationKind.LOFT else "profile"
        points = [tuple(float(value) for value in item) for item in _parameter(operation, name)]
        if kind is GeometryOperationKind.EXTRUSION:
            vector = tuple(float(item) for item in _parameter(operation, "vector"))
            points = [
                *points,
                *[
                    tuple(point[index] + vector[index] for index in range(3))
                    for point in points
                ],
            ]
        bounds = AABB(
            minimum=tuple(min(point[index] for point in points) for index in range(3)),
            maximum=tuple(max(point[index] for point in points) for index in range(3)),
        )
        basis = GeometryBoundsBasis.CONSERVATIVE_ENVELOPE
    elif kind is GeometryOperationKind.BOOLEAN_DIFFERENCE:
        base_index = int(_parameter(operation, "base_index"))
        try:
            base_id = operation.input_object_ids[base_index]
        except IndexError as exc:
            raise PantheonRelationControlError(
                f"operation {operation.op_id} base_index escaped its inputs"
            ) from exc
        bounds, _ = _object_bounds(base_id, by_output, cache)
        basis = GeometryBoundsBasis.CONSERVATIVE_ENVELOPE
    else:
        raise PantheonRelationControlError(
            f"operation {operation.op_id} cannot provide a bounded relation subject"
        )
    cache[object_id] = (bounds, basis)
    return cache[object_id]


def _assembly_subjects(
    inventory: StageSubjectInventory,
    proposal: GeometryProgramProposal,
) -> tuple[AssemblySubject, ...]:
    _, by_output = _operation_maps(proposal)
    specs = _subject_specs(int(inventory.stage_id.rsplit("-", 1)[1]))
    effective = _descendant_objects(specs)
    cache: dict[str, tuple[AABB, GeometryBoundsBasis]] = {}
    subjects = []
    for spec in specs:
        object_values = tuple(
            _object_bounds(object_id, by_output, cache)
            for object_id in effective[spec.component_id]
        )
        if not object_values:
            raise PantheonRelationControlError(
                f"subject {spec.component_id} has no explicit or descendant geometry"
            )
        bounds = _union_bounds(tuple(item[0] for item in object_values))
        basis = (
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID
            if len(object_values) == 1
            and object_values[0][1] is GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID
            else GeometryBoundsBasis.CONSERVATIVE_ENVELOPE
        )
        subjects.append(
            AssemblySubject(
                subject_ref=spec.identity_ref,
                bounds=bounds,
                bounds_basis=basis,
                geometry_ref=(
                    f"geometry-object:{effective[spec.component_id][0]}"
                    if len(effective[spec.component_id]) == 1
                    else (
                        "geometry-set:"
                        f"{spec.component_id}:{canonical_digest(list(effective[spec.component_id]))[:16]}"
                    )
                ),
            )
        )
    return tuple(subjects)


def _relation_endpoints(relation) -> tuple[str, str]:
    participants = {item.role: item.node_ref for item in relation.participants}
    if relation.kind is ArchitecturalRelationKind.SUPPORT:
        return participants["supported"], participants["supporter"]
    if relation.kind is ArchitecturalRelationKind.ALLOWS_PASSAGE:
        return participants["from"], participants["to"]
    if relation.kind is ArchitecturalRelationKind.HOSTS_VOID:
        return participants["void"], participants["host"]
    if relation.kind is ArchitecturalRelationKind.HOST:
        return participants["hosted"], participants["host"]
    raise PantheonRelationControlError(
        f"relation {relation.relation_id} has no assembly mapping"
    )


def _aabb_volume(bounds: AABB) -> float:
    return math.prod(
        bounds.maximum[index] - bounds.minimum[index] for index in range(3)
    )


def _assembly_profile(
    stage: int,
    proposal: GeometryProgramProposal,
    inventory: StageSubjectInventory,
    compilation: RelationAuthoringCompilation,
    *,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
) -> tuple[AssemblyProfile, dict[str, tuple[str, ...]]]:
    if compilation.graph is None:
        raise PantheonRelationControlError("relation compilation lacks a graph")
    subjects = _assembly_subjects(inventory, proposal)
    subjects_by_ref = {item.subject_ref: item for item in subjects}
    requirements: list[RelationshipRequirement] = []
    mapping: dict[str, list[str]] = {}

    def add(
        relation_ref: str | None,
        *,
        requirement_id: str,
        kind: RelationshipKind,
        endpoints: tuple[str, str],
        maximum_overlap_volume: float | None = None,
    ) -> RelationshipRequirement:
        requirement = RelationshipRequirement(
            requirement_id=requirement_id,
            kind=kind,
            subject_refs=endpoints,
            evidence_refs=evidence_refs,
            authority_refs=authority_refs,
            maximum_overlap_volume=maximum_overlap_volume,
        )
        requirements.append(requirement)
        if relation_ref is not None:
            mapping.setdefault(relation_ref, []).append(requirement.ref)
        return requirement

    support_subjects: set[str] = set()
    for relation in compilation.graph.relations:
        first, second = _relation_endpoints(relation)
        if relation.kind is ArchitecturalRelationKind.SUPPORT:
            support_subjects.add(first)
            add(
                relation.ref,
                requirement_id=f"{relation.relation_id}-support",
                kind=RelationshipKind.SUPPORT,
                endpoints=(first, second),
            )
            if relation.relation_id == "dome-on-rotunda":
                add(
                    relation.ref,
                    requirement_id="dome-drum-touch",
                    kind=RelationshipKind.TOUCH,
                    endpoints=(first, second),
                )
        elif relation.kind is ArchitecturalRelationKind.ALLOWS_PASSAGE:
            for row, column in _column_keys():
                obstruction = _component_ref(f"column-shaft-r{row}-c{column}")
                add(
                    relation.ref,
                    requirement_id=(
                        f"{relation.relation_id}-column-clear-r{row}-c{column}"
                    ),
                    kind=RelationshipKind.OPENING_CLEAR,
                    endpoints=(first, obstruction),
                )
        elif relation.kind is ArchitecturalRelationKind.HOST:
            add(
                relation.ref,
                requirement_id=f"{relation.relation_id}-containment",
                kind=RelationshipKind.HOST_CONTAINMENT,
                endpoints=(first, second),
            )
        elif relation.kind is ArchitecturalRelationKind.HOSTS_VOID:
            contained = subjects_by_ref[first].bounds
            assert contained is not None
            add(
                relation.ref,
                requirement_id=f"{relation.relation_id}-cut",
                kind=RelationshipKind.BOUNDED_EMBEDDED_OVERLAP,
                endpoints=(first, second),
                maximum_overlap_volume=_aabb_volume(contained),
            )
    foundation_ref = _component_ref("foundation")
    for subject_ref in sorted(support_subjects):
        if subject_ref == foundation_ref:
            continue
        add(
            None,
            requirement_id=(
                "load-path-" + subject_ref.removeprefix("design-component:")
            ),
            kind=RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            endpoints=(subject_ref, foundation_ref),
        )

    requirement_by_id = {item.requirement_id: item for item in requirements}
    requirements_by_subject: dict[str, list[tuple[RelationshipRequirement, int]]] = {}
    for requirement in requirements:
        for index, subject_ref in enumerate(requirement.subject_refs):
            requirements_by_subject.setdefault(subject_ref, []).append(
                (requirement, index)
            )
    relation_required_subjects = {
        entry.identity_ref
        for entry in inventory.entries
        if any(
            obligation.disposition is StageSubjectDisposition.REQUIRED
            and obligation.role
            in {
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                StageBaselineRole.OPENING_CLEARANCE,
                StageBaselineRole.LOAD_PATH,
            }
            for obligation in entry.role_obligations
        )
    }
    obligations = []
    for subject in subjects:
        candidates = tuple(
            sorted(
                requirements_by_subject.get(subject.subject_ref, ()),
                key=lambda item: (item[0].kind.value, item[0].requirement_id, item[1]),
            )
        )
        required = subject.subject_ref in relation_required_subjects
        if required and not candidates:
            raise PantheonRelationControlError(
                f"assembly subject {subject.subject_ref} has no exact requirement"
            )
        requirement, endpoint_index = (
            candidates[0] if required and candidates else (None, None)
        )
        obligations.append(
            AssemblySubjectObligation(
                obligation_id=(
                    "pantheon-assembly-"
                    + subject.subject_ref.removeprefix("design-component:")
                ),
                role_id="pantheon-relation-control",
                subject_ref=subject.subject_ref,
                disposition=(
                    AssemblyObligationDisposition.REQUIRED
                    if required
                    else AssemblyObligationDisposition.NOT_APPLICABLE
                ),
                relationship_kind=(None if requirement is None else requirement.kind),
                endpoint_index=endpoint_index,
                requirement_id=(
                    None if requirement is None else requirement.requirement_id
                ),
                evidence_refs=evidence_refs,
                authority_refs=authority_refs,
            )
        )
    candidates = []
    for relation in compilation.graph.relations:
        relation_requirements = tuple(sorted(mapping.get(relation.ref, ())))
        if not relation_requirements:
            raise PantheonRelationControlError(
                f"relation {relation.ref} is unmapped from assembly requirements"
            )
        first_requirement = next(
            item for item in requirements if item.ref == relation_requirements[0]
        )
        if set(first_requirement.subject_refs) != set(
            _relation_endpoints(relation)
        ):
            # The access relation is checked against every explicit column
            # obstruction, not against a fabricated main-entry/rotunda solid
            # pair.  The exact relation-to-requirement mapping above remains
            # complete; this optional pair-candidate view cannot represent a
            # one-to-many obstruction denominator and is therefore omitted.
            continue
        candidates.append(
            AssemblyRelationCandidate(
                candidate_id=f"pantheon-{relation.relation_id}",
                subject_refs=_relation_endpoints(relation),
                disposition=RelationCandidateDisposition.REQUIREMENT,
                requirement_id=first_requirement.requirement_id,
                evidence_refs=evidence_refs,
                authority_refs=authority_refs,
            )
        )
    manifest = AssemblyCoverageManifest(
        manifest_id=f"pantheon-assembly-coverage-stage-{stage}",
        stage_subject_refs=tuple(item.subject_ref for item in subjects),
        stage_subject_source_digest=inventory.stage_subject_digest,
        obligations=tuple(obligations),
        relation_candidates=tuple(candidates),
    )
    profile = AssemblyProfile(
        profile_id=f"pantheon-assembly-stage-{stage}",
        subjects=subjects,
        requirements=tuple(requirements),
        coverage_manifest=manifest,
        length_unit=proposal.length_unit.value,
        vertical_axis="y",
        linear_tolerance=proposal.tolerance.linear,
        volume_tolerance=proposal.tolerance.linear ** 3,
    )
    if set(requirement_by_id) != {
        item.requirement_id for item in profile.requirements
    }:
        raise AssertionError("assembly requirement denominator changed")
    return profile, {
        relation_ref: tuple(sorted(requirement_refs))
        for relation_ref, requirement_refs in mapping.items()
    }


def _solid_bounds(operation: GeometryOperation) -> AABB:
    if operation.kind is not GeometryOperationKind.SOLID:
        raise PantheonRelationControlError(
            f"operation {operation.op_id} is not an exact solid"
        )
    origin = tuple(float(item) for item in _parameter(operation, "origin"))
    size = tuple(float(item) for item in _parameter(operation, "size"))
    return AABB(
        minimum=origin,
        maximum=tuple(origin[index] + size[index] for index in range(3)),
    )


def _revolve_values(
    operation: GeometryOperation,
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    if operation.kind is not GeometryOperationKind.REVOLVE:
        raise PantheonRelationControlError(
            f"operation {operation.op_id} is not a declared revolve"
        )
    start = tuple(float(item) for item in _parameter(operation, "axis_start"))
    end = tuple(float(item) for item in _parameter(operation, "axis_end"))
    radius = max(
        float(_parameter(operation, "start_radius")),
        float(_parameter(operation, "end_radius")),
    )
    return start, end, radius


def _positive_plan_overlap(first: AABB, second: AABB) -> bool:
    return all(
        min(first.maximum[index], second.maximum[index])
        > max(first.minimum[index], second.minimum[index])
        for index in (0, 2)
    )


def _walking_surface_profile(
    stage: int,
    proposal: GeometryProgramProposal,
    program_digest: str,
) -> WalkingSurfaceContinuityProfile | None:
    """Compile the Stage 1+ soft-candidate entry route from exact operations."""

    if stage < 1:
        return None
    by_id, _ = _operation_maps(proposal)
    tolerance = proposal.tolerance.linear
    evidence_refs = tuple(
        sorted(
            {
                f"geometry-program:{program_digest}",
                PANTHEON_FRONT_STEPS_CANDIDATE_REF,
                *(
                    ref
                    for binding in proposal.semantic_bindings
                    for ref in binding.evidence_refs
                ),
            }
        )
    )
    step_ids = tuple(f"front-step-{index}" for index in range(5))
    steps = tuple(_solid_bounds(by_id[op_id]) for op_id in step_ids)
    portico = _solid_bounds(by_id["portico-floor"])
    transition = _solid_bounds(by_id["transition-floor"])
    threshold = _solid_bounds(by_id["door-tool"])
    _, rotunda_top, _ = _revolve_values(by_id["rotunda-floor"])
    grade_datum = min(item.minimum[1] for item in steps)
    step_datums = tuple(item.maximum[1] for item in steps)
    portico_datum = portico.maximum[1]
    transition_datum = transition.maximum[1]
    threshold_datum = threshold.minimum[1]
    rotunda_datum = rotunda_top[1]
    expected_candidate_rise = (
        portico_datum - grade_datum
    ) / len(step_datums)

    nodes = (
        WalkingSurfaceNode(
            node_ref="walking-surface:pantheon-exterior-grade",
            role=WalkingSurfaceNodeRole.EXTERIOR,
            datum=grade_datum,
            evidence_refs=evidence_refs,
        ),
        *(
            WalkingSurfaceNode(
                node_ref=f"geometry-object:front-step-{index}-object",
                role=WalkingSurfaceNodeRole.TRANSITION,
                datum=datum,
                evidence_refs=evidence_refs,
            )
            for index, datum in enumerate(step_datums)
        ),
        WalkingSurfaceNode(
            node_ref="geometry-object:portico-floor-object",
            role=WalkingSurfaceNodeRole.TRANSITION,
            datum=portico_datum,
            evidence_refs=evidence_refs,
        ),
        WalkingSurfaceNode(
            node_ref="geometry-object:transition-floor-object",
            role=WalkingSurfaceNodeRole.TRANSITION,
            datum=transition_datum,
            evidence_refs=evidence_refs,
        ),
        WalkingSurfaceNode(
            node_ref="geometry-object:door-tool-object",
            role=WalkingSurfaceNodeRole.TRANSITION,
            datum=threshold_datum,
            evidence_refs=evidence_refs,
        ),
        WalkingSurfaceNode(
            node_ref="geometry-object:rotunda-floor-object",
            role=WalkingSurfaceNodeRole.INTERIOR,
            datum=rotunda_datum,
            evidence_refs=evidence_refs,
        ),
    )
    node_refs = tuple(item.node_ref for item in nodes)
    edges = []
    for index in range(5):
        edges.append(
            WalkingSurfaceEdge(
                edge_ref=f"walking-surface-edge:pantheon-front-step-{index}",
                from_node_ref=node_refs[index],
                to_node_ref=node_refs[index + 1],
                kind=WalkingSurfaceEdgeKind.STEP,
                evidence_refs=evidence_refs,
            )
        )
    for name, from_index, to_index, kind in (
        ("step-to-portico", 5, 6, WalkingSurfaceEdgeKind.CONTINUOUS),
        ("portico-to-transition", 6, 7, WalkingSurfaceEdgeKind.CONTINUOUS),
        ("main-entry-threshold", 7, 8, WalkingSurfaceEdgeKind.THRESHOLD),
        ("threshold-to-rotunda", 8, 9, WalkingSurfaceEdgeKind.CONTINUOUS),
    ):
        edges.append(
            WalkingSurfaceEdge(
                edge_ref=f"walking-surface-edge:pantheon-{name}",
                from_node_ref=node_refs[from_index],
                to_node_ref=node_refs[to_index],
                kind=kind,
                evidence_refs=evidence_refs,
            )
        )
    return WalkingSurfaceContinuityProfile(
        profile_id=f"pantheon-stage-{stage}-soft-candidate-entry-route",
        nodes=nodes,
        edges=tuple(edges),
        paths=(
            WalkingSurfacePathRequirement(
                path_id="pantheon-exterior-to-rotunda-soft-candidate",
                node_refs=node_refs,
                evidence_refs=evidence_refs,
            ),
        ),
        criteria=WalkingSurfaceCriteria(
            max_continuous_delta=tolerance,
            max_step_rise=expected_candidate_rise + tolerance,
            max_ramp_slope=None,
            max_threshold_rise=tolerance,
        ),
        length_unit_ref="unit:meter",
    )


def _compose_relation_evidence_receipt(
    topology: CheckReceiptEnvelope,
    walking: CheckReceiptEnvelope,
) -> CheckReceiptEnvelope:
    """Retain both independent denominators in one relation-verification base."""

    if (
        topology.branch != walking.branch
        or topology.scope_digest != walking.scope_digest
        or topology.subject_digest != walking.subject_digest
    ):
        raise PantheonRelationControlError(
            "topology and walking-surface receipts crossed verification scope"
        )
    statuses = {topology.status, walking.status}
    status = (
        CheckStatus.FAIL
        if CheckStatus.FAIL in statuses
        else CheckStatus.UNKNOWN
        if CheckStatus.UNKNOWN in statuses
        else CheckStatus.PASS
    )
    return CheckReceiptEnvelope(
        check_id=(
            "pantheon-relation-evidence-"
            f"{canonical_digest([topology.receipt_digest, walking.receipt_digest])[:24]}"
        ),
        checker_id=PANTHEON_RELATION_EVIDENCE_CHECKER_ID,
        checker_version="1.0.0",
        branch=topology.branch,
        scope_digest=topology.scope_digest,
        subject_refs=tuple(sorted(set(topology.subject_refs) | set(walking.subject_refs))),
        subject_digest=topology.subject_digest,
        status=status,
        source_refs=tuple(sorted(set(topology.source_refs) | set(walking.source_refs))),
        authority_refs=tuple(
            sorted(set(topology.authority_refs) | set(walking.authority_refs))
        ),
        findings=tuple(
            sorted(
                topology.findings + walking.findings,
                key=lambda item: (item.code, item.subject_refs, item.message),
            )
        ),
        measurements=tuple(
            sorted(
                topology.measurements + walking.measurements,
                key=lambda item: item.measurement_id,
            )
        ),
        coverage_denominator=tuple(
            sorted(
                set(topology.coverage_denominator)
                | set(walking.coverage_denominator)
            )
        ),
        covered_refs=tuple(
            sorted(set(topology.covered_refs) | set(walking.covered_refs))
        ),
    )


def _topology_requirement_mapping(stage: int) -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {
        "architectural-relation:rotunda-on-foundation": (
            _topology_ref("rotunda-floor-foundation-contact"),
            _topology_ref("drum-rotunda-floor-contact"),
        ),
        "architectural-relation:dome-on-rotunda": (
            _topology_ref("dome-drum-interface"),
        ),
    }
    if stage >= 1:
        mapping["architectural-relation:portico-floor-on-foundation"] = (
            _topology_ref("portico-floor-foundation-contact"),
        )
        for row, column in _column_keys():
            mapping[
                f"architectural-relation:column-shaft-r{row}-c{column}-on-portico-floor"
            ] = (_topology_ref(f"shaft-floor-contact:r{row}:c{column}"),)
            mapping[
                f"architectural-relation:column-capital-r{row}-c{column}-on-shaft"
            ] = (_topology_ref(f"capital-shaft-contact:r{row}:c{column}"),)
            mapping[
                f"architectural-relation:portico-upper-on-column-capital-r{row}-c{column}"
            ] = (_topology_ref(f"upper-capital-contact:r{row}:c{column}"),)
        mapping["architectural-relation:main-entry-allows-rotunda"] = tuple(
            sorted(
                {
                    _topology_ref("main-entry-transition-continuity"),
                    *(
                        _topology_ref(f"main-entry-column-clear:r{row}:c{column}")
                        for row, column in _column_keys()
                    ),
                }
            )
        )
    if stage >= 2:
        cutter_objects = {
            "apse": "exedra-cutter-rear-object",
            "exedra-east": "exedra-cutter-east-object",
            "exedra-west": "exedra-cutter-west-object",
            "niche-ne": "niche-cutter-ne-object",
            "niche-nw": "niche-cutter-nw-object",
            "niche-se": "niche-cutter-se-object",
            "niche-sw": "niche-cutter-sw-object",
        }
        for component_id, object_id in cutter_objects.items():
            mapping[f"architectural-relation:rotunda-hosts-{component_id}"] = (
                _topology_ref(f"drum-boolean-input:{object_id}"),
            )
    if stage >= 3:
        for ring, column in _coffer_keys():
            mapping[
                f"architectural-relation:dome-hosts-coffer-r{ring}-c{column}"
            ] = (
                _topology_ref(
                    f"dome-boolean-input:coffer-cutter-r{ring}-c{column}-object"
                ),
            )
    return mapping


def compile_pantheon_program_topology_receipt(
    stage: int,
    program: GeometryProgramProposal | CompiledGeometryProgram,
    branch: BranchRef,
    scope_digest: str,
    stage_subject_digest: str,
) -> CheckReceiptEnvelope:
    """Prove only exact whitelisted Pantheon program-topology statements."""

    proposal, program_digest = _program_parts(program)
    _validate_program(stage, proposal, branch)
    scope_digest = require_sha256(scope_digest, "scope_digest")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    by_id, _ = _operation_maps(proposal)
    tolerance = proposal.tolerance.linear
    outcomes: dict[str, tuple[CheckStatus, str]] = {}

    def exact(ref: str, passed: bool, message: str) -> None:
        outcomes[ref] = (
            CheckStatus.PASS if passed else CheckStatus.FAIL,
            message,
        )

    try:
        plinth = _solid_bounds(by_id["plinth"])
        floor_start, floor_end, floor_radius = _revolve_values(
            by_id["rotunda-floor"]
        )
        drum_start, drum_end, drum_radius = _revolve_values(by_id["drum-outer"])
        exact(
            _topology_ref("rotunda-floor-foundation-contact"),
            abs(floor_start[1] - plinth.maximum[1]) <= tolerance
            and plinth.minimum[0] <= floor_start[0] - floor_radius
            and floor_start[0] + floor_radius <= plinth.maximum[0]
            and plinth.minimum[2] <= floor_start[2] - floor_radius
            and floor_start[2] + floor_radius <= plinth.maximum[2]
            and floor_radius > 0.0,
            "rotunda floor must bear on the exact foundation top",
        )
        exact(
            _topology_ref("drum-rotunda-floor-contact"),
            abs(drum_start[1] - floor_end[1]) <= tolerance
            and abs(drum_start[0] - floor_end[0]) <= tolerance
            and abs(drum_start[2] - floor_end[2]) <= tolerance
            and abs(drum_radius - floor_radius) <= tolerance,
            "drum base must meet the exact rotunda finished-floor top",
        )
        dome = by_id["dome-outer"]
        if dome.kind is not GeometryOperationKind.LOFT:
            raise PantheonRelationControlError("dome-outer is not the declared loft")
        profiles = [
            tuple(float(value) for value in point)
            for point in _parameter(dome, "profiles")
        ]
        profile_size = int(_parameter(dome, "profile_size"))
        base_ring = profiles[:profile_size]
        interface_ok = bool(base_ring) and all(
            abs(point[1] - drum_end[1]) <= tolerance
            and abs(
                math.hypot(point[0] - drum_end[0], point[2] - drum_end[2])
                - drum_radius
            )
            <= tolerance
            for point in base_ring
        )
        exact(
            _topology_ref("dome-drum-interface"),
            interface_ok,
            "dome base ring must equal the exact outer drum spring interface",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PantheonRelationControlError(
            "Stage 0 topology operations or parameters drifted"
        ) from exc

    if stage >= 1:
        floor = _solid_bounds(by_id["portico-floor"])
        exact(
            _topology_ref("portico-floor-foundation-contact"),
            abs(floor.minimum[1] - plinth.maximum[1]) <= tolerance
            and _positive_plan_overlap(floor, plinth),
            "portico floor must meet the plinth top with positive plan overlap",
        )
        upper = _solid_bounds(by_id["portico-mass"])
        for row, column in _column_keys():
            shaft = by_id[f"column-shaft-r{row}-c{column}"]
            capital = by_id[f"column-capital-r{row}-c{column}"]
            shaft_start, shaft_end, shaft_radius = _revolve_values(shaft)
            capital_start, capital_end, capital_radius = _revolve_values(capital)
            exact(
                _topology_ref(f"shaft-floor-contact:r{row}:c{column}"),
                abs(shaft_start[1] - floor.maximum[1]) <= tolerance
                and floor.minimum[0]
                <= shaft_start[0] - shaft_radius
                < shaft_start[0] + shaft_radius
                <= floor.maximum[0]
                and floor.minimum[2]
                <= shaft_start[2] - shaft_radius
                < shaft_start[2] + shaft_radius
                <= floor.maximum[2],
                "shaft base must meet and remain inside the exact portico floor",
            )
            exact(
                _topology_ref(f"capital-shaft-contact:r{row}:c{column}"),
                abs(capital_start[1] - shaft_end[1]) <= tolerance
                and abs(capital_start[0] - shaft_end[0]) <= tolerance
                and abs(capital_start[2] - shaft_end[2]) <= tolerance
                and shaft_radius > 0.0
                and capital_radius > 0.0,
                "capital axis start must equal its declared shaft axis end",
            )
            exact(
                _topology_ref(f"upper-capital-contact:r{row}:c{column}"),
                abs(upper.minimum[1] - capital_end[1]) <= tolerance
                and upper.minimum[0] <= capital_end[0] <= upper.maximum[0]
                and upper.minimum[2] <= capital_end[2] <= upper.maximum[2],
                "portico upper bottom must meet every declared capital top",
            )
        door = _solid_bounds(by_id["door-tool"])
        transition_door = _solid_bounds(by_id["transition-door"])
        exact(
            _topology_ref("main-entry-transition-continuity"),
            door.intersection_volume(transition_door) > 0.0,
            "main-entry and transition clearance cutters must overlap continuously",
        )
        for row, column in _column_keys():
            shaft_start, shaft_end, shaft_radius = _revolve_values(
                by_id[f"column-shaft-r{row}-c{column}"]
            )
            envelope = AABB(
                minimum=(
                    shaft_start[0] - shaft_radius,
                    min(shaft_start[1], shaft_end[1]),
                    shaft_start[2] - shaft_radius,
                ),
                maximum=(
                    shaft_start[0] + shaft_radius,
                    max(shaft_start[1], shaft_end[1]),
                    shaft_start[2] + shaft_radius,
                ),
            )
            exact(
                _topology_ref(f"main-entry-column-clear:r{row}:c{column}"),
                door.intersection_volume(envelope) == 0.0,
                "the exact shaft envelope must remain outside the entry cutter",
            )

    if stage >= 2:
        drum_wall = by_id["drum-wall"]
        if drum_wall.kind is not GeometryOperationKind.BOOLEAN_DIFFERENCE:
            raise PantheonRelationControlError("drum-wall is not a boolean difference")
        cutter_objects = (
            "exedra-cutter-east-object",
            "exedra-cutter-rear-object",
            "exedra-cutter-west-object",
            "niche-cutter-ne-object",
            "niche-cutter-nw-object",
            "niche-cutter-se-object",
            "niche-cutter-sw-object",
        )
        for object_id in cutter_objects:
            exact(
                _topology_ref(f"drum-boolean-input:{object_id}"),
                object_id in drum_wall.input_object_ids,
                "each declared Stage 2 void must be an exact drum boolean input",
            )

    if stage >= 3:
        dome_shell = by_id["dome-shell"]
        if dome_shell.kind is not GeometryOperationKind.BOOLEAN_DIFFERENCE:
            raise PantheonRelationControlError("dome-shell is not a boolean difference")
        for ring, column in _coffer_keys():
            object_id = f"coffer-cutter-r{ring}-c{column}-object"
            exact(
                _topology_ref(f"dome-boolean-input:{object_id}"),
                object_id in dome_shell.input_object_ids,
                "each of the 140 declared coffers must be an exact dome boolean input",
            )

    expected_refs = tuple(
        sorted(
            requirement_ref
            for values in _topology_requirement_mapping(stage).values()
            for requirement_ref in values
        )
    )
    if len(expected_refs) != len(set(expected_refs)) or set(outcomes) != set(
        expected_refs
    ):
        raise PantheonRelationControlError(
            "program topology checker did not equal the explicit relation denominator"
        )
    source_refs = tuple(
        sorted(
            {
                f"geometry-program:{program_digest}",
                *(
                    ref
                    for binding in proposal.semantic_bindings
                    for ref in binding.evidence_refs
                ),
            }
        )
    )
    findings = tuple(
        CheckFinding(
            code="pantheon-program-topology-unsatisfied",
            severity=FindingSeverity.ERROR,
            message=f"{requirement_ref}: {message}",
            subject_refs=(requirement_ref,),
            evidence_refs=source_refs,
        )
        for requirement_ref, (status, message) in sorted(outcomes.items())
        if status is CheckStatus.FAIL
    )
    status = CheckStatus.FAIL if findings else CheckStatus.PASS
    return CheckReceiptEnvelope(
        check_id=f"pantheon-program-topology-stage-{stage}",
        checker_id=PANTHEON_TOPOLOGY_CHECKER_ID,
        checker_version=PANTHEON_TOPOLOGY_CHECKER_VERSION,
        branch=branch,
        scope_digest=scope_digest,
        subject_refs=expected_refs,
        subject_digest=stage_subject_digest,
        status=status,
        source_refs=source_refs,
        authority_refs=(),
        findings=findings,
        measurements=(),
        coverage_denominator=expected_refs,
        covered_refs=tuple(
            sorted(
                ref
                for ref, (outcome_status, _) in outcomes.items()
                if outcome_status is CheckStatus.PASS
            )
        ),
    )


def _verification_profiles(
    stage: int,
    inventory: StageSubjectInventory,
    context: RelationAuthoringContext,
    compilation: RelationAuthoringCompilation,
    walking_surface_profile: WalkingSurfaceContinuityProfile | None,
    base_checker_id: str,
) -> tuple[RelationQuestionVerificationProfile, ...]:
    if compilation.graph is None:
        raise PantheonRelationControlError("relation compilation lacks a graph")
    mapping = {
        relation_ref: list(requirement_refs)
        for relation_ref, requirement_refs in _topology_requirement_mapping(
            stage
        ).items()
    }
    walking_subject_refs: tuple[str, ...] = ()
    if walking_surface_profile is not None:
        if len(walking_surface_profile.checker_requirement_refs) != 1:
            raise PantheonRelationControlError(
                "Pantheon entry continuity must retain one complete path"
            )
        path_ref = walking_surface_profile.checker_requirement_refs[0]
        walking_subject_refs = tuple(
            ref
            for path in walking_surface_profile.paths
            for ref in path.node_refs
        )
        for relation in compilation.graph.relations:
            if relation.kind is ArchitecturalRelationKind.ALLOWS_PASSAGE:
                mapping.setdefault(relation.ref, []).append(path_ref)
    mapping = {
        relation_ref: tuple(sorted(set(requirement_refs)))
        for relation_ref, requirement_refs in mapping.items()
    }
    if set(mapping) != {item.ref for item in compilation.graph.relations}:
        raise PantheonRelationControlError(
            "Pantheon relation-to-check mapping does not equal the graph denominator"
        )
    profiles = []
    for question in context.questions:
        relations = tuple(
            item
            for item in compilation.graph.relations
            if question.ref in item.source_refs
        )
        bindings = tuple(
            RelationVerificationBinding(
                relation_ref=relation.ref,
                checker_requirement_refs=mapping[relation.ref],
                checker_subject_refs=tuple(
                    sorted(
                        {
                            *(item.node_ref for item in relation.participants),
                            *(
                                walking_subject_refs
                                if relation.kind
                                is ArchitecturalRelationKind.ALLOWS_PASSAGE
                                else ()
                            ),
                        }
                    )
                ),
            )
            for relation in relations
        )
        profiles.append(
            RelationQuestionVerificationProfile(
                profile_id=f"pantheon-{question.question_id}",
                question=question,
                proposal_graph=compilation.graph,
                subject_inventory_ref=relation_subject_inventory_ref(inventory),
                output_checker_id=RELATION_VERIFICATION_CHECKERS[question.projection],
                base_checker_id=base_checker_id,
                bindings=bindings,
            )
        )
    return tuple(profiles)


def _validate_verification_base(
    supplied: CheckReceiptEnvelope,
    expected: CheckReceiptEnvelope,
) -> None:
    if (
        supplied.checker_id != expected.checker_id
        or supplied.checker_version != expected.checker_version
        or supplied.branch != expected.branch
        or supplied.scope_digest != expected.scope_digest
        or supplied.subject_digest != expected.subject_digest
        or supplied.subject_refs != expected.subject_refs
        or supplied.coverage_denominator != expected.coverage_denominator
        or supplied.revalidation_refs
    ):
        raise PantheonRelationControlError(
            "verification base receipt crossed the exact relation denominator"
        )


def compile_pantheon_relation_control(
    stage: int,
    program: GeometryProgramProposal | CompiledGeometryProgram,
    branch: BranchRef,
    *,
    scope_digest: str,
    stage_subject_ref: str,
    stage_subject_digest: str,
    component_proposal_ref: ProjectRecordRef,
    component_proposal_digest: str,
    component_index_ref: ProjectRecordRef,
    component_index_digest: str,
    evidence_refs: tuple[str, ...],
    authority_refs: tuple[str, ...],
    verification_base_receipt: CheckReceiptEnvelope | None = None,
) -> PantheonRelationControlResult:
    """Compile, independently check, and fail-closed promote one stage.

    All persistence locations and all evidence/authority refs are supplied by
    the caller.  The optional verification receipt exists for a retained
    independent successor checker; it must keep the exact combined topology
    and walking-surface denominator.
    """

    proposal, program_digest = _program_parts(program)
    _validate_program(stage, proposal, branch)
    scope_digest = require_sha256(scope_digest, "scope_digest")
    stage_subject_digest = require_sha256(
        stage_subject_digest,
        "stage_subject_digest",
    )
    if not evidence_refs or not authority_refs:
        raise PantheonRelationControlError(
            "caller must retain non-empty evidence_refs and authority_refs"
        )
    subject_records = pantheon_subject_record_payloads(stage, program, branch)
    inventory = _inventory(
        stage,
        proposal,
        program_digest,
        branch,
        stage_subject_ref=stage_subject_ref,
        stage_subject_digest=stage_subject_digest,
        component_proposal_ref=component_proposal_ref,
        component_proposal_digest=component_proposal_digest,
        component_index_ref=component_index_ref,
        component_index_digest=component_index_digest,
        subject_records=subject_records,
        evidence_refs=evidence_refs,
        authority_refs=authority_refs,
    )
    context, authored = _context_and_proposal(
        stage,
        proposal,
        branch,
        inventory,
        scope_digest=scope_digest,
        evidence_refs=evidence_refs,
        authority_refs=authority_refs,
    )
    compilation = compile_relation_authoring(context, authored)
    assembly_profile, _ = _assembly_profile(
        stage,
        proposal,
        inventory,
        compilation,
        evidence_refs=evidence_refs,
        authority_refs=authority_refs,
    )
    base_assembly_receipt = check_assembly(
        assembly_profile,
        branch=branch,
        scope_digest=scope_digest,
        stage_subject_digest=stage_subject_digest,
    )
    topology_receipt = compile_pantheon_program_topology_receipt(
        stage,
        program,
        branch,
        scope_digest,
        stage_subject_digest,
    )
    walking_surface_profile = _walking_surface_profile(
        stage,
        proposal,
        program_digest,
    )
    walking_surface_receipt = (
        None
        if walking_surface_profile is None
        else check_walking_surface_continuity(
            walking_surface_profile,
            branch=branch,
            scope_digest=scope_digest,
            stage_subject_digest=stage_subject_digest,
        )
    )
    expected_verification_base = (
        topology_receipt
        if walking_surface_receipt is None
        else _compose_relation_evidence_receipt(
            topology_receipt,
            walking_surface_receipt,
        )
    )
    verification_base = (
        expected_verification_base
        if verification_base_receipt is None
        else verification_base_receipt
    )
    _validate_verification_base(
        verification_base,
        expected_verification_base,
    )
    profiles = _verification_profiles(
        stage,
        inventory,
        context,
        compilation,
        walking_surface_profile,
        verification_base.checker_id,
    )
    question_verifications = tuple(
        PantheonQuestionVerification(
            profile=profile,
            receipt=compile_relation_question_verification(
                profile,
                verification_base,
            ),
        )
        for profile in profiles
    )
    promotion = None
    if all(
        item.receipt.status is CheckStatus.PASS
        for item in question_verifications
    ):
        promotion = promote_verified_relation_graph(
            context,
            compilation,
            inventory,
            tuple(item.receipt for item in question_verifications),
        )
    return PantheonRelationControlResult(
        stage=stage,
        program_digest=program_digest,
        subject_records=subject_records,
        inventory=inventory,
        context=context,
        proposal=authored,
        compilation=compilation,
        assembly_profile=assembly_profile,
        program_topology_receipt=topology_receipt,
        walking_surface_profile=walking_surface_profile,
        walking_surface_receipt=walking_surface_receipt,
        base_assembly_receipt=base_assembly_receipt,
        relation_verification_base_receipt=verification_base,
        question_verifications=question_verifications,
        promotion=promotion,
    )


__all__ = [
    "PANTHEON_PROJECT_ID",
    "PANTHEON_TOPOLOGY_CHECKER_ID",
    "PantheonQuestionVerification",
    "PantheonRelationControlError",
    "PantheonRelationControlResult",
    "PantheonSubjectRecordPayloads",
    "compile_pantheon_program_topology_receipt",
    "compile_pantheon_relation_control",
    "pantheon_subject_record_payloads",
]
