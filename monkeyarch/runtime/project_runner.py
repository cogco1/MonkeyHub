"""Record-driven project runner (P089 / P102).

A project enters as one ``StateRecord@1`` plus the discipline seats:

* the record's ``Component@1`` tree with its ``MassingLevel@1`` / ``Volume@1``
  / ``Space@1`` / ``Connection@1`` entities and its declared ``option``
  become a real ``SpatialOptionProposal`` / ``SchematicOption`` /
  ``SelectedSchematicInput`` / ``DevelopedDesignState`` through the state
  dataclasses' own validation; no test fixture, no building literal.
* its ``Level@1`` / ``GridAxis@1`` entities are the published project datums
  (P098), and its ``Element@1`` entities are the rows the canonical
  reference-reading producers take (``element_producers``): walls with hosted
  openings, prisms, ring walls, lofts, column arrays, dome caps, capitals,
  beams, pediments, and typed declinations. Heights are differences of
  project levels or the element's own dimensions, never a restated
  elevation; a plan position is a grid, axis or host reference.
* ``SeatPack@1`` — the discipline seats (P095) and the identity a live
  provider would have to present — stays a separate input: seats are people,
  not state.  While the runner records proposals nothing invokes that
  provider, so its identity is carried as ``declared_live_identity`` and the
  receipts name ``RECORDED_PROPOSAL_IDENTITY`` as what actually answered.

The runner owns orchestration only: seat rounds from ``schedule_seats``,
producers in reference order, one proposal per seat through the real
producer, coverage and datum gates, relation checks against the compiled
bounds (each relation measured once, in the seat where the last input its
declared checker reads appears - endpoint extent, or the named datum a
``support_contact`` check compares against - on compiler-predicted boxes,
never on a saved CAD box; what no seat can measure is retained as
unchecked), final solid-pair checks on verified OCCT STEP readback after
all seats export, handovers to consuming
seats (published datums and realized bounds as exclusions), optional CAD
export - in process through OCCT by default (exact STEP plus a mesh ``.3dm``
preview, retained as ``seat-occt-execution``), or through Rhino when the
caller names that backend (reuse, restamp, patch or rebuild, P103) - and
receipts with per-seat wall time.  It will not run without a retained
``ProjectStageWorkflow@1`` and exact ``StageRunEnvelope@1``; the record is
projected in the phase that envelope states (P112).

At the end of the run the runner closes the stage, because it is the only
thing that measured anything (ADR-007 rule 3): a
``CompositeStageClosureReceipt`` compiled from the run's own relation checks
and seat results, retained as ``stage-closure``, and — only when that closure
carries no finding — the ``StageExitBinding`` a successor stage may open
against, retained as ``stage-exit-binding``.  A successful seat proposal is
still never stage acceptance: the close obligation the envelope retained stays
OPEN, the closure is a separate statement about the checks the stage required,
and every record written here remains authority-free.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from archflow.adapters.cad_program import expected_object_bounds
from archflow.adapters.cad_backend import (
    CadExecutionError, CadExecutionRequest, CadExecutionSource, CadProgramBinding,
    cad_backend_ids, get_cad_backend,
)
from monkeyarch.capabilities.discipline_seats import (
    SeatSpec,
    check_seat_datums,
    compile_handover,
    owned_subtree,
    project_seat_context,
    schedule_seats,
)
from monkeyarch.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    load_compiled_geometry_program,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from monkeyarch.capabilities.element_producers import ElementProducerError, ElementRow, ProducedElement, ProducedRelation, ProductionContext, element_rows_of, produce_rows
from monkeyarch.capabilities.reference_resolver import HostLine, ReferenceContext
from monkeyarch.capabilities.relation_checks import RelationCheck, RelationCheckReport, check_relations
from archflow.contracts.authority import no_authority
from archflow.contracts.canonical import canonical_digest, canonical_json
from archflow.ports.model import ModelInvocationReceipt, ModelInvocationStatus
from archflow.project.record_kinds import (
    DEVELOPED_DESIGN_STATE,
    DISCIPLINE_SEAT,
    PROJECT_GRIDS,
    PROJECT_LEVELS,
    RUNNER_RUN_FAILURE,
    RUNNER_RUN_RECEIPT,
    SEAT_3DM_INSPECTION,
    SEAT_AUTHORING_CONTEXT,
    SEAT_GEOMETRY_PROGRAM,
    SEAT_HANDOVER,
    SEAT_OCCT_EXECUTION,
    SEAT_RELATION_CHECK,
    SEAT_ROUND_RECEIPT,
    SELECTED_SPATIAL_OPTION,
    STAGE_CLOSURE,
    STAGE_EXIT_BINDING,
    STATE_RECORD,
    stage_geometry_program,
)
from archflow.project.layout import cad_workspace_path
from archflow.project.repository import FilesystemProjectRepository, ProjectIntegrityError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef, record_ref_from_uri
from archflow.state.stage_workflow import (
    CompositeStageClosureReceipt,
    StageClosureFinding,
    StageClosureFindingCode,
    StageClosureStatus,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.design_portfolio import BranchRevisionRef
from archflow.state.developed_design import (
    DevelopedDesignError,
    DevelopedDesignState,
    DevelopmentCoordinationStatus,
    SelectedSchematicInput,
)
from archflow.state.geometry_program import (
    AffineTransform,
    CompiledGeometryProgram,
    CoordinateFrame,
    DatumBinding,
    GeometryOperation,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    InterfaceDatum,
    LengthUnit,
    ProjectGrids,
    ProjectLevels,
    SemanticBinding,
)
from archflow.state.spatial import SiteBounds
from archflow.state.state_record import Relation, SchematicPack, StateRecord, ValidatorBinding, bootstrap_developed_state, developed_design_view, project_grids_of, project_levels_of, volume_boxes_of
from archflow.state.stage_workflow import (
    HARNESS_WORKFLOW_IDS,
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
    require_stage_exit_binding,
    require_stage_run_envelope,
)
from archflow.state.spatial import (
    DesignComponent,
    MassingVolume,
    SchematicOption,
    SpatialConnection,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)

_AUTH = ("canonical_write_authority", "design_authority", "stage_acceptance_authority")
_M = LengthUnit.METER
FRAME_ID = "building-local"
# Historical imports remain available; selection uses the CAD owner's registry.
CAD_BACKEND_OCCT = "occt"
CAD_BACKEND_RHINO = "rhino"
CAD_BACKENDS = cad_backend_ids()
# What every relation check in a run is measured on. ``expected_object_bounds`` predicts an
# axis-aligned box per compiled object from the program itself; no exported solid and no saved
# CAD bounding box is consulted. A held check therefore says the compiled prediction satisfies
# the declared condition, not that a produced solid does. The label travels on every retained
# ``seat-relation-check`` record and on the run receipt so a reader never has to infer it.
RELATION_CHECK_BASIS = "compiled-predicted-bounds"
SOLID_RELATION_CHECK_BASIS = "occt-step-solid-pairs"
# The checkers that read a relation's ``datum_role`` out of ``datum_values`` (``relation_checks``):
# ``check_support_contact`` compares the measured face with the named datum, the other two never
# look at it. A relation bound to one of these is not ready to measure until that datum has been
# published - a level the record declares, or a ``<element>-top`` some seat's producer publishes -
# because measuring it earlier would silently skip the comparison and never return to it.
_DATUM_READING_CHECKS = frozenset({"support_contact"})


class ProjectRunnerError(ValueError):
    """Typed failure of the runner's contracts."""




# ---------------------------------------------------------------- producers: the canonical reference-reading set
# The runner's private ElementSpec/ElementPack/ProducerInputs/PRODUCERS were retired on
# 2026-09-02 into ``archflow.capabilities.element_producers``; the runner reads Element@1
# rows off the State Record and calls ``produce_rows`` in production order.


# ---------------------------------------------------------------- provider: the proposal, recorded
# No model is invoked here.  The deterministic element producers build the proposal and
# ``RecordedProposalProvider`` hands it back as a receipt, so the identity on that receipt
# names the producers and nothing else.  A seat pack's declared ``provider_identity`` is a
# live provider's identity; stamping it on a fabricated receipt would make every retained
# ``geometry-proposal-round`` record claim a model call that never happened.
RECORDED_PROPOSAL_IDENTITY = GeometryProposalProviderIdentity(
    provider_id="runner-recorded-proposal",
    model_id="element-producers",
    provider_version="1",
    provider_fingerprint=canonical_digest({
        "provider_id": "runner-recorded-proposal",
        "model_id": "element-producers",
        "provider_version": "1",
    }),
)


