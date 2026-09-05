"""Run a project from its State Record (P089 / P102).

    python tools/run_project.py --project <project root> --run <run id> \
      --workflow-ref project://... --stage-envelope-ref project://... \
      [--export [--cad-backend occt|rhino]] [--workspace <dir>]

``--export`` goes through OCCT unless ``--cad-backend rhino`` is named: an
exact STEP file and a mesh ``.3dm`` preview per seat, in process, retained as
``seat-occt-execution``. Rhino is never started by the default.

The design and the seats come from the project's own work-in-progress files —
``input/runner/state-record.json`` (``StateRecord@1``: components and massing,
levels and grid axes, element rows with references) and ``input/runner/seats.json``
(the provider identity and the commitment ref) — read by ``archflow.project.inputs``
at the paths the layout owns. There is no pack directory to point elsewhere: the
run executes the record the project holds (ADR-007). The run's records are the
receipt; this tool prints a summary only. The run must already exist and the
exact workflow and envelope must already be retained in P036 —
``tools/open_stage_run.py`` is what creates them; this CLI never turns a raw
run into a stage by side effect.

The runner closes the stage at the end of the run: it writes the closure from
its own checks and, when that closure is SATISFIED, the exit binding a
successor stage may open against. Both refs are printed with the summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.capabilities.declaration import DeclarationQuadrant  # noqa: E402
from archflow.capabilities.discipline_seats import SeatSpec  # noqa: E402
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity  # noqa: E402
from archflow.state.stage_workflow import CompositeStageClosureReceipt
from archflow.project.inputs import load_authored_record, load_seat_pack_file  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.refs import record_ref_from_uri  # noqa: E402
from archflow.state.state_record import StateRecord  # noqa: E402
from archflow.runtime.project_runner import (  # noqa: E402
    CAD_BACKEND_OCCT,
    CAD_BACKENDS,
    RunOptions,
    StageExecutionGuard,
    run_project,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline  # noqa: E402
from archflow.state.stage_workflow import (  # noqa: E402
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
)


def _stage_guard(repository, run, *, workflow_uri: str, envelope_uri: str) -> StageExecutionGuard:
    workflow_ref = record_ref_from_uri(workflow_uri, run.project_id)
    envelope_ref = record_ref_from_uri(envelope_uri, run.project_id)
    workflow = ProjectStageWorkflow.from_dict(repository.load_json(workflow_ref))
    envelope = StageRunEnvelope.from_dict(repository.load_json(envelope_ref))
    if envelope.predecessor is None:
        return StageExecutionGuard(
            workflow=workflow,
            workflow_record_ref=workflow_ref,
            envelope=envelope,
            envelope_record_ref=envelope_ref,
        )
    predecessor_ref = record_ref_from_uri(
        envelope.predecessor.envelope_ref,
        run.project_id,
    )
    exit_ref = record_ref_from_uri(
        envelope.predecessor.exit_binding_ref,
        run.project_id,
    )
    closure_ref = record_ref_from_uri(
        envelope.predecessor.exit_binding.closure_ref,
        run.project_id,
    )
    predecessor = StageRunEnvelope.from_dict(repository.load_json(predecessor_ref))
    exit_binding = StageExitBinding.from_dict(repository.load_json(exit_ref))
    closure = CompositeStageClosureReceipt.from_dict(repository.load_json(closure_ref))
    if exit_binding != envelope.predecessor.exit_binding:
        raise ValueError("embedded predecessor exit differs from its retained record")
    return StageExecutionGuard(
        workflow=workflow,
        workflow_record_ref=workflow_ref,
        envelope=envelope,
        envelope_record_ref=envelope_ref,
        predecessor=predecessor,
        predecessor_record_ref=predecessor_ref,
        predecessor_exit=exit_binding,
        predecessor_exit_record_ref=exit_ref,
        predecessor_closure=closure,
        predecessor_closure_record_ref=closure_ref,
    )


def _seat(payload: dict) -> SeatSpec:
    return SeatSpec(
        seat_id=payload["seat_id"], disciplines=tuple(DevelopmentDiscipline(d) for d in payload["disciplines"]),
        owned_component_ids=tuple(sorted(payload.get("owned_component_ids", ()))), phases=tuple(DesignPhase(p) for p in payload["phases"]),
        quadrants=tuple(DeclarationQuadrant(q) for q in payload.get("quadrants", ())), consumes=tuple(sorted(payload.get("consumes", ()))),
        reviewer=bool(payload.get("reviewer", False)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a project's authored State Record and seat pack, read from the "
                    "project root at input/runner/ (ADR-007), inside an already-retained stage.",
    )
    parser.add_argument("--project", required=True, help="project root; its input/runner/ holds the record and the seats")
    parser.add_argument("--run", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--stage-envelope-ref", required=True)
    parser.add_argument("--export", action="store_true", help="export every seat's compiled program through --cad-backend")
    parser.add_argument("--cad-backend", choices=CAD_BACKENDS, default=CAD_BACKEND_OCCT,
                        help="which executor an --export goes to: occt (default; in process, exact STEP plus a mesh .3dm preview) "
                             "or rhino (the supervised host export; never started unless named here)")
    parser.add_argument("--workspace")
    parser.add_argument("--powershell", default=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", help="used by --cad-backend rhino only")
    parser.add_argument("--relaxed-coverage", action="store_true")
    parser.add_argument("--patch-oracle", action="store_true", help="rhino only: when an export is patched, also rebuild in full and compare the two readbacks")
    parser.add_argument("--record-ref", help="run this state-record record (a candidate successor held in a run) instead of the authored record; the authored record is not touched")
    parser.add_argument("--seats-file", help="a seat pack JSON to partition this run by, instead of input/runner/seats.json (a compile partition for a candidate); the authored pack is not touched")
    args = parser.parse_args(argv)
    project_root = Path(args.project).resolve()
    repository = FilesystemProjectRepository.open(project_root)
    if args.record_ref:
        record = StateRecord.from_dict(repository.load_json(record_ref_from_uri(args.record_ref, repository.read_head().project_id)))
        print(f"record: {args.record_ref} (digest {record.digest[:16]}...)")
    else:
        record = load_authored_record(repository).record
    if args.seats_file:
        seats_payload = json.loads(Path(args.seats_file).read_text(encoding="utf-8"))
        if not isinstance(seats_payload, dict) or "seats" not in seats_payload or "commitment_ref" not in seats_payload:
            raise ValueError(f"{args.seats_file}: a seat pack is a JSON object with seats and commitment_ref")
        print(f"seats: {args.seats_file}")
    else:
        seats_payload = load_seat_pack_file(repository).payload
    seats = tuple(_seat(s) for s in seats_payload["seats"])
    # The pack's provider identity is what a live provider would have to present. The
    # runner records its own proposals, so a pack that declares none still runs.
    declared = seats_payload.get("provider_identity")
    identity = None if declared is None else GeometryProposalProviderIdentity(**declared)
    if not repository.layout.run(args.run).manifest.exists():
        raise ValueError(
            "stage run does not exist; create and retain its envelope before invoking the runner"
        )
    # Preserve typed repository-integrity failures.  A corrupt manifest is
    # not equivalent to an absent stage run.
    run = repository.load_run(args.run)
    stage_guard = _stage_guard(
        repository,
        run,
        workflow_uri=args.workflow_ref,
        envelope_uri=args.stage_envelope_ref,
    )
    options = RunOptions(commitment_ref=seats_payload["commitment_ref"], live_provider_identity=identity, strict_coverage=not args.relaxed_coverage, export=args.export,
                         cad_backend=args.cad_backend,
                         workspace_root=Path(args.workspace).resolve() if args.workspace else repository.layout.run(args.run).root / "workspaces", powershell=Path(args.powershell),
                         branch_id=stage_guard.envelope.branch_id, branch_epoch=stage_guard.envelope.branch_epoch, patch_oracle=args.patch_oracle)
    if options.export:
        for seat in seats:
            if not seat.reviewer:
                (options.workspace_root / f"cad-{stage_guard.envelope.stage_id}-{seat.seat_id}").mkdir(parents=True, exist_ok=True)
    receipt = run_project(repository, run=run, stage_guard=stage_guard, record=record, seats=seats, options=options)
    for seat_result in receipt["seat_results"]:
        cad = seat_result.get("cad") or {}
        print(f"[{seat_result['seat_id']}] round {seat_result['round']} {seat_result['status']} objects={seat_result['objects']} covered={len(seat_result['covered_components'])} "
              f"undeclared={seat_result['undeclared_components']} t={seat_result['wall_time_s']}s cad={cad.get('status')} readback={cad.get('readback_verified')}"
              + (f" backend={cad.get('backend')} path={cad.get('path')}" if cad else ""))
        for artifact_key in ("exact_artifact", "preview_artifact"):
            artifact = cad.get(artifact_key)
            if artifact:
                print(f"    {artifact_key}: {artifact.get('relative_path')} sha256={str(artifact.get('sha256'))[:12]}... ({artifact.get('format')})")
        for issue in seat_result["issues"][:6]:
            print("    ", issue.get("code"), "|", str(issue.get("detail"))[:200])
    print(f"unowned components: {receipt.get('unowned_components')}")
    print(f"seat_execution_complete={receipt['seat_execution_complete']} stage_status={receipt['stage']['status']} wall_time={receipt['wall_time_s']}s receipt={receipt['receipt_ref']}")
    print(f"closure={receipt['closure_status']} {receipt['closure_ref']}")
    if receipt["exit_binding_ref"] is None:
        print("no exit binding: this stage did not close, so no successor stage may open against this run")
    else:
        print(f"exit_binding={receipt['exit_binding_ref']}")
    return 0 if receipt["seat_execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
