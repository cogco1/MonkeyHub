"""X9, DD-agent: a model edits a native 3DM's own numbers in one call; the patcher writes what it says.

For each task of ``x1_tasks.json`` the model gets the request (``utterance``), the native file's object table (each
object's name, layer, stored primitive and other user text) and the embedded state (``archflow:state``), with the
system prompt ``prompts/x9_system.md``; it answers a verdict and edits to the file's data: object fields and the embedded
state's parameters. The fairness controls and the
cost ledger are ``x2``'s. ``native3dm.apply_edits`` applies the edits to the start file exactly as given (an edit it
cannot apply refuses the whole answer); the result is meshed by the harness (``native3dm.mesh_model``) and judged by
the oracle with the task's file, against the start's OCCT preview, and compared object by object with REGEN, the
rebuild of the gold change.

Scores: the verdict against the gold, strict and in the four classes of X2's main metric (held | blocked = violated or
refuse | unknown | flag_rejected); whether the edits applied; the oracle's verdict; agreement with REGEN; objects the
agent changed that REGEN leaves alone (unrelated) and objects REGEN changes that the agent left alone (missed); whether
the edited file is self-consistent (its embedded state compiles to exactly the solids it holds; else ``STATE_STALE``);
a false accept is ``held`` where the gold is not held or the oracle finds a violation.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labs.revision_tasks import native3dm, x1, x2, x9
from labs.revision_tasks import run as runner

SYSTEM = x2.PROMPTS / "x9_system.md"
CLASSES = {"held": "held", "violated": "blocked", "refuse": "blocked", "unknown": "unknown", "flag_rejected": "flag_rejected"}


def object_table(native_path: Path) -> tuple[list[dict[str, Any]], str]:
    """The file's objects as the model sees them, and the embedded state string."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(native_path))
    layers = {i: model.Layers[i].FullPath for i in range(len(model.Layers))}
    rows = []
    for obj in model.Objects:
        attributes = obj.Attributes
        text = {str(k): str(v) for k, v in attributes.GetUserStrings()}
        primitive = json.loads(text.pop("archflow:primitive")) if "archflow:primitive" in text else None
        if primitive and primitive.get("kind") == "mesh":
            primitive = {k: v for k, v in primitive.items() if k not in ("vertices", "triangles")}
        rows.append({"name": attributes.Name, "layer": layers.get(attributes.LayerIndex), "visible": bool(attributes.Visible),
                     "primitive": primitive, "user_text": dict(sorted(text.items()))})
    rows.sort(key=lambda r: r["name"] or "")
    strings = native3dm.strings_of(model)
    return rows, strings.get("archflow:state", "")