class RecordedProposalProvider:
    """Returns one authored proposal as a provider receipt; nothing is invented."""

    def __init__(self, output: Mapping[str, object]) -> None:
        self._output = output
        self.identity = RECORDED_PROPOSAL_IDENTITY
        self.requests: list = []

    async def invoke(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            return ModelInvocationReceipt(
                receipt_id=f"runner-receipt-{len(self.requests):02d}", status=ModelInvocationStatus.BUDGET_EXHAUSTED,
                request=request, provider_id=self.identity.provider_id, model_id=self.identity.model_id, provider_version=self.identity.provider_version,
                provider_fingerprint=self.identity.provider_fingerprint, input_bytes=len(request.payload_json.encode("utf-8")), output_bytes=0,
                output_sha256=None, output_json=None, error_code="runner_single_round", message="the runner authors one proposal per seat; repair rounds are recorded, not improvised")
        encoded = canonical_json(self._output)
        return ModelInvocationReceipt(
            receipt_id="runner-receipt-01", status=ModelInvocationStatus.SUCCESS, request=request, provider_id=self.identity.provider_id,
            model_id=self.identity.model_id, provider_version=self.identity.provider_version, provider_fingerprint=self.identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")), output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(), output_json=encoded)


# ---------------------------------------------------------------- the run
@dataclass(frozen=True, slots=True)
class SeatResult:
    seat_id: str
    round_index: int
    status: str
    program_ref: str | None
    program_digest: str | None
    objects: int
    covered_components: tuple[str, ...]
    undeclared_components: tuple[str, ...]
    issues: tuple[dict, ...]
    wall_time_s: float
    cad: dict | None = None
    receipt_ref: str | None = None
    declined: tuple[str, ...] = ()
    declination_reasons: Mapping[str, str] = field(default_factory=dict)
    relation_check_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RunOptions:
    commitment_ref: str
    # The identity a live provider must present; unused while the runner records
    # proposals; None means no live provider is declared.
    live_provider_identity: GeometryProposalProviderIdentity | None = None
    portfolio_id: str = "declared-schematic"
    branch_id: str = "runner-v1"
    branch_epoch: int = 1
    selection_decision_ref: str = "decision:declared-schematic-selection"
    strict_coverage: bool = True
    export: bool = False
    # Which executor an enabled export goes to. OCCT unless the caller says
    # ``rhino``. ``powershell`` is Rhino's launcher and is carried unread by OCCT;
    # ``patch_oracle`` is Rhino's patch check and is refused under any other backend.
    cad_backend: str = CAD_BACKEND_OCCT
    workspace_root: Path | None = None
    cad_backend_options: Mapping[str, object] = field(default_factory=dict)
    powershell: Path | None = None
    patch_oracle: bool = False
    # An exact retained source, chosen by the caller before this run starts.
    # None keeps the first-build and legacy callers on the full path.
    source_run_receipt_ref: ProjectRecordRef | None = None

    def __post_init__(self) -> None:
        if self.source_run_receipt_ref is not None and not isinstance(self.source_run_receipt_ref, ProjectRecordRef):
            raise TypeError("source_run_receipt_ref must be ProjectRecordRef")
        try:
            get_cad_backend(self.cad_backend).validate_options(self.execution_options())
        except CadExecutionError as exc:
            raise ProjectRunnerError(str(exc)) from exc

    def execution_options(self) -> dict:
        options = dict(self.cad_backend_options)
        if self.powershell is not None:
            options.setdefault("powershell_executable", self.powershell)
        if self.patch_oracle:
            options["patch_oracle"] = True
        return options



@dataclass(frozen=True)
class _SourceSeat:
    run: RunRef
    program: CompiledGeometryProgram
    rows: Mapping[str, ElementRow]
    elements: Mapping[str, Mapping[str, Any]]
    references: str
    producer_code: str | None
    cad: Mapping[str, Any] | None


def _reference_inputs(levels, grids) -> str:
    return canonical_json({"levels": levels.to_dict(), "grids": grids.to_dict() if grids else None})


def _source_seats(repository, run: RunRef, ref: ProjectRecordRef | None) -> dict[str, _SourceSeat]:
    if ref is None:
        return {}
    if ref.project_id != run.project_id or ref.record_kind != RUNNER_RUN_RECEIPT:
        raise ProjectRunnerError("source must name this project's retained runner receipt")
    receipt = repository.load_json(ref)
    source_run = repository.load_run(receipt["run_id"])
    prefix = f"runs/{source_run.run_id}/records/"
    if receipt.get("project_id") != run.project_id or not ref.relative_path.startswith(prefix):
        raise ProjectRunnerError("source runner receipt belongs to another run")
    record_ref = record_ref_from_uri(receipt["state_record_ref"], run.project_id)
    if record_ref.record_kind != STATE_RECORD or not record_ref.relative_path.startswith(prefix):
        raise ProjectRunnerError("source state record belongs to another run")
    record = StateRecord.from_dict(repository.load_json(record_ref))
    if (record.project_id != run.project_id or record.run_id != source_run.run_id or record.base != source_run.base
            or record.digest != receipt.get("state_record_digest")):
        raise ProjectRunnerError("source state record does not match its retained run receipt")
    rows = {row.element_id: row for row in element_rows_of(record)}
    references = _reference_inputs(project_levels_of(record), project_grids_of(record))
    found = {}
    for seat in receipt.get("seat_results", ()):
        if seat.get("status") != "proposal_accepted" or not seat.get("program_ref") or not seat.get("receipt_ref"):
            continue
        program_ref = record_ref_from_uri(seat["program_ref"], run.project_id)
        round_ref = record_ref_from_uri(seat["receipt_ref"], run.project_id)
        if (program_ref.record_kind != SEAT_GEOMETRY_PROGRAM or round_ref.record_kind != SEAT_ROUND_RECEIPT
                or not program_ref.relative_path.startswith(prefix) or not round_ref.relative_path.startswith(prefix)):
            raise ProjectRunnerError("source seat records belong to another run")
        program = load_compiled_geometry_program(repository.load_json(program_ref))
        round_receipt = repository.load_json(round_ref)
        if (program.program_digest != seat["program_digest"] or program.proposal.run_id != source_run.run_id
                or program.proposal.base != source_run.base or round_receipt.get("program_ref") != program_ref.uri
                or round_receipt.get("seat_id") != seat["seat_id"]):
            raise ProjectRunnerError("source seat program does not match its run receipt")
        elements = round_receipt.get("element_results", {})
        if not isinstance(elements, dict):
            raise ProjectRunnerError("source element results must be a mapping")
        found[seat["seat_id"]] = _SourceSeat(source_run, program, rows, elements, references,
                                            round_receipt.get("producer_code"), seat.get("cad"))
    return found


def _producer_code() -> str | None:
    """The implementations whose retained producer outputs may be reused."""
    import inspect
    from monkeyarch.capabilities import element_producers, reference_resolver, opening_solver, wall_solver

    try:
        return canonical_digest({
            "modules": {module.__name__: inspect.getsource(module) for module in
                        (element_producers, reference_resolver, opening_solver, wall_solver)},
            "producers": {name: inspect.getsource(producer) for name, producer in element_producers.PRODUCERS.items()},
        })
    except (OSError, TypeError):
        return None


def _production_inputs(row: ElementRow, context: ProductionContext) -> dict[str, Any]:
    def names(value):
        if isinstance(value, str):
            return {value, f"{value}-top"}
        if isinstance(value, Mapping):
            return set().union(*(names(item) for item in value.values()))
        if isinstance(value, (list, tuple)):
            return set().union(*(names(item) for item in value))
        return set()

    mentioned = names(row.references) | names(row.params)
    return {
        "frame_id": context.frame_id,
        "datums": {key: context.published[key].to_dict() for key in sorted(mentioned & context.published.keys())},
        "hosts": {key: {"origin": list(context.references.hosts[key].origin), "direction": list(context.references.hosts[key].direction)}
                  for key in sorted(mentioned & context.references.hosts.keys())},
        "exclusions": [[list(low), list(high)] for low, high in context.exclusions]
                      if row.producer == "wall" and row.params.get("respect_exclusions", True) else [],
    }


def _element_result(element: ProducedElement, inputs: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "inputs": inputs,
        "operations": [operation.op_id for operation in element.operations],
        "bindings": [binding.binding_id for binding in element.bindings],
        "datums": [datum.datum_id for datum in element.datums],
        "assemblies": [assembly.assembly_id for assembly in element.assemblies],
        "relations": [relation.to_dict() for relation in element.relations],
        "host_line": ({"origin": list(element.host_line.origin), "direction": list(element.host_line.direction)}
                      if element.host_line is not None else None),
    }


def _reload_element(source: _SourceSeat, value: Mapping[str, Any]) -> ProducedElement:
    program = source.program
    operations = {operation.op_id: operation for operation in program.proposal.operations}
    bindings = {binding.binding_id: binding for binding in program.datum_bindings}
    datums = {datum.datum_id: datum for datum in program.interface_datums}
    assemblies = {assembly.assembly_id: assembly for assembly in program.proposal.assemblies}
    selected_bindings = tuple(bindings[key] for key in value["bindings"])
    bound_parameters = {(binding.op_id, binding.parameter_name) for binding in selected_bindings}
    # The compiled program stores resolved literals. Restore their symbolic
    # bindings before compiling against this run; never restate a datum twice.
    selected_operations = tuple(replace(operations[key], parameters=tuple(
        parameter for parameter in operations[key].parameters if (key, parameter.name) not in bound_parameters
    )) for key in value["operations"])
    host = value.get("host_line")
    return ProducedElement(
        operations=selected_operations, bindings=selected_bindings,
        datums=tuple(datums[key] for key in value["datums"]),
        relations=tuple(ProducedRelation(**relation) for relation in value["relations"]),
        host_line=HostLine(tuple(host["origin"]), tuple(host["direction"])) if host else None,
        assemblies=tuple(assemblies[key] for key in value["assemblies"]),
    )


def _produce_incrementally(rows, context, source, references: str, producer_code: str | None,
                           *, operation_observer=None, input_identity=None, source_ref=None):
    results, retained, reused = [], {}, []
    details = {
        "scope": "producer_elements", "input_object_ids": [row.element_id for row in rows],
        "recomputed_object_ids": [], "reused_object_ids": reused, "emitted_object_ids": [],
        "cache_checks": {}, "cache_status": "unknown", "executed_stages": [],
        **({"input_identity": input_identity} if input_identity else {}),
    }
    reasons = set()
    with _observe_runner_operation(operation_observer, "element_production", details=details) as span:
        if source_ref is not None:
            span["source_ref"] = source_ref
        for row in rows:
            inputs = _production_inputs(row, context)
            previous = source.elements.get(row.element_id) if source is not None else None
            checks = {
                "retained_result": "same" if previous is not None else "missing",
                "producer_code": "missing" if producer_code is None or source is None or source.producer_code is None
                    else "same" if producer_code == source.producer_code else "changed",
                "references": "missing" if source is None else "same" if references == source.references else "changed",
                "element_row": "missing" if source is None or row.element_id not in source.rows
                    else "same" if row == source.rows[row.element_id] else "changed",
                "dependency_inputs": "missing" if previous is None or "inputs" not in previous
                    else "same" if inputs == previous["inputs"] else "changed",
            }
            details["cache_checks"].update({f"{row.element_id}.{key}": value for key, value in checks.items()})
            can_reuse = all(value == "same" for value in checks.values())
            if can_reuse:
                element = _reload_element(source, previous)
                context.published.update({datum.datum_id: datum for datum in element.datums})
                if element.host_line is not None:
                    context.references.hosts[row.element_id] = element.host_line
                reused.append(row.element_id)
                reasons.add("unchanged_inputs")
            else:
                reason = next(key + "_" + value for key, value in checks.items() if value != "same")
                reasons.add("source_not_selected" if source is None else reason)
                details.update(cache_status="partial" if reused else "miss",
                               cache_reason="source_not_selected" if source is None else reason)
                details["executed_stages"] = ["produce_rows"]
                element, = produce_rows((row,), context)
                details["recomputed_object_ids"].append(row.element_id)
            results.append(element)
            retained[row.element_id] = _element_result(element, inputs)
            details["emitted_object_ids"].append(row.element_id)
        details["cache_status"] = "hit" if reused and len(reused) == len(rows) else "partial" if reused else "miss"
        details["cache_reason"] = next(iter(reasons)) if len(reasons) == 1 else "mixed_element_inputs"
        if source is not None and all(value != "missing" for value in details["cache_checks"].values()):
            details["input_equivalent"] = all(value == "same" for value in details["cache_checks"].values())
    return tuple(results), retained, tuple(reused)


@dataclass(frozen=True, slots=True)
class StageExecutionGuard:
    """Verified retained inputs required before any seat record is written.

    Stage zero needs only the workflow and its own envelope.  A successor
    additionally needs the exact predecessor envelope, its retained
    ``StageExitBinding@1`` and the independently compiled SATISFIED closure
    receipt named by that binding.  Passing equivalent-looking in-memory
    payloads without their P036 refs is intentionally insufficient.
    """

    workflow: ProjectStageWorkflow
    workflow_record_ref: ProjectRecordRef
    envelope: StageRunEnvelope
    envelope_record_ref: ProjectRecordRef
    predecessor: StageRunEnvelope | None = None
    predecessor_record_ref: ProjectRecordRef | None = None
    predecessor_exit: StageExitBinding | None = None
    predecessor_exit_record_ref: ProjectRecordRef | None = None
    predecessor_closure: CompositeStageClosureReceipt | None = None
    predecessor_closure_record_ref: ProjectRecordRef | None = None

    def require(
        self,
        repository: FilesystemProjectRepository,
        *,
        run: RunRef,
        branch: BranchRef,
        state: DevelopedDesignState,
        seats: tuple[SeatSpec, ...],
    ) -> None:
        def retained(ref: ProjectRecordRef, payload: Mapping[str, object], label: str) -> None:
            if ref.project_id != run.project_id:
                raise ProjectRunnerError(f"{label} belongs to another project")
            try:
                actual = repository.load_json(ref)
            except Exception as exc:
                raise ProjectRunnerError(f"{label} is not an exact retained P036 record") from exc
            if actual != dict(payload):
                raise ProjectRunnerError(f"{label} retained content does not match the supplied typed record")

        retained(self.workflow_record_ref, self.workflow.to_dict(), "stage workflow")
        retained(self.envelope_record_ref, self.envelope.to_dict(), "stage envelope")
        if self.envelope.workflow_ref != self.workflow_record_ref.uri:
            raise ProjectRunnerError("stage envelope does not name the retained workflow record")
        if self.envelope.project_id != run.project_id or self.envelope.run_id != run.run_id:
            raise ProjectRunnerError("stage envelope belongs to another project or run")
        if (
            self.envelope.base_version != run.base.version
            or self.envelope.base_state_sha256 != run.base.require_digest()
        ):
            raise ProjectRunnerError("stage envelope does not bind the run's exact canonical base")
        if (
            self.envelope.branch_id != branch.branch_id
            or self.envelope.branch_epoch != branch.epoch
        ):
            raise ProjectRunnerError("stage envelope belongs to another branch or epoch")
        if self.envelope.state_digest != state.state_digest:
            raise ProjectRunnerError("stage envelope does not bind the exact developed state")
        if self.envelope.phase is not state.active_phase:
            raise ProjectRunnerError("stage envelope phase disagrees with the developed state")
        if any(
            self.envelope.phase not in seat.phases
            for seat in seats
            if not seat.reviewer
        ):
            raise ProjectRunnerError("a producing seat is not admitted in the envelope phase")

        require_stage_run_envelope(
            self.workflow,
            self.envelope,
            workflow_ref=self.workflow_record_ref.uri,
            predecessor=self.predecessor,
            predecessor_ref=(
                None
                if self.predecessor_record_ref is None
                else self.predecessor_record_ref.uri
            ),
            predecessor_exit=self.predecessor_exit,
            predecessor_exit_ref=(
                None
                if self.predecessor_exit_record_ref is None
                else self.predecessor_exit_record_ref.uri
            ),
        )
        successor_values = (
            self.predecessor,
            self.predecessor_record_ref,
            self.predecessor_exit,
            self.predecessor_exit_record_ref,
            self.predecessor_closure,
            self.predecessor_closure_record_ref,
        )
        if self.envelope.stage_index == 0:
            if any(value is not None for value in successor_values):
                raise ProjectRunnerError("stage 0 cannot carry predecessor completion")
            return
        if any(value is None for value in successor_values):
            raise ProjectRunnerError(
                "stage N requires retained predecessor envelope, exit binding, and closure"
            )
        assert self.predecessor is not None
        assert self.predecessor_record_ref is not None
        assert self.predecessor_exit is not None
        assert self.predecessor_exit_record_ref is not None
        assert self.predecessor_closure is not None
        assert self.predecessor_closure_record_ref is not None
        retained(
            self.predecessor_record_ref,
            self.predecessor.to_dict(),
            "predecessor stage envelope",
        )
        retained(
            self.predecessor_exit_record_ref,
            self.predecessor_exit.to_dict(),
            "predecessor stage exit binding",
        )
        retained(
            self.predecessor_closure_record_ref,
            self.predecessor_closure.to_dict(),
            "predecessor stage closure",
        )
        require_stage_exit_binding(
            self.predecessor,
            self.predecessor_exit,
            envelope_ref=self.predecessor_record_ref.uri,
        )
        closure = self.predecessor_closure
        if self.predecessor_exit.closure_ref != self.predecessor_closure_record_ref.uri:
            raise ProjectRunnerError("stage exit binding does not name the retained closure")
        if (
            closure.status is not StageClosureStatus.SATISFIED
            or closure.receipt_digest != self.predecessor_exit.closure_digest
            or closure.stage_id != self.predecessor.stage_id
            or closure.stage_subject_ref != self.predecessor.subject_ref
            or closure.subject_digest != self.predecessor.state_digest
            or closure.branch.run.project_id != self.predecessor.project_id
            or closure.branch.run.run_id != self.predecessor.run_id
            or closure.branch.run.base.version != self.predecessor.base_version
            or closure.branch.run.base.require_digest()
            != self.predecessor.base_state_sha256
            or closure.branch.branch_id != self.predecessor.branch_id
            or closure.branch.epoch != self.predecessor.branch_epoch
        ):
            raise ProjectRunnerError("predecessor closure is not SATISFIED for the exact stage state")


def _subtree_leaves(proposal: SpatialOptionProposal, subtree: tuple[str, ...]) -> tuple[str, ...]:
    parents = {c.parent_component_id for c in proposal.components if c.parent_component_id}
    return tuple(sorted(c for c in subtree if c not in parents))


def _export_workspace(options: RunOptions, stage_id: str) -> Path:
    """The caller-prepared stage workspace an export may write into; the runner never creates it."""

    try:
        workspace = cad_workspace_path(options.workspace_root, stage_id)
    except ValueError as exc:
        raise ProjectRunnerError(str(exc)) from exc
    if not workspace.is_dir():
        raise ProjectRunnerError(f"export workspace {workspace} does not exist: the caller prepares workspaces; the runner never creates files outside records")
    return workspace


@contextmanager
def _observe_runner_operation(observer, phase: str, *, parent_event_id=None, details=None):
    """Report an existing runner step; diagnostics cannot change its outcome."""

    event = {
        "event_id": f"runner:{uuid4().hex}", "phase": phase, "status": "succeeded",
        "started_at": datetime.now(timezone.utc).isoformat(),
        **({"parent_event_id": parent_event_id} if parent_event_id is not None else {}),
        "details": details if details is not None else {},
    }
    started = time.perf_counter()
    try:
        yield event
    except BaseException as exc:
        event["status"] = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)) else "failed"
        raise
    finally:
        event.update(ended_at=datetime.now(timezone.utc).isoformat(), duration_ms=round((time.perf_counter() - started) * 1000))
        if observer is not None:
            try:
                observer(deepcopy(event))
            except (Exception, asyncio.CancelledError):
                pass


