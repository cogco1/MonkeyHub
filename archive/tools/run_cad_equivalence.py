#!/usr/bin/env python3
"""P071: CAD realization equivalence receipt for a compiled program.

Loads a retained compiled-geometry-program record, re-emits the
deterministic rhinoscriptsyntax translation and the analytic per-object
bounds, then compares externally captured CAD measures against those
bounds within an explicit tolerance. The comparison result is persisted
as a typed equivalence receipt whose provenance names the program
record, the script digest, the measures digest, and the CAD adapter
identity. The tool owns no design authority and never mutates the
program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archflow.adapters.cad_program import (  # noqa: E402
    expected_object_bounds,
    expected_object_semantics,
    translate_to_rhino_python,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination


def load_program_shim(record_path: Path):
    """Shim the compiled program from its retained JSON record."""

    record = json.loads(record_path.read_text(encoding="utf-8"))
    content = record.get("content", record)
    proposal = content["proposal"]
    operations = tuple(
        SimpleNamespace(
            op_id=op["op_id"],
            kind=SimpleNamespace(value=op["kind"]),
            output_object_ids=tuple(op["output_object_ids"]),
            input_object_ids=tuple(op["input_object_ids"]),
            semantic_binding_ids=tuple(op.get("semantic_binding_ids", ())),
            parameters=tuple(
                SimpleNamespace(
                    name=item["name"], value_json=item["value_json"]
                )
                for item in op["parameters"]
            ),
        )
        for op in proposal["operations"]
    )
    bindings = tuple(
        SimpleNamespace(
            binding_id=item["binding_id"],
            component_id=item["component_id"],
            object_ids=tuple(item["object_ids"]),
            commitment_refs=tuple(item["commitment_refs"]),
            evidence_refs=tuple(item["evidence_refs"]),
        )
        for item in proposal.get("semantic_bindings", ())
    )
    return SimpleNamespace(
        proposal=SimpleNamespace(
            operations=operations,
            semantic_bindings=bindings,
            proposal_id=proposal.get("proposal_id", ""),
            project_id=proposal.get("project_id", ""),
            run_id=proposal.get("run_id", ""),
        ),
        operation_order=tuple(content["operation_order"]),
        proposal_digest=content.get("proposal_digest", ""),
    )


def compare_semantics(expected, observed):
    """Typed mismatches between program-derived and document-read semantics."""

    mismatches = []
    want_objects = expected["objects"]
    got_objects = observed.get("objects", {})
    for object_id in sorted(set(want_objects) | set(got_objects)):
        want, got = want_objects.get(object_id), got_objects.get(object_id)
        if want is None or got is None:
            mismatches.append(
                {"object_id": object_id, "code": "semantic_object_missing"}
            )
            continue
        if got.get("name") != want["name"]:
            mismatches.append(
                {
                    "object_id": object_id,
                    "code": "name_mismatch",
                    "expected": want["name"],
                    "observed": got.get("name"),
                }
            )
        if got.get("layer") != want["layer"]:
            mismatches.append(
                {
                    "object_id": object_id,
                    "code": "layer_mismatch",
                    "expected": want["layer"],
                    "observed": got.get("layer"),
                }
            )
        observed_text = got.get("user_text") or {}
        for key, value in want["user_text"].items():
            if observed_text.get(key) != value:
                mismatches.append(
                    {
                        "object_id": object_id,
                        "code": "user_text_mismatch",
                        "key": key,
                        "expected": value,
                        "observed": observed_text.get(key),
                    }
                )
    want_blocks = expected["blocks"]
    got_blocks = observed.get("blocks", {})
    for name in sorted(set(want_blocks) | set(got_blocks)):
        if want_blocks.get(name) != got_blocks.get(name):
            mismatches.append(
                {
                    "block": name,
                    "code": "family_multiplicity_mismatch",
                    "expected": want_blocks.get(name),
                    "observed": got_blocks.get(name),
                }
            )
    return mismatches


def compare(expected, measures, tolerance):
    mismatches = []
    max_deviation = 0.0
    for object_id in sorted(set(expected) | set(measures)):
        if object_id not in expected or object_id not in measures:
            mismatches.append(
                {
                    "object_id": object_id,
                    "code": "object_set_mismatch",
                }
            )
            continue
        want, got = expected[object_id], measures[object_id]
        if int(want["brep_count"]) != int(got.get("brep_count", -1)):
            mismatches.append(
                {
                    "object_id": object_id,
                    "code": "instance_count_mismatch",
                    "expected": want["brep_count"],
                    "measured": got.get("brep_count"),
                }
            )
        for corner in ("bbox_min", "bbox_max"):
            for axis in range(3):
                deviation = abs(
                    float(want[corner][axis]) - float(got[corner][axis])
                )
                max_deviation = max(max_deviation, deviation)
                if deviation > tolerance:
                    mismatches.append(
                        {
                            "object_id": object_id,
                            "code": "bounds_deviation",
                            "corner": corner,
                            "axis": axis,
                            "deviation": deviation,
                        }
                    )
    return mismatches, max_deviation


sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    parser.add_argument("--project-id", default="p065-monument-derivation")
    parser.add_argument("--run-id", default="monument-001")
    parser.add_argument("--program-record", required=True)
    parser.add_argument("--measures")
    parser.add_argument("--semantics")
    parser.add_argument("--adapter-id")
    parser.add_argument("--tolerance", type=float, default=0.6)
    parser.add_argument("--script-out")
    parser.add_argument("--expected-out")
    args = parser.parse_args(argv)

    record_path = Path(args.program_record)
    program = load_program_shim(record_path)
    translation = translate_to_rhino_python(
        program,
        provenance={
            "proposal_id": program.proposal.proposal_id,
            "project_id": program.proposal.project_id,
            "run_id": program.proposal.run_id,
            "proposal_digest": program.proposal_digest,
            "program_record": record_path.name,
        },
    )
    expected = expected_object_bounds(program)
    expected_semantics = expected_object_semantics(program)
    script_sha256 = hashlib.sha256(
        translation.script.encode("utf-8")
    ).hexdigest()
    print(f"  ops: {len(program.operation_order)}")
    print(f"  physical objects: {len(translation.physical_object_ids)}")
    print(f"  script sha256: {script_sha256[:16]}…")
    print(f"  losses: {list(translation.losses)}")
    if sorted(expected) != list(translation.physical_object_ids):
        print("EXPECTED/PHYSICAL SET DISAGREE")
        return 2
    if args.script_out:
        Path(args.script_out).write_text(
            translation.script, encoding="utf-8"
        )
    if args.expected_out:
        Path(args.expected_out).write_text(
            json.dumps(expected, indent=1), encoding="utf-8"
        )
    if not args.measures:
        return 0

    if not args.adapter_id:
        print("--adapter-id is required with --measures")
        return 2
    measures_text = Path(args.measures).read_text(encoding="utf-8")
    measures = json.loads(measures_text)
    mismatches, max_deviation = compare(
        expected, measures, args.tolerance
    )
    semantic_mismatches = None
    semantics_sha256 = None
    if args.semantics:
        semantics_text = Path(args.semantics).read_text(encoding="utf-8")
        semantics_sha256 = hashlib.sha256(
            semantics_text.encode("utf-8")
        ).hexdigest()
        semantic_mismatches = compare_semantics(
            expected_semantics, json.loads(semantics_text)
        )
    status = (
        "equivalent"
        if not mismatches and not semantic_mismatches
        else "diverged"
    )
    repository = FilesystemProjectRepository.open(
        resolve_probe_root(args.project_id)
    )
    run = repository.load_run(args.run_id)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD, run_id=args.run_id
    )
    record_ref = (
        f"project://{args.project_id}/runs/{args.run_id}/records/"
        f"{record_path.name}"
    )
    receipt = {
        "schema": "CadEquivalenceReceipt@2",
        "project_id": args.project_id,
        "run_id": args.run_id,
        "captured_at": args.now,
        "program_record_ref": record_ref,
        "adapter_id": args.adapter_id,
        "script_sha256": script_sha256,
        "measures_sha256": hashlib.sha256(
            measures_text.encode("utf-8")
        ).hexdigest(),
        "tolerance": float(args.tolerance),
        "object_count": len(expected),
        "instance_count": sum(
            item["brep_count"] for item in expected.values()
        ),
        "max_abs_deviation": max_deviation,
        "translation_losses": list(translation.losses),
        "mismatches": mismatches,
        "semantic_verification": (
            None
            if semantic_mismatches is None
            else {
                "semantics_sha256": semantics_sha256,
                "expected_family_blocks": expected_semantics["blocks"],
                "mismatches": semantic_mismatches,
                "verified": not semantic_mismatches,
            }
        ),
        "status": status,
        "execution_external": True,
        "canonical_write_authority": False,
    }
    ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="cad-equivalence-receipt",
        payload=receipt,
    )
    repository.verify()
    print(f"  retained {ref.uri[:110]}")
    if semantic_mismatches is not None:
        print(
            "  semantics: "
            + (
                "verified"
                if not semantic_mismatches
                else f"{len(semantic_mismatches)} mismatches"
            )
        )
    print(
        f"CAD EQUIVALENCE {status.upper()} "
        f"max_dev={max_deviation:.4f} tolerance={args.tolerance}"
    )
    return 0 if status == "equivalent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
