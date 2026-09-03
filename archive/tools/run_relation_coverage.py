#!/usr/bin/env python3
"""P077: compute the relation coverage ledger for one realized stage.

Candidate edges are enumerated from the retained stage scene; derived
edges are detected from the compiled program; declared edges are
supplied here per project with their provenance refs. Whatever remains
is typed uncovered — the honest dimension-2 ledger, retained beside the
scene it measures.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "archive" / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, str(entry))

from _probe_paths import resolve_probe_root  # noqa: E402
from archive.archflow.capabilities.relation_coverage import (  # noqa: E402
    detect_program_edges,
    enumerate_candidate_relations,
    relation_coverage_ledger,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination

MONUMENT_DECLARED_EDGES = [
    {
        "components": ["colonnade", "portico"],
        "kind": "constraint",
        "refs": ["commitment:primary-axis-center"],
    },
    {
        "components": ["portico", "rotunda"],
        "kind": "constraint",
        "refs": ["commitment:primary-axis-center"],
    },
    {
        "components": ["dome", "rotunda"],
        "kind": "constraint",
        "refs": ["commitment:preserve-monument-envelope"],
    },
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--project-id", default="p074-monument-symmetric")
    parser.add_argument("--run-id", default="monument-002")
    parser.add_argument("--scene-prefix", default="p065")
    parser.add_argument("--stage", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=0.0)
    args = parser.parse_args(argv)

    root = resolve_probe_root(args.project_id)
    records = root / "runs" / args.run_id / "records"
    scene_path = sorted(
        glob.glob(
            str(records / f"{args.scene_prefix}-stage-{args.stage}"
                          "-sandbox-scene-*.json")
        )
    )[0]
    scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
    programs = []
    for path in glob.glob(
        str(records / "production-geometry-program-*.json")
    ):
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        proposal = record["content"]["proposal"]
        programs.append((len(proposal["operations"]), proposal, path))
    _, proposal, program_path = max(programs, key=lambda item: item[0])
    bindings = proposal["semantic_bindings"]
    operations = proposal["operations"]

    candidates = enumerate_candidate_relations(
        scene["objects"], bindings, tolerance=args.tolerance
    )
    detected = detect_program_edges(operations, bindings)
    ledger = relation_coverage_ledger(
        candidates,
        detected=detected,
        declared=MONUMENT_DECLARED_EDGES,
    )
    payload = {
        **ledger,
        "project_id": args.project_id,
        "run_id": args.run_id,
        "stage": args.stage,
        "tolerance": args.tolerance,
        "computed_at": args.now,
        "scene_record": Path(scene_path).name,
        "program_record": Path(program_path).name,
        "declared_edges": MONUMENT_DECLARED_EDGES,
        "canonical_write_authority": False,
    }
    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(args.run_id)
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=args.run_id
        ),
        record_kind=f"relation-coverage-ledger-stage-{args.stage}",
        payload=payload,
    )
    repository.verify()
    print(f"  candidates: {ledger['candidate_count']}  "
          f"resolved: {ledger['resolved_count']}  "
          f"ratio: {ledger['coverage_ratio']}")
    for row in ledger["candidates"]:
        res = row["resolution"]
        tag = (
            f"{res['status']}"
            if res["status"] != "resolved"
            else f"resolved via {res['source']}"
        )
        print(f"    {row['components'][0]:14} <-> "
              f"{row['components'][1]:14} [{row['contact']}] {tag}")
    print(f"  retained {ref.uri[:110]}")
    print(f"RELATION COVERAGE {ledger['coverage_ratio']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
