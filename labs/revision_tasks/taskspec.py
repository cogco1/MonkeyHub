"""TaskSpec@paper/1: one revision task as one JSON file, and its validator.

A task names a fixture and its frozen start (content and exact-base digests),
one typed change, the relations that must be protected with the oracle's
measurement for each, the gold expectations a person wrote from the fixture's
physical definition, and the oracle that judges it. It is a research file
format: it is never written into a project.

``freeze`` fills only the fields computed from code (start digests, the
fixture generator's and the oracle's file hashes); every expectation stays as
the author wrote it.

    python -m labs.revision_tasks.taskspec --check            # validate every task file
    python -m labs.revision_tasks.taskspec --freeze           # (re)compute the derived fields
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SPEC_VERSION = "TaskSpec@paper/1"
LAB = Path(__file__).resolve().parent
TASKS = LAB / "tasks"

TASK_TYPES = {
    "T1_legal_local", "T2_cross_stage_protected", "T3_declared_interface_conflict", "T4_planted_hidden_dependency",
    "T5_missing_information", "T6_contradictory", "T7_history_rejected_alternative", "T8_stale_evidence",
}
VERDICTS = {"held", "violated", "unknown", "refuse"}
ARM_OUTCOMES = {"accept", "reopen", "refuse", "unknown"}
RELATION_KINDS = {"contact", "clearance", "equality", "bound", "non_interference"}
ORACLE_METHODS = {"cold_readback_geometry", "rule", "human"}
REQUIRED = ("spec_version", "task_id", "family", "task_type", "fixture", "start_state", "intended_change",
            "protected_relations", "gold", "oracle", "backend", "budget", "rubric")


class TaskSpecError(ValueError):
    """A task file does not satisfy TaskSpec@paper/1."""


def problems(spec: Any) -> list[str]:
    """Every way ``spec`` departs from the format; empty when it is valid."""

    from labs.revision_tasks.oracle import CHECKS

    found: list[str] = []
    if not isinstance(spec, dict):
        return ["a task is a JSON object"]
    for key in REQUIRED:
        if key not in spec:
            found.append(f"missing {key}")
    if found:
        return found
    if spec["spec_version"] != SPEC_VERSION:
        found.append(f"spec_version must be {SPEC_VERSION}")
    if spec["task_type"] not in TASK_TYPES:
        found.append(f"unknown task_type {spec['task_type']!r}")
    if not str(spec["task_id"]).startswith(f"{spec['family']}-"):
        found.append("task_id must start with its family")
    fixture = spec["fixture"]
    for key in ("fixture_id", "version", "generator", "files_sha256"):
        if key not in fixture:
            found.append(f"fixture.{key} missing")
    start = spec["start_state"]
    for key in ("state_ref", "content_digest", "exact_base_digest"):
        if not isinstance(start.get(key), str) or not start[key]:
            found.append(f"start_state.{key} missing")
    change = spec["intended_change"]
    deltas = change.get("typed_delta")
    if not isinstance(change.get("text_en"), str) or not change["text_en"]:
        found.append("intended_change.text_en missing")
    if not isinstance(deltas, list) or not deltas:
        found.append("intended_change.typed_delta must be a non-empty list")
    else:
        for k, delta in enumerate(deltas):
            if set(delta) != {"target", "parameter", "from", "to"}:
                found.append(f"typed_delta[{k}] must have exactly target, parameter, from, to")
            elif delta["target"] != f"parameter:{delta['parameter']}":
                found.append(f"typed_delta[{k}].target must be parameter:<parameter>")
    ids = set()
    for k, relation in enumerate(spec["protected_relations"]):
        rid = relation.get("id")
        if not rid or rid in ids:
            found.append(f"protected_relations[{k}].id missing or repeated")
        ids.add(rid)
        if relation.get("kind") not in RELATION_KINDS:
            found.append(f"{rid}: kind must be one of {sorted(RELATION_KINDS)}")
        if not isinstance(relation.get("declared_in_graph"), bool):
            found.append(f"{rid}: declared_in_graph must be true or false")
        measure = relation.get("measure")
        if measure is not None and measure.get("check") not in CHECKS:
            found.append(f"{rid}: measure.check must be one of {sorted(CHECKS)}")
    gold = spec["gold"]
    if gold.get("expected_verdict") not in VERDICTS:
        found.append("gold.expected_verdict invalid")
    outcomes = gold.get("acceptable_arm_outcomes")
    if not isinstance(outcomes, list) or not outcomes or not set(outcomes) <= ARM_OUTCOMES:
        found.append("gold.acceptable_arm_outcomes invalid")
    for key in ("affected_entities", "obligations_to_recheck"):
        if not isinstance(gold.get(key), list):
            found.append(f"gold.{key} must be a list")
    for key in ("annotated_by", "annotated_at"):
        if not isinstance(gold.get(key), str) or not gold[key]:
            found.append(f"gold.{key} missing")
    if "setup" in spec:
        if not isinstance(spec["setup"], list) or not spec["setup"] or spec.get("patch_base") != "before_setup":
            found.append("setup must be a non-empty list of deltas, with patch_base 'before_setup'")
        elif not isinstance(spec.get("patch_base_digests"), dict):
            found.append("a task with setup must freeze patch_base_digests")
    oracle = spec["oracle"]
    if oracle.get("reads_metadata") is not False:
        found.append("oracle.reads_metadata must be false")
    if oracle.get("method") not in ORACLE_METHODS:
        found.append("oracle.method invalid")
    for key in ("oracle_id", "version", "sha256"):
        if not isinstance(oracle.get(key), str) or not oracle[key]:
            found.append(f"oracle.{key} missing")
    return found


def load(path: Path) -> dict[str, Any]:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    found = problems(spec)
    if found:
        raise TaskSpecError(f"{Path(path).name}: " + "; ".join(found))
    return spec


def task_files() -> list[Path]:
    return sorted(TASKS.glob("*.json"))


def file_sha256(path: Path) -> str:
    """Hash of a text file's bytes with line endings normalised, so a checkout's autocrlf does not change it."""

    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def start_record(spec: dict[str, Any]):
    """The task's current start: the fixture, with the task's setup changes applied when it has any."""

    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    from labs.revision_tasks import fixtures

    record = fixtures.FIXTURES[spec["fixture"]["fixture_id"]]()
    if not spec.get("setup"):
        return record
    bound = fixtures.bound(record)
    for delta in spec["setup"]:
        bound = apply_state_record_operator(bound, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=bound.digest, base_state_digest=bound.state_digest,
            target_ref=delta["target"], key=delta["parameter"], value=delta["to"]))
    return bound


def freeze(path: Path) -> bool:
    """Recompute the derived fields of one task file; True when it changed."""

    from labs.revision_tasks import fixtures, oracle

    spec = json.loads(path.read_text(encoding="utf-8"))
    original = fixtures.FIXTURES[spec["fixture"]["fixture_id"]]()
    record = start_record(spec)
    bound = fixtures.bound(record)
    derived = json.loads(json.dumps(spec))
    if "setup" in spec:
        derived["patch_base_digests"] = {"content_digest": original.digest, "exact_base_digest": fixtures.bound(original).state_digest}
    derived["fixture"]["version"] = fixtures.FIXTURE_VERSION
    derived["fixture"]["files_sha256"] = {"labs/revision_tasks/fixtures.py": file_sha256(Path(fixtures.__file__))}
    derived["start_state"]["content_digest"] = record.digest
    derived["start_state"]["exact_base_digest"] = bound.state_digest
    derived["oracle"].update({"oracle_id": oracle.ORACLE_ID, "version": oracle.ORACLE_VERSION, "sha256": file_sha256(Path(oracle.__file__))})
    if derived == spec:
        return False
    path.write_text(json.dumps(derived, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

    parser = argparse.ArgumentParser(description="Validate or freeze the lab's task files.")
    parser.add_argument("--freeze", action="store_true", help="recompute digests and file hashes in place")
    parser.add_argument("--check", action="store_true", help="validate every task file")
    args = parser.parse_args(argv)
    status = 0
    for path in task_files():
        if args.freeze and freeze(path):
            print(f"froze {path.name}")
        found = problems(json.loads(path.read_text(encoding="utf-8")))
        if found:
            status = 1
            print(f"{path.name}: " + "; ".join(found))
    if status == 0:
        print(f"{len(task_files())} task files valid")
    return status


if __name__ == "__main__":
    sys.exit(main())