def _export(repository, run, branch, branch_destination, program, stage_id: str, options: RunOptions, provenance: dict, *, source: _SourceSeat | None = None,
            operation_observer: Callable[[Mapping[str, Any]], None] | None = None) -> dict:
    """Export through the selected executor, retaining one parent for its real steps."""

    details = {"scope": "cad_export", "input_identity": {"program_digest": program.program_digest},
               "execution_path": "unknown"}
    with _observe_runner_operation(operation_observer, f"geometry_export.{options.cad_backend}.unknown", details=details) as event:
        event["source_ref"] = provenance.get("state_record_ref")
        result = _execute_cad(repository, run, branch, branch_destination, program, stage_id, options, provenance,
                              source=source, operation_observer=operation_observer, observation_parent_id=event["event_id"])
        path = result.get("path", "unknown")
        event.update(phase=f"geometry_export.{options.cad_backend}.{path}",
                     status="succeeded" if result.get("status") == "succeeded" else "failed")
        details.update(execution_path=path, output_refs=[result["execution_ref"]] if result.get("execution_ref") else [])
        if result.get("reused_object_ids"):
            details["reused_object_ids"] = list(result["reused_object_ids"])
        return result


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _prior_cad_export(repository, run, request, backend, *, diagnostics):
    """Reuse only a P036 receipt of this exact request and still verified files."""
    details = diagnostics
    details.update(scope="verified_export_cache", cache_status="miss", cache_reason="no_retained_export", cache_checks={})
    matched_binding = False

    def rejected(reason, condition="changed"):
        if reason == "binding_changed" and matched_binding:
            return
        details["cache_checks"][reason] = condition
        details["cache_reason"] = reason if len(details["cache_checks"]) == 1 else "multiple_cache_conditions_failed"

    records = Path(repository.layout.run(run.run_id).records)
    for path in sorted(records.glob(f"{backend.record_kind}-*.json")):
        try:
            ref = record_ref_from_uri(f"project://{run.project_id}/runs/{run.run_id}/records/{path.name}", run.project_id)
            payload = repository.load_json(ref)
        except (ValueError, ProjectIntegrityError):
            rejected("receipt_integrity_failed")
            continue
        if payload.get("export_path") == "work-model":
            continue
        if payload.get("status") != "succeeded":
            rejected("receipt_not_succeeded")
            continue
        if payload.get("readback_verified") is not True or payload.get("failures") != []:
            rejected("readback_not_verified")
            continue
        if (payload.get("identity") or {}).get("binding") != request.binding.to_dict():
            rejected("binding_changed")
            continue
        if not matched_binding:
            details["cache_checks"].clear()
        matched_binding = True
        try:
            result = backend.read_receipt(request, payload)
        except (CadExecutionError, KeyError, TypeError, ValueError):
            rejected("required_artifact_missing", "missing")
            continue
        intact = True
        for artifact in result.artifacts:
            file = request.speculative_workspace / artifact.relative_path
            if not file.is_file():
                rejected("artifact_missing", "missing")
                intact = False
                break
            try:
                if _sha256_file(file) != artifact.sha256:
                    rejected("artifact_changed")
                    intact = False
                    break
            except OSError:
                rejected("artifact_unreadable", "unreadable")
                intact = False
                break
        if not intact:
            continue
        try:
            result.validate(request, backend.backend_id)
        except (CadExecutionError, OSError):
            rejected("artifact_changed")
            continue
        details.update(cache_status="hit", cache_reason="verified_matching_export",
                       cache_checks={"binding": "same", "artifacts": "same", "readback": "verified"},
                       input_equivalent=True, comparison_refs=[ref.uri], reused_object_ids=list(result.physical_object_ids),
                       executed_stages=[], output_refs=[ref.uri])
        return result, ref
    return None


