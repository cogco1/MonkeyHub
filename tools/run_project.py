"""Run a project from its State Record (P089 / P102).

    python tools/run_project.py --project <repo root> --run <run id> \
      --workflow-ref project://... --stage-envelope-ref project://... \
      --packs <dir> [--export] [--workspace <dir>]

``<dir>`` holds ``state-record.json`` (``StateRecord@1``: components and
massing, levels and grid axes, element rows with references) and
``seats.json``. Seats carry the provider identity and the commitment ref. The run's records are the receipt; this
tool prints a summary only.  The run must already exist and the exact workflow
and envelope must already be retained in P036; this CLI never turns a raw run
or a copied pack into a stage by side effect.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from archflow.capabilities.declaration import DeclarationQuadrant  # noqa: E402
from archflow.capabilities.discipline_seats import SeatSpec  # noqa: E402
from archflow.capabilities.geometry_proposal import GeometryProposalProviderIdentity  # noqa: E402
from archflow.state.stage_workflow import CompositeStageClosureReceipt
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.refs import ProjectRecordRef  # noqa: E402
from archflow.runtime.project_runner import (  # noqa: E402
    RunOptions,
    StageExecutionGuard,
    run_project,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.developed_design import DevelopmentDiscipline  # noqa: E402
from archflow.state.state_record import StateRecord  # noqa: E402
from archflow.state.stage_workflow import (  # noqa: E402
    ProjectStageWorkflow,
    StageExitBinding,
    StageRunEnvelope,
)


_RECORD_SHA = re.compile(r"-([0-9a-f]{64})\.json$")


def _record_ref(uri: str, *, project_id: str) -> ProjectRecordRef:
    parsed = urlsplit(uri)
    if parsed.scheme != "project" or unquote(parsed.netloc) != project_id:
        raise ValueError("record URI belongs to another project")
    relative_path = unquote(parsed.path.lstrip("/"))
    match = _RECORD_SHA.search(relative_path)
    if match is None:
        raise ValueError("record URI does not name a content-addressed JSON record")
    return ProjectRecordRef(
        project_id=project_id,
        relative_path=relative_path,
        sha256=match.group(1),
        media_type="application/json",
    )


def _stage_guard(repository, run, *, workflow_uri: str, envelope_uri: str) -> StageExecutionGuard:
    workflow_ref = _record_ref(workflow_uri, project_id=run.project_id)
    envelope_ref = _record_ref(envelope_uri, project_id=run.project_id)
    workflow = ProjectStageWorkflow.from_dict(repository.load_json(workflow_ref))
    envelope = StageRunEnvelope.from_dict(repository.load_json(envelope_ref))
    if envelope.predecessor is None:
        return StageExecutionGuard(
            workflow=workflow,
            workflow_record_ref=workflow_ref,
            envelope=envelope,
            envelope_record_ref=envelope_ref,
        )
    predecessor_ref = _record_ref(
        envelope.predecessor.envelope_ref,
        project_id=run.project_id,
    )
    exit_ref = _record_ref(
        envelope.predecessor.exit_binding_ref,
        project_id=run.project_id,
    )
    closure_ref = _record_ref(
        envelope.predecessor.exit_binding.closure_ref,
        project_id=run.project_id,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--stage-envelope-ref", required=True)
    parser.add_argument("--packs", required=True)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--workspace")
    parser.add_argument("--powershell", default=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    parser.add_argument("--relaxed-coverage", action="store_true")
    parser.add_argument("--patch-oracle", action="store_true", help="when an export is patched, also rebuild in full and compare the two readbacks")
    args = parser.parse_args()
    packs = Path(args.packs)
    load = lambda name: json.loads((packs / name).read_text(encoding="utf-8"))
    record = StateRecord.from_dict(load("state-record.json"))
    seats_payload = load("seats.json")
    seats = tuple(_seat(s) for s in seats_payload["seats"])
    identity = GeometryProposalProviderIdentity(**seats_payload["provider_identity"])
    project_root = Path(args.project).resolve()
    repository = FilesystemProjectRepository.open(project_root)
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
    options = RunOptions(commitment_ref=seats_payload["commitment_ref"], provider_identity=identity, strict_coverage=not args.relaxed_coverage, export=args.export,
                         workspace_root=Path(args.workspace).resolve() if args.workspace else project_root / "runs" / args.run / "workspaces", powershell=Path(args.powershell),
                         branch_id=stage_guard.envelope.branch_id, branch_epoch=stage_guard.envelope.branch_epoch, patch_oracle=args.patch_oracle)
    if options.export:
        for seat in seats:
            if not seat.reviewer:
                (options.workspace_root / f"cad-{stage_guard.envelope.stage_id}-{seat.seat_id}").mkdir(parents=True, exist_ok=True)
    receipt = run_project(repository, run=run, stage_guard=stage_guard, record=record, seats=seats, options=options)
    for seat_result in receipt["seat_results"]:
        cad = seat_result.get("cad") or {}
        print(f"[{seat_result['seat_id']}] round {seat_result['round']} {seat_result['status']} objects={seat_result['objects']} covered={len(seat_result['covered_components'])} "
              f"undeclared={seat_result['undeclared_components']} t={seat_result['wall_time_s']}s cad={cad.get('status')} readback={cad.get('readback_verified')}")
        for issue in seat_result["issues"][:6]:
            print("    ", issue.get("code"), "|", str(issue.get("detail"))[:200])
    print(f"unowned components: {receipt.get('unowned_components')}")
    print(f"seat_execution_complete={receipt['seat_execution_complete']} stage_status={receipt['stage']['status']} wall_time={receipt['wall_time_s']}s receipt={receipt['receipt_ref']}")
    return 0 if receipt["seat_execution_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
