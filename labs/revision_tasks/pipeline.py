"""Realize a bound fixture record through the production path, without a project.

record --element_rows_of/produce_rows--> operations --compile_geometry_program-->
program --execute_occt_export--> STEP + preview 3DM in a disposable workspace,
read back cold by the exporter itself. Every step is the owner's public
function; this module only assembles the proposal the way the project runner
does and supplies a synthetic CAD binding (no record is persisted, so the
binding names a record path that does not exist; the exporter checks its form,
never opens it).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef
from archflow.state.geometry_program import (
    AffineTransform,
    CompiledGeometryProgram,
    CoordinateFrame,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
)
from archflow.state.state_record import (
    StateRecord,
    StateRecordEditKind,
    StateRecordError,
    StateRecordOperator,
    apply_state_record_operator,
    evaluate_parameters,
    project_grids_of,
    project_levels_of,
)
from monkeyarch.authoring.element_producers import ProductionContext, element_rows_of, produce_rows, production_order
from monkeyarch.compilation.geometry import compile_geometry_program
from monkeyarch.domain.reference_resolver import ReferenceContext
from monkeyarch.domain.relation_checks import check_relations
from monkeycad.backends.occt.export import execute_occt_export
from monkeycad.backends.occt.measure import measure_occt_solid_pairs
from monkeycad.backends.occt.step import read_step
from monkeycad.execution import CadProgramBinding

FRAME_ID = "world"
COMMITMENT = "commitment:revision-task"


class RealizationError(RuntimeError):
    """The record could not be produced, compiled or exported; ``code`` names the stage."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Realization:
    program: CompiledGeometryProgram
    objects_by_element: dict[str, tuple[str, ...]]
    datum_values: dict[str, float]
    receipt: Any
    step_path: Path
    preview_path: Path
    phases_s: dict[str, float]


def compile_record(record: StateRecord) -> tuple[CompiledGeometryProgram, dict[str, tuple[str, ...]], dict[str, float]]:
    """Producers then the compiler, over a record already bound to its run."""

    levels = project_levels_of(record)
    context = ProductionContext(references=ReferenceContext(grids=project_grids_of(record), levels=levels), published={}, frame_id=FRAME_ID)
    try:
        produced = produce_rows(production_order(element_rows_of(record)), context)
    except Exception as exc:  # noqa: BLE001 - the producers' typed refusals all land here
        raise RealizationError("PRODUCE_REFUSED", f"{type(exc).__name__}: {exc}") from exc
    operations = tuple(op for element in produced for op in element.operations)
    assemblies = tuple(a for element in produced for a in element.assemblies)
    by_component: dict[str, list[str]] = {}
    for op in operations:
        by_component.setdefault(op.semantic_binding_ids[0].removeprefix("binding-"), []).extend(op.output_object_ids)
    bindings = tuple(
        SemanticBinding(binding_id=f"binding-{c}", component_id=c, object_ids=tuple(sorted(o)), commitment_refs=(COMMITMENT,),
                        evidence_refs=tuple(record.evidence_refs))
        for c, o in sorted(by_component.items()))
    frame = CoordinateFrame(frame_id=FRAME_ID, parent_frame_id=None, transform_from_parent=AffineTransform.identity(),
                            source_refs=tuple(record.evidence_refs[:1]))
    proposal = GeometryProgramProposal(
        proposal_id=f"{record.run_id}-proposal", project_id=record.project_id, run_id=record.run_id, base=record.base,
        design_state_digest=record.state_digest, predecessor_program_digest=None, length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001), frames=(frame,), assets=(), semantic_bindings=bindings,
        operations=tuple(sorted(operations, key=lambda o: o.op_id)), assemblies=tuple(sorted(assemblies, key=lambda a: a.assembly_id)))
    datums = tuple(sorted(list(context.published.values()) + list(levels.datums()), key=lambda d: d.datum_id))
    datum_bindings = tuple(b for element in produced for b in element.bindings)
    result = compile_geometry_program(record, proposal, active_commitment_refs=(COMMITMENT,), interface_datums=datums,
                                      datum_bindings=datum_bindings)
    if result.program is None:
        issues = "; ".join(f"{i.code.value}:{i.subject_id}:{i.detail}" for i in result.receipt.issues)
        raise RealizationError("COMPILE_REFUSED", issues)
    rows = element_rows_of(record)
    objects_by_element: dict[str, tuple[str, ...]] = {}
    for row, element in zip(production_order(rows), produced):
        objects_by_element[row.element_id] = tuple(sorted(o for op in element.operations for o in op.output_object_ids))
    datum_values = {d.datum_id: context.datum_value(d.datum_id) for d in datums}
    return result.program, objects_by_element, datum_values


