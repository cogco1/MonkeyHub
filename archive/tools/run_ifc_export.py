#!/usr/bin/env python3
"""P073: export a compiled program to IFC4 and validate by re-reading.

The element-class mapping is instance data supplied here per project
(``--map component=IfcClass``); unmapped components become proxies. The
receipt retains the file digest, library identity, representation notes,
and the re-read comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "archive" / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import ifcopenshell  # noqa: E402
import ifcopenshell.util.element  # noqa: E402

from archive.archflow.adapters.ifc_export import export_program_to_ifc  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from run_cad_equivalence import load_program_shim  # noqa: E402


sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--program-record", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        help="component=IfcClass element mapping (repeatable)",
    )
    parser.add_argument(
        "--material",
        action="append",
        default=[],
        help="component=material_id assignment (repeatable)",
    )
    args = parser.parse_args(argv)

    class_map = {}
    for entry in args.map:
        component, _, ifc_class = entry.partition("=")
        class_map[component] = ifc_class
    material_map = {}
    for entry in args.material:
        component, _, material_id = entry.partition("=")
        material_map[component] = material_id

    record_path = Path(args.program_record)
    program = load_program_shim(record_path)
    result = export_program_to_ifc(
        program,
        project_id=args.project_id,
        run_id=args.run_id,
        class_by_component=class_map,
        material_by_component=material_map or None,
        provenance={
            "proposal_id": program.proposal.proposal_id,
            "proposal_digest": program.proposal_digest,
            "program_record": record_path.name,
        },
    )
    out_path = Path(args.out)
    out_path.write_text(result.file_text, encoding="utf-8")
    print(f"  elements: {result.element_count}")
    print(f"  mapped instances: {result.mapped_instance_total}")
    print(f"  notes: {len(result.representation_notes)}")
    print(f"  file sha256: {result.file_sha256[:16]}…  -> {out_path}")

    model = ifcopenshell.open(str(out_path))
    by_class: dict[str, int] = {}
    read_names = set()
    pset_failures = []
    mapped_read = 0
    for element in model.by_type("IfcElement"):
        by_class[element.is_a()] = by_class.get(element.is_a(), 0) + 1
        read_names.add(element.Name)
        psets = ifcopenshell.util.element.get_psets(element)
        semantics = psets.get("Archflow_Semantics", {})
        if "archflow:producer_op" not in semantics:
            pset_failures.append(element.Name)
        shape = element.Representation.Representations[0]
        if shape.RepresentationType == "MappedRepresentation":
            mapped_read += len(shape.Items)
    from archflow.adapters.cad_program import expected_object_bounds

    expected_names = set(expected_object_bounds(program))
    validation = {
        "element_count_matches": len(read_names) == result.element_count,
        "names_match_program": read_names == expected_names,
        "mapped_instances_match": mapped_read == result.mapped_instance_total,
        "all_elements_carry_semantics": not pset_failures,
        "elements_by_class": dict(sorted(by_class.items())),
    }
    verified = all(
        value for key, value in validation.items()
        if key != "elements_by_class"
    )
    print(f"  re-read classes: {validation['elements_by_class']}")
    print(f"  validation: {'VERIFIED' if verified else 'FAILED'}")

    repository = FilesystemProjectRepository.open(
        resolve_probe_root(args.project_id)
    )
    run = repository.load_run(args.run_id)
    receipt = {
        "schema": "IfcExportReceipt@1",
        "project_id": args.project_id,
        "run_id": args.run_id,
        "exported_at": args.now,
        "program_record_ref": (
            f"project://{args.project_id}/runs/{args.run_id}/records/"
            f"{record_path.name}"
        ),
        "library": f"ifcopenshell-{ifcopenshell.version}",
        "ifc_schema": "IFC4",
        "file_name": out_path.name,
        "file_sha256": result.file_sha256,
        "element_count": result.element_count,
        "mapped_instance_total": result.mapped_instance_total,
        "class_map": class_map,
        "material_map": material_map,
        "representation_notes": list(result.representation_notes),
        "validation": validation,
        "status": "verified" if verified else "failed",
        "design_authority": False,
        "canonical_write_authority": False,
    }
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id=args.run_id
        ),
        record_kind="ifc-export-receipt",
        payload=receipt,
    )
    repository.verify()
    print(f"  retained {ref.uri[:110]}")
    print(f"IFC EXPORT {'VERIFIED' if verified else 'FAILED'}")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
