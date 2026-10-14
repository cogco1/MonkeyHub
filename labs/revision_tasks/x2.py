"""X2: a model decides a revision from one kind of material, in one call.

Every arm gets the same request, the same system prompt (``prompts/system_common.md``), the same
request template (``prompts/request.md``) and the same answer schema; only the materials differ.

| arm | materials |
|---|---|
| A | the history events (``history/events_v1.json``), written out as text |
| B | a readback of the model with its metadata stripped: object names and boxes |
| C | the C-task state (task rows of the record and the ``ContextPack@1``) without obligations or bounding relations, as text |
| D | the C-task state, as text |
| F | the C-task state exactly as embedded in the 3DM (JSON) |

The C-task pack is read from a disposable project that holds the fixture as production does, with the history's
rejected proposals recorded as decisions (``x1.production_pack``). Text is written by one generic renderer
(``render``), the same for every text arm.

The answer's ``changes`` are mapped to the fixture's parameters (history names through
``x1.HISTORY_PARAMETERS``; B names no parameter), applied with ``apply_state_record_operator`` to the start,
realized, and judged by the oracle against the X4 task file of the same id when its start is the fixture's.

Each call is ``claude -p`` with the flags in ``command``: one turn, no tools, no MCP servers, only project and
local settings (the trial directory holds no settings and no CLAUDE.md), bundled skills and slash commands off,
no session saved. Claude Code's own system prompt cannot be removed; it is the same for every arm.

Cost: each call is priced with ``monkeymonitor.pricing.quote`` at the manifest's API-equivalent rates, and the
larger of that and the CLI's own ``total_cost_usd`` goes into a shared append-only ledger. A part's cap or the
total cap stops the run before the call that could cross it.

The multi-turn G arm (MonkeyHub's construction tools) is not here.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archflow.state.state_record import StateRecordEditKind, StateRecordError, StateRecordOperator, apply_state_record_operator, evaluate_parameters

from labs.revision_tasks import fixtures, pipeline, reviser, taskspec, x1
from labs.revision_tasks import run as runner
from labs.revision_tasks.attacks import _rewrite, _strings

LAB = Path(__file__).resolve().parent
PROMPTS = LAB / "prompts"
ARMS = ("A", "B", "C", "D", "F")
MODEL = "claude-opus-5-5"
# Manifest v3.2 section 4: input $4 and output $20 per million tokens; cache reads and writes at the provider's
# standard multiples of input (0.1, 1.25 for five minutes, 2 for one hour).
RATES = {"input": "4", "cached_input": "0.4", "cache_write_input": "5", "cache_write_1h_input": "8", "output": "20"}
CAPS = {"pilot": 30.0, "x2_single": 100.0, "x2_g": 75.0, "x3": 280.0, "x3_gh": 120.0, "x8_i3": 15.0}
TOTAL_CAP = 620.0
PER_CALL_USD = 1.0
VERDICTS = ("held", "violated", "refuse", "unknown", "flag_rejected")


class CapReached(RuntimeError):
    pass


# ---------------------------------------------------------------- materials

def _scalar(value: Any) -> str:
    if isinstance(value, str) and value and value.strip() == value and ":" not in value:
        return value
    return json.dumps(value, ensure_ascii=False)


def _flat(value: Any) -> bool:
    return isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value)


def _render(value: Any, indent: int) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key in value:
            item = value[key]
            if item is None or item == [] or item == {} or item == "":
                continue
            if _flat(item):
                lines.append(f"{pad}{key}: [{', '.join(_scalar(v) for v in item)}]")
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_render(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
    else:
        for item in value:
            if isinstance(item, (dict, list)):
                inner = _render(item, indent + 1)
                if inner:
                    lines.append(f"{pad}- {inner[0].strip()}")
                    lines.extend(inner[1:])
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return lines


def render(value: Any) -> str:
    """Nested data as indented text: keys in their stored order, empty values left out, scalars as written."""

    return "\n".join(_render(value, 0))


def without_guards(state: dict[str, Any]) -> dict[str, Any]:
    """The state without its obligations and the relations that bound, clear or fix, wherever the pack repeats them."""

    state = json.loads(json.dumps(state))
    record = state["record"]
    removed = {o["obligation_id"] for o in record.get("obligations", [])}
    removed |= {r["relation_id"] for r in record.get("relations", []) if (r.get("validator") or {}).get("check_kind") in x1.BOUNDING_CHECKS}
    record["obligations"] = []

    def keep(item: Any) -> bool:
        text = json.dumps(item, ensure_ascii=False)
        return not any(f'"{i}"' in text or f":{i}\"" in text for i in removed)

    def prune(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: prune(v) for k, v in node.items()}
        if isinstance(node, list):
            return [prune(v) for v in node if keep(v)]
        return node

    return prune(state)


def readback(path: Path) -> list[dict[str, Any]]:
    """Each named mesh object's box, from the 3DM (the harness's reading, not the oracle's)."""

    import rhino3dm

    rows = []
    for obj in rhino3dm.File3dm.Read(str(path)).Objects:
        mesh, name = obj.Geometry, obj.Attributes.Name
        if not isinstance(mesh, rhino3dm.Mesh) or not name:
            continue
        points = [(mesh.Vertices[i].X, mesh.Vertices[i].Y, mesh.Vertices[i].Z) for i in range(len(mesh.Vertices))]
        rows.append({"name": name, "min": [round(min(p[i] for p in points), 3) for i in range(3)],
                     "max": [round(max(p[i] for p in points), 3) for i in range(3)]})
    return sorted(rows, key=lambda r: r["name"])


