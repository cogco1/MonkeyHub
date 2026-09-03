#!/usr/bin/env python3
"""Freeze and execute the P063 repair-locality experiment.

Twenty-seven paired episodes: three cases x three edit classes x three
strategies against one frozen delta per case/edit. Every executor is
deterministic and provider-free; concrete deltas and gold impact sets are
project-owned records; the framework owns only the generic contracts in
``archive.archflow.evaluation.repair_experiment``.

Commands:
  freeze    derive baselines, freeze the nine deltas and gold impact sets,
            and persist the immutable episode preregistration
  run       execute all twenty-seven episodes and persist receipts
  index     rebuild the study index from retained receipts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "archive" / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from archive.archflow.evaluation.repair_experiment import (  # noqa: E402
    BaselineGraph,
    EditClass,
    FrozenRepairDelta,
    GoldImpactSet,
    RepairStrategy,
    run_episode,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.project.bootstrap import bootstrap_raw_request_project
from archflow.contracts.canonical import canonical_digest

from run_assignment import (  # noqa: E402
    Assignment,
    _load_envelope,
    _measurements,
    _record_content,
    _resolve,
    _run_records,
)

STUDY_ROOT = ROOT / "probes" / "p063-repair-study"
STUDY_RUN = "repair-001"  # default; override with --run
CASES = ("clinic", "workshop", "courtyard")
STRATEGIES = (
    RepairStrategy.TARGET_ONLY,
    RepairStrategy.WHOLE_CHAIN,
    RepairStrategy.DEPENDENCY_SCOPED,
)


class _Args:
    def __init__(self, assignment: str):
        self.assignment = assignment


def load_baseline(case: str):
    """Baseline records of the study-028 full-condition candidate."""

    assignment = Assignment(resolve_probe_root(f"p062-{case}-case"))
    envelope = _load_envelope(
        assignment,
        "p062-study-028-execution-envelope",
        f"assignment-{case}-full",
    )
    roles: dict[str, dict] = {}
    for uri in envelope["record_refs"]:
        payload = _resolve(assignment, uri)
        roles.setdefault(payload.get("role"), payload)
    proposal = _record_content(
        roles["component-proposal"], expected_role="component-proposal"
    )
    geometry = _record_content(
        roles["geometry-program"], expected_role="geometry-program"
    )
    scene = _resolve(assignment, envelope["terminal_refs"]["sandbox-scene"])
    contract = _resolve(
        assignment, envelope["terminal_refs"]["usability-contract"]
    )
    graph = BaselineGraph(
        design_program=assignment.program,
        proposal=proposal,
        geometry=geometry,
        scene=scene,
        criteria=list(contract["criteria"]),
    ).build()
    commitments = tuple(assignment.context.required_commitment_refs)
    return assignment, graph, commitments, envelope


def _evaluator(assignment: Assignment):
    """Deterministic mandatory-criterion evaluation over graph records."""

    def evaluate(graph: BaselineGraph):
        try:
            from archflow.compilers.geometry import (  # noqa: F401
                compile_geometry_program,
            )
        except ImportError:
            pass

        class _Scene:
            def __init__(self, payload):
                self._payload = payload
                self.opening_object_ids = tuple(
                    payload.get("opening_object_ids", ())
                )
                self.objects = tuple(
                    _SceneObject(item) for item in payload.get("objects", ())
                )

        class _SceneObject:
            def __init__(self, payload):
                self._payload = payload
                self.object_id = payload["object_id"]

            def to_dict(self):
                return self._payload

        class _Program:
            def __init__(self, geometry):
                self.proposal = _Proposal(geometry["proposal"])
                self.objects = tuple(
                    _GeometryObject(item)
                    for item in geometry.get("objects", ())
                )

        class _GeometryObject:
            def __init__(self, payload):
                self._payload = payload

            def to_dict(self):
                return self._payload

        class _Proposal:
            def __init__(self, payload):
                self.semantic_bindings = tuple(
                    _Binding(item)
                    for item in payload.get("semantic_bindings", ())
                )

        class _Binding:
            def __init__(self, payload):
                self.component_id = payload["component_id"]
                self.object_ids = tuple(payload.get("object_ids", ()))

        values, _ = _measurements(
            graph.design_program,
            graph.proposal,
            _Program(graph.geometry),
            _Scene(graph.scene),
            graph.criteria,
        )
        findings = []
        for criterion in graph.criteria:
            key = criterion["measurement_key"]
            value = values.get(key)
            expected = json.loads(criterion["expected_json"])
            operator = criterion["operator"]
            if value is None:
                status = "unknown"
            elif operator == "minimum":
                status = "pass" if value >= expected else "fail"
            elif operator == "maximum":
                status = "pass" if value <= expected else "fail"
            else:
                status = "pass" if value == expected else "fail"
            findings.append((criterion["criterion_id"], status))
        return tuple(sorted(findings))

    return evaluate


def frozen_deltas(case: str, graph: BaselineGraph) -> list[FrozenRepairDelta]:
    """One deterministic, project-derived delta per edit class."""

    relationships = graph.design_program["relationships"]
    non_separation = [
        rel for rel in relationships if rel.get("kind") != "separation"
    ] or relationships
    relation = sorted(
        non_separation, key=lambda rel: rel["relationship_id"]
    )[0]
    components = [
        component
        for component in graph.proposal["components"]
        if component.get("parent_component_id") is not None
    ]
    bound_components = {
        binding["component_id"]
        for binding in graph.geometry["proposal"]["semantic_bindings"]
    }
    replaced = sorted(
        (
            component["component_id"]
            for component in components
            if component["component_id"] in bound_components
        )
    )
    replaced_id = replaced[0] if replaced else sorted(
        component["component_id"] for component in components
    )[0]
    operations = graph.geometry["proposal"]["operations"]
    sized = [
        operation
        for operation in operations
        if any(p["name"] == "size" for p in operation.get("parameters", []))
    ]
    target_op = sorted(
        sized or operations, key=lambda operation: operation["op_id"]
    )[0]
    parameter = next(
        (
            p
            for p in target_op.get("parameters", [])
            if p["name"] == "size"
        ),
        target_op["parameters"][0],
    )
    value = json.loads(parameter["value_json"])
    if isinstance(value, list) and value and isinstance(value[0], (int, float)):
        edited = [value[0] + 2.0, *value[1:]]
    elif isinstance(value, (int, float)):
        edited = value + 2.0
    else:
        edited = value
    return [
        FrozenRepairDelta(
            delta_id=f"{case}-relation-edit",
            case_id=f"case-{case}",
            edit_class=EditClass.PROGRAM_RELATION,
            target_node=(
                f"program-relationship:{relation['relationship_id']}"
            ),
            payload={"kind": "separation"},
            rationale=(
                "reclassify one declared non-separation relationship as a "
                "separation requirement, reopening its connection and "
                "relationship-coverage consumers"
            ),
        ),
        FrozenRepairDelta(
            delta_id=f"{case}-component-replacement",
            case_id=f"case-{case}",
            edit_class=EditClass.COMPONENT_REPLACEMENT,
            target_node=f"component:{replaced_id}",
            payload={
                "replacement_component_id": f"{replaced_id}-v2",
                "intent": (
                    "replace the component under an explicit responsibility "
                    "transfer keeping its volumes and geometry bindings"
                ),
            },
            rationale=(
                "semantic-component replacement with responsibility "
                "transfer; bindings, criteria localities, and family "
                "identity must follow or be reopened"
            ),
        ),
        FrozenRepairDelta(
            delta_id=f"{case}-geometry-constraint",
            case_id=f"case-{case}",
            edit_class=EditClass.GEOMETRIC_CONSTRAINT,
            target_node=f"operation:{target_op['op_id']}",
            payload={
                "parameter_name": parameter["name"],
                "value_json": json.dumps(edited),
            },
            rationale=(
                "dimension edit on one geometry operation parameter; its "
                "objects, realized scene footprint, and geometry-local "
                "criteria must be recomputed"
            ),
        ),
    ]


def gold_for(delta: FrozenRepairDelta, graph: BaselineGraph) -> GoldImpactSet:
    """Harness-proposed gold impact set: the typed downstream closure."""

    affected = sorted(graph.closure(delta.target_node))
    expected_failure = None
    if delta.edit_class is EditClass.GEOMETRIC_CONSTRAINT:
        expected_failure = None
    return GoldImpactSet(
        delta_id=delta.delta_id,
        annotator="harness:claude-fable-5",
        annotator_is_harness=True,
        affected_nodes=tuple(affected),
        expected_first_failure=expected_failure,
        basis=(
            "typed downstream closure of the edited node along explicit "
            "record references; proposed by the executing harness before "
            "any episode ran and open to human architectural review"
        ),
    )


def _study(run_id: str):
    repo = FilesystemProjectRepository.open(STUDY_ROOT)
    try:
        run = repo.load_run(run_id)
    except Exception:
        run = repo.create_run(run_id)
    dest = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id)
    return repo, run, dest


def _put(repo, run, dest, kind, payload):
    ref = repo.put_json(
        run=run, destination=dest, record_kind=kind, payload=payload
    )
    print(f"  retained {ref.uri}")
    return ref


def cmd_freeze(now: str, run_id: str) -> int:
    if not (STUDY_ROOT / "project.json").exists():
        bootstrap_raw_request_project(
            STUDY_ROOT,
            project_id="p063-repair-study",
            run_id=run_id,
            prompt=(
                "Measure repair locality over the retained study-028 "
                "baselines without design authority."
            ),
        )
    repo, run, dest = _study(run_id)
    base = {
        "project_id": run.base.project_id,
        "version": run.base.version,
        "state_sha256": run.base.require_digest(),
    }
    episodes = []
    for case in CASES:
        _, graph, commitments, envelope = load_baseline(case)
        baseline_digest = canonical_digest(
            {
                "program": graph.design_program,
                "proposal": graph.proposal,
                "geometry": graph.geometry,
                "scene": graph.scene,
                "criteria": graph.criteria,
            }
        )
        for delta in frozen_deltas(case, graph):
            gold = gold_for(delta, graph)
            delta_ref = _put(
                repo,
                run,
                dest,
                f"frozen-delta-{delta.delta_id}",
                delta.to_dict(),
            )
            gold_ref = _put(
                repo,
                run,
                dest,
                f"gold-impact-{delta.delta_id}",
                gold.to_dict(),
            )
            for strategy in STRATEGIES:
                episodes.append(
                    {
                        "episode_id": (
                            f"{delta.delta_id}--{strategy.value}"
                        ),
                        "case_id": delta.case_id,
                        "edit_class": delta.edit_class.value,
                        "strategy": strategy.value,
                        "delta_ref": delta_ref.uri,
                        "delta_digest": delta.delta_digest,
                        "gold_ref": gold_ref.uri,
                        "baseline_digest": baseline_digest,
                        "baseline_commitments": list(commitments),
                        "source_envelope_intent": envelope.get(
                            "intent_digest"
                        ),
                    }
                )
    prereg = {
        "schema": "RepairEpisodePreregistration@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": base,
        "study_id": "p063-repair-locality-study",
        "registered_at": now,
        "strategies": [strategy.value for strategy in STRATEGIES],
        "metric_ids": [
            "repair_success",
            "no_new_validator_failures",
            "recompute_ratio",
            "commitment_retention",
            "record_retention",
            "impact_precision",
            "impact_recall",
            "unintended_change_ratio",
            "failure_attribution_match",
        ],
        "episodes": episodes,
        "provider_invocation_authority": False,
        "canonical_write_authority": False,
        "aggregate_winner_authority": False,
    }
    prereg["preregistration_digest"] = canonical_digest(prereg)
    _put(repo, run, dest, "repair-preregistration", prereg)
    print(f"FROZEN: {len(episodes)} episodes")
    return 0


def _preregistration(repo, run, dest):
    for ref in repo.list_json(run=run, destination=dest):
        payload = repo.load_json(ref)
        if payload.get("schema") == "RepairEpisodePreregistration@1":
            return payload
    raise SystemExit("no repair preregistration; run freeze first")


def cmd_run(now: str, run_id: str) -> int:
    repo, run, dest = _study(run_id)
    prereg = _preregistration(repo, run, dest)
    existing = {
        repo.load_json(ref)["episode_id"]
        for ref in repo.list_json(run=run, destination=dest)
        if repo.load_json(ref).get("schema") == "RepairEpisodeReceipt@1"
    }
    baselines = {}
    for case in CASES:
        assignment, graph, commitments, _ = load_baseline(case)
        baselines[f"case-{case}"] = (assignment, graph, commitments)
    ran = 0
    for episode in prereg["episodes"]:
        if episode["episode_id"] in existing:
            continue
        assignment, graph, commitments = baselines[episode["case_id"]]
        delta = FrozenRepairDelta.from_dict(
            {
                key: value
                for key, value in repo.load_json(
                    next(
                        ref
                        for ref in repo.list_json(run=run, destination=dest)
                        if ref.uri == episode["delta_ref"]
                    )
                ).items()
                if key
                not in (
                    "geometry_generation_authority",
                    "canonical_write_authority",
                )
            }
        )
        if delta.delta_digest != episode["delta_digest"]:
            raise SystemExit("frozen delta drifted")
        gold_payload = repo.load_json(
            next(
                ref
                for ref in repo.list_json(run=run, destination=dest)
                if ref.uri == episode["gold_ref"]
            )
        )
        gold = GoldImpactSet.from_dict(
            {
                key: value
                for key, value in gold_payload.items()
                if key != "canonical_write_authority"
            }
        )
        result = run_episode(
            episode_id=episode["episode_id"],
            baseline=graph,
            delta=delta,
            strategy=RepairStrategy(episode["strategy"]),
            evaluate=_evaluator(assignment),
            commitments=tuple(episode["baseline_commitments"]),
        )
        metrics = result.metrics(gold)
        receipt = {
            "schema": "RepairEpisodeReceipt@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": {
                "project_id": run.base.project_id,
                "version": run.base.version,
                "state_sha256": run.base.require_digest(),
            },
            "episode_id": episode["episode_id"],
            "case_id": episode["case_id"],
            "edit_class": episode["edit_class"],
            "strategy": episode["strategy"],
            "delta_ref": episode["delta_ref"],
            "delta_digest": episode["delta_digest"],
            "gold_ref": episode["gold_ref"],
            "baseline_digest": episode["baseline_digest"],
            "preregistration_digest": prereg["preregistration_digest"],
            "executed_at": now,
            "status": result.status.value,
            "validators_passed": result.validators_passed,
            "findings": [list(item) for item in result.findings],
            "baseline_findings": [
                list(item) for item in result.baseline_findings
            ],
            "new_failures": list(result.new_failures),
            "first_failure": result.first_failure,
            "recomputed_node_count": len(result.recomputed_nodes),
            "eligible_node_count": result.eligible_nodes,
            "changed_node_count": len(result.changed_nodes),
            "baseline_node_count": result.baseline_nodes,
            "recomputed_nodes": list(result.recomputed_nodes),
            "changed_nodes": list(result.changed_nodes),
            "metrics": metrics,
            "design_authority": False,
            "canonical_write_authority": False,
        }
        _put(repo, run, dest, f"episode-{episode['episode_id']}", receipt)
        ran += 1
    print(f"RAN {ran} episodes ({len(existing)} already retained)")
    return 0


def cmd_index(now: str, run_id: str) -> int:
    repo, run, dest = _study(run_id)
    prereg = _preregistration(repo, run, dest)
    receipts = {}
    for ref in repo.list_json(run=run, destination=dest):
        payload = repo.load_json(ref)
        if payload.get("schema") == "RepairEpisodeReceipt@1":
            receipts[payload["episode_id"]] = payload
    rows = []
    for episode in prereg["episodes"]:
        receipt = receipts.get(episode["episode_id"])
        rows.append(
            {
                "episode_id": episode["episode_id"],
                "case_id": episode["case_id"],
                "edit_class": episode["edit_class"],
                "strategy": episode["strategy"],
                "status": receipt["status"] if receipt else "planned",
                "metrics": receipt["metrics"] if receipt else None,
            }
        )
    complete = all(row["status"] != "planned" for row in rows)
    index = {
        "schema": "RepairStudyIndex@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "study_id": prereg["study_id"],
        "preregistration_digest": prereg["preregistration_digest"],
        "generated_at": now,
        "episodes": rows,
        "table_ready": complete,
        "episode_count": len(rows),
        "aggregate_winner_claimed": False,
        "canonical_write_authority": False,
    }
    _put(repo, run, dest, "repair-study-index", index)
    repo.verify()
    print(f"INDEX table_ready={complete} episodes={len(rows)}")
    return 0


sys.path.insert(0, str(ROOT / "archive" / "tools"))
from _probe_paths import resolve_probe_root  # noqa: E402

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run", "index"))
    parser.add_argument("--now", required=True)
    parser.add_argument("--run", default=STUDY_RUN)
    args = parser.parse_args(argv)
    return {"freeze": cmd_freeze, "run": cmd_run, "index": cmd_index}[
        args.command
    ](args.now, args.run)


if __name__ == "__main__":
    raise SystemExit(main())