def synthetic_binding(program: CompiledGeometryProgram) -> CadProgramBinding:
    """A CAD binding of the exact form the exporter requires, naming a program record that was never written."""

    proposal = program.proposal
    record_sha = hashlib.sha256(program.program_digest.encode("ascii")).hexdigest()
    branch = BranchRef(run=RunRef(proposal.project_id, proposal.run_id, proposal.base), branch_id="candidate", epoch=1)
    return CadProgramBinding(
        program_ref=ProjectRecordRef(project_id=proposal.project_id,
                                     relative_path=f"runs/{proposal.run_id}/branches/candidate/records/task-geometry-program-{record_sha}.json",
                                     sha256=record_sha, media_type="application/json"),
        branch=branch, stage_id="task", program_digest=program.program_digest,
        design_state_digest=proposal.design_state_digest, predecessor_program_digest=proposal.predecessor_program_digest)


def realize(record: StateRecord, workspace: Path, stem: str, *, prior: Realization | None = None,
            provenance: dict[str, str] | None = None) -> Realization:
    """Compile and export ``record`` into ``workspace``; with ``prior``, unchanged shapes come from its verified STEP.

    ``provenance`` adds caller keys to the model's ``archflow:*`` document strings (``execute_occt_export``).
    """

    started = time.perf_counter()
    program, objects_by_element, datum_values = compile_record(record)
    compiled = time.perf_counter()
    incremental: dict[str, Any] = {}
    if prior is not None:
        incremental = {"prior_program": prior.program, "prior_step": prior.step_path,
                       "prior_step_sha256": hashlib.sha256(prior.step_path.read_bytes()).hexdigest()}
    receipt = execute_occt_export(program, binding=synthetic_binding(program), speculative_workspace=workspace, artifact_stem=stem,
                                  provenance=provenance, **incremental)
    exported = time.perf_counter()
    if receipt.status.value != "succeeded" or not receipt.readback_verified:
        failures = "; ".join(f"{f.get('code')}: {f.get('detail')}" for f in receipt.failures) if receipt.failures else receipt.status.value
        raise RealizationError("EXPORT_FAILED", failures)
    return Realization(program, objects_by_element, datum_values, receipt, workspace / f"{stem}.step", workspace / f"{stem}.preview.3dm",
                       {"compile_s": compiled - started, "occt_export_and_readback_s": exported - compiled})


# ---------------------------------------------------------------- what the system itself checks after execution

def _r(value: float) -> float:
    return round(float(value), 6) + 0.0


def visible_objects(realization: Realization) -> tuple[str, ...]:
    """Physical objects a person sees: hidden inspection witnesses (an aperture's void, say) are left out."""

    objects = realization.receipt.expected_semantics["objects"]
    return tuple(sorted(o for o in realization.receipt.readback if objects.get(o, {}).get("visible", True)))


def program_bounds(realization: Realization) -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:
    """Cold-read STEP bounds, turned from the CAD frame (x, plan, up) back to the program frame (x, up, plan)."""

    bounds = {}
    for object_id, row in realization.receipt.readback.items():
        lo, hi = row["bbox"]["min"], row["bbox"]["max"]
        bounds[object_id] = ((lo[0], lo[2], lo[1]), (hi[0], hi[2], hi[1]))
    return bounds


def declared_checks(record: StateRecord, realization: Realization) -> list[dict[str, Any]]:
    """The record's own relation validators, measured on the cold-read STEP bounds (``check_relations``)."""

    bounds = program_bounds(realization)
    by_element = {e: tuple(o for o in objs if o in bounds) for e, objs in realization.objects_by_element.items()}
    report = check_relations(record, bounds=bounds, objects_by_element=by_element, datum_values=realization.datum_values)
    return [{"relation_id": c.relation_id, "check_kind": c.check_kind, "status": c.status,
             "measured": {k: _r(v) for k, v in sorted(c.measured.items())}} for c in report.checks]


def obligation_checks(record: StateRecord) -> list[dict[str, Any]]:
    """Open obligations the record declares with an at-most or at-least validator, evaluated on its parameters.

    The kernel stores obligations and evaluates none; these two predicates belong
    to the harness, and they read only what the record declares.
    """

    from labs.revision_tasks.fixtures import AT_LEAST, AT_MOST

    values = evaluate_parameters(record)
    results = []
    for obligation in record.obligations:
        if obligation.validator_ref not in (AT_MOST, AT_LEAST) or obligation.status.value != "open":
            continue
        left, right = (ref.removeprefix("parameter:") for ref in obligation.subject_refs)
        holds = values[left] <= values[right] + 1e-9 if obligation.validator_ref == AT_MOST else values[left] >= values[right] - 1e-9
        results.append({"obligation_id": obligation.obligation_id, "validator": obligation.validator_ref, "left": left, "right": right,
                        "left_value": _r(values[left]), "right_value": _r(values[right]), "status": "held" if holds else "violated"})
    return results


def penetrating_pairs(realization: Realization) -> dict[tuple[str, str], float]:
    """Pairs of visible solids whose cold-read boxes overlap on all three axes and that OCCT finds penetrating."""

    rows = realization.receipt.readback
    names = visible_objects(realization)
    candidates = []
    for i, a in enumerate(names):
        alo, ahi = rows[a]["bbox"]["min"], rows[a]["bbox"]["max"]
        for b in names[i + 1:]:
            blo, bhi = rows[b]["bbox"]["min"], rows[b]["bbox"]["max"]
            if all(alo[k] < bhi[k] - 1e-6 and blo[k] < ahi[k] - 1e-6 for k in range(3)):
                candidates.append((a, b))
    if not candidates:
        return {}
    entries = read_step(realization.step_path, length_unit="meter")
    measured = measure_occt_solid_pairs(entries, object_pairs=candidates, length_unit="meter")
    return {pair: _r(row["common_volume_m3"]) for pair, row in measured.items()
            if row.get("status") == "penetrating" and row.get("common_volume_m3")}