def family_inputs(family: str, root: Path) -> dict[str, Any]:
    """The materials of every arm for one family, and the start model the answers are executed against."""

    record = x1.FAMILY_FIXTURE[family]()
    fam = root / "inputs" / family
    fam.mkdir(parents=True)
    pack_read = x1.production_pack(family, record, fam / "production")
    full = reviser.write_state_file(record, arm="C-full", fmt="3dm", out_dir=fam, stem="c-full", parent_sha256=None,
                                    context_root=fam / "ctx-full", utterance="Current design state.")
    task = reviser.write_state_file(record, arm="C-task", fmt="3dm", out_dir=fam, stem="c-task", parent_sha256=None,
                                    context_root=fam / "ctx-task", utterance="Current design state.", context_pack=pack_read["pack"])
    embedded = _strings(task.path)["archflow:state"]
    state = json.loads(embedded)
    stripped = fam / "geometry-only.3dm"
    _rewrite(full.path, stripped, strings={k: None for k in _strings(full.path) if k.startswith("archflow:")})
    events = json.loads(x1.HISTORY.read_text(encoding="utf-8"))["families"][family]["events"]
    materials = {"A": render({"events": events}), "B": render({"objects": readback(stripped)}),
                 "C": render(without_guards(state)), "D": render(state), "F": embedded}
    for arm, text in materials.items():
        (fam / f"materials-{arm}.txt").write_text(text, encoding="utf-8", newline="\n")
    return {"record": record, "materials": materials, "start_model": full.realization.preview_path,
            "objects_by_element": full.realization.objects_by_element}


def prompt_for(arm: str, utterance: str, materials: str) -> str:
    intros = json.loads((PROMPTS / "materials.json").read_text(encoding="utf-8"))
    template = (PROMPTS / "request.md").read_text(encoding="utf-8")
    return template.replace("{utterance}", utterance).replace("{intro}", intros[arm]).replace("{materials}", materials)


def prompt_files_sha256() -> dict[str, str]:
    return {p.name: taskspec.file_sha256(p) for p in sorted(PROMPTS.iterdir()) if p.is_file()}


# ---------------------------------------------------------------- one call

def command(cli: str, config: Path, max_turns: int = 1, tools: str = "") -> list[str]:
    return [cli, "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
            "--setting-sources", "project,local", "--disable-slash-commands", "--max-turns", str(max_turns), "--tools", tools,
            "--disallowedTools", "WebSearch,WebFetch", "--strict-mcp-config", "--mcp-config", str(config / "mcp.json"),
            "--append-system-prompt-file", str(PROMPTS / "system_common.md"), "--settings", str(config / "settings.json"),
            "--no-session-persistence", "--max-budget-usd", str(PER_CALL_USD)]