def _source_from_receipt(repository, run, request, backend, payload, model=None):
    """Verify a retained source under its own complete binding before reusing it."""
    identity = payload["identity"]["binding"]
    program_ref = record_ref_from_uri(identity["program_ref"]["uri"], run.project_id)
    program = load_compiled_geometry_program(repository.load_json(program_ref))
    binding = CadProgramBinding(program_ref, BranchRef(run, identity["branch_id"], identity["branch_epoch"]),
                                identity["stage_id"], program.program_digest, program.proposal.design_state_digest,
                                program.proposal.predecessor_program_digest)
    workspace = model.parent if model is not None else request.speculative_workspace
    source_request = replace(request, program=program, binding=binding, speculative_workspace=workspace, source=None)
    result = backend.read_receipt(source_request, payload)
    result.validate(source_request, backend.backend_id)
    if result.status != "succeeded":
        raise CadExecutionError("source receipt not verified")
    artifact = next(a for a in result.artifacts if a.name in ("exact", "model"))
    source_model = workspace / artifact.relative_path
    if model is not None and model != source_model:
        raise CadExecutionError("source model differs from retained artifact")
    return CadExecutionSource(program, source_model, artifact.sha256)


def _source_export(repository, source, request, backend, *, diagnostics):
    details = diagnostics
    details.update(scope="source_export_cache", cache_status="miss", cache_reason="source_not_selected", cache_checks={})
    if source is None:
        return None
    if not source.cad or not source.cad.get("execution_ref") or not source.cad.get("model"):
        details.update(cache_reason="source_export_missing", cache_checks={"source_export": "missing"})
        return None
    try:
        ref = record_ref_from_uri(source.cad["execution_ref"], source.run.project_id)
        if ref.record_kind != backend.record_kind or not ref.relative_path.startswith(f"runs/{source.run.run_id}/records/"):
            details.update(cache_status="refused", cache_reason="source_record_binding_changed", cache_checks={"binding": "changed"})
            return None
        payload = repository.load_json(ref)
        execution_source = _source_from_receipt(repository, source.run, request, backend, payload, Path(source.cad["model"]))
        if execution_source.program.program_digest != source.program.program_digest:
            raise CadExecutionError("source program changed")
        details.update(cache_status="hit", cache_reason="verified_source_export", cache_checks={"binding": "same", "artifact": "same"},
                       comparison_refs=[ref.uri], input_identity={"source_program_digest": source.program.program_digest,
                                                               "source_step_sha256": execution_source.sha256})
        return execution_source, ref.uri
    except (KeyError, ValueError, TypeError, OSError, ProjectIntegrityError, StopIteration):
        details.update(cache_status="refused", cache_reason="source_receipt_or_artifact_unreadable", cache_checks={"source_export": "unreadable"})
        return None


