"""The table of record kinds the spine retains.

A record written through :mod:`archflow.project.repository` is named
``<kind>-<sha256>.json``: the kind is the only word a reader has for what a
retained file is, and until now any identifier at all could become one. This
module is that vocabulary written down once. Every kind a spine writer uses
appears here with the ``schema`` literal its payload carries (or ``None``
where the payload declares no schema), the persistence area it lands in, and
one line saying what it is for.

``put_json`` refuses a kind this table does not hold. Reads stay unrestricted:
retained runs from the archived lanes carry kinds the spine never writes, and
they must stay readable (ADR-004).

A kind belongs in this table when a spine module writes it, or when a spine
module reads it as a retained record and names its contract. Three entries are
of the second sort and no spine module writes them; each says so in its note:
the research bridge's ledger, and the component template and catalog
confrontation that ``produce_geometry_program_proposal`` still accepts, whose
library was archived. A kind no spine module writes, reads or names is not in
the table: the retired lanes' vocabulary stays where the retired lanes are.

Three kinds are written with a computed suffix, so they are registered as
patterns rather than as exact strings. Their table key is the shape a person
reads; ``kind_pattern`` is what a written kind is matched against.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from archflow.project.ports import PersistenceArea


@dataclass(frozen=True, slots=True)
class RecordKind:
    """One retained record kind: what it is, and what its payload declares."""

    kind: str
    schema: str | None
    area: str
    note: str
    kind_pattern: str | None = None

    def matches(self, kind: str) -> bool:
        if self.kind_pattern is None:
            return kind == self.kind
        return re.fullmatch(self.kind_pattern, kind) is not None


# ---- the runner's kinds (archflow/runtime/project_runner.py)

STATE_RECORD = "state-record"
PROJECT_LEVELS = "project-levels"
PROJECT_GRIDS = "project-grids"
SELECTED_SPATIAL_OPTION = "selected-spatial-option"
DEVELOPED_DESIGN_STATE = "developed-design-state"
DISCIPLINE_SEAT = "discipline-seat"
SEAT_AUTHORING_CONTEXT = "seat-authoring-context"
SEAT_GEOMETRY_PROGRAM = "seat-geometry-program"
SEAT_RELATION_CHECK = "seat-relation-check"
SEAT_ROUND_RECEIPT = "seat-round-receipt"
SEAT_HANDOVER = "seat-handover"
SEAT_RHINO_EXECUTION = "seat-rhino-execution"
SEAT_OCCT_EXECUTION = "seat-occt-execution"
SEAT_BLENDER_EXECUTION = "seat-blender-execution"
SEAT_3DM_INSPECTION = "seat-3dm-inspection"
RUNNER_RUN_FAILURE = "runner-run-failure"
RUNNER_RUN_RECEIPT = "runner-run-receipt"
STAGE_GEOMETRY_PROGRAM = "<identifier>-geometry-program"

# ---- the drawing consumer's kind (archflow/runtime/drawing_elevation.py)

DRAWING_PROJECTION_RECEIPT = "drawing-projection-receipt"

# ---- the geometry proposal producer's kinds (archflow/capabilities)

GEOMETRY_PROPOSAL_ROUND = "geometry-proposal-round-NN"
GEOMETRY_PROPOSAL_COMPLETION = "geometry-proposal-completion-NN"
GEOMETRY_PROGRAM_PROPOSAL = "geometry-program-proposal"
GEOMETRY_PROPOSAL_DEFERRAL = "geometry-proposal-deferral"
GEOMETRY_PROPOSAL_ESCALATION = "geometry-proposal-escalation"
GEOMETRY_PROPOSAL_LINEAGE = "geometry-proposal-lineage"

# ---- the tools' kinds (tools/)

PROJECT_STAGE_WORKFLOW = "project-stage-workflow"
PROJECT_STAGE_WORKFLOW_FREEZE_RECEIPT = "project-stage-workflow-freeze-receipt"
EQUIVALENCE_HARNESS_WORKFLOW = "equivalence-harness-workflow"
EQUIVALENCE_HARNESS_ENVELOPE = "equivalence-harness-envelope"
STATE_RECORD_EQUIVALENCE = "state-record-equivalence"

# ---- the Studio's kinds (apps/archflow-studio/api)

STUDIO_CANDIDATE_WORKFLOW = "studio-candidate-workflow"
STUDIO_CANDIDATE_ENVELOPE = "studio-candidate-envelope"
INTENT_COMPILATION = "intent-compilation"
DELIBERATION_EPISODE = "deliberation-episode"
STUDIO_SOURCE_DOCUMENT = "studio-source-document"
STUDIO_DOCUMENT_ANNOTATIONS = "studio-document-annotations"
STUDIO_DOCUMENT_COMMENT = "studio-document-comment"
STUDIO_MODEL_ASSET = "studio-model-asset"
STUDIO_DOCUMENT_MODEL_SOURCE = "studio-document-model-source"
STUDIO_WORKING_COPY = "studio-working-copy"
STUDIO_MODEL_ANNOTATIONS = "studio-model-annotations"
DESIGN_STAGE = "design-stage"
STUDIO_CANDIDATE_DELTA = "studio-candidate-delta"
STUDIO_BOARD_SCENE = "studio-board-scene"
AUDIT_EVENT = "audit-event"

# ---- read by the spine, written by nobody on it

COMPONENT_TEMPLATE = "component-template"
CATALOG_CONFRONTATION = "catalog-confrontation"
COMPONENT_CATALOG = "component-catalog"

# ---- the issue's kind (archflow/project/issue.py)

PROMOTION_DECISION = "promotion-decision"

# ---- the stage ladder's own records (ADR-007)

STAGE_RUN_ENVELOPE = "stage-run-envelope"
STAGE_EXIT_BINDING = "stage-exit-binding"
STAGE_CLOSURE = "stage-closure"

# ---- reserved: named here before the module that will write them exists

RESEARCH_EVIDENCE_LEDGER = "research-evidence-ledger"


_RUN_RECORD = PersistenceArea.RUN_RECORD.value
_RUN_BRANCH = PersistenceArea.RUN_BRANCH.value


_TABLE: tuple[RecordKind, ...] = (
    RecordKind(
        STUDIO_BOARD_SCENE,
        "StudioBoardScene@1",
        _RUN_RECORD,
        "Single-operator whiteboard scene with exact registered document-page sources; retained in its studio-board run, separate from design Stages.",
    ),
    RecordKind(
        DESIGN_STAGE,
        "DesignStage@1",
        PersistenceArea.RUN_REVIEW.value,
        "an explicitly accepted immutable design state and its parent, retained in the actual model run; not formal issue",
    ),
    RecordKind(
        AUDIT_EVENT,
        "AuditEvent@1",
        PersistenceArea.RUN_REVIEW.value,
        "who authorized one authoritative retained decision and through which "
        "surface, bound to the exact base, subject and result the decision "
        "already retains; the decision and its result stay in their own "
        "records, and this adds no authority and replays nothing",
    ),
    RecordKind(
        STUDIO_CANDIDATE_DELTA,
        "StudioCandidateDelta@1",
        _RUN_RECORD,
        "the exact source and typed operator executed by a candidate; generation does not accept the result",
    ),
    RecordKind(
        STUDIO_WORKING_COPY,
        "StudioWorkingCopy@1",
        _RUN_RECORD,
        "one local work item's explicit model options and selected option, retained in its common-base run",
    ),
    RecordKind(
        STUDIO_MODEL_ANNOTATIONS,
        "StudioModelAnnotations@1",
        _RUN_RECORD,
        "one exact model source's saved 3D annotations and previous revision",
    ),
    RecordKind(
        STUDIO_DOCUMENT_MODEL_SOURCE,
        "StudioDocumentModelSource@1",
        _RUN_RECORD,
        "an explicit one-time model association for an existing unbound source document",
    ),
    RecordKind(
        STUDIO_MODEL_ASSET,
        "StudioModelAsset@1",
        _RUN_RECORD,
        "an existing composed model retained with its explicit exact run state binding",
    ),
    RecordKind(
        STUDIO_SOURCE_DOCUMENT,
        "StudioSourceDocument@1",
        _RUN_RECORD,
        "an imported PDF or image, bound to its original bytes in the project object store",
    ),
    RecordKind(
        STUDIO_DOCUMENT_ANNOTATIONS,
        "StudioDocumentAnnotations@1",
        _RUN_RECORD,
        "one saved page annotation revision, bound to an exact source document and its previous revision",
    ),
    RecordKind(
        STUDIO_DOCUMENT_COMMENT,
        "StudioDocumentComment@1",
        _RUN_RECORD,
        "the architect's submitted words and exact saved document annotation references",
    ),
    RecordKind(
        STATE_RECORD,
        "StateRecord@1",
        _RUN_RECORD,
        "the authored record the run actually executed, bound to this run",
    ),
    RecordKind(
        PROJECT_LEVELS,
        "ProjectLevels@1",
        _RUN_RECORD,
        "the published level datums the record declared",
    ),
    RecordKind(
        PROJECT_GRIDS,
        "ProjectGrids@1",
        _RUN_RECORD,
        "the published grid axes the record declared, when it declares any",
    ),
    RecordKind(
        SELECTED_SPATIAL_OPTION,
        "SpatialOptionProposal@2",
        _RUN_RECORD,
        "the component tree the record's selected option resolves to",
    ),
    RecordKind(
        DEVELOPED_DESIGN_STATE,
        "DevelopedDesignState@1",
        _RUN_RECORD,
        "the developed state the run computed from the record",
    ),
    RecordKind(
        DISCIPLINE_SEAT,
        "SeatSpec@1",
        _RUN_RECORD,
        "one discipline seat as the run received it",
    ),
    RecordKind(
        SEAT_AUTHORING_CONTEXT,
        "SeatAuthoringContext@1",
        _RUN_RECORD,
        "what one seat was given to author against in one round",
    ),
    RecordKind(
        SEAT_GEOMETRY_PROGRAM,
        "CompiledGeometryProgram@3",
        _RUN_RECORD,
        "the compiled program one seat's proposal produced",
    ),
    RecordKind(
        SEAT_RELATION_CHECK,
        "RelationCheckReport@1",
        _RUN_RECORD,
        "the declared relations measured against one seat's compiled bounds",
    ),
    RecordKind(
        SEAT_ROUND_RECEIPT,
        "SeatRoundReceipt@1",
        _RUN_RECORD,
        "what one seat did in one round, and what answered for it",
    ),
    RecordKind(
        SEAT_HANDOVER,
        "SeatHandover@1",
        _RUN_RECORD,
        "the datums and realized bounds one seat hands to its consumers",
    ),
    RecordKind(
        SEAT_RHINO_EXECUTION,
        "RhinoCadExecutionReceipt@4",
        _RUN_RECORD,
        "one supervised Rhino export and its independent readback",
    ),
    RecordKind(
        SEAT_OCCT_EXECUTION,
        "OcctExecutionReceipt@1",
        _RUN_RECORD,
        "one in-process OCCT export: the exact STEP file and the mesh .3dm "
        "preview it wrote into the stage workspace, bound to the exact "
        "run/base/branch/program, with the cold readback of the STEP file",
    ),
    RecordKind(
        SEAT_BLENDER_EXECUTION,
        "BlenderExecutionReceipt@1",
        _RUN_RECORD,
        "one Blender scene save and fresh-process mesh/semantic/binding readback in the caller's speculative workspace",
    ),
    RecordKind(
        SEAT_3DM_INSPECTION,
        "ThreeDmInspectionSummary@4",
        _RUN_RECORD,
        "what reading the exported 3dm file back found in it",
    ),
    RecordKind(
        RUNNER_RUN_FAILURE,
        "RunnerRunFailure@1",
        _RUN_RECORD,
        "why a run stopped; the records it already wrote stay",
    ),
    RecordKind(
        RUNNER_RUN_RECEIPT,
        "RunnerRunReceipt@3",
        _RUN_RECORD,
        "the one receipt of a completed run, naming every record it wrote",
    ),
    RecordKind(
        STAGE_GEOMETRY_PROGRAM,
        "CompiledGeometryProgram@3",
        _RUN_BRANCH,
        "the program an export is bound to, on the branch, named by its stage",
        kind_pattern=r"[A-Za-z0-9][A-Za-z0-9._-]*-geometry-program",
    ),
    RecordKind(
        DRAWING_PROJECTION_RECEIPT,
        "DrawingProjectionReceipt@1",
        _RUN_RECORD,
        "one orthographic drawing derived from a retained exact STEP: the "
        "source run/base/STEP/CAD receipt/object ids and frame it was "
        "projected from, and the SVG and PNG it wrote into the drawing run's "
        "documentation workspace",
    ),
    RecordKind(
        GEOMETRY_PROPOSAL_ROUND,
        "GeometryProposalRoundReceipt@2",
        _RUN_RECORD,
        "one authoring round: the request, what answered, the issues left",
        kind_pattern=r"geometry-proposal-round-\d{2}",
    ),
    RecordKind(
        GEOMETRY_PROPOSAL_COMPLETION,
        "ProtocolCompletion@1",
        _RUN_RECORD,
        "derivable bookkeeping filled from records instead of asked for again",
        kind_pattern=r"geometry-proposal-completion-\d{2}",
    ),
    RecordKind(
        GEOMETRY_PROGRAM_PROPOSAL,
        "GeometryProgramProposalRecord@1",
        _RUN_RECORD,
        "the accepted proposal, with the round that accepted it",
    ),
    RecordKind(
        GEOMETRY_PROPOSAL_DEFERRAL,
        "PartialAcceptanceDeferral@1",
        _RUN_RECORD,
        "which objects a partial acceptance dropped or restored, and why",
    ),
    RecordKind(
        GEOMETRY_PROPOSAL_ESCALATION,
        "GeometryProposalEscalation@1",
        _RUN_RECORD,
        "a stall handed over with its exact issue list and last proposal",
    ),
    RecordKind(
        GEOMETRY_PROPOSAL_LINEAGE,
        "GeometryProposalLineage@2",
        _RUN_RECORD,
        "every round of one proposal production, and what it ended as",
    ),
    RecordKind(
        PROJECT_STAGE_WORKFLOW,
        "ProjectStageWorkflow@1",
        _RUN_RECORD,
        "the project's own frozen stage ladder; freezing accepts no stage",
    ),
    RecordKind(
        PROJECT_STAGE_WORKFLOW_FREEZE_RECEIPT,
        "ProjectStageWorkflowFreezeReceipt@1",
        _RUN_RECORD,
        "proof that freezing a workflow issued no new published design",
    ),
    RecordKind(
        EQUIVALENCE_HARNESS_WORKFLOW,
        "ProjectStageWorkflow@1",
        _RUN_RECORD,
        "the equivalence harness's own workflow; it closes no project stage",
    ),
    RecordKind(
        EQUIVALENCE_HARNESS_ENVELOPE,
        "StageRunEnvelope@1",
        _RUN_RECORD,
        "the harness stage the equivalence run executed under",
    ),
    RecordKind(
        STATE_RECORD_EQUIVALENCE,
        "StateRecordEquivalence@1",
        _RUN_RECORD,
        "state identity and geometry compared against a reference run",
    ),
    RecordKind(
        STUDIO_CANDIDATE_WORKFLOW,
        "ProjectStageWorkflow@1",
        _RUN_RECORD,
        "the Studio candidate harness's workflow; it closes no project stage",
    ),
    RecordKind(
        STUDIO_CANDIDATE_ENVELOPE,
        "StageRunEnvelope@1",
        _RUN_RECORD,
        "the harness stage a Studio candidate run executed under",
    ),
    RecordKind(
        INTENT_COMPILATION,
        "IntentCompilation@1",
        _RUN_RECORD,
        "the model call that turned an utterance into this run's proposal",
    ),
    RecordKind(
        DELIBERATION_EPISODE,
        "DeliberationEpisode@1",
        _RUN_RECORD,
        "one judgement of the studio: the intent, the proposals on the table, "
        "the decision on each with its reason and scope, what was protected, "
        "the evidence and validation refs read, and the run it produced; "
        "written by the studio at accept/reject/modify",
    ),
    RecordKind(
        COMPONENT_TEMPLATE,
        "ComponentTemplate@1",
        _RUN_RECORD,
        "read by capabilities.geometry_proposal through template_refs; the "
        "library that harvests one was archived, so no spine module writes one",
    ),
    RecordKind(
        CATALOG_CONFRONTATION,
        "CatalogConfrontationReceipt@1",
        _RUN_RECORD,
        "which templates a catalog confrontation selected; capabilities."
        "geometry_proposal checks this schema by name, and writes none",
    ),
    RecordKind(
        COMPONENT_CATALOG,
        "ComponentCatalog@1",
        _RUN_RECORD,
        "what re-indexing an exported model against the record found: object "
        "-> component -> element drafts with residuals, coverage per component, "
        "ambiguities; written by tools/reindex_project.py through the repository",
    ),
    RecordKind(
        STAGE_RUN_ENVELOPE,
        "StageRunEnvelope@1",
        _RUN_RECORD,
        "the project's own stage a run opened against, written by "
        "tools/open_stage_run.py; the two harnesses write their own under "
        "their own kinds",
    ),
    RecordKind(
        STAGE_EXIT_BINDING,
        "StageExitBinding@1",
        _RUN_RECORD,
        "the SATISFIED exit a successor stage cites, derived by the runner "
        "from the closure it just wrote",
    ),
    RecordKind(
        STAGE_CLOSURE,
        "CompositeStageClosureReceipt@1",
        _RUN_RECORD,
        "the closure the runner writes from its own checks: SATISFIED, or the "
        "findings saying why the stage did not close",
    ),
    RecordKind(
        PROMOTION_DECISION,
        "PromotionDecision@1",
        PersistenceArea.RUN_REVIEW.value,
        # The kind and the schema keep their names: prepare_transition compares
        # the payload's key set literally and retained receipts bind it
        # (ADR-004). The act it gates is an issue (ADR-007).
        "the accepted exact-base decision prepare_transition demands before a "
        "run is issued as the published design; project.issue mints one",
    ),
    RecordKind(
        RESEARCH_EVIDENCE_LEDGER,
        "EvidenceLedger@1",
        _RUN_RECORD,
        "reserved (the research bridge): one entry per evidence id a record "
        "may cite; nothing writes it yet",
    ),
)


RECORD_KINDS: Mapping[str, RecordKind] = MappingProxyType(
    {entry.kind: entry for entry in _TABLE}
)

_PATTERN_KINDS: tuple[RecordKind, ...] = tuple(
    entry for entry in _TABLE if entry.kind_pattern is not None
)


def geometry_proposal_round(round_index: int) -> str:
    """The kind of one authoring round's receipt."""

    return f"geometry-proposal-round-{round_index:02d}"


def geometry_proposal_completion(round_index: int) -> str:
    """The kind of one round's protocol-completion summary."""

    return f"geometry-proposal-completion-{round_index:02d}"


def stage_geometry_program(stage_id: str) -> str:
    """The kind of the branch-retained program an export is bound to."""

    return f"{stage_id}-geometry-program"


def is_registered(kind: str) -> bool:
    """Whether this table holds the kind, exactly or by pattern."""

    if not isinstance(kind, str):
        return False
    entry = RECORD_KINDS.get(kind)
    if entry is not None and entry.kind_pattern is None:
        return True
    return any(candidate.matches(kind) for candidate in _PATTERN_KINDS)


def require_registered(kind: str) -> RecordKind:
    """The table entry for a kind, or a refusal naming the kind and the table."""

    entry = RECORD_KINDS.get(kind)
    if entry is not None and entry.kind_pattern is None:
        return entry
    for candidate in _PATTERN_KINDS:
        if candidate.matches(kind):
            return candidate
    raise ValueError(
        f"record kind {kind!r} is not in the retained record-kind table "
        "(archflow.project.record_kinds.RECORD_KINDS); register it there with "
        "its schema and what it is for, or write a kind the table already holds"
    )