def write_config(config: Path) -> None:
    config.mkdir(parents=True, exist_ok=True)
    (config / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({"disableBundledSkills": True}), encoding="utf-8")


def price(result: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """API-equivalent cost of one call: MonkeyMonitor's quote at the manifest rates, or the CLI's own if larger."""

    from monkeymonitor.pricing import RateCard, quote
    from monkeymonitor.usage import TokenUsage

    usage = result.get("usage") or {}
    plain, write, read = (int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    hour = int((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
    tokens = TokenUsage(input_tokens=plain + write + read, cached_input_tokens=read, cache_write_input_tokens=write,
                        cache_write_1h_input_tokens=hour, output_tokens=int(usage.get("output_tokens") or 0))
    quoted = quote(tokens, RateCard(provider="claude", model=MODEL, label="manifest v3.2 section 4 API equivalent", **RATES))
    monitor = float(quoted["amount_usd"] or quoted["known_subtotal_usd"])
    cli = float(result.get("total_cost_usd") or 0.0)
    return max(monitor, cli), {"tokens": tokens.to_dict(), "monkeymonitor_usd": monitor, "cli_total_cost_usd": cli}


class Ledger:
    """Append-only JSON lines shared by every run that spends from the same caps."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def spent(self, part: str | None = None) -> float:
        if not self.path.exists():
            return 0.0
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return sum(r["cost_usd"] for r in rows if part is None or r["part"] == part)

    def check(self, part: str) -> None:
        if self.spent(part) + PER_CALL_USD > CAPS[part]:
            raise CapReached(f"part {part}: {self.spent(part):.2f} of {CAPS[part]:.2f} spent")
        if self.spent() + PER_CALL_USD > TOTAL_CAP:
            raise CapReached(f"total: {self.spent():.2f} of {TOTAL_CAP:.2f} spent")

    def add(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def call_model(prompt: str, trial: Path, *, cli: str, config: Path) -> dict[str, Any]:
    trial.mkdir(parents=True)
    started = time.perf_counter()
    done = subprocess.run(command(cli, config), input=prompt, cwd=trial, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900)
    seconds = time.perf_counter() - started
    (trial.parent / f"{trial.name}.stream.jsonl").write_text(done.stdout, encoding="utf-8", newline="\n")
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    return {"exit_code": done.returncode, "stderr": done.stderr[-2000:], "seconds": seconds, "result": result,
            "text": result.get("result") or "", "init": {k: init.get(k) for k in ("model", "tools", "mcp_servers", "permissionMode",
                                                                                     "slash_commands", "skills", "plugins", "claude_code_version")}}


def parse_answer(text: str) -> dict[str, Any] | None:
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        answer = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


# ---------------------------------------------------------------- execution and score

def map_changes(changes: dict[str, Any], arm: str, family: str, record) -> tuple[dict[str, float] | None, str | None]:
    evaluated = evaluate_parameters(record)
    values = {p.key: float(evaluated[p.key]) for p in record.parameters}
    history = x1.HISTORY_PARAMETERS[family]
    mapped: dict[str, float] = {}
    for name, value in changes.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None, f"NOT_A_NUMBER: {name}"
        if arm == "A" and name in history:
            key, convert = history[name]
            mapped[key] = float(convert(value, values))
        elif arm != "B" and name.removeprefix("parameter:") in values:
            mapped[name.removeprefix("parameter:")] = float(value)
        else:
            return None, f"UNKNOWN_PARAMETER: {name}"
    return mapped, None


def execute(answer: dict[str, Any], arm: str, family: str, task: dict[str, Any], inputs: dict[str, Any], trial: Path) -> dict[str, Any]:
    changes = answer.get("changes")
    if answer.get("verdict") not in ("held", "violated") or not isinstance(changes, dict) or not changes:
        return {"executed": False, "why": "no changes to execute", "oracle_verdict": "unavailable"}
    mapped, error = map_changes(changes, arm, family, inputs["record"])
    if mapped is None:
        return {"executed": False, "why": error, "oracle_verdict": "unavailable"}
    record = fixtures.bound(inputs["record"])
    try:
        for key, value in mapped.items():
            record = apply_state_record_operator(record, StateRecordOperator(
                kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
                target_ref=f"parameter:{key}", key=key, value=value))
        work = trial.parent / f"{trial.name}.candidate"
        work.mkdir()
        candidate = pipeline.realize(record, work.resolve(), "candidate")
    except (StateRecordError, pipeline.RealizationError) as exc:
        return {"executed": False, "why": f"{type(exc).__name__}: {exc}"[:300], "mapped": mapped, "oracle_verdict": "unavailable"}
    spec_path = LAB / "tasks" / f"{task['task_id']}-01.json"
    if not spec_path.exists():
        return {"executed": True, "mapped": mapped, "oracle_verdict": "unavailable", "why": "no oracle task"}
    spec = taskspec.load(spec_path)
    if taskspec.start_record(spec).digest != inputs["record"].digest:
        return {"executed": True, "mapped": mapped, "oracle_verdict": "unavailable", "why": "the oracle task starts elsewhere"}
    report = runner.run_oracle(spec_path, candidate.preview_path, inputs["start_model"], trial.parent / f"{trial.name}.oracle.json")
    return {"executed": True, "mapped": mapped, "oracle_verdict": report.get("verdict", "unchecked"),
            "oracle_violated": [r.get("relation_id") for r in report.get("relations", []) if r.get("status") == "violated"]}


def _element_ids(names: Any, inputs: dict[str, Any]) -> list[str]:
    if not isinstance(names, list):
        return []
    owner = runner.element_of(inputs["objects_by_element"])
    elements = set(inputs["objects_by_element"])
    out = set()
    for name in names:
        name = str(name).removeprefix("entity:")
        out.add(name if name in elements else owner.get(name, owner.get(name.removeprefix("obj-"), name.removeprefix("obj-"))))
    return sorted(out)


def score(answer: dict[str, Any] | None, task: dict[str, Any], family: str, execution: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    verdict = (answer or {}).get("verdict")
    gold = task["gold"]
    affected = _element_ids((answer or {}).get("affected"), inputs)
    precision, recall = x1._set_scores(affected, gold["affected"])
    declared = x1.HISTORY_DECLARATIONS[family]
    conditions = {declared.get(str(c), str(c)) or str(c) for c in ((answer or {}).get("conditions") or []) if isinstance(c, str)}
    _, recheck_recall = x1._set_scores(sorted(conditions), gold["recheck"])
    return {"parsed": answer is not None, "verdict": verdict, "verdict_ok": verdict in VERDICTS and x1._ok(verdict, task),
            "affected": affected, "affected_precision": precision, "affected_recall": recall, "recheck_recall": recheck_recall,
            "false_accept": verdict == "held" and ("held" not in gold["verdicts"] or execution.get("oracle_verdict") == "violated"),
            "usable_successor": bool(execution.get("executed")) and execution.get("oracle_verdict") == "held"}


# ---------------------------------------------------------------- the run

def run(out: Path, *, families: tuple[str, ...], arms: tuple[str, ...], repeats: int, part: str, ledger: Ledger | None,
        cli: str | None, task_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
    tasks = [t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]
             if t["family"] in families and (task_ids is None or t["task_id"] in task_ids)]
    config = out / "config"
    write_config(config)
    identity = {"harness": runner.harness_identity(), "prompt_files_sha256": prompt_files_sha256(), "model": MODEL,
                "command": command("claude", config)[1:], "rates": RATES, "per_call_usd": PER_CALL_USD, "part": part}
    if cli:
        identity["cli_version"] = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
    (out / "identity.json").write_text(json.dumps(identity, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    inputs = {family: family_inputs(family, out) for family in families}
    rows: list[dict[str, Any]] = []
    stopped = None
    for index, task in enumerate(tasks):
        family = task["family"]
        for repeat in range(repeats):
            shift = (index + repeat) % len(arms)
            for slot, arm in enumerate(arms[shift:] + arms[:shift]):
                trial_id = f"X2-{arm}-{task['task_id']}-r{repeat}"
                prompt = prompt_for(arm, task["utterance"], inputs[family]["materials"][arm])
                trial = out / "trials" / trial_id
                row = {"trial_id": trial_id, "task_id": task["task_id"], "arm": arm, "repeat_idx": repeat, "order_slot": slot,
                       "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "prompt_chars": len(prompt)}
                if cli is None:
                    trial.parent.mkdir(parents=True, exist_ok=True)
                    (trial.parent / f"{trial_id}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
                    rows.append(row)
                    continue
                try:
                    ledger.check(part)
                except CapReached as exc:
                    stopped = str(exc)
                    break
                (out / "trials").mkdir(exist_ok=True)
                (trial.parent / f"{trial_id}.prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
                call = call_model(prompt, trial, cli=cli, config=config)
                cost, priced = price(call["result"])
                ledger.add({"at": datetime.now(timezone.utc).isoformat(), "experiment": "X2", "part": part, "trial_id": trial_id,
                            "out": str(out), "cost_usd": cost, **priced})
                answer = parse_answer(call["text"])
                execution = execute(answer, arm, family, task, inputs[family], trial) if answer else {"executed": False, "oracle_verdict": "unavailable"}
                scored = score(answer, task, family, execution, inputs[family])
                bundle = {"trial": row, "task": task, "call": {k: call[k] for k in ("exit_code", "seconds", "init", "text", "stderr")},
                          "usage": priced, "cost_usd": cost, "answer": answer, "execution": execution, "score": scored}
                (trial.parent / f"{trial_id}.bundle.json").write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
                row.update({**{k: scored[k] for k in ("parsed", "verdict", "verdict_ok", "affected_precision", "affected_recall",
                                                       "recheck_recall", "false_accept", "usable_successor")},
                            "executed": execution.get("executed"), "oracle_verdict": execution.get("oracle_verdict"),
                            "gold_verdicts": "|".join(task["gold"]["verdicts"]), "cost_usd": round(cost, 4),
                            "input_tokens": priced["tokens"]["input_tokens"], "output_tokens": priced["tokens"]["output_tokens"],
                            "seconds": round(call["seconds"], 1)})
                rows.append(row)
                print(f"{trial_id:22} {str(scored['verdict']):13} ok={scored['verdict_ok']!s:5} exec={execution.get('executed')!s:5} "
                      f"oracle={execution.get('oracle_verdict'):11} ${cost:.3f}", flush=True)
            if stopped:
                break
        if stopped:
            break
    if rows:
        fields = sorted({k for row in rows for k in row}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
        with (out / "x2.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    summary: dict[str, Any] = {"stopped": stopped, "spent_part_usd": ledger.spent(part) if ledger else 0.0,
                               "spent_total_usd": ledger.spent() if ledger else 0.0, "by_arm": {}}
    for row in rows:
        if "verdict_ok" not in row:
            continue
        cell = summary["by_arm"].setdefault(row["arm"], {"trials": 0, "verdict_ok": 0, "false_accept": 0, "usable_successor": 0,
                                                         "unparsed": 0, "cost_usd": 0.0, "input_tokens": []})
        cell["trials"] += 1
        cell["verdict_ok"] += int(row["verdict_ok"])
        cell["false_accept"] += int(row["false_accept"])
        cell["usable_successor"] += int(row["usable_successor"])
        cell["unparsed"] += int(not row["parsed"])
        cell["cost_usd"] = round(cell["cost_usd"] + row["cost_usd"], 4)
        cell["input_tokens"].append(row["input_tokens"])
    for cell in summary["by_arm"].values():
        tokens = sorted(cell.pop("input_tokens"))
        cell["median_input_tokens"] = tokens[len(tokens) // 2] if tokens else None
    (out / "x2_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run X2 single-call arms into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1")
    parser.add_argument("--tasks", help="comma-separated task ids (default: every task of the families)")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--part", default="pilot", choices=sorted(CAPS))
    parser.add_argument("--ledger", type=Path, help="the shared cost ledger (required with --cli)")
    parser.add_argument("--cli", help="the claude executable; without it, only the prompts are written")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    if args.cli and not args.ledger:
        parser.error("--ledger is required with --cli")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, families=tuple(args.families.split(",")), arms=tuple(args.arms.split(",")), repeats=args.repeats,
                  part=args.part, ledger=Ledger(args.ledger.resolve()) if args.ledger else None, cli=args.cli,
                  task_ids=tuple(args.tasks.split(",")) if args.tasks else None)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
