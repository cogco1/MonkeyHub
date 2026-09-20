"""Run the preregistered public A/B0/D0 pilot; outputs are diagnostic evidence.

All image and exact-query inputs come from the same Fixture retained STEP.
Gold is used only by scoring after the model finishes, never by the consumer.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from threading import Lock
from time import perf_counter

from .fixture import Fixture, VIEWS
from .provider import ClaudeConsumer, image_block


CONDITIONS = ("A", "B0", "D0")
ROUNDS = 3
MAX_QUERIES = 12
GEOMETRY_QUERY_LOCK = Lock()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def task_set(scale: float):
    """Fixed questions; no answers, measurements or condition-specific hints."""
    return [
        {"id": "identity", "question": "Give the physical object ID bound to the element labelled square marker A.",
         "value_type": "string"},
        {"id": "twin_gap", "question": "What is the minimum solid distance between twin-a and twin-b, in meters?",
         "value_type": "number"},
        {"id": "shell_gap", "question": "What is the minimum solid distance between u-shell and insert, in meters?",
         "value_type": "number"},
        {"id": "clash_overlap", "question": "What is the common solid volume of clash-a and clash-b, in cubic meters?",
         "value_type": "number"},
        {"id": "front_visibility", "question": "Does twin-a have any visible edge in the front orthographic view when ALL scene solids participate?",
         "value_type": "boolean"},
        {"id": "back_visibility", "question": "Does twin-a have any visible edge in the back orthographic view when ALL scene solids participate?",
         "value_type": "boolean"},
        {"id": "declared_impact", "question": "List screen and ALL of its transitive downstream declared dependents if screen changes. Do not infer undeclared design relations.",
         "value_type": "array of entity ID strings"},
        {"id": "unknown_role", "question": "What is the declared architectural role of mystery? If no role is declared, status must be unknown and value null.",
         "value_type": "string or null"},
        {"id": "contains", "question": f"Is CAD Z-up point {encoded([10 * scale, 2.5 * scale, .5 * scale])} meters inside the insert solid?",
         "value_type": "boolean"},
        {"id": "correction", "question": "A previous unverified answer said u-shell and insert overlap because their bounding boxes overlap. Verify or correct it: give their actual common solid volume in cubic meters.",
         "value_type": "number"},
        {"id": "stale", "question": "An old observation has the same revision but state_digest is 64 zero characters. Can it be used as a verified answer about this requested source? If stale, status stale and value null.",
         "value_type": "null"},
    ]


def gold(variant: str, scale: float):
    """Hand-computable audit declared before calls; cross-checked in fixture tests."""
    return {"identity": "obj-twin-a", "twin_gap": (4 if variant == "heldout" else 3) * scale,
            "shell_gap": (.25 if variant == "heldout" else .5) * scale,
            "clash_overlap": (.5 if variant == "heldout" else .25) * scale ** 3,
            "front_visibility": False, "back_visibility": True,
            "declared_impact": ["screen", "lintel", "seal", "fixing", "drain"],
            "unknown_role": None, "contains": True, "correction": 0.0, "stale": None}


def score(parsed: dict | None, expected: dict):
    answers = parsed.get("answers", []) if isinstance(parsed, dict) else []
    # Duplicate task answers are invalid, not last-writer-wins.
    by_id = {row.get("id"): row for row in answers if isinstance(row, dict) and isinstance(row.get("id"), str)} if isinstance(answers, list) else {}
    counts = {key: sum(isinstance(row, dict) and row.get("id") == key for row in answers)
              for key in expected} if isinstance(answers, list) else {}
    results = []
    for key, value in expected.items():
        row = by_id.get(key, {})
        observed, status = row.get("value"), row.get("status")
        expected_status = "unknown" if key == "unknown_role" else "stale" if key == "stale" else "known"
        correct = counts.get(key) == 1 and status == expected_status
        absolute_error = None
        if isinstance(value, bool):
            correct = correct and type(observed) is bool and observed == value
        elif isinstance(value, (int, float)):
            numeric = type(observed) in (int, float)
            absolute_error = abs(observed - value) if numeric else None
            correct = correct and numeric and absolute_error <= max(1e-6, abs(value) * 1e-6)
        elif isinstance(value, list):
            correct = (correct and isinstance(observed, list) and all(isinstance(v, str) for v in observed)
                       and len(observed) == len(value) and set(observed) == set(value))
        else:
            correct = correct and observed == value
        results.append({"id": key, "correct": bool(correct), "status": status, "value": observed,
                        "expected": value, "absolute_error": absolute_error,
                        "abstained": status == "unknown" and key != "unknown_role"})
    return results


QUERY_HELP = {
    "protocol": "Return queries as {action,args,source}. source must equal the intended binding. Results arrive next round.",
    "state": {"ids": "optional list of entity IDs; omitted returns entire declared snapshot"},
    "dependencies": {"ids": "list of entity IDs; includes transitive declared closure"},
    "pair": {"first": "entity ID", "second": "entity ID"},
    "point": {"id": "entity ID", "point": "three CAD Z-up coordinates in meters"},
    "shape": {"id": "entity ID"},
    "visibility": {"view": "front/back/left/right/top", "ids": "optional entity IDs; ALL solids occlude"},
}


def run_trial(consumer, fixture, views, *, condition, variant, scale, repetition, view_names):
    trial_id = f"{variant}-s{scale:g}-{'-'.join(view_names)}-{condition}-r{repetition}"
    before_head = fixture.repository.layout.head.read_bytes()
    tasks = task_set(scale)
    common = {"requested_source": fixture.source, "units": "meter", "coordinates": "CAD Z-up",
              "camera_convention": {"front": "look +Y, right +X, up +Z", "back": "look -Y, right -X, up +Z",
                  "left": "look +X, right -Y, up +Z", "right": "look -X, right +Y, up +Z",
                  "top": "look -Z, right +X, up +Y"},
              "representation": {"A": "structured JSON only", "B0": "orthographic line PNG only",
                                 "D0": "same orthographic line PNG plus on-demand exact queries"}[condition],
              "tasks": tasks, "rounds": ROUNDS,
              "answer_format": {"answers": [{"id": "task id", "status": "known|unknown|stale",
                                                "value": "requested type", "evidence": ["observation/query ref"]}],
                                "queries": []},
              "policy": "Return all provisional answers every round. Review them on later rounds. Abstain when unavailable. No gold feedback will be supplied.",
              "query_budget": MAX_QUERIES if condition == "D0" else 0}
    if condition == "A":
        common["observation"] = fixture.snapshot()
    if condition == "D0":
        common["allowed_queries"] = QUERY_HELP
    blocks = [{"type": "text", "text": encoded(common)}]
    image_bytes, packing_seconds = 0, 0.0
    if condition != "A":
        for name in view_names:
            view = views[name]
            blocks.append({"type": "text", "text": encoded({"view": name, "width": view["width"],
                          "height": view["height"], "representation": "orthographic-line-projection"})})
            start = perf_counter()
            blocks.append(image_block(view["png"]))
            packing_seconds += perf_counter() - start
            image_bytes += len(view["png"])
    calls, history, queries = [], [], []
    start = perf_counter()
    for round_number in range(ROUNDS):
        instruction = "Final round: answer all tasks; no more queries." if round_number == ROUNDS - 1 else "Give provisional answers and any permitted queries now."
        current = blocks + [{"type": "text", "text": encoded({"round": round_number,
                   "instruction": instruction, "previous_rounds": history,
                   "remaining_queries": max(0, MAX_QUERIES - len(queries)) if condition == "D0" else 0})}]
        call = consumer.call(current, call_id=f"{trial_id}-turn{round_number}")
        call["completed_after_seconds"] = perf_counter() - start
        calls.append(call)
        if (call["timeout"] or call["exit_code"] != 0 or call["parsed"] is None
                or call["provider_result"].get("is_error") or call["provider_result"].get("subtype") != "success"):
            break
        parsed = call["parsed"]
        requested = parsed.get("queries", [])
        returns = []
        if isinstance(requested, list) and round_number < ROUNDS - 1:
            for request in requested:
                if condition != "D0" or len(queries) >= MAX_QUERIES:
                    returns.append({"error": "query unavailable or budget exhausted"})
                    continue
                clock = perf_counter()
                try:
                    with GEOMETRY_QUERY_LOCK:
                        result = fixture.exact_query(request["action"], request.get("args", {}),
                                                     source=request["source"])
                except Exception as exc:
                    result = {"error": type(exc).__name__, "message": str(exc)}
                queries.append({"request": request, "response": result, "elapsed_seconds": perf_counter() - clock})
                returns.append(result)
        history.append({"answer": parsed, "query_results": returns})
    scores = [score(call["parsed"] if call["provider_result"].get("subtype") == "success"
                    and not call["provider_result"].get("is_error") and call["exit_code"] == 0 else None,
                    gold(variant, scale)) for call in calls]
    final = scores[-1] if scores else score(None, gold(variant, scale))
    first = scores[0] if scores else final
    head_unchanged = fixture.repository.layout.head.read_bytes() == before_head
    return {"trial_id": trial_id, "condition": condition, "variant": variant, "scale": scale,
            "repetition": repetition, "views": list(view_names), "source": fixture.source,
            "prompt": common, "image_bytes": image_bytes, "image_base64_seconds": packing_seconds,
            "structured_bytes": len(encoded(fixture.snapshot()).encode()) if condition == "A" else 0,
            "render_seconds": sum(views[name]["render_seconds"] for name in view_names) if condition != "A" else 0,
            "calls": calls, "queries": queries, "scores_by_round": scores, "final_scores": final,
            "correct": sum(row["correct"] for row in final), "denominator": len(final),
            "initial_wrong": sum(not row["correct"] for row in first),
            "corrected": sum(not a["correct"] and b["correct"] for a, b in zip(first, final)),
            "regressed": sum(a["correct"] and not b["correct"] for a, b in zip(first, final)),
            "head_unchanged": head_unchanged, "trial_seconds": perf_counter() - start,
            "api_equivalent_usd": sum(call["cli_api_equivalent_usd"] or 0 for call in calls),
            "api_equivalent_complete": all(call["cli_api_equivalent_usd"] is not None for call in calls),
            "actual_charge_usd": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="explicit external diagnostic directory")
    parser.add_argument("--claude", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-5[1m]")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("positive repetitions required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    consumer = ClaudeConsumer(args.claude, model=args.model, diagnostics=output / "diagnostics")
    configurations = [("base", 1.0)] if args.smoke else [("base", 1.0), ("heldout", 1.0), ("base", 2.0)]
    manifest = {"protocol_revision": "explicit-camera-v2", "started_at": datetime.now(timezone.utc).isoformat(), "model": args.model,
                "cli_version": consumer.version, "repetitions": args.repetitions, "seed": None,
                "seed_reason": "CLI exposes no sampling seed", "rounds": ROUNDS, "max_queries": MAX_QUERIES,
                "output_tokens_per_call": consumer.output_tokens, "timeout_per_call": consumer.timeout_seconds,
                "code_revision": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip(),
                "code_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")},
                "synthetic_only": True, "fixtures": [], "trials": []}
    (output / "manifest.json").write_text(encoded(manifest), encoding="utf-8")
    for variant, scale in configurations:
        label = f"{variant}-s{scale:g}"
        fixture = Fixture.create(output / "fixtures" / label, variant=variant, scale=scale)
        views = fixture.render_views()
        folder = output / label
        folder.mkdir(exist_ok=True)
        for name, row in views.items():
            (folder / f"{name}.png").write_bytes(row["png"])
        (folder / "snapshot.json").write_text(encoded(fixture.snapshot()), encoding="utf-8")
        manifest["fixtures"].append({"name": label, "source": fixture.source, "preprocessing": fixture.preprocessing_seconds,
            "views": {name: {key: val for key, val in row.items() if key != "png"} for name, row in views.items()}})
        jobs = [(rep, condition, VIEWS) for rep in range(args.repetitions) for condition in CONDITIONS]
        if not args.smoke and variant == "base" and scale == 1:
            jobs += [(rep, condition, subset) for subset in (("front",), ("top",))
                     for rep in range(args.repetitions) for condition in ("B0", "D0")]
        # Parallel isolated consumers; query/OCCT operations remain inside their
        # trial. All reads target the immutable retained fixture.
        def execute(job):
            rep, condition, names = job
            return run_trial(consumer, fixture, views, condition=condition, variant=variant,
                             scale=scale, repetition=rep, view_names=names)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for trial in pool.map(execute, jobs):
                (folder / f"{trial['trial_id']}.json").write_text(encoded(trial), encoding="utf-8")
                manifest["trials"].append({k: trial[k] for k in ("trial_id", "condition", "correct", "denominator",
                    "corrected", "regressed", "trial_seconds", "api_equivalent_usd", "api_equivalent_complete", "head_unchanged")})
                (output / "manifest.json").write_text(encoded(manifest), encoding="utf-8")
                print(encoded(manifest["trials"][-1]), flush=True)
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output / "manifest.json").write_text(encoded(manifest), encoding="utf-8")


if __name__ == "__main__":
    main()
