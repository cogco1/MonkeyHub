"""Run revision tasks into an empty directory: one bundle per attempt, an index and a gate summary.

    python -m labs.revision_tasks.run --out D:/runs/e0-a
    python -m labs.revision_tasks.run --out D:/runs/e0-b --compare D:/runs/e0-a

Each task: realize the frozen start, run the ArchFlow arm (``pipeline.run_archflow_arm``),
judge any executed candidate with the oracle in a separate ``python -I`` process,
and write ``<task_id>/bundle.json``. A bundle's ``bundle_sha256`` covers its
``deterministic`` part only; wall-clock times, file hashes and the environment
sit in ``observed``, because STEP and 3DM bytes carry timestamps and entity order
that differ between runs of the same program.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)
from labs.revision_tasks import fixtures, infer, oracle, pipeline, taskspec  # noqa: E402

LAB = Path(__file__).resolve().parent
CHECKOUT = _bootstrap.CHECKOUT

EXPERIMENT = "E0"
ARM = "B3_archflow"
BASE_COMMIT = "389b054839f549501cf91b9e9518dd0930b832cd"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=CHECKOUT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def harness_identity() -> dict[str, Any]:
    dirty = git("status", "--porcelain", "--", "labs/revision_tasks")
    return {"base_commit": BASE_COMMIT, "harness_commit": git("rev-parse", "HEAD") or None, "harness_dirty": bool(dirty)}


def environment() -> dict[str, Any]:
    versions: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform()}
    for module in ("OCP", "rhino3dm", "numpy"):
        try:
            imported = __import__(module)
            versions[module] = getattr(imported, "__version__", "present")
        except ImportError:
            versions[module] = None
    return versions


def readback_summary(realization: pipeline.Realization) -> dict[str, Any]:
    rows = {}
    for object_id in pipeline.visible_objects(realization):
        row = realization.receipt.readback[object_id]
        rows[object_id] = {"valid": row["valid"], "solid_count": row["solid_count"], "closed": row["closed"],
                           "volume_m3": pipeline._r(row["volume"]),
                           "bbox": {"min": [pipeline._r(v) for v in row["bbox"]["min"]], "max": [pipeline._r(v) for v in row["bbox"]["max"]]}}
    return rows


def run_oracle(task_path: Path, candidate: Path, start: Path, out: Path) -> dict[str, Any]:
    command = [sys.executable, "-I", str(LAB / "oracle.py"), "--task", str(task_path), "--candidate", str(candidate), "--start", str(start), "--out", str(out)]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return {"verdict": "unchecked", "relations": [], "error": (done.stderr or done.stdout)[-2000:]}
    return json.loads(out.read_text(encoding="utf-8"))


def element_of(objects_by_element: dict[str, tuple[str, ...]]) -> dict[str, str]:
    return {o: e for e, objs in objects_by_element.items() for o in objs}


def score(spec: dict[str, Any], claim: str, oracle_verdict: str) -> dict[str, Any]:
    gold = spec["gold"]
    # ``ask`` (a broken inferred relation, X8) is right wherever the gold says violated; on a held task it is an unneeded question.
    outcome_ok = claim in gold["acceptable_arm_outcomes"] or (claim == "ask" and gold["expected_verdict"] == "violated")
    if gold["expected_verdict"] == "refuse":
        oracle_ok = None if oracle_verdict == "unavailable" else False
        success = outcome_ok and oracle_verdict == "unavailable"
    else:
        # A refusal before execution leaves no candidate to judge; it counts when the gold accepts a refusal.
        oracle_ok = None if oracle_verdict == "unavailable" else oracle_verdict == gold["expected_verdict"]
        success = outcome_ok and (claim == "refuse" or bool(oracle_ok))
    return {"success": success, "outcome_matches_gold": outcome_ok, "oracle_matches_gold": oracle_ok,
            "false_accept": claim == "accept" and (oracle_verdict == "violated" or gold["expected_verdict"] == "refuse")}


def run_task(task_path: Path, out: Path, order_slot: int, arm_name: str = ARM) -> dict[str, Any]:
    spec = taskspec.load(task_path)
    task_dir = out / spec["task_id"] if arm_name == ARM else out / arm_name / spec["task_id"]
    (task_dir / "start").mkdir(parents=True)
    (task_dir / "candidate").mkdir()
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    record = taskspec.start_record(spec)
    deterministic: dict[str, Any] = {
        "attempt_id": f"{EXPERIMENT}-{arm_name}-{spec['task_id']}-r0", "task_id": spec["task_id"], "experiment": EXPERIMENT, "arm": arm_name,
        "repeat_idx": 0, "order_slot": order_slot, "harness": harness_identity(), "taskspec_sha256": taskspec.spec_sha256(spec),
        "inputs": {"context_ref": None, "sha256": None, "tokens": 0},
    }
    observed: dict[str, Any] = {"started_at": started_at, "environment": environment(), "artifacts": {}}
    status = "completed"
    if record.digest != spec["start_state"]["content_digest"]:
        deterministic.update({"status": "excluded", "reasons": [{"code": "FIXTURE_DRIFT", "detail": "fixture no longer matches the frozen start"}]})
        return finish(task_dir, deterministic, observed, t0)
    frozen = spec["oracle"]
    if (frozen["oracle_id"], frozen["version"], frozen["sha256"]) != (oracle.ORACLE_ID, oracle.ORACLE_VERSION, taskspec.file_sha256(LAB / "oracle.py")):
        deterministic.update({"status": "excluded", "reasons": [{"code": "ORACLE_DRIFT", "detail": "oracle code differs from the one the task froze"}]})
        return finish(task_dir, deterministic, observed, t0)
    start = record if record.base is not None else fixtures.bound(record)
    start_real = pipeline.realize(start, (task_dir / "start").resolve(), "start")
    t_start = time.perf_counter()
    inferred_report = None
    if arm_name in (ARM, "B3+I"):
        # A patch prepared against an older start carries that start's digests: the arm sees the stale base.
        arm_spec = spec if "patch_base_digests" not in spec else {**spec, "start_state": {**spec["start_state"], **spec["patch_base_digests"]}}
        arm = pipeline.run_archflow_arm(arm_spec, start, start_real, (task_dir / "candidate").resolve())
        if arm_name == "B3+I":
            kept, dropped = infer.infer_all(start, start_real)
            inferred_report = {"kept": [f"{k.layer} {k.kind} {k.a}/{k.b}" for k in kept], "dropped_on_start": [f"{k.layer} {k.kind} {k.a}/{k.b}" for k in dropped],
                               "harness_added": True}
            if arm.claim == "accept" and arm.realization is not None:
                broken = [r for r in infer.check_inferred(infer.with_inferred(arm.record, kept), arm.realization) if r["status"] == "broken"]
                inferred_report["broken"] = broken
                if broken:
                    arm.claim = "ask"
                    arm.reasons.append({"code": "INFERRED_RELATION_BROKEN", "refs": [f"{r['a']}/{r['b']} {r['kind']}" for r in broken]})
    else:
        arm = pipeline.run_baseline_arm(arm_name, spec, start, start_real, (task_dir / "candidate").resolve())
    t_arm = time.perf_counter()
    report: dict[str, Any] = {"verdict": "unavailable", "relations": [], "detail": "no executed candidate"}
    if arm.model_path is not None:
        report = run_oracle(task_path, arm.model_path, start_real.preview_path, task_dir / "oracle_report.json")
    t_oracle = time.perf_counter()
    start_shapes = {name: oracle.signature(mesh) for name, mesh in oracle.load_meshes(start_real.preview_path).items()}
    ids: dict[str, Any] = {"closure": arm.closure}
    if arm.realization is not None:
        shapes = report.get("object_signatures", {})
        owners = {**element_of(start_real.objects_by_element), **element_of(arm.realization.objects_by_element)}
        changed = sorted(o for o in set(start_shapes) | set(shapes)
                         if o not in start_shapes or o not in shapes or not oracle.same_shape(start_shapes[o], shapes[o]))
        recompiled = arm.recompiled if arm.recompiled is not None else []
        ids.update({
            "recompiled": recompiled, "retained": sorted(set(shapes) - set(recompiled)), "updated_elements": arm.updated_elements,
            "retired": sorted(set(start_shapes) - set(shapes)), "added": sorted(set(shapes) - set(start_shapes)),
            "changed_objects": changed, "untouched": sorted(set(shapes) - set(changed)),
            "unrelated_touched": sorted(o for o in changed if owners.get(o) not in spec["gold"]["affected_entities"]),
        })
        observed["artifacts"].update({"candidate.step": sha256_file(arm.realization.step_path), "candidate.model": sha256_file(arm.model_path)})
    observed["artifacts"].update({"start.step": sha256_file(start_real.step_path), "start.preview.3dm": sha256_file(start_real.preview_path)})
    if arm.claim == "refuse":
        status = "refused"
    deterministic.update({
        "start": {"content_digest": record.digest, "exact_base_digest": start.state_digest, "program_digest": start_real.program.program_digest,
                  "fixture_files_sha256": spec["fixture"]["files_sha256"]},
        "proposal": {"parse_status": "frozen_patch", "typed_patch": spec["intended_change"]["typed_delta"], "operators": arm.operators},
        "execution": None if arm.realization is None else {
            "program_digest": arm.realization.program.program_digest,
            "operation_ids": [op.op_id for op in arm.realization.program.proposal.operations],
            "reused_object_ids": sorted(arm.realization.receipt.reused_object_ids)},
        "readback": None if arm.realization is None else readback_summary(arm.realization),
        "checker": {"system": {"declared": arm.declared, "obligations": arm.obligations, "new_interference": arm.new_interference,
                               "inferred": inferred_report},
                    "oracle": {"oracle_id": report.get("oracle_id", oracle.ORACLE_ID), "oracle_version": report.get("oracle_version", oracle.ORACLE_VERSION),
                               "verdict": report["verdict"], "relations": report.get("relations", []), "error": report.get("error")}},
        "ids": ids,
        "arm_claim": arm.claim, "reasons": arm.reasons,
        "acceptance": {"candidate": "retained" if arm.realization is not None else "none", "human_accept": False},
        "status": status,
        "score": score(spec, arm.claim, report["verdict"]),
    })
    observed["costs"] = {"input_tokens": 0, "output_tokens": 0, "usd_api_equivalent": 0.0, "usd_paid": 0.0,
                         "wall_clock_s": {"start_realization": round(t_start - t0, 3), **{k: round(v, 3) for k, v in arm.phases_s.items()},
                                          "arm_total": round(t_arm - t_start, 3), "oracle_process": round(t_oracle - t_arm, 3)}}
    return finish(task_dir, deterministic, observed, t0)


def finish(task_dir: Path, deterministic: dict[str, Any], observed: dict[str, Any], t0: float) -> dict[str, Any]:
    observed["finished_at"] = datetime.now(timezone.utc).isoformat()
    observed.setdefault("costs", {})["total_s"] = round(time.perf_counter() - t0, 3)
    bundle = {"schema": "RevisionTaskBundle@1", "deterministic": deterministic, "observed": observed,
              "bundle_sha256": hashlib.sha256(canonical(deterministic)).hexdigest()}
    (task_dir / "bundle.json").write_text(json.dumps(bundle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return bundle


def gate_one(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    by_task = {b["deterministic"]["task_id"]: b["deterministic"] for b in bundles}

    def ok(task_id: str) -> bool:
        return bool(by_task.get(task_id, {}).get("score", {}).get("success"))

    accepted_held = [t for t, d in by_task.items() if d.get("arm_claim") == "accept" and d["checker"]["oracle"]["verdict"] == "held"]
    return {"one_accept_with_oracle_held": accepted_held,
            "conflict_caught": [t for t in ("F2-T3-01", "F2-T4-01") if ok(t)],
            "missing_information_refused": [t for t in ("F3-T5-01",) if ok(t)],
            "false_accepts": [t for t, d in by_task.items() if d.get("score", {}).get("false_accept")],
            "passed": bool(accepted_held) and (ok("F2-T3-01") or ok("F2-T4-01")) and ok("F3-T5-01")}


def main(argv: list[str] | None = None) -> int:
    global EXPERIMENT
    parser = argparse.ArgumentParser(description="Run the revision tasks into an empty directory.")
    parser.add_argument("--out", type=Path, required=True, help="an empty or new directory outside the repository")
    parser.add_argument("--tasks", help="comma-separated task ids (default: every task file)")
    parser.add_argument("--compare", type=Path, help="an earlier output directory whose bundle hashes must match")
    parser.add_argument("--arms", default=ARM, help="comma-separated arms: B0 (full rebuild), B1 (target only), B2 (declared closure), B3_archflow, B3+I (B3 with inferred relations)")
    parser.add_argument("--experiment", default=EXPERIMENT)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(args.tasks.split(",")) if args.tasks else None
    paths = [p for p in taskspec.task_files() if wanted is None or p.stem in wanted]
    bundles = []
    EXPERIMENT = args.experiment
    for arm_name in args.arms.split(","):
        for slot, path in enumerate(paths):
            bundle = run_task(path, out, slot, arm_name)
            d = bundle["deterministic"]
            prefix = "" if arm_name == ARM else f"{arm_name} "
            print(f"{prefix}{d['task_id']}: claim={d.get('arm_claim')} oracle={d.get('checker', {}).get('oracle', {}).get('verdict')} "
                  f"success={d.get('score', {}).get('success')} false_accept={d.get('score', {}).get('false_accept')} "
                  f"bundle={bundle['bundle_sha256'][:16]}", flush=True)
            bundles.append(bundle)
    with (out / "index.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_id", "task_id", "experiment", "arm", "status", "arm_claim", "oracle_verdict", "success", "bundle_sha256"])
        for b in bundles:
            d = b["deterministic"]
            writer.writerow([d["attempt_id"], d["task_id"], d["experiment"], d["arm"], d["status"], d.get("arm_claim"),
                             d.get("checker", {}).get("oracle", {}).get("verdict"), d.get("score", {}).get("success"), b["bundle_sha256"]])
    summary: dict[str, Any] = {"gate_1": gate_one([b for b in bundles if b["deterministic"]["arm"] == ARM]), "tasks": len(bundles), "by_arm": {}}
    for b in bundles:
        d = b["deterministic"]
        cell = summary["by_arm"].setdefault(d["arm"], {"tasks": 0, "success": 0, "false_accept": 0})
        cell["tasks"] += 1
        cell["success"] += int(bool(d.get("score", {}).get("success")))
        cell["false_accept"] += int(bool(d.get("score", {}).get("false_accept")))
    if args.compare:
        earlier = {(row["arm"], row["task_id"]): row["bundle_sha256"] for row in csv.DictReader((args.compare / "index.csv").open(encoding="utf-8"))}
        summary["replay"] = {f"{b['deterministic']['arm']}/{b['deterministic']['task_id']}": b["bundle_sha256"] == earlier.get((b["deterministic"]["arm"], b["deterministic"]["task_id"]))
                             for b in bundles}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))
    if args.compare and not all(summary["replay"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
