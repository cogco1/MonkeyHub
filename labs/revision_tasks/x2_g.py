"""X2 arm G: the same task worked through MonkeyHub's own tools, in one multi-turn ``claude -p`` turn.

A round is one family x one X2 task x one repeat:

1. a disposable production project of the family's fixture, made exactly as the C-task pack's project is
   (``x1.production_pack``: the stage run through the runner, the history's rejected proposals recorded as decisions);
2. a disposable Hub and Project Runtime on it (``x3_hub.isolated_hub``), the working design at the stage run;
3. the request: ``prompts/request.md`` with the task's utterance, the intro ``prompts/g_intro.md`` (the only words
   that differ from the other arms: G has tools, carries out held and violated changes, ends with the same JSON)
   and, as materials, the context MonkeyHub's own chat gives a turn (``turn_context._CONTEXT_NOTE`` and
   ``POST /api/intents/context`` for the working design: X3 mode M's injection, protocol 7.11);
4. one fresh ``claude -p`` turn (``x3_hub.run_turn``): the common system prompt ``prompts/system_common.md``, x2's
   fairness flags, MonkeyHub's tools through the lab proxy (20 calls), 600 s, its cost in the shared ledger;
5. the answer parsed as every arm's (``x2.parse_answer``); the model the turn left (``x3_hub.result_model``: the
   working design if it moved, else an admitted, named or latest candidate), judged by the oracle with the X4 task
   file of the same id, against the Hub's own start model; when MonkeyHub exported no model for that run, its
   retained state record realized by the lab (``x3_hub.lab_realization``) against the lab's start realization;
6. the score: x2's (verdict, affected and recheck, false accept, usable successor), manifest 7.2's four verdict
   categories, and 7.11's landing classes (``correct_not_landed``, ``would_complete_if_landed``).

The answer's ``changes`` are not executed by the harness: the agent made its change itself. A round counts as
executed, as in x2, when the answer says held or violated and the turn left a changed model; a model left under
another verdict is judged and reported apart (``left_model_oracle_verdict``). ``usable_successor`` counts the left
model whether it reached the working design or not, as x2 counts the harness's candidate; ``landing`` says which.

Order: G runs apart from x2's Latin square; rounds go repeat by repeat, and each row records its repeat index.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from archflow.state.state_record import evaluate_parameters

from labs.revision_tasks import fixtures, pipeline, taskspec, x1, x2, x3, x3_hub
from labs.revision_tasks import run as runner

LAB = Path(__file__).resolve().parent
PROMPTS = LAB / "prompts"
SYSTEM = PROMPTS / "system_common.md"
INTRO = PROMPTS / "g_intro.md"
ARM = "G"
PART = "x2_g"
TURN_BUDGET_USD = x3.TURN_BUDGET_USD      # a runaway guard; the binding limits are the 20 calls and 600 s
MAX_TURNS = x3.MAX_TURNS
TIMEOUT_S = x3.REQUEST_TIMEOUT_S
CATEGORY = {"held": "held", "violated": "blocked", "refuse": "blocked", "unknown": "unknown", "flag_rejected": "flag_rejected"}
HARNESS_ADDED = ("tool_call_cap_20", "history_rejections_recorded_as_decisions", "prepared_context_for_working_design")


# ---------------------------------------------------------------- the tasks and the project

def tasks(families: tuple[str, ...], task_ids: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """The X2 tasks of these families, as ``x2.run`` selects them."""

    return [t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]
            if t["family"] in families and (task_ids is None or t["task_id"] in task_ids)]


def family_project(family: str, projects: Path) -> Path:
    """The family's production project, made as the C-task pack's is (``x1.production_pack``): the arm-A history's
    rejections become recorded decisions, never prompt text. What the recording answered is kept beside it."""

    record = x1.FAMILY_FIXTURE[family]()
    read = x1.production_pack(family, record, projects)
    (projects / "production_pack.json").write_text(json.dumps({"family": family, "decisions": read["decisions"]}, indent=1) + "\n",
                                                   encoding="utf-8", newline="\n")
    return projects / record.project_id


def family_start(family: str, out: Path) -> dict[str, Any]:
    """The fixture, its lab realization (object ownership for scoring; the start a lab-realized result is judged
    against), its components' elements, and whether each X4 task file of the family starts from it."""

    record = x1.FAMILY_FIXTURE[family]()
    work = out / "inputs" / family
    work.mkdir(parents=True)
    realized = pipeline.realize(fixtures.bound(record), work.resolve(), "start")
    components: dict[str, list[str]] = {}
    for element in record.entities_of("Element@1"):
        if element.parent_id:
            components.setdefault(element.parent_id, []).append(element.entity_id)
    return {"record": record, "lab_start": realized.preview_path, "objects_by_element": realized.objects_by_element,
            "component_elements": components}


