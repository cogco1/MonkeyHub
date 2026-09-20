"""Run/replay the GH-173 four-arm comparison using caller-assigned P036 storage."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, stdev
import subprocess

from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord

from .fixture import checks
from .harness import ARMS, Budget, RetainedTrial, replay, run_trial
from .provider import CallResult, ClaudeProvider


class ScriptedProvider:
    """Labeled mechanism control, never represented as a model experiment."""

    def __init__(self, scenario="wrong_entry"):
        self.scenario = scenario
        self.count = 0

    def describe(self):
        return {"kind": "deterministic", "scenario": self.scenario, "model": None,
                "label": "scripted mechanism control, not model evidence"}

    def call(self, prompt, schema, *, timeout_seconds, max_cost_usd):
        self.count += 1
        usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
                 "cache_write_input_tokens": 0, "api_equivalent_cost_usd": 0.0}
        if self.scenario in ("timeout", "missing") and self.count == 2:
            return CallResult(None, {key: None for key in usage}, 0.0,
                              "timeout" if self.scenario == "timeout" else "malformed", None,
                              {"kind": "deterministic", "injected_failure": True})
        stage = prompt["checkpoint"]
        task = prompt["task"]
        if task == "review":
            names = [x["name"] for x in checks(StateRecord.from_dict(prompt["candidate"]), final=stage == 3)]
            binding = prompt["binding"]
            if self.scenario == "stale":
                binding = {**binding, "delta": {**binding["delta"], "sha256": "0" * 64}}
            objections = []
            if self.scenario == "false_veto" and stage < 3:
                pending = next(x for x in checks(StateRecord.from_dict(prompt["candidate"])) if x["status"] == "pending")
                objections = [{"check": pending["name"], "category": pending["category"],
                               "request": "hard_block", "summary": "Injected unsupported veto of permitted incomplete work"}]
            answer = {"binding": binding, "requested_checks": [] if self.scenario == "missed" else names,
                      "objections": objections, "summary": "Scripted review of labeled fixture checks"}
        else:
            if task == "repair":
                stage = 0
                action = {"entry_side": "east" if self.scenario == "veto_loop" else "west", "semantic_detail": "unknown"}
            elif stage == 1:
                wrong = self.scenario in ("wrong_entry", "missed", "veto_loop", "timeout", "missing")
                action = {"entry_side": "east" if wrong else "west", "semantic_detail": "unknown"}
                if self.scenario == "invariant":
                    action["courtyard_width"] = 2
            elif stage == 2:
                action = {"gallery": "omit" if self.scenario == "omission" else "add"}
            else:
                action = {"unit_access": "omit" if self.scenario == "omission" else "add", "semantic_detail": "generic"}
            answer = {"checkpoint": stage, "action": action, "assumptions": ["Synthetic declared fixture only"],
                      "unresolved": ["Physical behavior has no inputs"], "summary": "Scripted bounded decision"}
        return CallResult(answer, usage, 0.0, "success", None, {"kind": "deterministic"})


def _statistics(values):
    known = [value for value in values if value is not None]
    return {"n": len(known), "missing": len(values) - len(known),
            "mean": mean(known) if known else None, "sample_sd": stdev(known) if len(known) > 1 else None,
            "values": values}


def summarize(reports: list[dict]) -> dict:
    result = {}
    for arm in ARMS:
        trials = [row for row in reports if row["arm"] == arm]
        propagated = [error for row in trials for error in row["propagation"]]
        known_depths = [error["depth"] for error in propagated if error["status"] == "detected"]
        token_keys = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens")
        def token_sum(trial, key):
            values = [call["usage"].get(key) for call in trial["calls"]]
            return sum(values) if values and all(x is not None for x in values) else None
        result[arm] = {
            "trials": len(trials), "completed": sum(t["complete"] for t in trials),
            "completion_rate": sum(t["complete"] for t in trials) / len(trials) if trials else None,
            "outcomes": dict(Counter(t["status"] for t in trials)),
            "unresolved": [t["final_assessment"]["unresolved"] for t in trials],
            "unavailable": [t["final_assessment"]["unavailable"] for t in trials],
            "propagation": {"introduced": len(propagated), "detected": len(known_depths),
                            "unavailable_trials": sum(t["status"] == "harness_error" for t in trials),
                            "missed": sum(e["status"] == "missed" for e in propagated),
                            "censored": sum(e["status"] == "censored" for e in propagated),
                            "detected_depth": _statistics(known_depths)},
            "metrics": {key: _statistics([t["metrics"].get(key) for t in trials]) for key in (
                "provider_calls", "wall_seconds", "api_equivalent_cost_usd", "repair_closure_size",
                "late_revisions", "discarded_operations", "false_objections", "false_hard_blocks",
                "unsupported_hard_block_requests")},
            "tokens": {key: _statistics([token_sum(t, key) for t in trials]) for key in token_keys},
            "successful_candidate_diversity": len({t["final_content_digest"] for t in trials if t["complete"]}),
        }
    return result


def run_batch(repository, batch: str, *, real: bool, repeats: int = 3, parallel_blocks: int = 1,
              budget: Budget = Budget(), scenario="wrong_entry"):
    if repeats < 1 or not 1 <= parallel_blocks <= 3:
        raise ValueError("repeats positive; at most three independent repetition blocks")
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "labs/checkpoint_critique"], cwd=root, text=True).strip())
    provider = ClaudeProvider() if real else ScriptedProvider(scenario)
    order = [list(ARMS)[r % 4:] + list(ARMS)[:r % 4] for r in range(repeats)]
    master = RetainedTrial(repository, f"{batch}-plan")
    plan = {"batch": batch, "kind": "real_provider" if real else "deterministic_control",
            "repetitions": repeats, "schedule": order, "parallel_blocks": parallel_blocks,
            "budget_per_trial": asdict(budget), "provider": provider.describe(),
            "git_revision": revision, "lab_dirty": dirty, "started_utc": datetime.now(timezone.utc).isoformat(),
            "seed_control": "unsupported for Claude CLI; repetition index is not an RNG seed",
            "primary_endpoint": "independent fixture completion under equal total ceilings",
            "secondary_endpoints": ["review-detected root error propagation", "actual downstream values repaired",
                                    "all-call tokens, equivalent cost and wall time", "false objections and failures"],
            "excluded": "none: every scheduled trial remains in denominators; no success selection",
            "interpretation": "small fixed-fixture pilot; not evidence of architectural quality or general superiority"}
    plan_ref = master.artifact("preregistered-plan", plan)  # before the first scheduled model call

    def block(rep):
        rows = []
        for arm in order[rep]:
            current = ClaudeProvider() if real else ScriptedProvider(scenario)
            trial_id = f"{batch}-r{rep + 1:02d}-{arm.lower()}"
            try:
                row = run_trial(repository, trial_id, arm, current, budget, repetition=rep + 1)
            except Exception as exc:
                # Keep the scheduled denominator and all already-written call artifacts;
                # never retry a potentially paid invocation to manufacture a complete row.
                failure = RetainedTrial(repository, trial_id)
                row = {"trial": trial_id, "arm": arm, "repetition": rep + 1, "status": "harness_error",
                       "complete": False, "run": failure.run.to_dict(), "error_category": type(exc).__name__,
                       "partial_data": "Already-written immutable artifacts remain under this exact run; metrics unavailable.",
                       "calls": [], "metrics": {}, "propagation": [], "repairs": [],
                       "final_assessment": {"complete": False, "unresolved": ["harness_failure"], "unavailable": ["final_assessment"]}}
                row["report_ref"] = failure.artifact("harness-failure", row)
            rows.append(row)
            print(json.dumps({"trial": row["trial"], "status": row["status"],
                              "calls": None if row["status"] == "harness_error" else len(row["calls"]),
                              "cost": row["metrics"].get("api_equivalent_cost_usd")}), flush=True)
        return rows

    with ThreadPoolExecutor(max_workers=parallel_blocks) as pool:
        reports = [row for rows in pool.map(block, range(repeats)) for row in rows]
    replayed = [replay(FilesystemProjectRepository.open(repository.layout.root), report["report_ref"])
                for report in reports if report["status"] != "harness_error"]
    result = {"plan_ref": plan_ref, "trials": [r["report_ref"] for r in reports],
              "summary": summarize(reports), "replayed_trials": len(replayed),
              "completed_utc": datetime.now(timezone.utc).isoformat()}
    result_ref = master.artifact("batch-result", result)
    return {**result, "result_ref": result_ref}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="explicit synthetic project/probe root, named checkpoint-critique")
    parser.add_argument("--batch", required=True, help="unique immutable batch name")
    parser.add_argument("--real", action="store_true", help="make real paid/subscription Claude calls with synthetic inputs only")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--parallel-blocks", type=int, default=1)
    parser.add_argument("--scenario", default="wrong_entry")
    args = parser.parse_args()
    if args.project.name != "checkpoint-critique":
        parser.error("project root name must be checkpoint-critique to isolate this synthetic experiment")
    repository = (FilesystemProjectRepository.open(args.project) if (args.project / "project.json").exists()
                  else FilesystemProjectRepository.initialize(args.project, project_id="checkpoint-critique", initial_state={}))
    result = run_batch(repository, args.batch, real=args.real, repeats=args.repeats,
                       parallel_blocks=args.parallel_blocks, scenario=args.scenario)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
