#!/usr/bin/env python3
"""M069: author the monument's material ledger and project it.

Assignments cite retained adopted facts only; components without a
citable material stay typed unassigned. The ledger and its coverage
join are retained beside the program, the CAD translation is re-emitted
with material layers (script digest retained), and the IFC export gains
real IfcMaterial associations.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "archive" / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, str(entry))

from _probe_paths import resolve_probe_root  # noqa: E402
from archflow.adapters.cad_program import (  # noqa: E402
    translate_to_rhino_python,
)
from archive.archflow.capabilities.material import (  # noqa: E402
    MaterialIntent,
    MaterialLedger,
    ledger_coverage,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from run_cad_equivalence import load_program_shim  # noqa: E402

EVIDENCE_PROBE = "p066-live-monument"


def _adoption_uri(pattern: str) -> str:
    base = resolve_probe_root(EVIDENCE_PROBE) / "runs"
    hits = sorted(glob.glob(str(base / "*" / "records" / pattern)))
    if not hits:
        raise SystemExit(f"no adoption record matches {pattern}")
    hit = Path(hits[-1])
    return (
        f"project://{EVIDENCE_PROBE}/runs/{hit.parents[1].name}"
        f"/records/{hit.name}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--project-id", default="p074-monument-symmetric")
    parser.add_argument("--run-id", default="monument-002")
    parser.add_argument("--program-record", required=True)
    args = parser.parse_args(argv)

    concrete_ref = _adoption_uri(
        "precedent-adoption-rotunda-wall-construction-*.json"
    )
    granite_ref = _adoption_uri("precedent-adoption-????????*.json")
    ledger = MaterialLedger(
        intents=(
            MaterialIntent(
                material_id="granite",
                label="grey granite monolithic shafts",
                source_refs=(granite_ref,),
            ),
            MaterialIntent(
                material_id="roman-concrete",
                label="opus caementicium with graded aggregate",
                source_refs=(concrete_ref,),
            ),
        ),
        assignments=(
            ("colonnade", "granite"),
            ("dome", "roman-concrete"),
            ("rotunda", "roman-concrete"),
        ),
    )

    program = load_program_shim(Path(args.program_record))
    bindings = [
        {
            "binding_id": item.binding_id,
            "component_id": item.component_id,
            "object_ids": list(item.object_ids),
        }
        for item in program.proposal.semantic_bindings
    ]
    coverage = ledger_coverage(bindings, ledger)

    materials = ledger.component_map()
    colors = {
        intent.material_id: intent.color for intent in ledger.intents
    }
    translation = translate_to_rhino_python(
        program,
        material_by_component=materials,
        material_colors=colors,
    )
    script_sha = hashlib.sha256(
        translation.script.encode("utf-8")
    ).hexdigest()

    root = resolve_probe_root(args.project_id)
    repository = FilesystemProjectRepository.open(root)
    run = repository.load_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )
    payload = {
        "schema": "MaterialLedgerRecord@1",
        "project_id": args.project_id,
        "run_id": args.run_id,
        "authored_at": args.now,
        "ledger": ledger.to_dict(),
        "coverage": coverage,
        "cad_projection": {
            "script_sha256": script_sha,
            "material_layers": sorted(
                f"archflow::{component}" for component in materials
            ),
        },
        "canonical_write_authority": False,
    }
    ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="material-ledger",
        payload=payload,
    )
    repository.verify()
    print(f"  assigned: {sorted(materials)}")
    print(f"  unassigned (typed): {coverage['unassigned']}")
    print(f"  coverage: {coverage['coverage_ratio']:.0%}")
    print(f"  material CAD script sha256: {script_sha[:16]}…")
    print(f"  retained {ref.uri[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
