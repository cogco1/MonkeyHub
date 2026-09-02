"""Run a project from its record packs (P089 first cut).

    python tools/run_project.py --project <repo root> --run <run id> --packs <dir> [--export] [--workspace <dir>]

``<dir>`` holds ``schematic-pack.json``, ``element-pack.json``, ``seats.json``,
``levels.json`` and optionally ``grids.json``. Seats carry the provider
identity and the commitment ref. The run's records are the receipt; this
tool prints a summary only.
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
from archflow.project import FilesystemProjectRepository  # noqa: E402
from archflow.runtime.project_runner import ElementPack, RunOptions, SchematicPack, run_project  # noqa: E402
from archflow.state.design_maturity import DesignPhase  # noqa: E402
from archflow.state.developed_design import DevelopmentDiscipline  # noqa: E402
from archflow.state.geometry_program import ProjectGrids, ProjectLevels  # noqa: E402


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
    parser.add_argument("--packs", required=True)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--workspace")
    parser.add_argument("--powershell", default=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    parser.add_argument("--relaxed-coverage", action="store_true")
    args = parser.parse_args()
    packs = Path(args.packs)
    load = lambda name: json.loads((packs / name).read_text(encoding="utf-8"))
    schematic = SchematicPack.from_dict(load("schematic-pack.json"))
    elements = ElementPack.from_dict(load("element-pack.json"))
    seats_payload = load("seats.json")
    seats = tuple(_seat(s) for s in seats_payload["seats"])
    levels = ProjectLevels.from_dict(load("levels.json"))
    grids = ProjectGrids.from_dict(load("grids.json")) if (packs / "grids.json").exists() else None
    identity = GeometryProposalProviderIdentity(**seats_payload["provider_identity"])
    project_root = Path(args.project).resolve()
    repository = FilesystemProjectRepository.open(project_root)
    try:
        run = repository.load_run(args.run)
    except Exception:
        run = repository.create_run(args.run)
    options = RunOptions(commitment_ref=seats_payload["commitment_ref"], provider_identity=identity, strict_coverage=not args.relaxed_coverage, export=args.export,
                         workspace_root=Path(args.workspace).resolve() if args.workspace else project_root / "runs" / args.run / "workspaces", powershell=Path(args.powershell),
                         branch_id=seats_payload.get("branch_id", "runner-v1"))
    if options.export:
        for seat in seats:
            if not seat.reviewer:
                (options.workspace_root / f"cad-{options.stage_prefix}-{seat.seat_id}").mkdir(parents=True, exist_ok=True)
    receipt = run_project(repository, run=run, schematic=schematic, elements=elements, seats=seats, levels=levels, grids=grids, options=options)
    for stage in receipt["stages"]:
        cad = stage.get("cad") or {}
        print(f"[{stage['seat_id']}] round {stage['round']} {stage['status']} objects={stage['objects']} covered={len(stage['covered_components'])} "
              f"undeclared={stage['undeclared_components']} t={stage['wall_time_s']}s cad={cad.get('status')} readback={cad.get('readback_verified')}")
        for issue in stage["issues"][:6]:
            print("    ", issue.get("code"), "|", str(issue.get("detail"))[:200])
    print(f"unowned components: {receipt.get('unowned_components')}")
    print(f"accepted={receipt['accepted']} wall_time={receipt['wall_time_s']}s receipt={receipt['receipt_ref']}")
    return 0 if receipt["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
