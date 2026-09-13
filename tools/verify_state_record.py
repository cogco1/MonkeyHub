"""Prove a State Record reproduces a reference runner run (P102 migration receipt).

    python tools/verify_state_record.py --project <project root> \\
        --reference-run runner-002 --run equivalence-001 [--rename old=new ...] \\
        [--export [--cad-backend occt|rhino|blender]]

``--export`` defaults to OCCT unless another ``--cad-backend`` is named, exactly
as ``tools/run_project.py`` does; ``--patch-oracle`` is Rhino's patch check and
is refused under the other backends rather than ignored.

The record and the seats are the project's own work in progress, read by
``archflow.project.inputs`` at ``input/runner/`` under ``--project``; there is
no pack directory to point elsewhere, because the claim is about the record
this project holds (ADR-007).

Two checks, both recorded in ``--run`` as ``state-record-equivalence``:

1. State identity — the developed state the record yields through
   ``developed_design_view`` (with the reference run's RunRef, branch and
   selection) has the same digest the reference run recorded.
2. Geometry — the record is run through the real runner (``run_project``,
   canonical producers, seats, handovers) inside ``--run`` under an
   *equivalence-harness* workflow frozen in that run (one stage in the
   phase the reference run's stage ran in, clearly labelled; it is not the
   project's own stage workflow and grants nothing), and every seat program's analytic bounds
   are compared object by object with the reference run's seat programs.
   ``--rename old=new`` maps element ids the record had to rename (an
   element id may not collide with a component id inside one record).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.adapters.cad_program import expected_object_bounds  # noqa: E402
from monkeyarch.capabilities.geometry_proposal import GeometryProposalProviderIdentity, load_compiled_geometry_program  # noqa: E402
from archflow.contracts.authority import no_authority  # noqa: E402
from archflow.project.inputs import load_authored_record, load_seat_pack_file  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (  # noqa: E402
    EQUIVALENCE_HARNESS_ENVELOPE,
    EQUIVALENCE_HARNESS_WORKFLOW,
    RUNNER_RUN_RECEIPT,
    STATE_RECORD_EQUIVALENCE,
)
from archflow.project.refs import parse_record_file_name, record_ref_from_uri  # noqa: E402
from monkeyarch.runtime.project_runner import (  # noqa: E402
    CAD_BACKEND_OCCT,
    CAD_BACKEND_RHINO,
    CAD_BACKENDS,
    RunOptions,
    StageExecutionGuard,
    run_project,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.operational_state import DesignObligation  # noqa: E402
from archflow.state.stage_workflow import ProjectStage, ProjectStageWorkflow, open_stage_run_envelope  # noqa: E402
from archflow.state.state_record import developed_design_view  # noqa: E402
from tools.run_project import _seat  # noqa: E402

_AUTH = ("canonical_write_authority", "design_authority", "stage_acceptance_authority")


def _latest(records_dir: Path, record_kind: str) -> Path:
    # The record file-name rule lives in archflow.project.refs; the kind it
    # reads back is compared by equality, never as a prefix, so a longer kind
    # that happens to start with this one is not this one.
    paths = []
    for path in records_dir.glob(f"{record_kind}-*.json"):
        try:
            name_kind, _ = parse_record_file_name(path.name)
        except ValueError:
            continue
        if name_kind == record_kind:
            paths.append(path)
    paths.sort(key=lambda path: path.stat().st_mtime)
    if not paths:
        raise SystemExit(f"no {record_kind} record in {records_dir}")
    return paths[-1]


def _receipt_phase(receipt: dict) -> DesignPhase:
    """The phase a runner receipt says its stage ran in; design_development for a receipt that states none."""

    stage = receipt.get("stage")
    phase = stage.get("phase") if isinstance(stage, dict) else None
    return DesignPhase(phase) if isinstance(phase, str) else DesignPhase.DESIGN_DEVELOPMENT


def _harness_guard(repository, run, state, options) -> StageExecutionGuard:
    """The harness stage this comparison runs under; it closes nothing.

    The stage requires no check. A workflow may only require checks the spine
    can measure (ADR-007 rule 3), and the equivalence claim this tool makes is
    not one of them: it is a comparison against a reference run, written here
    as ``state-record-equivalence``, not a relation the runner measured. So
    the harness names no requirement and its closure states only that the
    seats ran — which is all a harness ever closes.

    The stage's phase is the one ``state`` was projected in (the reference
    run's, see ``main``): the runner projects the record in the envelope's
    phase and admits only seats named for it, so a harness fixed to one phase
    could never verify a reference run made in another.
    """

    workflow = ProjectStageWorkflow(
        project_id=run.project_id, workflow_id="equivalence-harness",
        stages=(ProjectStage(stage_id="equivalence-check", stage_index=0, phase=state.active_phase, required_roles=("geometry-program",),
                             required_checks=(), close_obligation_id="close-equivalence-check"),),
        basis_refs=("decision:state-record-equivalence-harness",))
    destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id)
    workflow_ref = repository.put_json(run=run, destination=destination, record_kind=EQUIVALENCE_HARNESS_WORKFLOW, payload=workflow.to_dict())
    envelope = open_stage_run_envelope(
        workflow, workflow_ref=workflow_ref.uri, run_id=run.run_id, base_version=run.base.version, base_state_sha256=run.base.require_digest(),
        branch_id=options.branch_id, branch_epoch=options.branch_epoch, subject_ref="state:developed-design-state", state_digest=state.state_digest, stage_index=0,
        close_obligation=DesignObligation(obligation_id="close-equivalence-check", statement="An equivalence harness stage; it closes nothing in the project's own workflow.",
                                          source_ref="workflow:equivalence-harness/stage-0", subject_refs=("state:developed-design-state",), validator_ref="validator:composite-stage-closure"))
    envelope_ref = repository.put_json(run=run, destination=destination, record_kind=EQUIVALENCE_HARNESS_ENVELOPE, payload=envelope.to_dict())
    return StageExecutionGuard(workflow=workflow, workflow_record_ref=workflow_ref, envelope=envelope, envelope_record_ref=envelope_ref)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove the project's authored State Record, read from its own input/runner/ "
                    "(ADR-007), reproduces a reference runner run; both checks are retained in --run.",
    )
    parser.add_argument("--project", required=True, help="project root; its input/runner/ holds the record and the seats")
    parser.add_argument("--reference-run", required=True); parser.add_argument("--run", required=True)
    parser.add_argument("--rename", action="append", default=[], help="old=new element id mapping")
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--compare-only", action="store_true", help="compare the latest runner receipt already in --run instead of running again")
    parser.add_argument("--export", action="store_true", help="also export each seat program through --cad-backend")
    parser.add_argument("--cad-backend", choices=CAD_BACKENDS, default=CAD_BACKEND_OCCT,
                        help="which executor an --export goes to: occt (default; in process, exact STEP plus a mesh .3dm preview, reused when an "
                             "intact retained export of the same binding exists) or rhino (the supervised host export: full rebuild, or a patch "
                             "when a prior export exists), or blender (closed meshes saved to .blend and cold-read; "
                             "solid/straight-extrusion only; executable on PATH)")
    parser.add_argument("--patch-oracle", action="store_true",
                        help="with --export --cad-backend rhino: rebuild in full beside every patch and compare; refused under the other backends")
    parser.add_argument("--powershell", default=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", help="used by --cad-backend rhino only")
    args = parser.parse_args()
    if args.patch_oracle and args.cad_backend != CAD_BACKEND_RHINO:
        # the oracle is Rhino's patch check; under OCCT it would either start Rhino unasked or be
        # reported as an oracle that never ran, so the request is refused naming the backend that has one
        parser.error(f"--patch-oracle is the Rhino patch check: name --cad-backend {CAD_BACKEND_RHINO} to request it "
                     f"(the {args.cad_backend} backend neither patches nor runs an oracle)")
    renames = dict(item.split("=", 1) for item in args.rename)
    repository = FilesystemProjectRepository.open(Path(args.project).resolve())
    record = load_authored_record(repository).record
    seats_payload = load_seat_pack_file(repository).payload
    seats = tuple(_seat(s) for s in seats_payload["seats"])
    # What a live provider would have to present; the runner records its own proposals,
    # so a pack that declares no provider identity still runs.
    declared = seats_payload.get("provider_identity")
    identity = None if declared is None else GeometryProposalProviderIdentity(**declared)
    reference = repository.load_run(args.reference_run)
    reference_records = Path(repository.layout.run(reference.run_id).records)
    reference_receipt = json.loads(_latest(reference_records, RUNNER_RUN_RECEIPT).read_text(encoding="utf-8"))
    options = RunOptions(commitment_ref=seats_payload["commitment_ref"], live_provider_identity=identity, branch_id=seats_payload.get("branch_id", "runner-v1"))
    if args.export:
        workspace_root = Path(args.project).resolve() / "runs" / args.run / "workspaces"
        options = RunOptions(commitment_ref=options.commitment_ref, live_provider_identity=identity, branch_id=options.branch_id, export=True, cad_backend=args.cad_backend,
                             workspace_root=workspace_root, powershell=Path(args.powershell), patch_oracle=args.patch_oracle)
        for seat in seats:
            if not seat.reviewer:
                (workspace_root / f"cad-equivalence-check-{seat.seat_id}").mkdir(parents=True, exist_ok=True)

    # 1. state identity against the reference run, projected in the phase that run's stage states
    #    (RunnerRunReceipt@3 carries it under ``stage.phase``); an older receipt that names no phase
    #    was written when the projection was fixed to design_development, so that is what it is read in
    reference_phase = _receipt_phase(reference_receipt)
    reference_state = developed_design_view(record, run=reference, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref,
                                            phase=reference_phase)
    state_equal = reference_state.state_digest == reference_receipt.get("design_state_digest")

    # 2. geometry through the real runner in the equivalence run
    try:
        run = repository.load_run(args.run)
    except Exception:
        run = repository.create_run(args.run)
    if args.compare_only:
        receipt = json.loads(_latest(Path(repository.layout.run(run.run_id).records), RUNNER_RUN_RECEIPT).read_text(encoding="utf-8"))
    else:
        # the same phase as the identity check above: the harness stage is opened in it and the
        # runner projects the record in it, so the seat pack is admitted exactly as it was in the reference run
        state = developed_design_view(record, run=run, portfolio_id=options.portfolio_id, branch_id=options.branch_id, selection_decision_ref=options.selection_decision_ref,
                                      phase=reference_phase)
        guard = _harness_guard(repository, run, state, options)
        receipt = run_project(repository, run=run, stage_guard=guard, record=record, seats=seats, options=options)
    # RunnerRunReceipt@1 (first cut) listed seats under "stages"; @2/@3 under "seat_results"
    reference_seats = {s["seat_id"]: s for s in (reference_receipt.get("seat_results") or reference_receipt.get("stages") or ())}
    comparisons = []
    worst = 0.0
    for seat in receipt["seat_results"]:
        ref_seat = reference_seats.get(seat["seat_id"])
        if seat["status"] != "proposal_accepted" or not ref_seat or ref_seat.get("status") not in ("proposal_accepted", "accepted"):
            comparisons.append({"seat_id": seat["seat_id"], "status": seat["status"], "reference_status": (ref_seat or {}).get("status"), "compared": 0})
            continue
        new_program = load_compiled_geometry_program(repository.load_json(record_ref_from_uri(seat["program_ref"], run.project_id)))
        old_program = load_compiled_geometry_program(repository.load_json(record_ref_from_uri(ref_seat["program_ref"], run.project_id)))
        new_bounds, old_bounds = expected_object_bounds(new_program), expected_object_bounds(old_program)
        mapped_old = {}
        for oid, row in old_bounds.items():
            name = oid
            for old_id, new_id in renames.items():
                if oid == f"obj-{old_id}" or oid.startswith(f"obj-{old_id}-"):
                    name = f"obj-{new_id}" + oid[len(f"obj-{old_id}"):]
            mapped_old[name] = row
        rows, missing, extra = [], sorted(set(mapped_old) - set(new_bounds)), sorted(set(new_bounds) - set(mapped_old))
        for oid in sorted(set(mapped_old) & set(new_bounds)):
            a, b = new_bounds[oid], mapped_old[oid]
            delta = max(abs(x - y) for x, y in zip(a["bbox_min"] + a["bbox_max"], b["bbox_min"] + b["bbox_max"]))
            worst = max(worst, delta)
            rows.append({"object": oid, "max_abs_delta_m": round(delta, 9), "equal": delta <= args.tolerance})
        comparisons.append({"seat_id": seat["seat_id"], "status": seat["status"], "compared": len(rows), "missing_in_record": missing, "extra_in_record": extra,
                            "all_equal": all(r["equal"] for r in rows) and not missing and not extra, "worst_m": round(max((r["max_abs_delta_m"] for r in rows), default=0.0), 9),
                            "differences": [r for r in rows if not r["equal"]][:20], "relation_check_ref": seat.get("relation_check_ref")})
    geometry_equal = all(c.get("all_equal", False) for c in comparisons if c.get("compared", 0) or c.get("status") == "proposal_accepted")
    payload = {"schema": "StateRecordEquivalence@1", "project_id": run.project_id, "run_id": run.run_id, "reference_run_id": reference.run_id,
               "state_record_digest": record.digest, "state_record_ref": receipt["state_record_ref"], "renames": renames, "tolerance_m": args.tolerance,
               "state_digest_equal": state_equal, "state_digest": reference_state.state_digest, "reference_state_digest": reference_receipt.get("design_state_digest"),
               "reference_phase": reference_phase.value,
               "geometry_equal": geometry_equal, "worst_m": round(worst, 9), "seats": comparisons, "runner_receipt_ref": receipt.get("receipt_ref"),
               "harness": "equivalence-harness workflow frozen in this run; grants no stage authority", **no_authority(_AUTH)}
    ref = repository.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id), record_kind=STATE_RECORD_EQUIVALENCE, payload=payload)
    print(json.dumps({k: payload[k] for k in ("state_digest_equal", "geometry_equal", "worst_m")}), ref.uri)
    for c in comparisons:
        print(" ", c["seat_id"], c["status"], "compared", c.get("compared"), "equal", c.get("all_equal"), "worst", c.get("worst_m"), "missing", c.get("missing_in_record"), "extra", c.get("extra_in_record"))
    for seat in receipt["seat_results"]:
        cad = seat.get("cad") or {}
        if cad:
            print("  cad", seat["seat_id"], cad.get("status"), "readback", cad.get("readback_verified"), "path", cad.get("path"), "seconds", cad.get("seconds"), "rebuilt", cad.get("rebuilt_objects"), "kept", cad.get("kept_objects"),
                  "oracle", (cad.get("oracle") or {}).get("equal"), (cad.get("oracle") or {}).get("seconds"))
    return 0 if state_equal and geometry_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