# ---------------------------------------------------------------- the request

class _HubView:
    """x3's Hub readers (``Session.head_model``) use nothing of a session but its services."""

    def __init__(self, services: x3_hub.HubServices) -> None:
        self.services = services


def prepared_context(services: x3_hub.HubServices, utterance: str, head: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """X3 mode M's injection (``x3.Session.prepared_prompt``) without the request in front: MonkeyHub chat's context
    note and the context read for the working design, as the Hub page names it."""

    from monkeyhub_api.chat import providers, turn_context

    project_id = json.loads((services.project_dir / "project.json").read_text(encoding="utf-8"))["project_id"]
    pack = x3_hub._call(services.runtime_url, "/api/intents/context", "POST", {"utterance": utterance, "projectId": project_id,
                                                                              "sourceRunId": head["run_id"], "stateDigest": head["state_digest"]})
    block = turn_context._CONTEXT_NOTE + "\n" + providers._redact(json.dumps(pack, ensure_ascii=False))
    return block, {"source_run": head["run_id"], "chars": len(block)}


def request_prompt(utterance: str, context: str) -> str:
    """The x2 request template with G's intro and the prepared context as its materials."""

    template = (PROMPTS / "request.md").read_text(encoding="utf-8")
    intro = INTRO.read_text(encoding="utf-8").strip()
    return template.replace("{utterance}", utterance).replace("{intro}", intro).replace("{materials}", context)


# ---------------------------------------------------------------- budget

def check_round(ledger: x2.Ledger, part: str, spent_this_run: float, limit_usd: float, budget_usd: float) -> None:
    """Refuse a round that could take the part, the total (``x3_hub.check_budget``, as x2 and x3) or this
    invocation's limit past its cap; a turn may spend its whole budget."""

    x3_hub.check_budget(ledger, part, budget_usd)
    if spent_this_run + budget_usd > limit_usd + 1e-9:
        raise x2.CapReached(f"this run's limit: {spent_this_run:.2f} of {limit_usd:.2f} spent, a round may spend {budget_usd:.2f}")


# ---------------------------------------------------------------- the result

def run_parameters(project_dir: Path, run_id: str) -> dict[str, float] | None:
    """A run's evaluated parameters, from its one retained state record (read through the P036 port, as
    ``x3_hub.lab_realization`` reads it); None when it has not exactly one."""

    from archflow.project.ports import PersistenceArea, PersistenceDestination
    from archflow.project.record_kinds import STATE_RECORD
    from archflow.project.repository import FilesystemProjectRepository
    from archflow.state.state_record import StateRecord

    repository = FilesystemProjectRepository.open(Path(project_dir))
    run = repository.load_run(run_id)
    refs = [ref for ref in repository.list_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id))
            if ref.record_kind == STATE_RECORD]
    if len(refs) != 1:
        return None
    return _values(StateRecord.from_dict(repository.load_json(refs[0])))


def _values(record) -> dict[str, float]:
    evaluated = evaluate_parameters(record)
    return {p.key: float(evaluated[p.key]) for p in record.parameters}


