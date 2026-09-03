#!/usr/bin/env python3
"""Execute the P064 Pantheon inverse derivation and fidelity measurement.

The V3 golden sample is read-only external evidence (L8 quarantine): its
files are referenced by pinned digests and never copied or executed. Every
transcription record cites the exact V3 trace or manifest field it
transcribes. The replay is coarse massing only; the fidelity receipt
denies byte-replay and full-fidelity claims explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.archflow.evaluation.inverse_derivation import (  # noqa: E402
    CoarseMassing,
    crown_oculus_open,
    iou,
    load_sponge_schematic,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.project.bootstrap import bootstrap_raw_request_project

STUDY_ROOT = ROOT / "probes" / "p064-pantheon-inverse"
RUN_ID = "inverse-001"
GOLDEN = Path(r"D:/ARCHFLOW_V3/samples/pantheon_golden")
SCHEM_SHA = "61e0e7cbdb0944b8065071d11a46bbaff843e8c8fe6802dd97484b8176886540"

COMPONENT_TREE = [
    {"component_id": "building", "parent": None,
     "semantic_kind": "roman-pantheon-monument",
     "cites": "trace.stage_2_routing.typology=roman_pantheon"},
    {"component_id": "portico", "parent": "building",
     "semantic_kind": "semi-exterior-porch",
     "cites": "zoning_manifest.zoning_volumes[porch]"},
    {"component_id": "portico-column-family", "parent": "portico",
     "semantic_kind": "column-family",
     "cites": "trace.stage_6_structure_seed.column_anchors=16"},
    {"component_id": "portico-pediment", "parent": "portico",
     "semantic_kind": "entablature-and-pediment",
     "cites": "trace.stage_8_verification.val_soft=entablature_over_H"},
    {"component_id": "portico-roof", "parent": "portico",
     "semantic_kind": "truss-roof",
     "cites": "trace.stage_7_components.by_source_rule.roof_truss=4"},
    {"component_id": "rotunda", "parent": "building",
     "semantic_kind": "interior-round-hall",
     "cites": "zoning_manifest.zoning_volumes[rotunda_hall]"},
    {"component_id": "floor-plinth", "parent": "rotunda",
     "semantic_kind": "floor-datum",
     "cites": "trace.stage_9 Generate 'drum floor plinth ... datum z=0'"},
    {"component_id": "drum-wall", "parent": "rotunda",
     "semantic_kind": "load-bearing-perimeter-ring",
     "cites": "trace.stage_9 Generate 'load-bearing cylindrical drum wall'"},
    {"component_id": "axial-doorway", "parent": "drum-wall",
     "semantic_kind": "opening",
     "cites": "trace.stage_9 Generate 'axial doorway into the round hall'"},
    {"component_id": "interior-recess-family", "parent": "drum-wall",
     "semantic_kind": "recess-family",
     "cites": "trace.stage_7_components.by_source_rule.interior_recess=15"},
    {"component_id": "aedicula-family", "parent": "drum-wall",
     "semantic_kind": "aedicula-family",
     "cites": "trace.stage_7_components.by_source_rule.aedicula=36"},
    {"component_id": "dome", "parent": "rotunda",
     "semantic_kind": "hemispherical-dome",
     "cites": "zoning_manifest.canon_refs rise/span 0.5"},
    {"component_id": "oculus", "parent": "dome",
     "semantic_kind": "crown-aperture",
     "cites": "zoning_manifest.zoning_volumes[oculus] 0.188 in [0.18,0.22]"},
    {"component_id": "coffer-field", "parent": "dome",
     "semantic_kind": "coffer-field",
     "cites": "trace.stage_7_components.by_source_rule.coffers=1 (5x28)"},
    {"component_id": "statuary-family", "parent": "building",
     "semantic_kind": "ornament-family",
     "cites": "trace.stage_7_components.by_source_rule.sculpt_statuary=161"},
]

DECISION_SEQUENCE = [
    {"decision_id": "d01", "statement": "commit typology roman_pantheon at large scale",
     "cites": "trace.stage_2_routing"},
    {"decision_id": "d02", "statement": "derive two-zone program porch+hall with connects_to relation",
     "cites": "trace.stage_3_program"},
    {"decision_id": "d03", "statement": "commit enclosure obligations: porch semi-exterior roofed, hall interior walled and roofed",
     "cites": "trace.stage_5_zoning_obligations"},
    {"decision_id": "d04", "statement": "commit structure seed: 16 column anchors, spans checked",
     "cites": "trace.stage_6_structure_seed"},
    {"decision_id": "d05", "statement": "place drum floor plinth at datum z=0",
     "cites": "trace.stage_9 Generate[0]"},
    {"decision_id": "d06", "statement": "raise load-bearing cylindrical drum wall carrying the dome span",
     "cites": "trace.stage_9 Generate[1]"},
    {"decision_id": "d07", "statement": "cut axial doorway into the round hall",
     "cites": "trace.stage_9 Generate[2]"},
    {"decision_id": "d08", "statement": "span hemispherical dome on the drum, rise/span 0.500",
     "cites": "trace.stage_9 Generate[3]"},
    {"decision_id": "d09", "statement": "open oculus crown aperture, oculus/span 0.188",
     "cites": "trace.stage_9 Generate[4]"},
    {"decision_id": "d10", "statement": "apply coffer ring field 5x28 on the dome interior",
     "cites": "trace.stage_9 Generate[5]"},
]

DEPENDENCY_EDGES = [
    {"from": "enclosure(hall)=interior", "to": "obligation:roof-required",
     "effect": "invalidates", "cites": "trace.stage_9 T_program because"},
    {"from": "dome.span", "to": "drum-wall.bearing",
     "effect": "requires_revalidation", "cites": "trace.stage_9 Generate[1]"},
    {"from": "obligation:daylight-from-above", "to": "oculus",
     "effect": "invalidates", "cites": "trace.stage_9 T_program 'daylit from above'"},
    {"from": "relation:porch-connects_to-hall", "to": "axial-doorway",
     "effect": "invalidates", "cites": "trace.stage_3_program.relations[0]"},
    {"from": "structure-seed.max_span", "to": "portico-column-family",
     "effect": "requires_revalidation", "cites": "trace.stage_6_structure_seed"},
]


sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)

    if not (STUDY_ROOT / "project.json").exists():
        bootstrap_raw_request_project(
            STUDY_ROOT,
            project_id="p064-pantheon-inverse",
            run_id=RUN_ID,
            prompt=(
                "Inverse-derive the frozen V3 Pantheon golden sample into "
                "explicit V4 records and measure coarse massing fidelity."
            ),
        )
    repo = FilesystemProjectRepository.open(STUDY_ROOT)
    run = repo.load_run(RUN_ID)
    dest = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=RUN_ID)
    base = {
        "project_id": run.base.project_id,
        "version": run.base.version,
        "state_sha256": run.base.require_digest(),
    }

    def put(kind, payload):
        ref = repo.put_json(
            run=run, destination=dest, record_kind=kind, payload=payload
        )
        print(f"  retained {ref.uri}")
        return ref

    evidence_files = {}
    for name in ("pantheon.schem", "generation_trace.json",
                 "zoning_manifest.json", "README.md"):
        digest = hashlib.sha256((GOLDEN / name).read_bytes()).hexdigest()
        evidence_files[name] = digest
    if evidence_files["pantheon.schem"] != SCHEM_SHA:
        raise SystemExit("frozen schematic digest drifted; stopping")
    evidence_ref = put(
        "v3-golden-external-evidence",
        {
            "schema": "P064ExternalEvidence@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": base,
            "source_path": str(GOLDEN),
            "quarantine": (
                "L8 read-only external evidence: files referenced by digest, "
                "never copied, imported, or executed; V3 owns no generation "
                "authority here"
            ),
            "file_sha256": evidence_files,
            "freeze_identity": "M0.1/v5 re-frozen 2026-07-18",
            "canonical_write_authority": False,
        },
    )
    tree_ref = put(
        "transcribed-component-tree",
        {
            "schema": "P064TranscribedComponentTree@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": base,
            "evidence_ref": evidence_ref.uri,
            "components": COMPONENT_TREE,
            "component_count_v3": 333,
            "transcription_note": (
                "fifteen semantic components transcribe the 333 V3 rule "
                "outputs by source rule; repeated elements become family "
                "components rather than enumerated instances"
            ),
            "canonical_write_authority": False,
        },
    )
    sequence_ref = put(
        "transcribed-decision-sequence",
        {
            "schema": "P064TranscribedDecisionSequence@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": base,
            "evidence_ref": evidence_ref.uri,
            "decisions": DECISION_SEQUENCE,
            "canonical_write_authority": False,
        },
    )
    edges_ref = put(
        "transcribed-dependency-edges",
        {
            "schema": "P064TranscribedDependencyEdges@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": base,
            "evidence_ref": evidence_ref.uri,
            "edges": DEPENDENCY_EDGES,
            "note": (
                "typed edges recovered from the retained explanation chain; "
                "the V3 chain is retrospective text while these edges are "
                "the operational V4 form"
            ),
            "canonical_write_authority": False,
        },
    )

    grid = load_sponge_schematic(
        GOLDEN / "pantheon.schem", expected_sha256=SCHEM_SHA
    )
    massing = CoarseMassing(
        width=grid.width,
        length=grid.length,
        height=grid.height,
        drum_span=94.0,
        rise_over_span=0.5,
        oculus_over_span=0.188,
    )
    footprint_iou = iou(massing.footprint(), grid.footprint())
    silhouette_iou = iou(massing.silhouette_xy(), grid.silhouette_xy())
    oculus_open = crown_oculus_open(grid, massing)
    put(
        "coarse-fidelity-receipt",
        {
            "schema": "P064CoarseFidelityReceipt@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": base,
            "evidence_ref": evidence_ref.uri,
            "component_tree_ref": tree_ref.uri,
            "decision_sequence_ref": sequence_ref.uri,
            "dependency_edges_ref": edges_ref.uri,
            "measured_at": args.now,
            "schematic_sha256": SCHEM_SHA,
            "grid": {
                "width": grid.width,
                "height": grid.height,
                "length": grid.length,
                "solid_cells": grid.solid_count,
                "bounding_box": list(grid.bounding_box()),
            },
            "massing_inputs": {
                "drum_span": 94.0,
                "rise_over_span": 0.5,
                "oculus_over_span": 0.188,
                "source": (
                    "zoning_manifest canon_refs and scale fields only; no "
                    "value fitted against the occupancy grid"
                ),
            },
            "metrics": {
                "bounding_box_match": list(grid.bounding_box())
                == [grid.width, grid.height, grid.length],
                "solid_cell_count_matches_manifest": grid.solid_count
                == 325282,
                "plan_footprint_iou": footprint_iou,
                "elevation_silhouette_iou": silhouette_iou,
                "crown_oculus_open": oculus_open,
            },
            "claim_boundary": {
                "byte_replay_claimed": False,
                "full_fidelity_claimed": False,
                "v3_execution_claimed": False,
                "coarse_massing_only": True,
                "note": (
                    "V3 reproduces its own golden byte-for-byte; this "
                    "receipt claims only that the transcribed coarse "
                    "massing derived from canon ratios agrees with the "
                    "frozen envelope at the reported IoU levels"
                ),
            },
            "canonical_write_authority": False,
        },
    )
    repo.verify()
    print(
        f"FIDELITY footprint={footprint_iou} silhouette={silhouette_iou} "
        f"oculus_open={oculus_open}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
