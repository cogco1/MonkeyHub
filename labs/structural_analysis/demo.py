"""Run/replay the synthetic experiment through existing P036 persistence ports."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
import io
import json
from pathlib import Path

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord, StateRecordOperator, apply_state_record_operator, compile_component_edit

from labs.structural_analysis.analysis import compile_analysis, readiness
from labs.structural_analysis.fixture import MEMBERS, commitment_review, enrich, geometry_projection, initial_record
from labs.structural_analysis.solver import hand_solution, solve


def _artifact(repo, run, name, value):
    ref = repo.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
                      artifact_id=name, media_type="application/json",
                      source=io.BytesIO(canonical_json(value).encode("utf-8")))
    return asdict(ref)


def _read_artifact(repo, ref):
    if ref["project_id"] != repo.load_manifest().project_id:
        raise ValueError("artifact belongs to another project")
    # P036's content-verified JSON reader accepts the exact path/digest. These
    # experiment artifact refs are returned in stdout, not registered as a Hub
    # model/document or silently attached to the canonical design record.
    return repo.load_json(ProjectRecordRef(ref["project_id"], ref["relative_path"], ref["sha256"], ref["media_type"]))


def sensitivity_edit(record, change):
    edits = []
    for member_id in MEMBERS:
        if change == "double-beam-Iz" and not member_id.startswith("beam"):
            continue
        entity = record.entity(member_id)
        fields = deepcopy(entity.fields)
        structural = fields["structural"]
        if change == "half-E":
            profile = structural["physical_profile"]
            profile["profile_ref"] = "demo-uncracked-concrete-v2-half-E"
            q = profile["properties"]["E"]
            q["value"] /= 2
            q["uncertainty"] = {"low": 13.5, "high": 16.5, "unit": "GPa"}
        elif change == "double-beam-Iz":
            structural["section"]["section_ref"] = "beam-effective-Iz-x2"
            q = structural["section"]["Iz"]
            q["value"] *= 2
            q["conditions"] = "Explicit sensitivity of effective inertia only; architectural geometry is unchanged."
        else:
            raise ValueError("unknown sensitivity change")
        structural["revision_reason"] = change
        edits.append(replace(entity, fields=fields, lineage=replace(entity.lineage, revised_at=change)))
    return compile_component_edit(record, entities=tuple(edits))


def run_demo(project_dir: Path):
    """Caller selects a new external project. Refuse reuse; never find a live project."""
    record = initial_record()
    repo = FilesystemProjectRepository.initialize(project_dir, project_id=record.project_id,
        initial_state={"phase": "request", "commitments": []}, authored_record=record.to_dict())
    head = repo.read_head()
    initial_geometry = geometry_projection(record)
    revisions = []

    def retain(label, current, operator=None, previous=None, milestone=4):
        run = repo.create_run(label)
        current = current.bound_to(run)
        ref = repo.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
                            record_kind=STATE_RECORD, payload=current.to_dict())
        check = readiness(current, MEMBERS, expected_run=run, expected_digest=current.digest)
        evidence = {"label": label, "commitment_profile": milestone,
                    "accepted_design_stage": None, "record_ref": ref.to_dict(),
                    "source_run": run.to_dict(), "record_digest": current.digest,
                    "previous_record_ref": None if previous is None else previous["record_ref"],
                    "operator": None if operator is None else operator.to_dict(),
                    "readiness": {"ready": check.ready, "findings": [asdict(f) for f in check.findings]},
                    "commitment_review": commitment_review(current, milestone),
                    "geometry_projection": geometry_projection(current)}
        if evidence["geometry_projection"] != initial_geometry:
            raise AssertionError("semantic enrichment changed geometry producer output")
        if previous is not None:
            before = StateRecord.from_dict(repo.load_json(ProjectRecordRef.from_dict(previous["record_ref"])))
            evidence["unchanged_entities"] = [e.entity_id for e in before.entities if e == current.entity(e.entity_id)]
        if check.ready:
            spec = compile_analysis(current, MEMBERS, expected_run=run, expected_digest=current.digest)
            evidence["analysis_projection"] = spec.to_dict()
            evidence["result"] = solve(spec)
            evidence["hand_solution"] = hand_solution(spec)
        artifact = _artifact(repo, run, f"{label}-evidence", evidence)
        entry = {"label": label, "record_ref": ref.to_dict(), "evidence_ref": artifact}
        if "result" in evidence:
            result = evidence["result"]
            entry["analysis"] = {"status": result["status"], "solver": result["solver"]}
            if result["status"] == "solved":
                entry["analysis"].update({
                    "beam_midpoint_dy_m": [result["members"][f"beam-{i}"]["midpoint_local_dy_m"] for i in range(2)],
                    "top_dy_m": [result["nodes"][f"top-{i}"]["displacement_m"][1] for i in range(3)],
                    "base_reaction_y_N": [result["nodes"][f"base-{i}"]["reaction_N"][1] for i in range(3)],
                    "hand_solution": evidence["hand_solution"],
                })
            else:
                entry["analysis"]["reason"] = result["reason"]
        revisions.append(entry)
        return current, entry

    record, previous = retain("geometry", record, milestone=1)
    for milestone, label in ((2, "roles"), (3, "material"), (4, "physical")):
        operator = enrich(record, milestone)
        record, previous = retain(label, apply_state_record_operator(record, operator), operator, previous, milestone)
    baseline, baseline_entry = record, previous
    for change in ("half-E", "double-beam-Iz"):
        operator = sensitivity_edit(baseline, change)
        retain(change, apply_state_record_operator(baseline, operator), operator, baseline_entry)
    entity = baseline.entity("beam-0")
    fields = deepcopy(entity.fields)
    fields["semantic"] = {"status": "unknown", "source": "fixture:gh-125-synthetic-assumptions",
                          "revision_reason": "withdraw structural interpretation pending review"}
    operator = compile_component_edit(baseline, entities=(replace(entity, fields=fields,
        lineage=replace(entity.lineage, revised_at="withdraw-role")),))
    retain("withdraw-role", apply_state_record_operator(baseline, operator), operator, baseline_entry)
    if repo.read_head() != head or repo.read_design_branches():
        raise AssertionError("experiment changed canonical issue HEAD or accepted design history")
    report = {"project_id": record.project_id, "canonical_head": head.to_dict(),
              "accepted_design_stages": [], "revisions": revisions}
    report["cold_replay"] = replay(project_dir, report)
    return report


def replay(project_dir: Path, report):
    """Read exact retained refs afresh, replay normal operators, and rerun the solver."""
    repo = FilesystemProjectRepository.open(project_dir)
    checks = []
    for entry in report["revisions"]:
        evidence = _read_artifact(repo, entry["evidence_ref"])
        record = StateRecord.from_dict(repo.load_json(ProjectRecordRef.from_dict(entry["record_ref"])))
        if evidence["record_ref"] != entry["record_ref"] or record.digest != evidence["record_digest"]:
            raise ValueError("evidence source revision mismatch")
        expected_run = RunRef.from_dict(evidence["source_run"])
        if record.run_ref != expected_run or repo.load_run(record.run_id) != expected_run:
            raise ValueError("retained run binding mismatch")
        if geometry_projection(record) != evidence["geometry_projection"]:
            raise ValueError("historical geometry projection differs")
        if evidence["operator"] is not None:
            previous = StateRecord.from_dict(repo.load_json(ProjectRecordRef.from_dict(evidence["previous_record_ref"])))
            result = apply_state_record_operator(previous, StateRecordOperator.from_dict(evidence["operator"]))
            if result.bound_to(expected_run).to_dict() != record.to_dict():
                raise ValueError("historical enrichment operator replay differs")
        check = readiness(record, MEMBERS, expected_run=expected_run, expected_digest=evidence["record_digest"])
        if {"ready": check.ready, "findings": [asdict(f) for f in check.findings]} != evidence["readiness"]:
            raise ValueError("historical readiness differs")
        solver_status = None
        if check.ready:
            spec = compile_analysis(record, MEMBERS, expected_run=expected_run, expected_digest=evidence["record_digest"])
            if json.loads(canonical_json(spec.to_dict())) != evidence["analysis_projection"]:
                raise ValueError("historical analysis projection differs")
            solved = solve(spec)
            solver_status = solved["status"]
            # Backend absence is not evidence that a previously solved result replayed.
            if solved != evidence["result"]:
                raise ValueError("historical solver result differs; version/environment or retained output changed")
        checks.append({"label": entry["label"], "record_digest": record.digest,
                       "operator_replayed": evidence["operator"] is not None,
                       "ready": check.ready, "solver_status": solver_status})
    if repo.read_head().to_dict() != report["canonical_head"] or repo.read_design_branches():
        raise ValueError("project issue HEAD or accepted design history changed")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True, help="Explicit new external P036 project root (existing root only with --replay-report)")
    parser.add_argument("--replay-report", type=Path, help="External stdout JSON from a previous run")
    args = parser.parse_args()
    if not args.project_dir.is_absolute():
        parser.error("--project-dir must be an explicit absolute external path")
    if args.project_dir.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("unpromoted project output must be outside the source checkout")
    report = (replay(args.project_dir, json.loads(args.replay_report.read_text(encoding="utf-8-sig")))
              if args.replay_report else run_demo(args.project_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