def _prior_patch_source(repository, run, request, backend):
    records = Path(repository.layout.run(run.run_id).records)
    for path in sorted(records.glob(f"{backend.record_kind}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            ref = record_ref_from_uri(f"project://{run.project_id}/runs/{run.run_id}/records/{path.name}", run.project_id)
            payload = repository.load_json(ref)
            if payload.get("export_path") == "work-model" or payload["identity"]["binding"]["stage_id"] != request.binding.stage_id:
                continue
            return _source_from_receipt(repository, run, request, backend, payload), ref.uri
        except (KeyError, ValueError, TypeError, OSError, ProjectIntegrityError, StopIteration):
            continue
    return None


def _execute_cad(repository, run, branch, branch_destination, program, stage_id, options, provenance, *, source=None,
                 operation_observer=None, observation_parent_id=None):
    backend = get_cad_backend(options.cad_backend)
    workspace = _export_workspace(options, stage_id)
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
    program_ref = repository.put_json(run=run, destination=branch_destination, record_kind=stage_geometry_program(stage_id), payload=program.to_dict())
    binding = CadProgramBinding(program_ref, branch, stage_id, program.program_digest,
                                program.proposal.design_state_digest, program.proposal.predecessor_program_digest)
    request = CadExecutionRequest(program, binding, workspace, f"{stage_id}@{program.program_digest[:12]}",
        provenance=provenance, backend_options=options.execution_options(), operation_observer=operation_observer,
        observation_parent_id=observation_parent_id)

    def summary(result, ref, *, path=None, seconds=None):
        artifacts = {a.name: a for a in result.artifacts}
        model = artifacts.get("exact") or artifacts.get("model")
        out = {**result.details, "execution_ref": ref.uri if ref else None, "status": result.status, "readback_verified": result.readback_verified,
               "failures": list(result.failures), "path": path or result.execution_path, "backend": result.backend_id,
               "model": str(workspace / model.relative_path) if model else None}
        for name in ("exact", "preview"):
            out[f"{name}_artifact"] = artifacts[name].to_dict() if name in artifacts else None
        if seconds is not None:
            out["seconds"] = seconds
        if result.reused_object_ids:
            out.update(reused_object_ids=list(result.reused_object_ids), source_execution_ref=request.provenance.get("source_execution_ref"))
        if result.status == "succeeded" and result.inspection is not None:
            out["inspection_ref"] = repository.put_json(run=run, destination=destination, record_kind=SEAT_3DM_INSPECTION, payload=result.inspection).uri
            out["_bboxes"] = {str(r["name"]): (list(r["bbox"]["min"]), list(r["bbox"]["max"])) for r in result.inspection.get("named_object_bboxes", ())}
        return out

    cache_details = {"input_identity": {"program_digest": program.program_digest, "backend": backend.backend_id}}
    with _observe_runner_operation(operation_observer, "export_cache_lookup", parent_event_id=observation_parent_id, details=cache_details) as event:
        event["source_ref"] = program_ref.uri
        prior = _prior_cad_export(repository, run, request, backend, diagnostics=cache_details)
    if prior is not None:
        out = summary(*prior, path="reused")
        out.pop("_bboxes", None)
        return out

    source_details = {}
    with _observe_runner_operation(operation_observer, "source_export_lookup", parent_event_id=observation_parent_id, details=source_details) as event:
        event["source_ref"] = program_ref.uri
        source_export = _source_export(repository, source, request, backend, diagnostics=source_details)
    if source_export is None and backend.patch_rebuild:
        source_export = _prior_patch_source(repository, run, request, backend)
    if source_export is not None:
        execution_source, source_ref = source_export
        request = replace(request, source=execution_source, provenance={**provenance, "source_execution_ref": source_ref})

    def execute_once(current_request, label=None):
        base_stem = current_request.artifact_stem + (f".{label}" if label else "")
        stem, attempt = base_stem, 1
        while any(workspace.glob(f"{stem}.*")):
            attempt += 1
            stem = f"{base_stem}.r{attempt}"
        current_request = replace(current_request, artifact_stem=stem,
            provenance={**current_request.provenance, "export_path": label or backend.backend_id})
        started = time.perf_counter()
        result = backend.execute(current_request)
        result.validate(current_request, backend.backend_id)
        seconds = round(time.perf_counter() - started, 3)
        execution_ref = None
        if result.receipt_payload is not None:
            payload = result.receipt_payload
            if backend.patch_rebuild:
                payload = {**payload, "export_path": label or result.execution_path, "seconds": seconds}
            execution_ref = repository.put_json(run=run, destination=destination, record_kind=backend.record_kind, payload=payload)
        return summary(result, execution_ref, seconds=seconds)

    attempted = None
    if request.source is not None and backend.patch_rebuild:
        try:
            attempted = execute_once(request, "patch")
        except CadExecutionError as exc:
            attempted = {"status": "not_patchable", "detail": str(exc), "path": "patch"}
        if attempted.get("status") != "succeeded":
            cad = execute_once(replace(request, source=None))
            cad["patch_attempt"] = {k: v for k, v in attempted.items() if k != "_bboxes"}
        else:
            cad = attempted
    else:
        cad = execute_once(request)
    if attempted is not None and attempted.get("status") == "succeeded" and request.backend_options.get("patch_oracle"):
        full = execute_once(replace(request, source=None), "rebuild-oracle")
        a, b = cad.get("_bboxes") or {}, full.get("_bboxes") or {}
        worst = max((max(abs(x - y) for x, y in zip(a[k][0] + a[k][1], b[k][0] + b[k][1])) for k in set(a) & set(b)), default=0.0)
        cad["oracle"] = {"execution_ref": full.get("execution_ref"), "status": full.get("status"), "seconds": full.get("seconds"),
                         "objects_compared": len(set(a) & set(b)), "missing": sorted(set(b) - set(a)), "extra": sorted(set(a) - set(b)), "worst_m": round(worst, 6),
                         "equal": full.get("status") == "succeeded" and set(a) == set(b) and worst <= 0.001}
        if not cad["oracle"]["equal"]:
            raise ProjectRunnerError(f"patch oracle disagrees with the full rebuild for {stage_id}: {cad['oracle']}")
    cad.pop("_bboxes", None)
    return cad


def run_project(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    stage_guard: StageExecutionGuard,
    record: StateRecord,
    seats: tuple[SeatSpec, ...],
    options: RunOptions,
    operation_observer: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, object]:
    """Run admitted seat rounds from one State Record; return an authority-free stage-run receipt.

    The record is the only design input: its Component/Massing entities are
    the spatial option, its Level/GridAxis entities the published datums,
    its Element@1 entities the rows the canonical producers read. Seats are
    people, not state, and come separately.
    """

    started = time.perf_counter()
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
    branch = BranchRef(run=run, branch_id=options.branch_id, epoch=options.branch_epoch)
    branch_destination = PersistenceDestination(PersistenceArea.RUN_BRANCH, run_id=run.run_id, branch_id=options.branch_id)
    put = lambda kind, payload: repository.put_json(run=run, destination=destination, record_kind=kind, payload=payload)
    # what produced the proposals, and what the seat pack declared beside it: the record
    # says both, so nobody has to infer from a seat pack whether a model was called
    provider_block = {
        "kind": "recorded",
        "identity": RECORDED_PROPOSAL_IDENTITY.to_dict(),
        "declared_live_identity": (
            None if options.live_provider_identity is None else options.live_provider_identity.to_dict()
        ),
    }
    # Compute and admit the exact state before the first write.  This prevents
    # a raw create_run or a copied pack from acquiring a stage by side effect.
    if record.project_id != run.project_id:
        raise ProjectRunnerError("state record belongs to another project")
    # an authored record is portable; this run binds it to its own identity and canonical base,
    # and that binding is exactly what the compiler checks against every proposal (P102)
    record = record.bound_to(run)
    # the phase is the stage's, stated by the envelope this run opened (ADR-007, P112): the
    # projection carries it into the state digest, and the guard then checks that the envelope
    # binds exactly this state - so an envelope opened under another phase is refused below
    try:
        state = developed_design_view(record, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref,
                                      phase=stage_guard.envelope.phase)
    except DevelopedDesignError as exc:
        raise ProjectRunnerError(f"the developed-design projection cannot carry the envelope phase {stage_guard.envelope.phase.value!r}: {exc}") from exc
    stage_guard.require(
        repository,
        run=run,
        branch=branch,
        state=state,
        seats=seats,
    )
    levels = project_levels_of(record)
    grids = project_grids_of(record)
    sources = _source_seats(repository, run, options.source_run_receipt_ref)
    references = _reference_inputs(levels, grids)
    producer_code = _producer_code()
    try:
        rows = element_rows_of(record)
    except ElementProducerError as exc:
        raise ProjectRunnerError(str(exc)) from exc
    record_ref = put(STATE_RECORD, {**record.to_dict(), **no_authority(_AUTH)})
    levels_ref = put(PROJECT_LEVELS, {**levels.to_dict(), **no_authority(_AUTH)})
    grids_ref = put(PROJECT_GRIDS, {**grids.to_dict(), **no_authority(_AUTH)}).uri if grids is not None else None
    proposal_tree = state.selected_schematic.option.proposal
    spatial_ref = put(SELECTED_SPATIAL_OPTION, proposal_tree.to_dict())
    state_ref = put(DEVELOPED_DESIGN_STATE, {**state.to_dict(), **no_authority(_AUTH)}) if hasattr(state, "to_dict") else None
    seat_by_id = {s.seat_id: s for s in seats}
    seat_refs = {s.seat_id: put(DISCIPLINE_SEAT, s.to_dict()).uri for s in seats}
    rounds = schedule_seats(seats)
    project_datums = tuple(sorted(levels.datums() + (grids.datums() if grids is not None else ()), key=lambda d: d.datum_id))
    programs: dict[str, Any] = {}
    handovers_for: dict[str, list] = {s.seat_id: [] for s in seats}
    results: list[SeatResult] = []
    # What the stage closure is compiled from: the reports themselves, and the
    # digests of the records they were retained as.
    relation_reports: list[Any] = []
    check_receipt_digests: list[str] = []
    # Relations wait here until every endpoint has extent, then are measured once against
    # everything realized so far (this seat and the seats before it, in the normal order).
    ledger = _RelationLedger.open(record, levels)
    frame = CoordinateFrame(frame_id=FRAME_ID, parent_frame_id=None, transform_from_parent=AffineTransform.identity(), source_refs=tuple(record.evidence_refs[:1]) or (record_ref.uri,))
    try:
        for round_index, round_seats in enumerate(rounds):
            for seat_id in round_seats:
                seat = seat_by_id[seat_id]
                if seat.reviewer:
                    continue
                t0 = time.perf_counter()
                subtree = owned_subtree(proposal_tree, seat.owned_component_ids)
                leaves = _subtree_leaves(proposal_tree, subtree)
                own = tuple(r for r in rows if r.component_id in set(subtree))
                handovers = tuple(handovers_for[seat_id])
                context = project_seat_context(seat=seat, design_state=state, inherited_commitment_refs=(options.commitment_ref,), handovers=handovers,
                                               project_levels=levels, project_grids=grids)
                context_ref = put(SEAT_AUTHORING_CONTEXT, context.to_dict())
                exclusions = tuple(b for h in handovers for b in _exclusion_bounds(h))
                production = ProductionContext(references=ReferenceContext(grids=grids, levels=levels), published={d.datum_id: d for h in handovers for d in h.datums}, exclusions=exclusions)
                try:
                    elements_produced, element_results, reused_elements = _produce_incrementally(
                        own, production, sources.get(seat_id), references, producer_code,
                        operation_observer=operation_observer,
                        source_ref=record_ref.uri,
                        input_identity={"record_digest": record.digest, "producer_code": producer_code, "seat_id": seat_id}
                            if producer_code is not None else None)
                except ElementProducerError as exc:
                    raise ProjectRunnerError(str(exc)) from exc
                produced = _gather(elements_produced)
                check_seat_datums(seat=seat, published_datums=produced.datums, project_levels=levels, project_grids=grids)
                declined = tuple(sorted({e.component_id for e in own if e.producer == "declined"}))
                # a component is covered when an element names it or when produced geometry is bound to it
                # (openings hosted by a wall bind their own components)
                realized = {b.removeprefix("binding-") for op in produced.operations for b in op.semantic_binding_ids}
                covered = tuple(sorted({e.component_id for e in own if e.producer != "declined"} | (realized & set(subtree))))
                undeclared = tuple(c for c in leaves if c not in set(covered) | set(declined))
                if undeclared and options.strict_coverage:
                    raise ProjectRunnerError(f"seat {seat_id!r} owns components with no element and no declination: {undeclared}")
                if not covered:
                    results.append(SeatResult(seat_id, round_index, "empty", None, None, 0, covered, undeclared, (), time.perf_counter() - t0, declined=declined))
                    continue
                by_component: dict[str, list[str]] = {}
                for op in produced.operations:
                    by_component.setdefault(op.semantic_binding_ids[0].removeprefix("binding-"), []).extend(op.output_object_ids)
                bindings = tuple(SemanticBinding(binding_id=f"binding-{c}", component_id=c, object_ids=tuple(sorted(o)), commitment_refs=(options.commitment_ref,),
                                                 evidence_refs=tuple(sorted({spatial_ref.uri, record_ref.uri}))) for c, o in sorted(by_component.items()))
                proposal = GeometryProgramProposal(
                    proposal_id=f"{run.project_id}-{seat_id}-round-{round_index}", project_id=run.project_id, run_id=run.run_id, base=run.base,
                    design_state_digest=state.state_digest, predecessor_program_digest=None, length_unit=_M, tolerance=GeometryTolerance(0.001, 0.001),
                    frames=(frame,), assets=(), semantic_bindings=bindings, operations=produced.operations, assemblies=produced.assemblies)
                datums = tuple(sorted({d.datum_id: d for d in project_datums + tuple(d for h in handovers for d in h.datums) + produced.datums}.values(), key=lambda d: d.datum_id))
                provider = RecordedProposalProvider(proposal_authoring_output(proposal))
                # the identity checked against the receipt is the one the provider actually
                # presents, so a silent substitution is still refused and no retained round
                # says a model answered
                result = asyncio.run(produce_geometry_program_proposal(
                    repository, provider, run=run, destination=destination, spatial_option_ref=spatial_ref, design_state=state,
                    required_commitment_refs=(options.commitment_ref,), provider_identity=RECORDED_PROPOSAL_IDENTITY, policy=GeometryProposalPolicy(1),
                    seat_scope=subtree, interface_datums=datums, datum_bindings=produced.bindings))
                issues = tuple(row for r in result.round_refs for row in repository.load_json(r).get("issues", []))
                if result.status is not GeometryProposalStatus.ACCEPTED:
                    results.append(SeatResult(seat_id, round_index, result.status.value, None, None, 0, covered, undeclared, issues, time.perf_counter() - t0))
                    break
                program = result.program
                program_ref = put(SEAT_GEOMETRY_PROGRAM, program.to_dict())
                programs[seat_id] = program
                bounds = expected_object_bounds(program)
                realized = {oid: (tuple(row["bbox_min"]), tuple(row["bbox_max"])) for oid, row in bounds.items()}
                # this seat's compiled bounds join what earlier seats realized; every relation whose
                # endpoints now all have extent - the producers' own and the record's, including one
                # that spans this seat and an earlier one - is measured here, once; nothing is healed
                ledger.realize(own, elements_produced, produced, realized)
                relation_report = ledger.check(elements_produced)
                relation_check_record = put(SEAT_RELATION_CHECK, {**relation_report.to_dict(), "seat_id": seat_id, "program_ref": program_ref.uri, "basis": RELATION_CHECK_BASIS, **no_authority(_AUTH)})
                relation_check_ref = relation_check_record.uri
                relation_reports.append(relation_report)
                check_receipt_digests.append(relation_check_record.sha256)
                if not relation_report.held:
                    # a violated relation is a result, not a crash: program, report and seat are retained with
                    # its issues, nothing is handed over or exported, and the relation report (held / violated /
                    # unchecked) is the clause a verdict reads; the seat itself did produce its program
                    violations = [{"code": "relation_violated", "relation_id": c.relation_id, "detail": c.detail} for c in relation_report.checks if c.status == "violated"]
                    seat_result = SeatResult(seat_id, round_index, "proposal_accepted", program_ref.uri, program.program_digest, len(program.objects), covered, undeclared,
                                             tuple(issues) + tuple(violations), time.perf_counter() - t0, relation_check_ref=relation_check_ref)
                    receipt_ref = put(SEAT_ROUND_RECEIPT, {"schema": "SeatRoundReceipt@1", "stage_id": stage_guard.envelope.stage_id, "stage_index": stage_guard.envelope.stage_index, "stage_envelope_ref": stage_guard.envelope_record_ref.uri, **_seat_dict(seat_result), "context_ref": context_ref.uri, "seat_ref": seat_refs[seat_id], "provider": provider_block,
                                                       "element_results": element_results, "producer_code": producer_code, "reused_element_ids": list(reused_elements), **no_authority(_AUTH)})
                    results.append(replace(seat_result, receipt_ref=receipt_ref.uri))
                    continue
                digests = {o.object_id: o.object_digest for o in program.objects}
                for consumer in seats:
                    if seat_id in consumer.consumes and not consumer.reviewer:
                        handover = compile_handover(from_seat=seat, to_seat=consumer, design_state=state, published_datums=program.interface_datums,
                                                    program_bindings=program.proposal.semantic_bindings, realized_bounds=realized, object_digests=digests)
                        put(SEAT_HANDOVER, handover.to_dict())
                        handovers_for[consumer.seat_id].append(handover)
                cad = None
                if options.export:
                    cad = _export(repository, run, branch, branch_destination, program, f"{stage_guard.envelope.stage_id}-{seat_id}", options,
                                  {"target": "PROJECT_RUNNER", "workflow_stage_id": stage_guard.envelope.stage_id, "workflow_stage_index": str(stage_guard.envelope.stage_index), "stage_envelope_ref": stage_guard.envelope_record_ref.uri, "seat": seat_id, "candidate_status": "HOLD", "frame_semantics": "BUILDING_LOCAL_Y_UP", "state_record_ref": record_ref.uri}, source=sources.get(seat_id), operation_observer=operation_observer)
                seat_status = "proposal_accepted" if cad is None or cad.get("status") == "succeeded" else "export_failed"
                seat_result = SeatResult(seat_id, round_index, seat_status, program_ref.uri, program.program_digest, len(program.objects), covered, undeclared, issues, time.perf_counter() - t0, cad, declined=declined,
                                         declination_reasons={e.component_id: str(e.params.get("reason")) for e in own if e.producer == "declined"}, relation_check_ref=relation_check_ref)
                receipt_ref = put(SEAT_ROUND_RECEIPT, {"schema": "SeatRoundReceipt@1", "stage_id": stage_guard.envelope.stage_id, "stage_index": stage_guard.envelope.stage_index, "stage_envelope_ref": stage_guard.envelope_record_ref.uri, **_seat_dict(seat_result), "context_ref": context_ref.uri, "seat_ref": seat_refs[seat_id], "provider": provider_block,
                                                   "element_results": element_results, "producer_code": producer_code, "reused_element_ids": list(reused_elements), **no_authority(_AUTH)})
                results.append(replace(seat_result, receipt_ref=receipt_ref.uri))
            else:
                continue
            break
    except Exception as exc:  # retained records stay; the run says why it stopped
        put(RUNNER_RUN_FAILURE, {"schema": "RunnerRunFailure@1", "project_id": run.project_id, "run_id": run.run_id,
                                   "state_record_ref": record_ref.uri, "stage_id": stage_guard.envelope.stage_id,
                                   "error": type(exc).__name__, "detail": str(exc)[:2000],
                                   "seat_results": [_seat_dict(r) for r in results], "wall_time_s": round(time.perf_counter() - started, 3)})
        raise
    # Solid relations wait until all seats have exported. A compiler box is
    # never an early substitute, and each relation is retained exactly once.
    solid_relations = tuple(r for r in ledger.pending.values() if r.validator and r.validator.check_kind == "solid_nonpenetration")
    solid_check_ref = None
    if solid_relations:
        solid_report, execution_refs = _check_final_solid_relations(repository, run, record, solid_relations, results, ledger.objects)
        solid_record = put(SEAT_RELATION_CHECK, {**solid_report.to_dict(), "seat_id": None, "scope": "stage-solid-pairs",
                                               "basis": SOLID_RELATION_CHECK_BASIS, "execution_refs": list(execution_refs), **no_authority(_AUTH)})
        solid_check_ref = solid_record.uri
        relation_reports.append(solid_report)
        check_receipt_digests.append(solid_record.sha256)
        for relation in solid_relations:
            del ledger.pending[relation.relation_id]
    # What no seat could measure: a relation whose endpoint never acquired extent in this run
    # (an element nobody produced, a declined component, an entity with no geometry). It is
    # retained as its own unchecked report so the closure reads it, instead of being dropped.
    unmeasured = ledger.unmeasured()
    unmeasured_ref = None
    if unmeasured is not None:
        unmeasured_record = put(SEAT_RELATION_CHECK, {**unmeasured.to_dict(), "seat_id": None, "scope": "stage-unmeasured", "basis": RELATION_CHECK_BASIS, **no_authority(_AUTH)})
        unmeasured_ref = unmeasured_record.uri
        relation_reports.append(unmeasured)
        check_receipt_digests.append(unmeasured_record.sha256)
    owned_any = set()
    for seat in seats:
        owned_any.update(owned_subtree(proposal_tree, seat.owned_component_ids))
    unowned = tuple(sorted(c for c in _subtree_leaves(proposal_tree, tuple(c.component_id for c in proposal_tree.components)) if c not in owned_any))
    # The stage closes here or not at all (ADR-007 rule 3). The closure is
    # written either way, because a stage that did not close still owes the
    # project the statement of why; only a SATISFIED one yields an exit
    # binding, and only that binding lets a successor stage open.
    seat_execution_complete = all(s.status in ("proposal_accepted", "empty") for s in results) and any(s.status == "proposal_accepted" for s in results)
    closure = _stage_closure(stage_guard, branch=branch, results=results, relation_reports=tuple(relation_reports),
                             check_receipt_digests=tuple(check_receipt_digests), seat_execution_complete=seat_execution_complete)
    closure_ref = put(STAGE_CLOSURE, closure.to_dict())
    exit_binding_ref = None
    if closure.status is StageClosureStatus.SATISFIED:
        exit_binding = StageExitBinding.bind(stage_guard.envelope, envelope_ref=stage_guard.envelope_record_ref.uri,
                                             closure_ref=closure_ref.uri, closure_digest=closure.receipt_digest)
        exit_binding_ref = put(STAGE_EXIT_BINDING, exit_binding.to_dict()).uri
    payload = {
        "schema": "RunnerRunReceipt@3", "project_id": run.project_id, "run_id": run.run_id, "state_record_ref": record_ref.uri, "state_record_digest": record.digest,
        "coverage_mode": "strict" if options.strict_coverage else "relaxed",
        "unowned_components": list(unowned),
        "levels_ref": levels_ref.uri, "grids_ref": grids_ref, "spatial_option_ref": spatial_ref.uri, "design_state_ref": state_ref.uri if state_ref else None,
        "design_state_digest": state.state_digest,
        "workflow_ref": stage_guard.workflow_record_ref.uri,
        "workflow_digest": stage_guard.workflow.workflow_digest,
        "stage_envelope_ref": stage_guard.envelope_record_ref.uri,
        "stage_envelope_digest": stage_guard.envelope.envelope_digest,
        "stage": {
            "stage_id": stage_guard.envelope.stage_id,
            "stage_index": stage_guard.envelope.stage_index,
            "phase": stage_guard.envelope.phase.value,
            # The close obligation retained in the envelope is OPEN and stays
            # OPEN; whether it was satisfied is what the closure says, below.
            "status": "OPEN",
            "close_obligation_id": stage_guard.envelope.close_obligation.obligation_id,
        },
        "closure_ref": closure_ref.uri,
        "closure_status": closure.status.value,
        "exit_binding_ref": exit_binding_ref,
        # A harness closes its own container, never a project stage (ADR-007
        # rule 4), so a reader never has to infer that from the workflow id.
        "workflow_is_harness": stage_guard.workflow.workflow_id in HARNESS_WORKFLOW_IDS,
        "provider": provider_block,
        "rounds": [list(r) for r in rounds], "seat_results": [_seat_dict(s) for s in results],
        "seat_execution_complete": seat_execution_complete,
        # Each report states its measurement basis. The original extent
        # checkers remain analytic; explicit solid pairs use final CAD readback.
        "relation_checks": {"basis": RELATION_CHECK_BASIS, "unmeasured_check_ref": unmeasured_ref,
                            **({"solid_check_ref": solid_check_ref, "solid_check_basis": SOLID_RELATION_CHECK_BASIS} if solid_check_ref else {}),
                            "unmeasured_relation_ids": [] if unmeasured is None else [c.relation_id for c in unmeasured.checks]},
        "wall_time_s": round(time.perf_counter() - started, 3), **no_authority(_AUTH),
    }
    if options.source_run_receipt_ref is not None:
        payload["source_run_receipt_ref"] = options.source_run_receipt_ref.uri
    payload["receipt_ref"] = put(RUNNER_RUN_RECEIPT, payload).uri
    return payload


def _stage_closure(stage_guard: StageExecutionGuard, *, branch: BranchRef, results, relation_reports, check_receipt_digests, seat_execution_complete: bool) -> CompositeStageClosureReceipt:
    """The stage's closure, compiled from this run's own checks (ADR-007 r3).

    ``envelope.required_checks`` are checker ids (``state_record.CHECK_KINDS``:
    ``support_contact``, ``clearance_interval``, ``aperture_exists``), matched
    against each check's ``check_kind`` - the validator the relation binds -
    and never against ``Relation.kind``. A relation that binds no validator
    reports ``check_kind`` ``none``; it is attributed to no requirement,
    stays ``unchecked`` in its seat report, and neither satisfies nor fails a
    required checker. Nothing here guesses a checker or a threshold for it
    from the relation's kind.

    One finding per problem and nothing else: a required checker no relation
    is bound to, a relation bound to a required checker that stayed unchecked
    (no seat could supply what the checker reads), a relation bound to a
    required checker that did not hold, a seat that did not finish, or a seat
    whose owned leaves remain undeclared or explicitly declined. No finding is
    SATISFIED, and only a SATISFIED closure may become a ``StageExitBinding``.
    SATISFIED therefore says: every relation bound to a required checker was
    measured and held, and every seat finished - not that every relation the
    record declares was proved.

    Requirements are read relation by relation, never by kind alone: a
    required checker is not satisfied because *some* relation bound to it was
    measured while another bound to the same checker was never measured.
    Findings are kept by identity, not by arrival.
    """

    envelope = stage_guard.envelope
    findings: dict[tuple, StageClosureFinding] = {}

    def note(finding: StageClosureFinding) -> None:
        findings.setdefault(finding.identity, finding)

    by_kind: dict[str, list] = {}
    for report in relation_reports:
        for check in report.checks:
            by_kind.setdefault(check.check_kind, []).append(check)
    for required in envelope.required_checks:
        checks = by_kind.get(required, ())
        if not checks:
            note(StageClosureFinding(code=StageClosureFindingCode.MISSING_CHECK, requirement_id=required))
            continue
        for check in checks:
            if check.status == "violated":
                note(StageClosureFinding(code=StageClosureFindingCode.CHECK_FAILED, requirement_id=required, receipt_id=check.relation_id))
            elif check.status == "unchecked":
                # an unchecked relation of a required kind is a missing check with a name; a
                # measured sibling of the same kind says nothing about it
                note(StageClosureFinding(code=StageClosureFindingCode.MISSING_CHECK, requirement_id=required, receipt_id=check.relation_id))
    incomplete = tuple(r for r in results if r.status not in ("proposal_accepted", "empty"))
    for result in incomplete:
        note(StageClosureFinding(code=StageClosureFindingCode.SEAT_INCOMPLETE, requirement_id=result.seat_id))
    for result in results:
        if result.undeclared_components:
            note(StageClosureFinding(code=StageClosureFindingCode.SEAT_INCOMPLETE,
                                     requirement_id=f"{result.seat_id}:undeclared_components",
                                     refs=result.undeclared_components))
        if result.declined:
            note(StageClosureFinding(code=StageClosureFindingCode.SEAT_INCOMPLETE,
                                     requirement_id=f"{result.seat_id}:declined_components",
                                     refs=result.declined))
    if not seat_execution_complete and not incomplete:
        # Every seat was admitted and none produced a program: no seat failed,
        # and the stage still has nothing to close over.
        note(StageClosureFinding(code=StageClosureFindingCode.SEAT_INCOMPLETE, requirement_id="seat-execution-complete"))
    return CompositeStageClosureReceipt(
        profile_id=stage_guard.workflow.workflow_id,
        profile_digest=stage_guard.workflow.workflow_digest,
        stage_id=envelope.stage_id,
        branch=branch,
        stage_subject_ref=envelope.subject_ref,
        subject_digest=envelope.state_digest,
        check_receipt_digests=tuple(sorted(set(check_receipt_digests))),
        findings=tuple(findings.values()),
        status=StageClosureStatus.SATISFIED if not findings else StageClosureStatus.OPEN,
    )


def _check_final_solid_relations(repository, run, record, relations, results, objects_by_element):
    """Cold-read this run's verified final OCCT exports, then measure requested pairs.

    Pairs may span seats. Consumed operands and synthetic bounds never
    enter the geometry set; an absent/failed/non-OCCT export leaves its
    requested objects unchecked. The retained execution refs bind the
    measurement to the exact STEP bytes read here.
    """

    from archflow.adapters.cad_execution import OcctBackendError, measure_occt_solid_pairs, read_step

    pairs = tuple(sorted({tuple(pair) for relation in relations for pair in relation.parameters["object_pairs"]}))
    wanted = {name for pair in pairs for name in pair}
    entries, execution_refs, problems = [], [], []
    for seat in results:
        cad = seat.cad
        if not cad or cad.get("backend") != CAD_BACKEND_OCCT or cad.get("status") != "succeeded" or cad.get("readback_verified") is not True:
            continue
        try:
            ref = record_ref_from_uri(cad["execution_ref"], run.project_id)
            payload = repository.load_json(ref)
            binding = payload["identity"]["binding"]
            exact = payload["exact_artifact"]
            physical = payload["physical_object_ids"]
            if not wanted.intersection(physical):
                continue
            if (ref.record_kind != SEAT_OCCT_EXECUTION or payload.get("schema") != "OcctExecutionReceipt@1"
                    or payload.get("status") != "succeeded" or payload.get("readback_verified") is not True
                    or payload.get("failures") or binding.get("project_id") != run.project_id
                    or binding.get("run_id") != run.run_id or binding.get("base") != run.base.to_dict()
                    or binding.get("program_digest") != seat.program_digest or exact.get("exact_brep") is not True):
                raise ValueError("final STEP receipt does not certify this seat's current run/program")
            path = Path(cad["model"])
            if path.name != exact["relative_path"] or _sha256_file(path) != exact["sha256"]:
                raise ValueError("final STEP bytes do not match the retained CAD receipt")
            cold = read_step(path, length_unit="meter")
            names = [entry.name for entry in cold]
            if len(names) != len(set(names)) or set(names) != set(physical):
                raise ValueError("final STEP names do not match the retained physical objects")
            entries.extend(entry for entry in cold if entry.name in wanted)
            execution_refs.append(ref.uri)
        except (ValueError, TypeError, KeyError, OSError, ProjectIntegrityError, OcctBackendError) as exc:
            problems.append(f"{seat.seat_id}: {exc}")
    measurements = measure_occt_solid_pairs(entries, object_pairs=pairs, length_unit="meter") if entries else {}
    for pair in pairs:
        if pair not in measurements or measurements[pair]["status"] == "unchecked":
            row = measurements.setdefault(pair, {"status": "unchecked", "distance_m": None, "common_volume_m3": None})
            reason = row.get("detail") or "no verified final OCCT solid export in this run"
            row["detail"] = f"{reason}{'; ' + '; '.join(problems) if problems else ''}"
    return check_relations(record, bounds={}, objects_by_element=objects_by_element, relations=relations,
                           solid_measurements=measurements), tuple(sorted(set(execution_refs)))


@dataclass(frozen=True, slots=True)
class Produced:
    """What one seat's rows produced, in deterministic order."""

    operations: tuple[GeometryOperation, ...]
    bindings: tuple[DatumBinding, ...]
    assemblies: tuple[HostedAssembly, ...] = ()
    datums: tuple[InterfaceDatum, ...] = ()


def _gather(elements) -> Produced:
    ops = [op for e in elements for op in e.operations]
    ids = [op.op_id for op in ops]
    if len(set(ids)) != len(ids):
        raise ProjectRunnerError("element producers emitted duplicate operation ids")
    return Produced(tuple(sorted(ops, key=lambda o: o.op_id)), tuple(sorted((b for e in elements for b in e.bindings), key=lambda b: b.binding_id)),
                    tuple(sorted((a for e in elements for a in e.assemblies), key=lambda a: a.assembly_id)), tuple(sorted((d for e in elements for d in e.datums), key=lambda d: d.datum_id)))


@dataclass
class _RelationLedger:
    """The relations a run owes a measurement, and what has been realized to measure them on.

    A relation is *ready* when every input its declared checker reads is
    there: each endpoint has extent - a ``Level@1`` (an unbounded datum), a
    ``Space@1`` zone (its volumes' declared boxes), or an element some seat
    has produced objects for - and, for a checker that compares against the
    relation's ``datum_role`` (``_DATUM_READING_CHECKS``), that named datum
    has been published. The record's own relations and the ``support``
    relations the producers build wait in ``pending`` until they are ready
    and are then measured once, by ``check_relations`` against everything
    realized so far - so a relation between the structure seat's columns and
    the envelope seat's wall is measured when the wall exists, in the
    envelope seat's own report; a wall's support on the ground is not asked
    of the structure seat, which has no wall to measure; and a support
    between two structure members whose ``datum_role`` names a datum the
    envelope seat publishes waits for the envelope seat, where a datum that
    disagrees with the measured face fails the check instead of being
    skipped. The seat order itself is untouched: a relation waits, nothing
    is reordered.

    What is still pending when the run ends is ``unmeasured``: reported as
    ``unchecked`` with the endpoint that never acquired extent or the datum
    no seat published, never discarded, so the closure can refuse to exit
    over it.

    A zone is measurable from the start: each ``Space@1`` enters ``objects``
    under its own entity id, with one synthetic bound per ``volume_ids`` entry
    taken from that ``Volume@1``'s declared ``min`` / ``max`` (the one reader
    of that box is ``volume_boxes_of``). Realized geometry always wins over a
    synthetic bound with the same id. All bounds are compiler-predicted
    (``RELATION_CHECK_BASIS``); nothing here reads a saved CAD box.
    Explicit solid_nonpenetration relations remain pending for the final
    STEP measurement after every seat has had its export opportunity.
    """

    record: StateRecord
    pending: dict[str, Relation]
    bounds: dict[str, tuple[list[float], list[float]]]
    objects: dict[str, list[str]]
    datum_values: dict[str, float]
    seen: set[str]

    @classmethod
    def open(cls, record: StateRecord, levels: ProjectLevels) -> "_RelationLedger":
        bounds: dict[str, tuple[list[float], list[float]]] = {}
        objects: dict[str, list[str]] = {}
        volumes = volume_boxes_of(record)
        for zone in record.entities_of("Space@1"):
            zone_objects = []
            for volume_id in zone.fields.get("volume_ids", ()):
                box = volumes.get(volume_id)
                if box is None:
                    continue
                bounds.setdefault(volume_id, ([*box[0]], [*box[1]]))
                zone_objects.append(volume_id)
            if zone_objects:
                objects.setdefault(zone.entity_id, zone_objects)
        return cls(record, {r.relation_id: r for r in record.relations}, bounds, objects,
                   {l.level_id: l.elevation for l in levels.levels}, {r.relation_id for r in record.relations})

    def realize(self, rows, elements, produced: Produced, realized) -> None:
        """Add one seat's production: its compiled bounds, its objects per element, its published datums, its support relations."""

        import json as _json

        self.bounds.update({oid: ([*low], [*high]) for oid, (low, high) in realized.items()})
        for row, element in zip(rows, elements):
            # what the compiled program keeps: a wall's cut body and its aperture fills, not the
            # uncut body or the void tool a boolean consumed (those have no compiled bounds)
            object_ids = [oid for op in element.operations for oid in op.output_object_ids if oid in realized]
            if object_ids:                              # a declined row has no extent and stays unmeasurable
                self.objects[row.element_id] = object_ids
        self.datum_values.update({d.datum_id: float(_json.loads(d.value_json)) for d in produced.datums})
        known = {e.entity_id for e in self.record.entities}
        for element in elements:
            for r in element.relations:
                if r.kind != "support" or r.subject not in known or r.object not in known or r.relation_id in self.seen:
                    continue                            # a relation the record declares itself keeps the record's validator
                self.seen.add(r.relation_id)
                self.pending[r.relation_id] = Relation(r.relation_id, r.kind, r.subject, r.object, datum_role=r.datum_id, propagation="revalidate",
                                                       validator=ValidatorBinding("support_contact", tolerance=0.001), parameters=dict(r.parameters))

    def _missing_inputs(self, relation: Relation) -> tuple[str, ...]:
        """What the relation's checker would read and nothing has supplied yet: endpoints without extent, then the unpublished datum.

        Only the inputs the *declared* checker actually reads count. A relation
        without a validator, or bound to a checker that never looks at
        ``datum_values``, waits for its endpoints alone; guessing a checker or
        a datum need from ``relation.kind`` is exactly what this does not do.
        """

        missing = []
        for end in (relation.subject, relation.object):
            entity = self.record.entity(end)
            if entity.schema != "Level@1" and end not in self.objects:
                missing.append(end)
        if (relation.validator is not None and relation.validator.check_kind in _DATUM_READING_CHECKS
                and relation.datum_role is not None and relation.datum_role not in self.datum_values):
            missing.append(f"datum {relation.datum_role}")
        return tuple(missing)

    def check(self, elements=()) -> RelationCheckReport:
        """Measure every pending relation whose checker inputs are all present now; the rest keep waiting."""

        ready = tuple(r for r in self.pending.values()
                      if not (r.validator and r.validator.check_kind == "solid_nonpenetration") and not self._missing_inputs(r))
        for relation in ready:
            del self.pending[relation.relation_id]
        return check_relations(self.record, bounds=self.bounds, objects_by_element=self.objects, datum_values=self.datum_values, relations=ready)

    def unmeasured(self) -> RelationCheckReport | None:
        """Every relation still pending, as ``unchecked`` naming the endpoint no seat gave extent or the datum none published; None when nothing is pending."""

        if not self.pending:
            return None
        checks = []
        for relation in self.pending.values():
            missing = self._missing_inputs(relation)
            extents = [m for m in missing if not m.startswith("datum ")]
            datums = [m.removeprefix("datum ") for m in missing if m.startswith("datum ")]
            reasons = []
            if extents:
                reasons.append(f"no seat realized extent for {', '.join(extents)}")
            if datums:
                reasons.append(f"no seat published datum {', '.join(datums)}, which its {relation.validator.check_kind} check compares against")
            checks.append(RelationCheck(relation.relation_id, relation.kind, relation.validator.check_kind if relation.validator else "none", "unchecked", 0.0, {},
                                        f"not measurable in this run: {'; '.join(reasons)} ({RELATION_CHECK_BASIS})"))
        return RelationCheckReport(self.record.digest, tuple(checks))


def _check_produced_relations(record: StateRecord, rows, elements, produced: Produced, realized, levels: ProjectLevels):
    """One seat's production measured on its own: the ready relations checked, the rest reported unchecked (RelationCheck@1).

    The single-seat reading of ``_RelationLedger``; the runner itself keeps one
    ledger across all seats so a cross-seat relation is measured when its second
    endpoint appears.
    """

    ledger = _RelationLedger.open(record, levels)
    ledger.realize(rows, elements, produced, realized)
    report = ledger.check(elements)
    leftover = ledger.unmeasured()
    return RelationCheckReport(record.digest, report.checks + (leftover.checks if leftover is not None else ()))


def _exclusion_bounds(handover) -> tuple:
    import json as _json

    out = []
    for c in handover.constraints:
        if c.kind.value != "exclusion_bounds":
            continue
        payload = _json.loads(c.payload_json)
        low, high = payload.get("min"), payload.get("max")
        if low is not None and high is not None:
            out.append((tuple(float(v) for v in low), tuple(float(v) for v in high)))
    return tuple(out)


def _seat_dict(seat: SeatResult) -> dict[str, object]:
    return {"seat_id": seat.seat_id, "round": seat.round_index, "status": seat.status, "program_ref": seat.program_ref, "program_digest": seat.program_digest,
            "objects": seat.objects, "covered_components": list(seat.covered_components), "undeclared_components": list(seat.undeclared_components),
            "declined_components": list(seat.declined), "declination_reasons": dict(seat.declination_reasons),
            "issues": list(seat.issues), "wall_time_s": round(seat.wall_time_s, 3), "cad": seat.cad, "receipt_ref": seat.receipt_ref, "relation_check_ref": seat.relation_check_ref}