def parameter_changes(start: dict[str, float], result: dict[str, float] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    keys = sorted(set(start) | set(result))
    return {k: [start.get(k), result.get(k)] for k in keys if start.get(k) is None or result.get(k) is None
            or abs(start[k] - result[k]) > 1e-9}


def answer_mismatch(answer: dict[str, Any] | None, result: dict[str, float] | None) -> list[str] | None:
    """The answer's stated changes that the left model's parameters do not carry (a statement, not executed)."""

    changes = (answer or {}).get("changes")
    if result is None or not isinstance(changes, dict) or not changes:
        return None
    out = []
    for name, value in changes.items():
        key = str(name).removeprefix("parameter:")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or key not in result or abs(result[key] - float(value)) > 1e-6:
            out.append(key)
    return sorted(out)


def judge_left_model(result: dict[str, Any], task: dict[str, Any], start: dict[str, Any], head_path: Path, project_dir: Path,
                     round_dir: Path) -> dict[str, Any]:
    """The oracle on the model the turn left, with the X4 task file of the task's id (when it starts from the fixture)."""

    if result["how"] == "unchanged":
        return {"model": None, "oracle_verdict": "unavailable", "why": "no candidate and the working design unchanged"}
    model = result.get("model") or {}
    if model.get("path") and model.get("verified"):
        judged = {"path": model["path"], "sha256": model.get("sha256"), "source": "monkeyhub_export"}
        against = head_path
    else:
        lab = x3_hub.lab_realization(project_dir, result["run_id"], round_dir / "lab-model")
        judged = {"path": lab["path"], "sha256": lab["sha256"], "source": "lab_realization", "detail": lab["source"]}
        against = start["lab_start"]
    spec_path = LAB / "tasks" / f"{task['task_id']}-01.json"
    if not spec_path.exists():
        return {"model": judged, "oracle_verdict": "unavailable", "why": "no oracle task"}
    spec = taskspec.load(spec_path)
    if taskspec.start_record(spec).digest != start["record"].digest:
        return {"model": judged, "oracle_verdict": "unavailable", "why": "the oracle task starts elsewhere"}
    report = runner.run_oracle(spec_path, Path(judged["path"]), Path(against), round_dir / "oracle.json")
    return {"model": judged, "start_model": str(against), "oracle_verdict": report.get("verdict", "unchecked"),
            "oracle_violated": [r.get("id") for r in report.get("relations", []) if r.get("status") == "violated"]}


# ---------------------------------------------------------------- score

def category(verdict: Any) -> str | None:
    return CATEGORY.get(verdict) if isinstance(verdict, str) else None


def score(answer: dict[str, Any] | None, task: dict[str, Any], family: str, judged: dict[str, Any], how: str,
          start: dict[str, Any]) -> dict[str, Any]:
    """x2's score where its definitions apply, the four verdict categories (manifest 7.2) and the landing classes (7.11).

    ``affected`` may name MonkeyHub's geometry ids, which are the fixture's component ids; each is read as its
    elements before x2's mapping of element ids and object names.
    """

    verdict = (answer or {}).get("verdict")
    executed = verdict in ("held", "violated") and how != "unchanged" and judged.get("model") is not None
    execution = {"executed": executed, "oracle_verdict": judged["oracle_verdict"] if executed else "unavailable"}
    mapped = answer
    if answer is not None and isinstance(answer.get("affected"), list):
        names: list[Any] = []
        for name in answer["affected"]:
            names.extend(start["component_elements"].get(str(name).removeprefix("entity:"), [name]))
        mapped = {**answer, "affected": names}
    scored = x2.score(mapped, task, family, execution, {"objects_by_element": start["objects_by_element"]})
    gold = task["gold"]["verdicts"]
    landed = how == "working_design"
    candidate = how not in ("working_design", "unchanged")
    # A candidate left unlanded would complete the request if its answer says held, the gold holds and the oracle
    # agrees; None when nothing is left unlanded or the oracle cannot judge it.
    would = ((verdict == "held" and "held" in gold and judged["oracle_verdict"] == "held")
             if candidate and judged["oracle_verdict"] in ("held", "violated") else None)
    landing = ("landed" if landed else "correct_not_landed" if would else "candidate_not_landed" if candidate else "no_change")
    return {**scored, "verdict_category": category(verdict),
            "category_ok": verdict in CATEGORY and category(verdict) in {category(v) for v in gold},
            "executed": executed, "oracle_verdict": execution["oracle_verdict"],
            "left_model_oracle_verdict": judged["oracle_verdict"] if how != "unchanged" else None,
            "landing": landing, "landed": landed, "correct_not_landed": bool(would), "would_complete_if_landed": would}


# ---------------------------------------------------------------- one round

def run_round(task: dict[str, Any], repeat: int, start: dict[str, Any], out: Path, *, cli: str | None, ledger: x2.Ledger, part: str,
              settings_file: Path, budget_usd: float = TURN_BUDGET_USD,
              turn: Callable[..., dict[str, Any]] = x3_hub.run_turn) -> dict[str, Any]:
    """One G round in ``out/rounds/<task>-r<repeat>``; returns its bundle. ``turn`` is ``x3_hub.run_turn`` unless a
    scripted stand-in replaces the model (a dry run)."""

    family = task["family"]
    trial_id = f"X2-G-{task['task_id']}-r{repeat}"
    round_dir = out / "rounds" / f"{task['task_id']}-r{repeat}"
    round_dir.mkdir(parents=True)
    started = time.perf_counter()
    bundle: dict[str, Any] = {"trial": {"trial_id": trial_id, "task_id": task["task_id"], "arm": ARM, "family": family, "repeat_idx": repeat,
                                        "order_slot": None},
                              "task": task, "harness_added": list(HARNESS_ADDED)}
    with x3_hub.isolated_hub(round_dir / "hub", make_project=lambda projects: family_project(family, projects)) as services:
        bundle["decisions_recorded"] = json.loads((services.root / "projects" / "production_pack.json").read_text(encoding="utf-8"))["decisions"]
        head = x3.Session.head_model(_HubView(services))
        context, info = prepared_context(services, task["utterance"], head)
        prompt = request_prompt(task["utterance"], context)
        bundle["trial"].update(prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(), prompt_chars=len(prompt))
        bundle["prepared_context"] = info
        bundle["start"] = {"run_id": head["run_id"], "state_digest": head["state_digest"], "model": str(head["path"])}
        session = f"x2g-{uuid.uuid4()}"
        child = x3_hub.mcp_config(hub_url=services.hub_url, project_dir=services.project_dir, session_id=session)["mcpServers"][x3_hub.SERVER]
        proxy_dir = round_dir / "proxy"
        proxy_dir.mkdir()
        settings = x3_hub.proxy_settings(child, log=proxy_dir / "calls.jsonl", restricted=False)
        (proxy_dir / "proxy.json").write_text(json.dumps(settings, indent=1), encoding="utf-8", newline="\n")
        mcp = {"mcpServers": {x3_hub.SERVER: x3_hub.proxy_server_config(proxy_dir / "proxy.json")}}
        done = turn(task["utterance"], workdir=round_dir / "turn", cli=cli, ledger=ledger, part=part, hub_url=services.hub_url,
                    project_dir=services.project_dir, session_id=session, system_prompt_file=SYSTEM, max_turns=MAX_TURNS,
                    budget_usd=budget_usd, parent_env=services.parent_env, trial_id=trial_id, timeout=TIMEOUT_S, mcp=mcp,
                    allowed=x3_hub.ALLOWED, prompt=prompt, settings_file=settings_file, experiment="X2")
        final = done["summary"]["final_text"]
        answer = x2.parse_answer(final)
        result = x3_hub.result_model(services.runtime_url, services.project_dir, hub_url=services.hub_url, runtime_id=services.runtime_id,
                                     session_id=session, start_run=head["run_id"], answer_text=final)
        judged = judge_left_model(result, task, start, Path(head["path"]), services.project_dir, round_dir)
        values = None
        if result["how"] != "unchanged":
            try:
                values = run_parameters(services.project_dir, result["run_id"])
            except Exception as exc:  # noqa: BLE001 - the parameter read is a report, not the result
                bundle["parameter_read_error"] = f"{type(exc).__name__}: {exc}"[:500]
    if judged.get("model", {}) and judged["model"].get("source") == "lab_realization":
        bundle["harness_added"].append("lab_realization_of_unexported_result")
    if (done.get("hub_conversation") or {}).get("closed_by") == "harness":
        bundle["harness_added"].append("harness_closed_open_conversation_turn")
    start_values = _values(start["record"])
    scored = score(answer, task, family, judged, result["how"], start)
    tokens = done["usage"]["tokens"]
    scored.update(tool_calls=done["tool_call_count"], input_tokens=tokens["input_tokens"], output_tokens=tokens["output_tokens"],
                  cost_usd=round(done["cost_usd"], 4), seconds=done["seconds"], round_seconds=round(time.perf_counter() - started, 1),
                  answer_mismatch=answer_mismatch(answer, values))
    bundle.update(turn=done, answer=answer, result={k: result[k] for k in ("how", "run_id", "state_digest", "head_moved", "candidates", "admitted",
                                                                             "working_head")},
                  judged=judged, parameter_changes=parameter_changes(start_values, values), score=scored)
    (round_dir / "bundle.json").write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return bundle


def csv_row(bundle: dict[str, Any]) -> dict[str, Any]:
    s = bundle["score"]
    return {**{k: bundle["trial"][k] for k in ("trial_id", "task_id", "arm", "family", "repeat_idx", "order_slot", "prompt_sha256", "prompt_chars")},
            **{k: s[k] for k in ("parsed", "verdict", "verdict_ok", "verdict_category", "category_ok", "affected_precision", "affected_recall",
                                 "recheck_recall", "false_accept", "usable_successor", "executed", "oracle_verdict", "left_model_oracle_verdict",
                                 "landing", "correct_not_landed", "would_complete_if_landed", "tool_calls", "input_tokens", "output_tokens",
                                 "cost_usd", "seconds", "round_seconds")},
            "gold_verdicts": "|".join(bundle["task"]["gold"]["verdicts"]), "result_how": bundle["result"]["how"],
            "model_source": (bundle["judged"].get("model") or {}).get("source"),
            "changed_parameters": "|".join(sorted(bundle["parameter_changes"] or {})),
            "answer_mismatch": "|".join(s["answer_mismatch"]) if s["answer_mismatch"] is not None else None}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def median(values: list[float]) -> float | None:
        values = sorted(v for v in values if v is not None)
        return values[len(values) // 2] if values else None

    out: dict[str, Any] = {}
    for key in sorted({r["family"] for r in rows}) + ["all"]:
        cell = [r for r in rows if key in ("all", r["family"]) and "verdict_ok" in r]
        out[key] = {"rounds": len(cell), **{name: sum(int(bool(r[name])) for r in cell) for name in
                                             ("verdict_ok", "category_ok", "false_accept", "usable_successor", "correct_not_landed")},
                    "landed": sum(int(r["landing"] == "landed") for r in cell), "unparsed": sum(int(not r["parsed"]) for r in cell),
                    "cost_usd": round(sum(r["cost_usd"] for r in cell), 4), "median_input_tokens": median([r["input_tokens"] for r in cell]),
                    "median_seconds": median([r["seconds"] for r in cell]), "median_tool_calls": median([r["tool_calls"] for r in cell])}
    return out


# ---------------------------------------------------------------- the run

def run(out: Path, *, families: tuple[str, ...], repeats: int, first_repeat: int, part: str, ledger: x2.Ledger, cli: str | None,
        limit_usd: float, task_ids: tuple[str, ...] | None = None, budget_usd: float = TURN_BUDGET_USD,
        turn: Callable[..., dict[str, Any]] = x3_hub.run_turn) -> dict[str, Any]:
    chosen = tasks(families, task_ids)
    config = out / "config"
    x2.write_config(config)
    identity = {"started_at": datetime.now(timezone.utc).isoformat(), "harness": runner.harness_identity(), "arm": ARM,
                "prompt_files_sha256": x2.prompt_files_sha256(), "model": x2.MODEL, "part": part, "limit_usd": limit_usd,
                "turn_budget_usd": budget_usd, "spent_part_before": ledger.spent(part), "tasks": [t["task_id"] for t in chosen],
                "repeats": list(range(first_repeat, first_repeat + repeats)), "max_tool_calls": x3_hub.MAX_TOOL_CALLS,
                "timeout_s": TIMEOUT_S, "max_turns": MAX_TURNS, "scripted_turn": turn is not x3_hub.run_turn,
                "command": x3_hub.command("claude", Path("mcp.json"), max_turns=MAX_TURNS, budget_usd=budget_usd,
                                         system_prompt_file=Path("prompts/system_common.md"), settings_file=Path("settings.json"))[1:]}
    if cli:
        identity["cli_version"] = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
    (out / "identity.json").write_text(json.dumps(identity, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    starts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    stopped = None
    for repeat in range(first_repeat, first_repeat + repeats):
        for task in chosen:
            try:
                check_round(ledger, part, ledger.spent_by(out), limit_usd, budget_usd)
            except x2.CapReached as exc:
                stopped = str(exc)
                break
            family = task["family"]
            if family not in starts:
                starts[family] = family_start(family, out)
            try:
                bundle = run_round(task, repeat, starts[family], out, cli=cli, ledger=ledger, part=part, settings_file=config / "settings.json",
                                   budget_usd=budget_usd, turn=turn)
            except x2.CapReached as exc:
                stopped = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - a harness or service failure ends this round, recorded, not the run
                import traceback

                error_dir = out / "rounds" / f"{task['task_id']}-r{repeat}"
                error_dir.mkdir(parents=True, exist_ok=True)
                (error_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8", newline="\n")
                rows.append({"trial_id": f"X2-G-{task['task_id']}-r{repeat}", "task_id": task["task_id"], "arm": ARM, "family": family,
                             "repeat_idx": repeat, "error": f"{type(exc).__name__}: {exc}"[:500]})
                print(f"X2-G-{task['task_id']}-r{repeat} error: {type(exc).__name__}: {exc}"[:300], flush=True)
                continue
            rows.append(csv_row(bundle))
            r = rows[-1]
            print(f"{r['trial_id']:18} {str(r['verdict']):13} ok={r['verdict_ok']!s:5} {r['landing']:20} oracle={r['left_model_oracle_verdict']!s:9} "
                  f"calls={r['tool_calls']:2} ${r['cost_usd']:.3f} {r['seconds']}s", flush=True)
        if stopped:
            break
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (out / "x2g.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    summary = {"stopped": stopped, "spent_this_run_usd": round(ledger.spent_by(out), 4), "spent_part_usd": ledger.spent(part), "spent_total_usd": ledger.spent(),
               "errors": sum(1 for r in rows if "error" in r), "by_family": summarize([r for r in rows if "error" not in r])}
    (out / "x2g_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run X2 arm G (MonkeyHub's tools, one multi-turn turn per round) into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--families", default="F1,F2,F3")
    parser.add_argument("--tasks", help="comma-separated task ids (default: every X2 task of the families)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--first-repeat", type=int, default=0, help="the repeat index of the first repeat (to split a run)")
    parser.add_argument("--part", default=PART, choices=sorted(x2.CAPS))
    parser.add_argument("--ledger", type=Path, required=True, help="the shared cost ledger")
    parser.add_argument("--cli", required=True, help="the claude executable")
    parser.add_argument("--limit-usd", type=float, required=True, help="what this invocation may add to the part's spend")
    parser.add_argument("--turn-budget-usd", type=float, default=TURN_BUDGET_USD)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(out, families=tuple(args.families.split(",")), repeats=args.repeats, first_repeat=args.first_repeat, part=args.part,
                  ledger=x2.Ledger(args.ledger.resolve()), cli=args.cli, limit_usd=args.limit_usd,
                  task_ids=tuple(args.tasks.split(",")) if args.tasks else None, budget_usd=args.turn_budget_usd)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