def _covered_pairs(record: StateRecord, realization: Realization) -> set[frozenset[str]]:
    """Object pairs some declared relation already speaks about."""

    covered = set()
    for relation in record.relations:
        for a in realization.objects_by_element.get(relation.subject, ()):
            for b in realization.objects_by_element.get(relation.object, ()):
                covered.add(frozenset((a, b)))
    return covered


# ---------------------------------------------------------------- the ArchFlow arm (B3)

@dataclass
class ArmResult:
    claim: str                                  # accept | reopen | refuse | unknown
    reasons: list[dict[str, Any]]
    record: StateRecord | None = None
    realization: Realization | None = None
    operators: list[dict[str, Any]] = field(default_factory=list)
    closure: list[str] = field(default_factory=list)
    declared: list[dict[str, Any]] = field(default_factory=list)
    obligations: list[dict[str, Any]] = field(default_factory=list)
    new_interference: list[dict[str, Any]] = field(default_factory=list)
    phases_s: dict[str, float] = field(default_factory=dict)


def run_archflow_arm(spec: dict[str, Any], start: StateRecord, start_realization: Realization, workspace: Path) -> ArmResult:
    """Closure, execution, cold readback, declared checks, obligation evaluation and an undeclared-interference scan.

    The exact base is the task's frozen digests, never recomputed from the input.
    Refuses before executing when a value is missing, the base is stale, or an
    open obligation cannot hold; reopens when an executed candidate violates a
    declared relation or brings a new interference no declared relation covers.
    """

    t0 = time.perf_counter()
    deltas = spec["intended_change"]["typed_delta"]
    changed = tuple(d["target"] for d in deltas)
    closure = list(start.closure(changed))
    missing = [d["target"] for d in deltas if d["to"] is None]
    if missing:
        return ArmResult("refuse", [{"code": "MISSING_INFORMATION", "refs": missing,
                                     "detail": "the change names no value for these parameters"}], closure=closure)
    record, operators = start, []
    base_record, base_state = spec["start_state"]["content_digest"], spec["start_state"]["exact_base_digest"]
    for delta in deltas:
        operator = StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=base_record, base_state_digest=base_state,
                                       target_ref=delta["target"], key=delta["parameter"], value=delta["to"])
        try:
            record = apply_state_record_operator(record, operator)
        except StateRecordError as exc:
            code = "STALE_BASE" if "stale" in str(exc) else "OPERATOR_REFUSED"
            return ArmResult("refuse", [{"code": code, "detail": str(exc)}], operators=operators, closure=closure)
        operators.append(operator.to_dict())
        base_record, base_state = record.digest, record.state_digest
    obligations = obligation_checks(record)
    broken = [o["obligation_id"] for o in obligations if o["status"] == "violated"]
    if broken:
        return ArmResult("refuse", [{"code": "OBLIGATION_UNSATISFIABLE", "refs": broken,
                                     "detail": "after the change an open obligation cannot hold; nothing in the request relaxes it"}],
                         record=record, operators=operators, closure=closure, obligations=obligations)
    t1 = time.perf_counter()
    try:
        candidate = realize(record, workspace, "candidate", prior=start_realization)
    except RealizationError as exc:
        return ArmResult("refuse", [{"code": exc.code, "detail": exc.detail}], record=record, operators=operators, closure=closure,
                         obligations=obligations)
    t2 = time.perf_counter()
    declared = declared_checks(record, candidate)
    baseline = set(penetrating_pairs(start_realization))
    covered = _covered_pairs(record, candidate)
    new = [{"pair": list(pair), "common_volume_m3": volume} for pair, volume in sorted(penetrating_pairs(candidate).items())
           if pair not in baseline and frozenset(pair) not in covered]
    t3 = time.perf_counter()
    violated = [c["relation_id"] for c in declared if c["status"] == "violated"]
    reasons: list[dict[str, Any]] = [{"code": "DECLARED_RELATION_VIOLATED", "refs": violated}] if violated else []
    if new:
        reasons.append({"code": "UNDECLARED_INTERFERENCE", "refs": [" / ".join(n["pair"]) for n in new],
                        "detail": "the executed candidate brings solids into contact that no declared relation covers"})
    unchecked = [c["relation_id"] for c in declared if c["status"] == "unchecked"]
    claim = "reopen" if reasons else "unknown" if unchecked else "accept"
    if unchecked:
        reasons.append({"code": "DECLARED_RELATION_UNCHECKED", "refs": unchecked})
    return ArmResult(claim, reasons, record=record, realization=candidate, operators=operators, closure=closure, declared=declared,
                     obligations=obligations, new_interference=new,
                     phases_s={"patch_s": t1 - t0, "execute_s": t2 - t1, "system_checks_s": t3 - t2})
