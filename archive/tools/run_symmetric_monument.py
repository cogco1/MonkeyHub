#!/usr/bin/env python3
"""P074: re-derive the monument with axis-derived rows, then measure.

``derive`` runs the corrected scripted proof into a fresh probe.
``measure`` runs the axial-symmetry measurement over one probe's stage
scenes and persists a findings record per stage. The runner supplies the
axis value and subject groups; the framework only measures.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import (  # noqa: E402
    WORKSPACE_PROJECTS,
    resolve_probe_root,
)
from archive.archflow.evaluation.symmetry import axial_group_offsets
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination

PHYSICAL_GROUPS = {
    "portico": ("portico-binding",),
    "colonnade": ("colonnade-binding",),
    "rotunda": ("rotunda-binding",),
    "dome": ("dome-binding",),
}
TOOL_GROUPS = {"main-entry": ("entry-binding",)}
AXIS_VALUE = 24.0
AXIS_INDEX = 0


def derive(args) -> int:
    from archive.tools.projects.pantheon import monument_support as monument

    monument.PROJECT_ID = args.project_id
    monument.RUN_ID = args.run_id
    root = WORKSPACE_PROJECTS / args.project_id
    manifest = monument._run_proof(root)
    print("stages:", [item["stage"] for item in manifest["stages"]])
    print(
        "occupied:",
        [item.get("occupied_cell_count") for item in manifest["stages"]],
    )
    print("instances:", monument._instance_count(manifest["final_scene"]))
    return 0


def measure(args) -> int:
    root = resolve_probe_root(args.project_id)
    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )
    prefix = args.scene_prefix
    worst_overall = 0.0
    for stage in (1, 2, 3):
        pattern = str(
            root
            / "runs"
            / args.run_id
            / "records"
            / f"{prefix}-stage-{stage}-sandbox-scene-*.json"
        )
        paths = sorted(glob.glob(pattern))
        if not paths:
            print(f"stage {stage}: no scene record, skipped")
            continue
        scene = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
        findings = list(
            axial_group_offsets(
                scene["objects"],
                axis_value=AXIS_VALUE,
                axis_index=AXIS_INDEX,
                groups=PHYSICAL_GROUPS,
            )
        )
        findings += list(
            axial_group_offsets(
                scene["objects"],
                axis_value=AXIS_VALUE,
                axis_index=AXIS_INDEX,
                groups=TOOL_GROUPS,
                physical_only=False,
            )
        )
        worst = max(abs(item.offset) for item in findings)
        worst_overall = max(worst_overall, worst)
        payload = {
            "schema": "AxialSymmetryFindings@1",
            "project_id": args.project_id,
            "run_id": args.run_id,
            "stage": stage,
            "axis_value": AXIS_VALUE,
            "axis_index": AXIS_INDEX,
            "axis_commitment_ref": "commitment:primary-axis-center",
            "scene_record": Path(paths[0]).name,
            "findings": [item.to_dict() for item in findings],
            "max_abs_offset": worst,
            "status": "fail" if worst > args.tolerance else "pass",
            "canonical_write_authority": False,
        }
        repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"axial-symmetry-findings-stage-{stage}",
            payload=payload,
        )
        print(f"stage {stage}: max offset {worst:.4f} -> {payload['status']}")
        for item in findings:
            flag = " FAIL" if abs(item.offset) > args.tolerance else ""
            print(
                f"   {item.group_id:12} center {item.center:7.3f} "
                f"offset {item.offset:+.3f}{flag}"
            )
    repository.verify()
    print(f"MEASURE DONE worst={worst_overall:.4f}")
    return 0 if worst_overall <= args.tolerance else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    derive_parser = sub.add_parser("derive")
    derive_parser.add_argument(
        "--project-id", default="p074-monument-symmetric"
    )
    derive_parser.add_argument("--run-id", default="monument-002")
    measure_parser = sub.add_parser("measure")
    measure_parser.add_argument(
        "--project-id", default="p074-monument-symmetric"
    )
    measure_parser.add_argument("--run-id", default="monument-002")
    measure_parser.add_argument("--scene-prefix", default="p065")
    measure_parser.add_argument("--tolerance", type=float, default=1e-9)
    args = parser.parse_args(argv)
    if args.command == "derive":
        return derive(args)
    return measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