def prompt_for(utterance: str, native_path: Path) -> str:
    rows, state = object_table(native_path)
    template = (x2.PROMPTS / "x9_request.md").read_text(encoding="utf-8")
    return (template.replace("{utterance}", utterance)
            .replace("{objects}", "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows))
            .replace("{state}", state))


def changed_objects(after: dict[str, dict[str, Any]], before: dict[str, dict[str, Any]]) -> set[str]:
    """Objects whose box or volume differ (or that appear or vanish), at 1e-6."""

    out = set(after) ^ set(before)
    for name in set(after) & set(before):
        a, b = after[name], before[name]
        if any(abs(x - y) > 1e-6 for side in ("min", "max") for x, y in zip(a[side], b[side])) or \
                abs(a["volume"] - b["volume"]) > 1e-6 * max(1.0, abs(b["volume"])):
            out.add(name)
    return out


def shapes(numbers: dict[str, dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The model's solids without their names: each box and volume, rounded to 1e-5, sorted."""

    return sorted((tuple(round(v, 5) for v in n["min"]), tuple(round(v, 5) for v in n["max"]), round(n["volume"], 5)) for n in numbers.values())


def _state_edits(record, edits: list[dict[str, Any]]):
    """The answer's state edits applied to the file's record: inputs set, derived values checked (as X2's rule 7.2)."""

    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator, evaluate_parameters

    keys = {p.key for p in record.parameters}
    derived = {p.key for p in record.parameters if p.expr}
    asked = {}
    for edit in edits:
        key, value = str(edit.get("state", "")).removeprefix("parameter:"), edit.get("value")
        if key not in keys or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise native3dm.NativeError(f"state edit {key!r}: not a parameter of the embedded state with a number")
        asked[key] = float(value)
    for key, value in asked.items():
        if key not in derived:
            record = apply_state_record_operator(record, StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                target_ref=f"parameter:{key}", key=key, value=value))
    evaluated = evaluate_parameters(record)
    conflicts = sorted(k for k, v in asked.items() if k in derived and abs(float(evaluated[k]) - v) > 1e-6)
    if conflicts:
        raise native3dm.NativeError(f"DERIVED_CONFLICT: {', '.join(conflicts)}")
    return record


def execute(answer: dict[str, Any], task: dict[str, Any], start: dict[str, Any], gold_regen: dict[str, Any] | None, trial: Path) -> dict[str, Any]:
    """Apply the answer's object and state edits to the start file as given; then judge, compare and check the file is consistent.

    The file is consistent when the edited embedded state compiles to exactly the solids the file holds (every object's
    box and volume, under the same names); it is then sealed again (state and binding). Otherwise it is written with its
    old binding and recorded ``STATE_STALE``.
    """

    from labs.revision_tasks import fixtures, pipeline

    edits = answer.get("edits") or []
    if answer.get("verdict") not in ("held", "violated") or not edits:
        return {"applied": False, "why": "no edits", "oracle_verdict": "unavailable"}
    read = native3dm.read_native(start["native"])
    model = read.model
    object_edits = [e for e in edits if isinstance(e, dict) and "object" in e]
    state_edits = [e for e in edits if isinstance(e, dict) and "state" in e]
    try:
        applied = native3dm.apply_edits(model, object_edits)
        record = _state_edits(fixtures.bound(read.record), state_edits)
    except Exception as exc:  # noqa: BLE001 - any edit the patcher cannot apply refuses the answer as given
        return {"applied": False, "why": f"EDIT_REFUSED: {type(exc).__name__}: {exc}"[:300], "oracle_verdict": "unavailable"}
    candidate = trial.parent / f"{trial.name}.native.3dm"
    if not model.Write(str(candidate), 8):
        return {"applied": False, "why": "the patched file could not be written", "oracle_verdict": "unavailable"}
    file_numbers = native3dm.geometry_boxes(candidate)
    try:
        program, _, _ = pipeline.compile_record(record)
        expected = {name: native3dm.shape_numbers(p) for name, p in native3dm.primitives(program).items()}
        consistency = native3dm.compare_numbers(file_numbers, expected)
        consistent = consistency["agree"]
    except pipeline.RealizationError as exc:
        program, consistent, consistency = None, False, {"error": f"{exc.code}: {exc.detail}"[:300]}
    if consistent:
        native3dm.seal(model, record, program, parent_sha256=read.file_sha256)
        if not model.Write(str(candidate), 8):
            return {"applied": False, "why": "the sealed file could not be written", "oracle_verdict": "unavailable"}
    meshed = trial.parent / f"{trial.name}.mesh.3dm"
    numbers = native3dm.mesh_model(candidate, meshed)
    start_numbers = native3dm.readback_numbers(start["realization"])
    agent_changed = changed_objects(numbers, start_numbers)
    result: dict[str, Any] = {"applied": True, "applied_edits": applied, "state_edits": len(state_edits), "agent_changed": sorted(agent_changed),
                              "self_consistent": consistent, "state": "consistent" if consistent else "STATE_STALE",
                              "consistency": {k: consistency.get(k) for k in ("missing", "extra", "max_box_dev_m", "max_volume_rel_dev", "error")
                                              if k in consistency}}
    if gold_regen is not None:
        regen_numbers = native3dm.readback_numbers(gold_regen["realization"])
        regen_changed = changed_objects(regen_numbers, start_numbers)
        agreement = native3dm.compare_numbers(numbers, regen_numbers)
        # the same solids under other names (an array the agent shortened by deleting its middle, not its end) is a naming
        # difference, scored apart from a wrong shape
        result.update({"agrees_regen": agreement["agree"], "agrees_regen_unnamed": shapes(numbers) == shapes(regen_numbers),
                       "unrelated": sorted(agent_changed - regen_changed),
                       "missed": sorted(regen_changed - agent_changed)})
    spec_path, why = x9.oracle_spec(task["task_id"], start["record"])
    if spec_path is None:
        result.update({"oracle_verdict": "unavailable", "oracle_why": why})
    else:
        report = runner.run_oracle(spec_path, meshed, start["realization"].preview_path, trial.parent / f"{trial.name}.oracle.json")
        result.update({"oracle_verdict": report.get("verdict", "unchecked"),
                       "oracle_violated": [r.get("id") for r in report.get("relations", []) if r.get("status") == "violated"]})
    return result

def score(answer: dict[str, Any] | None, task: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    verdict = (answer or {}).get("verdict")
    gold = task["gold"]["verdicts"]
    return {"parsed": answer is not None, "verdict": verdict,
            "verdict_ok": verdict in x2.VERDICTS and x1._ok(verdict, task),
            "verdict_ok_four": verdict in CLASSES and CLASSES[verdict] in {CLASSES[g] for g in gold if g in CLASSES},
            "false_accept": verdict == "held" and ("held" not in gold or execution.get("oracle_verdict") == "violated"),
            "edits_applied": bool(execution.get("applied")), "oracle_verdict": execution.get("oracle_verdict"),
            "agrees_regen": execution.get("agrees_regen"), "agrees_regen_unnamed": execution.get("agrees_regen_unnamed"),
            "self_consistent": execution.get("self_consistent"), "unrelated": len(execution.get("unrelated", []) or []),
            "missed": len(execution.get("missed", []) or [])}


def run(out: Path, *, families: tuple[str, ...], task_ids: tuple[str, ...] | None, repeats: int, part: str, ledger: x2.Ledger | None,
        cli: str | None) -> dict[str, Any]:
    tasks = [t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]
             if t["family"] in families and (task_ids is None or t["task_id"] in task_ids)]
    config = out / "config"
    x2.write_config(config)
    (out / "identity.json").write_text(json.dumps({"harness": runner.harness_identity(), "prompt_files_sha256": x2.prompt_files_sha256(),
                                                   "model": x2.MODEL, "command": x2.command("claude", config, system=SYSTEM)[1:]},
                                                  indent=1, sort_keys=True) + "\n", encoding="utf-8")
    starts = {family: x9.family_start(family, out) for family in families}
    rows: list[dict[str, Any]] = []
    stopped = None
    for task in tasks:
        start = starts[task["family"]]
        values, _ = x9.requested_values(task, start["record"])
        changed = x9.changed_values(values, start["bound"]) if values else None
        executable = changed and any(v in ("held", "violated") for v in task["gold"]["verdicts"])
        gold_regen = x9.regen(start["bound"], changed, out / "regen" / task["task_id"]) if executable else None
        prompt = prompt_for(task["utterance"], start["native"])
        for repeat in range(repeats):
            trial_id = f"X9-DDagent-{task['task_id']}-r{repeat}"
            trial = out / "trials" / trial_id
            row = {"trial_id": trial_id, "task_id": task["task_id"], "repeat_idx": repeat, "gold_verdicts": "|".join(task["gold"]["verdicts"]),
                   "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_chars": len(prompt)}
            (out / "trials").mkdir(exist_ok=True)
            (trial.parent / f"{trial_id}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
            if cli is None:
                rows.append(row)
                continue
            try:
                ledger.check(part)
            except x2.CapReached as exc:
                stopped = str(exc)
                break
            call = x2.call_model(prompt, trial, cli=cli, config=config, system=SYSTEM)
            cost, priced = x2.price(call["result"])
            ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X9", "part": part, "trial_id": trial_id,
                        "out": str(out), "cost_usd": cost, **priced})
            answer = x2.parse_answer(call["text"])
            execution = execute(answer, task, start, gold_regen, trial) if answer else {"applied": False, "oracle_verdict": "unavailable"}
            scored = score(answer, task, execution)
            (trial.parent / f"{trial_id}.bundle.json").write_text(json.dumps(
                {"trial": row, "task": task, "call": {k: call[k] for k in ("exit_code", "seconds", "init", "text", "stderr")}, "usage": priced,
                 "cost_usd": cost, "answer": answer, "execution": execution, "score": scored}, indent=1, sort_keys=True, default=str) + "\n",
                encoding="utf-8")
            row.update({**scored, "cost_usd": round(cost, 4), "input_tokens": priced["tokens"]["input_tokens"],
                        "output_tokens": priced["tokens"]["output_tokens"], "seconds": round(call["seconds"], 1)})
            rows.append(row)
            print(f"{trial_id:26} {str(scored['verdict']):13} ok={scored['verdict_ok_four']!s:5} applied={scored['edits_applied']!s:5} "
                  f"oracle={str(scored['oracle_verdict']):11} regen={scored['agrees_regen']!s:5} ${cost:.3f}", flush=True)
        if stopped:
            break
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (out / "x9_agent.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, restval="")
            writer.writeheader()
            writer.writerows(rows)
    judged = [r for r in rows if "verdict_ok" in r]
    summary = {"stopped": stopped, "trials": len(judged), "verdict_ok": sum(r["verdict_ok"] for r in judged),
               "verdict_ok_four": sum(r["verdict_ok_four"] for r in judged), "false_accept": sum(r["false_accept"] for r in judged),
               "edits_applied": sum(r["edits_applied"] for r in judged), "agrees_regen": sum(bool(r["agrees_regen"]) for r in judged),
               "agrees_regen_unnamed": sum(bool(r["agrees_regen_unnamed"]) for r in judged),
               "self_consistent": sum(bool(r["self_consistent"]) for r in judged),
               "cost_usd": round(sum(r["cost_usd"] for r in judged), 4),
               "spent_part_usd": ledger.spent(part) if ledger else 0.0, "spent_total_usd": ledger.spent() if ledger else 0.0}
    (out / "x9_agent_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DD-agent (a model edits the 3DM's numbers) into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1")
    parser.add_argument("--tasks", help="comma-separated task ids (default: every task of the families)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--part", default="pilot", choices=sorted(x2.CAPS))
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--cli", help="the claude executable; without it, only the prompts are written")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    if args.cli and not args.ledger:
        parser.error("--ledger is required with --cli")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, families=tuple(args.families.split(",")), task_ids=tuple(args.tasks.split(",")) if args.tasks else None,
                  repeats=args.repeats, part=args.part, ledger=x2.Ledger(args.ledger.resolve()) if args.ledger else None, cli=args.cli)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
