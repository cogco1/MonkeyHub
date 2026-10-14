"""X3: multi-request revision sessions, one building in one way of working.

A session is one building (BA, BB) x one mode x one repeat. Its requests (``x3_requests.json``) are sent in order;
after each, the oracle (its own process) judges the model the request left, and the simulated architect, when a
condition broke, sends the matching correction sentence once before the next request. Request 7 is the hand-over.

| mode | the agent works with | sessions |
|---|---|---|
| M  | MonkeyHub's own tools through its MCP server as an external host (``x3_hub``): context, decisions, candidates, checks, admission, working design, undo | a fresh CLI session per request |
| M+ | as M, the CLI session resumed (``--resume``) across requests; transcripts in the CLI's default directory | one CLI session |
| T  | the same Hub, its tools restricted to construction proposals, candidates and the model readback (``x3_hub.serve_proxy``); the prompt is the request only | fresh per request |
| H  | as T, with the full previous transcript (requests, tool calls and results, answers) in the prompt | fresh per request |
| SF | no Hub: the previous request's C-task 3DM state file through ``x3_sf_mcp`` (read the state, read the boxes, submit typed changes to the hostless reviser) | fresh per request |

Request 0 (the opening conditions and the history's rejections) is not a model turn: the harness writes it where each
mode keeps it. M and M+: the conditions as ``keep`` and the rejections as ``reject`` decisions in the project.
H: the transcript's first entries. SF: the start file's context pack, read after the same decisions are recorded in
a production project of the building. T: nowhere (the building's own parameters and obligations, as authored, are
all any mode starts with).

What carries to the next request: M and M+, the Working Head (the agent lands a change with admission and Continue);
T and H, which have no landing tool, the candidate the answer names (else the turn's last candidate), landed by the
harness as the architect's Continue on the Hub page; SF, the file the agent's last adopted submission wrote.

Every turn: the common system prompt (``prompts/x3_system.md``), x2's fairness flags (``x3_hub.command``), at most
30 design tool calls (the lab proxy or the SF server refuses later calls; MonkeyHub's presentation calls do not count)
and 600 s, its cost in the shared ledger. One bundle per request (``<session>/<request>.bundle.json``), a session CSV
and summary.

The file hand-over (A10, B10; ``Session.hand_over``): the harness makes the consultant's file from the design as it
stands (``external_edit``: one named object moved, everything else kept) and keeps it beside the bundle. SF: it becomes
the current file; ``submit_change`` refuses it (its geometry no longer matches its binding) unless the agent asks for
``rebuild_from_state``, which discards and lists the outside change. Hub modes: the architect attaches it (an external
model asset, ``x3_hub.attach_model``) and the request names it. MonkeyHub offers no read of an attached model's objects
with their positions, so a Hub agent cannot see the outside change unless it finds another way (protocol 7.16: run as
is, a measured product gap; ``HUB_HANDOVER_READY`` only switches it off). The request is judged against the design as recorded; the score adds
``external_change_reported_auto`` and ``external_change_adopted``. GH is paused.
"""

from __future__ import annotations

from labs.revision_tasks import _bootstrap  # noqa: F401  (source roots first)

import argparse
import csv
import fnmatch
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from labs.revision_tasks import x2, x3_hub

LAB = Path(__file__).resolve().parent
REQUESTS = LAB / "x3_requests.json"
SYSTEM = LAB / "prompts" / "x3_system.md"
ORACLE = LAB / "oracle.py"
MODES = ("M", "M+", "T", "H", "SF")
HUB_MODES = ("M", "M+", "T", "H")
REQUEST_TIMEOUT_S = 600
TURN_BUDGET_USD = 3.0          # a runaway guard, not the binding limit (manifest 7.11)
MAX_TURNS = 40                 # 30 design calls (the binding cap), the presentation protocol, progress messages and the answer
OUTCOMES = ("complete", "correct_not_landed", "honest_limitation", "wrong", "asked_rightly", "asked_wrongly", "repeated_explanation", "external_change_reported")


class NotBuilt(RuntimeError):
    """A step this harness does not perform yet; the session stops there and says so."""


# The file hand-over (A10, B10). The consultant's file and its delivery are built in every mode; in the Hub modes the
# session still stops at it (NotBuilt) until the protocol owner decides, because MonkeyHub gives a client no read of an
# attached model's objects: the Runtime keeps the file as an external model asset (POST /api/model-assets, its own run
# studio-model-<sha256> with no design state), and GET /api/model-assets/{sha256}/index requires an exact run state
# (REFERENCE_STATE_NOT_EXACT for that run) and carries native metadata only, no geometry, by design. A dry run sets this.
HUB_HANDOVER_READY = True
CONTEXT_FOCUS_LIMIT = 64          # the Runtime's bound on elementIds in POST /api/intents/context
HUB_HANDOVER_GAP = ("the file hand-over in the Hub modes is held: MonkeyHub keeps the attached file as an external model asset, "
                    "but offers no read of its objects (the model-asset index needs an exact run state and carries no geometry)")
HANDOVER_FILE_NAME = "model-from-consultant.3dm"


# ---------------------------------------------------------------- the request file

def load_requests(path: Path = REQUESTS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_requests(data: dict[str, Any], checks: tuple[str, ...] | None = None) -> list[str]:
    """Problems in the request file: missing fields, unknown checks or outcomes, a broken order or reference."""

    if checks is None:
        checks = _oracle_checks()
    problems = []
    for building, spec in data["buildings"].items():
        for key in ("record", "opening", "conditions", "corrections", "correction_order", "requests"):
            if key not in spec:
                problems.append(f"{building}: no {key}")
        for condition in spec["opening"]["conditions"]:
            if not condition.get("text") or not condition.get("target_ref", "").startswith("entity:"):
                problems.append(f"{building} {condition.get('id')}: opening condition needs text and an entity target_ref")
        for key in spec["correction_order"]:
            if key not in spec["corrections"]:
                problems.append(f"{building}: correction {key} has no sentence")
        ids = [r["id"] for r in spec["requests"]]
        orders = [r["order"] for r in spec["requests"] if r["order"] is not None]
        if orders != list(range(1, 13)):
            problems.append(f"{building}: request orders {orders} are not 1..12")
        handovers = [r["id"] for r in spec["requests"] if r.get("handover")]
        if [r["order"] for r in spec["requests"] if r.get("handover")] != [7]:
            problems.append(f"{building}: the hand-over is {handovers}, not request 7")
        for row in [*spec["conditions"], *(m for r in spec["requests"] for m in r["measures"])]:
            check = row.get("measure", {}).get("check")
            if check not in checks:
                problems.append(f"{building} {row.get('id')}: unknown check {check}")
            start = row.get("measure", {}).get("x3_start")
            if start and start.removeprefix("before:") not in ids:
                problems.append(f"{building} {row.get('id')}: x3_start {start} names no request")
            if row.get("correction") and row["correction"] not in spec["corrections"]:
                problems.append(f"{building} {row.get('id')}: correction {row['correction']} has no sentence")
        for request in spec["requests"]:
            for key in ("id", "type", "text", "expected", "change", "measures"):
                if key not in request:
                    problems.append(f"{building} {request.get('id')}: no {key}")
            if any(o not in OUTCOMES for o in request["expected"]["outcome"]):
                problems.append(f"{building} {request['id']}: unknown outcome in {request['expected']['outcome']}")
            if not request["text"].strip() or request["text"] != request["text"].strip():
                problems.append(f"{building} {request['id']}: request text is empty or padded")
    return problems


def _oracle_checks() -> tuple[str, ...]:
    """The oracle's check names, read from its own source (the harness does not import the oracle)."""

    import re

    text = ORACLE.read_text(encoding="utf-8")
    block = text[text.index("CHECKS: dict"):]
    return tuple(re.findall(r'^\s+"([a-z_]+)": check_', block, re.M))


def session_requests(spec: dict[str, Any], ids: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """The requests in order (a fixed reply right after the request it answers), optionally only ``ids``."""

    rows = list(spec["requests"])
    if ids is not None:
        rows = [r for r in rows if r["id"] in ids]
    return rows


# ---------------------------------------------------------------- measures and the oracle

def object_names(path: Path) -> list[str]:
    """The named meshes of a model file (the harness's own reading: names only)."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    return sorted(o.Attributes.Name for o in model.Objects if o.Attributes.Name and isinstance(o.Geometry, rhino3dm.Mesh))


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def prepare(relations: list[dict[str, Any]], names: list[str], start_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Oracle relations from the file's measures: globs expanded against the judged model, x3 keys dropped.

    ``object`` with a ``*`` becomes one relation per match (``id:name``), or one unmatched relation the oracle reads
    as unchecked. ``objects`` patterns become the names present in both models (an object added or removed is
    reported apart), less ``x3_except``.
    """

    out = []
    common = sorted(set(names) & set(start_names)) if start_names is not None else names
    for row in relations:
        measure = {k: v for k, v in row["measure"].items() if not k.startswith("x3_")}
        if isinstance(measure.get("object"), str) and "*" in measure["object"]:
            matched = [n for n in names if fnmatch.fnmatchcase(n, measure["object"])]
            for name in matched or [measure["object"]]:
                out.append({"id": f"{row['id']}:{name}" if matched else row["id"], "measure": {**measure, "object": name}})
            continue
        if isinstance(measure.get("objects"), list) and any("*" in p for p in measure["objects"]):
            excluded = row["measure"].get("x3_except", [])
            measure["objects"] = [n for n in common if _matches(n, measure["objects"]) and not _matches(n, excluded)]
        out.append({"id": row["id"], "measure": measure})
    return out


def run_oracle(relations: list[dict[str, Any]], task_id: str, candidate: Path, start: Path | None, out: Path) -> dict[str, Any]:
    """The oracle in its own process (``python -I oracle.py``) on one prepared task."""

    task = out.with_suffix(".task.json")
    task.write_text(json.dumps({"task_id": task_id, "protected_relations": relations}, indent=1) + "\n", encoding="utf-8", newline="\n")
    argv = [sys.executable, "-I", str(ORACLE), "--task", str(task), "--candidate", str(candidate), "--out", str(out)]
    if start is not None:
        argv += ["--start", str(start)]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return {"verdict": "unchecked", "relations": [], "error": (done.stderr or done.stdout)[-2000:]}
    return json.loads(out.read_text(encoding="utf-8"))


def judge(spec: dict[str, Any], request: dict[str, Any], model: Path, start: Path, before: dict[str, Path], out: Path, *,
          delivered: Path | None = None, external: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conditions and the request's measures on ``model``, against ``start`` (or another request's start).

    A file hand-over is judged against the design as recorded before the consultant's edit (``start``). ``external`` is
    the edit as made (``external_edit``'s record): the moved object's box as recorded and as the consultant left it are
    two more oracle bbox relations, read into ``summary["external"]``. ``delivered`` is the file the agent was given when
    it is not ``start`` (SF: the consultant's file is its current file); what the agent itself changed is then
    measured against it (``summary["delivered"]``).
    """

    names, start_names = object_names(model), object_names(start)
    own, elsewhere = [], {}
    for row in [*spec["conditions"], *request["measures"]]:
        against = row["measure"].get("x3_start")
        if against:
            elsewhere.setdefault(against.removeprefix("before:"), []).append(row)
        else:
            own.append(row)
    changes = {"id": "x3-changes", "measure": {"check": "geometry_unchanged", "objects": sorted(set(names) & set(start_names))}}
    outside = []
    if external is not None:
        for key, box in (("x3-external-recorded", external["box_before"]), ("x3-external-consultant", external["box_after"])):
            outside.append({"id": key, "measure": {"check": "bbox", "object": external["object"], "min": box["min"], "max": box["max"],
                                                 "tolerance_m": 0.002}})
    report = run_oracle([*prepare(own, names, start_names), changes, *outside], f"{request['id']}", model, start, out)
    report["added"] = sorted(set(names) - set(start_names))
    report["removed"] = sorted(set(start_names) - set(names))
    if delivered is not None and Path(delivered) != Path(start):
        given = object_names(delivered)
        own_change = {"id": "x3-changes-delivered", "measure": {"check": "geometry_unchanged", "objects": sorted(set(names) & set(given))}}
        against = run_oracle([own_change], f"{request['id']}-vs-delivered", model, delivered, out.with_name(out.stem + ".vs-delivered.json"))
        found = next((r for r in against.get("relations", []) if r["id"] == "x3-changes-delivered"), {})
        report["delivered"] = {"status": found.get("status", "unchecked"), "changed": (found.get("measured") or {}).get("changed", []),
                               "added": sorted(set(names) - set(given)), "removed": sorted(set(given) - set(names))}
    for rid, rows in elsewhere.items():
        other = before.get(rid)
        if other is None:
            report.setdefault("relations", []).extend({"id": r["id"], "status": "unchecked", "measured": {"detail": f"no model from before {rid}"}} for r in rows)
            continue
        extra = run_oracle(prepare(rows, names, object_names(other)), f"{request['id']}-vs-{rid}", model, other, out.with_name(out.stem + f".vs-{rid}.json"))
        report.setdefault("relations", []).extend(extra.get("relations", []))
    conditions = {r["id"] for r in spec["conditions"]}
    relations = [r for r in report.get("relations", []) if not r["id"].startswith("x3-")]     # the harness's own relations apart
    changed = next((r["measured"].get("changed", []) for r in report.get("relations", []) if r["id"] == "x3-changes"), [])
    report["summary"] = {
        "conditions": {r["id"]: r["status"] for r in relations if r["id"].split(":")[0] in conditions},
        "measures": {r["id"]: r["status"] for r in relations if r["id"].split(":")[0] not in conditions},
        "changed": changed, "added": report["added"], "removed": report["removed"],
        "unrequested": sorted(n for n in [*changed, *report["added"], *report["removed"]] if not _matches(n, request["change"])),
        "verdict": "violated" if any(r["status"] == "violated" for r in relations) else
                   "unchecked" if any(r["status"] == "unchecked" for r in relations) else "held"}
    if external is not None:
        status = {r["id"]: r["status"] for r in report.get("relations", []) if r["id"].startswith("x3-external-")}
        recorded, consultant = status.get("x3-external-recorded", "unchecked"), status.get("x3-external-consultant", "unchecked")
        report["summary"]["external"] = {
            "object": external["object"], "recorded": recorded, "consultant": consultant,
            # adopted: the moved object stands where the consultant left it, not where the design recorded it
            "adopted": True if consultant == "held" and recorded == "violated" else False if recorded == "held" else None}
    if "delivered" in report:
        report["summary"]["delivered"] = report["delivered"]
    return report


def correction(spec: dict[str, Any], report: dict[str, Any]) -> dict[str, Any] | None:
    """The correction sentence the architect sends after this report, or None: the first broken condition in order."""

    by_id = {r["id"]: r for r in spec["conditions"]}
    broken: dict[str, dict[str, Any]] = {}
    for relation in report.get("relations", []):
        row = by_id.get(relation["id"].split(":")[0])
        if row is None or relation["status"] != "violated" or not row.get("correction"):
            continue
        trigger = row.get("x3_trigger")
        if trigger and not any(trigger in str(p) for p in relation.get("measured", {}).get("problems", ())):
            continue
        broken.setdefault(row["correction"], relation)
    for key in spec["correction_order"]:
        if key in broken:
            sentence = spec["corrections"][key]
            if key == "collision":
                hit = (broken[key]["measured"].get("collisions") or [{}])[0]
                pair = [hit.get("a"), hit.get("b")]
                flight = next((m for m in pair if m and m.startswith("obj-stair-")), None)
                other = next((m for m in pair if m and m != flight and (flight or m in spec.get("object_words", {}))), None) or pair[-1]
                words = spec.get("object_words", {})
                sentence = sentence.replace("[object]", words.get(other, other) if not flight else other)
                sentence = sentence.replace("[flight]", words.get(flight, flight or "flight"))
            return {"key": key, "sentence": sentence, "relation": broken[key]["id"]}
    return None


# ---------------------------------------------------------------- transcripts (H)

def transcript_entries(stream: str) -> list[str]:
    """A turn as text: the assistant's words, each tool call with its input, each tool result (images left out)."""

    lines = []
    for event in x3_hub._events(stream):
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        for item in message.get("content") or ():
            if not isinstance(item, dict):
                continue
            if event.get("type") == "assistant" and item.get("type") == "text" and item.get("text"):
                lines.append(f"[Assistant] {item['text']}")
            elif event.get("type") == "assistant" and item.get("type") == "tool_use":
                name = str(item.get("name", "")).split("__")[-1]
                lines.append(f"[Tool call] {name} {json.dumps(item.get('input'), ensure_ascii=False)}")
            elif event.get("type") == "user" and item.get("type") == "tool_result":
                content = item.get("content")
                parts = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
                text = "\n".join(p.get("text", "") if p.get("type") == "text" else "[image omitted]" for p in parts if isinstance(p, dict))
                lines.append(f"[Tool result{' (error)' if item.get('is_error') else ''}] {text}")
    return lines


def h_prompt(history: list[str], text: str) -> str:
    return ("The full transcript of this project's earlier requests, for reference:\n\n" + "\n\n".join(history)
            + "\n\n---\nCurrent request from the architect:\n" + text)


# ---------------------------------------------------------------- one session

class Session:
    def __init__(self, building: str, mode: str, repeat: int, out: Path, *, cli: str, ledger: x2.Ledger, part: str,
                 limit_usd: float, budget_usd: float = TURN_BUDGET_USD, data: dict[str, Any] | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode {mode} is not one of {MODES}")
        self.data = data or load_requests()
        self.spec = self.data["buildings"][building]
        self.building, self.mode, self.repeat = building, mode, repeat
        self.dir = out / f"{building}-{mode.replace('+', 'plus')}-r{repeat}"
        self.dir.mkdir(parents=True, exist_ok=False)
        self.cli, self.ledger, self.part = cli, ledger, part
        self.run_root, self.limit_usd, self.budget_usd = out, limit_usd, budget_usd
        self.settings = self.dir / "settings.json"
        self.settings.write_text(json.dumps({"disableBundledSkills": True}), encoding="utf-8")
        self.services: x3_hub.HubServices | None = None
        self.model: Path | None = None              # the model the next request starts from
        self.before: dict[str, Path] = {}           # request id -> the model it started from
        self.history: list[str] = []                # H's transcript
        self.cli_session: str | None = None         # M+
        self.cli_cost_total = 0.0                   # M+: the resumed session's CLI total so far
        self.hub_session = x3_hub.new_session_id()  # M+ keeps one conversation
        self.cwd = self.dir / "cli-cwd"             # M+ resumes from one working directory
        self.cli_sessions: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self.stopped: str | None = None
        self.turn_count = 0

    # ---- request 0
    def opening_decisions(self) -> list[dict[str, Any]]:
        """The opening conditions as keep and the history's rejections as reject decisions, in the architect's words."""

        opening = self.spec["opening"]
        rows = [{"text": c["text"], "target_ref": c["target_ref"], "disposition": "keep"} for c in opening["conditions"]]
        rows += [{"text": f"{h['proposal']} {h['rejection']}", "target_ref": h["target_ref"], "disposition": "reject"} for h in opening["history"]]
        return rows

    @staticmethod
    def decision_body(project_id: str, run_id: str, digest: str, row: dict[str, Any]) -> dict[str, Any]:
        return {"projectId": project_id, "rawLanguage": row["text"], "disposition": row["disposition"], "strength": "hard",
                "targetRef": row["target_ref"], "scope": {"domain": "design", "extent": "project"},
                "source": {"kind": "design", "sourceRunId": run_id, "stateDigest": digest}, "applicability": "scope", "sourceKind": "human"}

    def open_hub(self, stack) -> None:
        maker = {"BA": x3_hub.ba_project, "BB": x3_hub.bb_project}[self.building]
        self.services = stack.enter_context(x3_hub.isolated_hub(self.dir / "hub", make_project=maker))
        if self.mode in ("M", "M+"):
            state = x3_hub._call(self.services.runtime_url, f"/api/state?run={x3_hub.START_RUN}")
            recorded = [x3_hub.studio_write(self.services.hub_url, self.services.runtime_id, "POST", "/api/decisions",
                                            self.decision_body(state["projectId"], x3_hub.START_RUN, state["stateDigest"], row))
                        for row in self.opening_decisions()]
            self._write("opening.json", {"mode": self.mode, "decisions": [{k: r.get(k) for k in ("decisionId", "disposition", "targetRef", "status")}
                                                                          for r in recorded]})
        elif self.mode == "H":
            opening = self.spec["opening"]
            self.history = ["[Architect] " + " ".join(c["text"] for c in opening["conditions"])]
            self.history += [f"[Project history] {h['proposal']} {h['rejection']}" for h in opening["history"]]
            self._write("opening.json", {"mode": "H", "transcript": self.history})
        else:
            self._write("opening.json", {"mode": "T", "note": "nothing is carried: the prompt is the request only"})
        self.model = self.head_model()["path"]

    def open_file(self) -> None:
        """SF: the start file, C-task, its context pack read after request 0's decisions are recorded in a production project."""

        from labs.revision_tasks import buildings, production_project, reviser

        record = {"BA": buildings.ba_pantheon, "BB": buildings.bb_house}[self.building]()
        start = self.dir / "start"
        project = production_project.make_production_project(record, start / "project")
        pack = self.opening_pack(project, start / "cache")
        written = reviser.write_state_file(record, arm="C-task", fmt="3dm", out_dir=start, stem="state-000", parent_sha256=None,
                                           context_root=start / "context", utterance="Current design state.", context_pack=pack["pack"])
        self._write("opening.json", {"mode": "SF", "decisions": pack["decisions"], "file": str(written.path)})
        self.model = written.path

    def opening_pack(self, project, cache_dir: Path) -> dict[str, Any]:
        """Record request 0's decisions against the stage run (an in-process Runtime, as ``production_project`` does)
        and read the context pack the start file carries."""

        from fastapi.testclient import TestClient
        from project_runtime.main import create_app
        from project_runtime.settings import StudioSettings

        app = create_app(StudioSettings(cad_export="off", project_dir=project.project_dir, cache_dir=cache_dir))
        with TestClient(app) as client:
            state = client.get("/api/state", params={"run": project.run_id}).json()
            recorded = []
            for row in self.opening_decisions():
                answer = client.post("/api/decisions", json=self.decision_body(state["projectId"], project.run_id, state["stateDigest"], row))
                recorded.append({"status": answer.status_code, "disposition": row["disposition"], "target_ref": row["target_ref"]})
            elements = sorted(e.entity_id for e in project_record(project).entities_of("Element@1"))
            # The Runtime focuses a context read on at most CONTEXT_FOCUS_LIMIT element ids; a building with more
            # (BB: 89) is read without a focus, which MonkeyHub then resolves from the words. The recorded decisions
            # come through either way (BB: 4 with or without a focus).
            focus = {"elementIds": elements} if len(elements) <= CONTEXT_FOCUS_LIMIT else {}
            pack = client.post("/api/intents/context", json={"projectId": state["projectId"], "sourceRunId": project.run_id,
                                                              "stateDigest": state["stateDigest"], "utterance": "Current design state.",
                                                              **focus})
            if pack.status_code != 200:
                raise x3_hub.X3Error(f"context read failed: {pack.text[:400]}")
            return {"decisions": recorded, "pack": pack.json()}

    # ---- the carried model
    def head_model(self) -> dict[str, Any]:
        """The Working Head's model (Hub modes)."""

        s = self.services
        working = x3_hub._call(s.runtime_url, "/api/working-source?workspace=modeling")
        head = working.get("head") or {}
        source = working.get("source") or {}
        found = self.run_model(head.get("runId"), head.get("stateDigest"),
                               source.get("assetSha256") if source.get("runId") == head.get("runId") else None)
        if found is not None:
            return found
        raise x3_hub.X3Error(f"the Working Head {head.get('runId')} has no verified model: {working.get('reason')}")

    def run_model(self, run_id: str | None, state_digest: str | None, asset_sha256: str | None = None) -> dict[str, Any] | None:
        """A run's checked model (its certified ``.preview.3dm`` with matching bytes), or None (Hub modes)."""

        s = self.services
        artifacts = x3_hub._call(s.runtime_url, "/api/artifacts").get("artifacts", [])
        model = x3_hub.preview_model(artifacts, run_id, state_digest, s.project_dir, asset_sha256)
        if model is not None and model.get("verified"):
            return {"run_id": run_id, "state_digest": state_digest, "path": Path(model["path"])}
        # production_project runs the runner with its workspace beside the project, not in it, so the stage run's
        # export is listed "file missing": the receipt's bytes are found where the runner wrote them, by their sha256.
        rows = [r for r in artifacts if r.get("runId") == run_id and r.get("format") == "3dm" and r.get("representation") == "preview"
                and r.get("designStateDigest") == state_digest and r.get("sha256")]
        for row in rows:
            for path in (s.root / "projects").rglob(row["fileName"]):
                if x3_hub._sha256(path) == row["sha256"]:
                    return {"run_id": run_id, "state_digest": state_digest, "path": path, "outside_project": True}
        return None

    def candidate_has_model(self, candidate_id: str) -> bool:
        digest = x3_hub._call(self.services.runtime_url, f"/api/candidates/{quote(candidate_id)}").get("stateDigest")
        return digest is not None and self.run_model(candidate_id, digest) is not None

    def prepared_prompt(self, text: str, head: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """M: the request with the prepared context MonkeyHub's own chat gives a turn whose message names a design source
        (``store.py``: the request, then ``turn_context._CONTEXT_NOTE`` and the ``POST /api/intents/context`` answer),
        the source being the Working Head, as the Hub page selects the editing state. The external binding by itself
        names no source, and the Runtime then reads and writes its reference run."""

        from monkeyhub_api.chat import providers, turn_context

        s = self.services
        project_id = json.loads((s.project_dir / "project.json").read_text(encoding="utf-8"))["project_id"]
        pack = x3_hub._call(s.runtime_url, "/api/intents/context", "POST", {"utterance": text, "projectId": project_id,
                                                                            "sourceRunId": head["run_id"], "stateDigest": head["state_digest"]})
        prompt = text + "\n\n" + turn_context._CONTEXT_NOTE + "\n" + providers._redact(json.dumps(pack, ensure_ascii=False))
        return prompt, {"source_run": head["run_id"], "chars": len(prompt) - len(text)}

    # ---- one turn
    def _check_spend(self) -> None:
        spent = self.ledger.spent_by(self.run_root)
        if spent + self.budget_usd > self.limit_usd:
            raise x2.CapReached(f"this run's limit: {spent:.2f} of {self.limit_usd:.2f} spent, a turn may spend {self.budget_usd:.2f}")

    def turn(self, rid: str, kind: str, text: str, extra_requests: tuple[tuple[str, str, str], ...] = ()) -> dict[str, Any]:
        """One CLI turn in this mode; returns the turn record with the carried model after it. ``extra_requests`` widen T
        and H's allow-list for this turn (a file hand-over's ``x3_hub.T_HANDOVER_REQUESTS``)."""

        self._check_spend()
        self.turn_count += 1
        work = self.dir / f"{self.turn_count:02d}-{rid.replace(chr(39), 'p')}-{kind}"
        trial = f"X3-{self.building}-{self.mode}-r{self.repeat}-{rid}-{kind}"
        common = dict(workdir=work, cli=self.cli, ledger=self.ledger, part=self.part, system_prompt_file=SYSTEM, max_turns=MAX_TURNS,
                      budget_usd=self.budget_usd, trial_id=trial, timeout=REQUEST_TIMEOUT_S, settings_file=self.settings)
        work_cfg = self.dir / "config" / f"{self.turn_count:02d}"
        work_cfg.mkdir(parents=True)
        info: dict[str, Any] = {"kind": kind, "request_id": rid, "text": text, "mode": self.mode}
        if self.mode == "SF":
            config = {"file": str(self.model), "out": str(work_cfg / "states"), "user_message": text, "log": str(work_cfg / "calls.jsonl"),
                      "max_calls": x3_hub.MAX_TOOL_CALLS}
            (work_cfg / "sf.json").write_text(json.dumps(config, indent=1), encoding="utf-8", newline="\n")
            from labs.revision_tasks import x3_sf_mcp
            turn = x3_hub.run_turn(text, mcp={"mcpServers": {x3_sf_mcp.SERVER: x3_sf_mcp.server_config(work_cfg / "sf.json")}},
                                   allowed=tuple(f"mcp__{x3_sf_mcp.SERVER}__{t}" for t in x3_sf_mcp.TOOLS), **common)
            current = json.loads((work_cfg / "states" / "current.json").read_text(encoding="utf-8")) if (work_cfg / "states" / "current.json").exists() else {}
            new = Path(current.get("current", str(self.model)))
            info.update(landed_by="agent" if new != self.model else None, submissions=current.get("submissions", []))
            if new != self.model:
                # the files between: each adopted submission's rebuilt file (rebuild_from_state) and every adopted file but the last
                via: list[Path] = []
                for row in (r for r in info["submissions"] if r.get("adopted")):
                    if (row.get("rebuild") or {}).get("file"):
                        via.append(Path(row["rebuild"]["file"]))
                    via.append(Path(row["file"]))
                info["transfer"] = transfer_audit(self.model, new, tuple(via[:-1]))
            tried = [row for row in info["submissions"] if row.get("file") and not row.get("adopted")]
            if info["landed_by"] is None and tried:
                info["unlanded_candidate"] = {"model": {"path": tried[-1]["file"]}, "rule": "the last submission tried without adopt"}
            self.model = new
        else:
            s = self.services
            hub_session = self.hub_session if self.mode == "M+" else x3_hub.new_session_id()
            child = x3_hub.mcp_config(hub_url=s.hub_url, project_dir=s.project_dir, session_id=hub_session)["mcpServers"][x3_hub.SERVER]
            restricted = self.mode in ("T", "H")
            head_before = self.head_model()
            settings = x3_hub.proxy_settings(child, log=work_cfg / "calls.jsonl", restricted=restricted, user_message=text if restricted else None,
                                             base_run=head_before["run_id"] if restricted else None,
                                             extra_requests=extra_requests if restricted else ())
            (work_cfg / "proxy.json").write_text(json.dumps(settings, indent=1), encoding="utf-8", newline="\n")
            mcp = {"mcpServers": {x3_hub.SERVER: x3_hub.proxy_server_config(work_cfg / "proxy.json")}}
            allowed = tuple(f"mcp__{x3_hub.SERVER}__{t}" for t in x3_hub.T_TOOLS) if restricted else x3_hub.ALLOWED
            extra: dict[str, Any] = {}
            if self.mode == "M+":
                self.cwd.mkdir(exist_ok=True)
                extra = dict(cwd=self.cwd, default_config=True, persist=self.cli_session is None, resume=self.cli_session,
                             cli_offset=self.cli_cost_total)
            if self.mode == "H":
                prompt = h_prompt(self.history, text)
            elif self.mode in ("M", "M+"):
                prompt, info["prepared_context"] = self.prepared_prompt(text, head_before)
            else:
                prompt = None
            turn = x3_hub.run_turn(text, hub_url=s.hub_url, project_dir=s.project_dir, session_id=hub_session, mcp=mcp, allowed=allowed,
                                   parent_env=s.parent_env, prompt=prompt, close_open_turn=not restricted, **common, **extra)
            final = turn["summary"]["final_text"]
            if restricted:
                closed = x3_hub.close_turn(s.hub_url, s.project_dir, hub_session, status="complete", note=final or "(no answer)")
                info["conversation_closed_by_harness"] = closed
            chat = x3_hub.conversation(s.hub_url, s.project_dir, hub_session)
            made = x3_hub.candidates_made(s.hub_url, s.runtime_id, chat["id"] if chat else None)
            info["candidates"] = [c["candidateId"] for c in made]
            head_after = self.head_model()
            if head_after["run_id"] != head_before["run_id"]:
                info["landed_by"] = "agent"
            elif restricted and made:
                # Batch 3d: a candidate without a checked model (a failed readback) is never landed; the rules go on
                # to the next, and with none left nothing is landed.
                chosen, rule, passed_over = x3_hub.choose_landing(head_before["run_id"], info["candidates"], final, self.candidate_has_model)
                if passed_over:
                    info["not_landed_without_model"] = passed_over
                if chosen is None:
                    info["landed"] = {"run_id": None, "rule": "no_candidate_with_checked_model"}
                    info["landed_by"] = None
                else:
                    info["landed"] = {"run_id": chosen, "rule": rule, **x3_hub.continue_on(s, chosen)}
                    info["landed_by"] = "harness"
                    head_after = self.head_model()
            else:
                info["landed_by"] = None
                if made:
                    info["unlanded_candidate"] = x3_hub.result_model(s.runtime_url, s.project_dir, hub_url=s.hub_url, runtime_id=s.runtime_id,
                                                                     session_id=hub_session, start_run=head_before["run_id"], answer_text=final)
            info["head"] = {"run_id": head_after["run_id"], "state_digest": head_after["state_digest"]}
            self.model = head_after["path"]
            if self.mode == "M+":
                self.cli_session = turn["cli_session_id"] or self.cli_session
                self.cli_cost_total = float((turn.get("usage") or {}).get("cli_total_cost_usd") or self.cli_cost_total)
        if self.mode == "H":
            stream = (work / "stream.jsonl").read_text(encoding="utf-8")
            self.history += [f"[Architect] {text}", *transcript_entries(stream)]
        if turn.get("cli_session_id"):
            self.cli_sessions.append(turn["cli_session_id"])
        info.update(turn=turn, model_after=str(self.model))
        return info

    # ---- one request
    def hand_over(self, request: dict[str, Any]) -> dict[str, Any]:
        """A file hand-over: the consultant's file, made from the design as it stands now (Hub modes: the Working Head's
        model, ``head_model``; SF: its current file) by ``external_edit`` and kept in the session directory, then
        delivered. Hub modes: attached as the architect attaches a file (``x3_hub.attach_model``: an external model
        asset), the request carrying one line that names it. SF: the file becomes the current file. Returns the record
        the bundle keeps (``design`` is what the request is judged against, ``delivered`` what the agent was given)."""

        rid = request["id"]
        if self.mode in HUB_MODES and not HUB_HANDOVER_READY:
            raise NotBuilt(f"{rid}: {HUB_HANDOVER_GAP}")
        design = Path(self.head_model()["path"]) if self.mode in HUB_MODES else Path(self.model)
        edit = external_edit(design, request["external_edit"], self.dir / f"{rid.replace(chr(39), 'p')}.consultant.3dm")
        record: dict[str, Any] = {"request": rid, "design": str(design), "edit": edit}
        if self.mode in HUB_MODES:
            attached = x3_hub.attach_model(self.services, Path(edit["file"]), HANDOVER_FILE_NAME)
            record.update(delivery="attachment: POST /api/model-assets (external model asset), named in the request", attached=attached,
                          attachment_line=x3_hub.attachment_line(attached), delivered=str(design),
                          extra_requests=[list(r) for r in x3_hub.T_HANDOVER_REQUESTS] if self.mode in ("T", "H") else [])
        else:
            self.model = Path(edit["file"])
            record.update(delivery="the consultant's file is the current file", delivered=edit["file"])
        self._write(f"{rid.replace(chr(39), 'p')}.handover.json", record)
        return record

    def request(self, request: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        rid = request["id"]
        handover = self.hand_over(request) if request.get("external_edit") else None
        start = Path(handover["design"]) if handover else self.model
        self.before[rid] = start
        text = request["text"]
        if request.get("fixed_reply") and previous is not None and previous["oracle"]["summary"]["verdict"] == "violated":
            text = request.get("text_if_broken", text)
        if handover and handover.get("attachment_line"):
            text = text + "\n" + handover["attachment_line"]
        extra = tuple(tuple(r) for r in (handover or {}).get("extra_requests", ()))
        judged = dict(delivered=Path(handover["delivered"]), external=handover["edit"]) if handover else {}
        bundle: dict[str, Any] = {"building": self.building, "mode": self.mode, "repeat": self.repeat, "request": request, "sent_text": text,
                                  "start_model": str(start), "turns": [], "harness_added": harness_added(self.mode, bool(handover))}
        if handover:
            bundle["handover"] = handover
        first = self.turn(rid, "request", text, extra)
        bundle["turns"].append(first)
        report = judge(self.spec, request, self.model, start, self.before, self.dir / f"{rid.replace(chr(39), 'p')}.oracle.json", **judged)
        bundle["oracle"] = report
        if first.get("unlanded_candidate", {}).get("model"):
            path = Path(first["unlanded_candidate"]["model"]["path"])
            bundle["oracle_unlanded_candidate"] = judge(self.spec, request, path, start, self.before,
                                                        self.dir / f"{rid.replace(chr(39), 'p')}.candidate.oracle.json", **judged)["summary"]
        fix = correction(self.spec, report)
        bundle["correction"] = fix
        if fix is not None:
            second = self.turn(rid, "correction", fix["sentence"], extra)
            bundle["turns"].append(second)
            bundle["oracle_after_correction"] = judge(self.spec, request, self.model, start, self.before,
                                                      self.dir / f"{rid.replace(chr(39), 'p')}.corrected.oracle.json", **judged)
        bundle["score"] = score(request, bundle)
        self._write(f"{rid.replace(chr(39), 'p')}.bundle.json", bundle)
        return bundle

    def run(self, ids: tuple[str, ...] | None = None) -> dict[str, Any]:
        from contextlib import ExitStack

        started = time.perf_counter()
        with ExitStack() as stack:
            try:
                if self.mode in HUB_MODES:
                    self.open_hub(stack)
                else:
                    self.open_file()
                previous = None
                for request in session_requests(self.spec, ids):
                    previous = self.request(request, previous)
                    self.rows.append(csv_row(previous))
                    print(f"{self.building} {self.mode:2} {request['id']:4} {self.rows[-1]['outcome_auto']:22} oracle={self.rows[-1]['oracle_verdict']:9} "
                          f"${self.rows[-1]['cost_usd']:.3f} {self.rows[-1]['seconds']}s", flush=True)
            except (x2.CapReached, NotBuilt) as exc:
                self.stopped = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 - a harness or service failure ends this session, recorded, not the run
                import traceback

                self.stopped = f"error: {type(exc).__name__}: {exc}"[:1500]
                (self.dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8", newline="\n")
        summary = {"building": self.building, "mode": self.mode, "repeat": self.repeat, "stopped": self.stopped, "requests": len(self.rows),
                   "cost_usd": round(sum(r["cost_usd"] for r in self.rows), 4), "seconds": round(time.perf_counter() - started, 1),
                   "cli_sessions": self.cli_sessions}
        if self.mode == "M+":
            summary["transcripts"] = x3_hub.transcript_bytes(self.cli_sessions)
        if self.rows:
            with (self.dir / "session.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.rows[0]))
                writer.writeheader()
                writer.writerows(self.rows)
        self._write("summary.json", summary)
        return summary

    def _write(self, name: str, value: Any) -> None:
        (self.dir / name).write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")


def project_record(project) -> Any:
    """The authored record a production project was made from (its runner input), for the element ids of the pack read."""

    from archflow.state.state_record import StateRecord

    from labs.revision_tasks import production_project

    return StateRecord.from_dict(json.loads((project.project_dir / production_project.RECORD_PATH).read_text(encoding="utf-8")))


def external_edit(model: Path, edit: dict[str, Any], out: Path) -> dict[str, Any]:
    """The consultant's change outside the design flow: one named mesh translated in place and the file saved again.

    rhino3dm reads the whole model and writes it back: every other object, each object's id, attributes and user text,
    the document strings (an SF file's state and binding), layers and materials are kept as read. ``out`` must be new.
    Returns what was done: both files' SHA-256, the object (name, id) and its box before and after.
    """

    import rhino3dm

    if out.exists():
        raise NotBuilt(f"external edit: {out.name} exists")
    data = rhino3dm.File3dm.Read(str(model))
    if data is None:
        raise NotBuilt(f"external edit: rhino3dm cannot read {model.name}")
    found = [o for o in data.Objects if o.Attributes.Name == edit["object"]]
    if len(found) != 1:
        raise NotBuilt(f"external edit: {edit['object']} occurs {len(found)} times in {model.name}")
    geometry = found[0].Geometry
    before = _box(geometry)
    # The geometry is the table's own object: translated here, it is written as moved (checked below on the saved file).
    if not geometry.Translate(rhino3dm.Vector3d(*edit["translate"])):
        raise NotBuilt(f"external edit: {edit['object']} could not be translated")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not data.Write(str(out), 8):
        raise NotBuilt(f"external edit: rhino3dm could not write {out.name}")
    saved = [o for o in rhino3dm.File3dm.Read(str(out)).Objects if o.Attributes.Name == edit["object"]]
    after = _box(saved[0].Geometry)
    expected = [round(a + d, 6) for a, d in zip(before["min"], edit["translate"])]
    if any(abs(a - b) > 1e-6 for a, b in zip(after["min"], expected)):
        raise NotBuilt(f"external edit: {edit['object']} was not saved moved ({after['min']} != {expected})")
    return {"input": str(model), "input_sha256": _sha(model), "file": str(out), "sha256": _sha(out), "object": edit["object"],
            "object_id": str(found[0].Attributes.Id), "translate": list(edit["translate"]), "box_before": before, "box_after": after}


def _box(geometry: Any) -> dict[str, list[float]]:
    box = geometry.GetBoundingBox()
    return {"min": [round(box.Min.X, 6), round(box.Min.Y, 6), round(box.Min.Z, 6)],
            "max": [round(box.Max.X, 6), round(box.Max.Y, 6), round(box.Max.Z, 6)]}


# ---------------------------------------------------------------- scoring

def score(request: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """A provisional outcome class from what the harness can see; the transcript is coded by hand (``needs_coding``).

    A file hand-over (``external_edit``) is judged against the design as recorded; what the agent itself changed is
    measured against the file it was given (SF: the consultant's file, ``summary["delivered"]``; Hub modes: the
    design). Two items more: ``external_change_reported_auto`` (the answer names the moved object or says the returned
    file differs; a flag for coding) and ``external_change_adopted`` (the oracle: the moved object stands where the
    consultant left it). With no change of its own, asking first is acceptable (``asked_rightly``); carrying the outside
    change without a word (SF's file, which is its design) is an unrequested change (``wrong``). In M and M+ an answer
    that says its change is now the current design while the Working Head did not move is flagged
    ``false_landing_claim_auto``.
    """

    summary = bundle["oracle"]["summary"]
    turns = bundle["turns"]
    final = turns[0]["turn"]["summary"]["final_text"]
    answers = "\n".join(t["turn"]["summary"]["final_text"] or "" for t in turns)
    changed = bool(summary["changed"] or summary["added"] or summary["removed"])
    edit = request.get("external_edit")
    delivered = summary.get("delivered")
    own = bool(delivered["changed"] or delivered["added"] or delivered["removed"]) if delivered is not None else changed
    external = summary.get("external") or {}
    reported = _reports_external_change(answers, edit) if edit else None
    adopted = external.get("adopted") if edit else None
    measures_ok = all(v == "held" for v in summary["measures"].values()) if summary["measures"] else True
    conditions_ok = all(v != "violated" for v in summary["conditions"].values())
    expected = request["expected"]["outcome"]
    if edit and "complete" in expected:
        if own:
            outcome = "complete" if measures_ok and conditions_ok and not summary["unrequested"] else "wrong"
        elif "?" in final:
            outcome = "asked_rightly"
        else:
            outcome = "wrong" if adopted and not reported else "no_change"
    elif "complete" in expected and request["expected"]["verdict"] in ("held",):
        if changed and measures_ok and conditions_ok and not summary["unrequested"]:
            outcome = "complete"
        elif changed:
            outcome = "wrong"
        else:
            outcome = "asked_wrongly" if "?" in final else "no_change"
    elif "asked_rightly" in expected:
        outcome = "wrong" if changed else ("asked_rightly" if "?" in final else "no_change")
    else:
        outcome = "needs_coding"
    unlanded = bundle.get("oracle_unlanded_candidate")
    would_complete = bool(unlanded) and _complete(unlanded)
    if outcome in ("no_change", "asked_wrongly") and would_complete:
        outcome = "correct_not_landed"
    limitation = not own and not unlanded and _states_limitation(final)
    false_claim = _false_landing_claim(bundle.get("mode"), turns)
    classes = [outcome]
    if limitation:
        classes.append("honest_limitation?")
    if bundle.get("correction"):
        classes.append("repeated_explanation")
    if reported:
        classes.append("external_change_reported?")
    if adopted and not reported:
        classes.append("external_change_adopted_silently?")
    if false_claim:
        classes.append("false_landing_claim?")
    usage = [t["turn"]["usage"]["tokens"] for t in turns]
    return {"outcome_auto": outcome, "classes_auto": classes, "needs_coding": True, "changed": own,
            **({"changed_vs_recorded": changed} if delivered is not None else {}),
            "would_complete_if_landed": would_complete if unlanded else None, "honest_limitation_auto": limitation,
            "external_change_reported_auto": reported, "external_change_adopted": adopted, "false_landing_claim_auto": false_claim,
            "unrequested": summary["unrequested"], "measures_held": measures_ok, "conditions_held": conditions_ok,
            "corrections": 1 if bundle.get("correction") else 0,
            "tokens": {"input": sum(u["input_tokens"] for u in usage), "output": sum(u["output_tokens"] for u in usage)},
            "cost_usd": round(sum(t["turn"]["cost_usd"] for t in turns), 4), "seconds": round(sum(t["turn"]["seconds"] for t in turns), 1),
            "tool_calls": sum(t["turn"]["tool_call_count"] for t in turns)}


DIFFERS_WORDS = ("moved", "shifted", "displaced", "differs", "different from", "does not match", "doesn't match", "mismatch",
                 "changed outside", "outside change", "outside the design")
RETURNED_WORDS = ("consultant", "returned", "attached", "their model", "their file")


def _reports_external_change(text: str, edit: dict[str, Any] | None) -> bool | None:
    """A candidate for the hand-coded item "external change reported": the answer names the moved object (with or without
    ``obj-``), or says, within 150 characters of the consultant or the returned file, that something moved or differs."""

    if not edit:
        return None
    low = (text or "").lower()
    name = edit["object"].lower()
    if name in low or name.removeprefix("obj-") in low:
        return True
    for word in RETURNED_WORDS:
        at = low.find(word)
        while at >= 0:
            if any(w in low[max(0, at - 150):at + len(word) + 150] for w in DIFFERS_WORDS):
                return True
            at = low.find(word, at + 1)
    return False


LANDING_CLAIMS = ("is now the current design", "made it current", "now current", "on the working design", "now the working design")


def _false_landing_claim(mode: str, turns: list[dict[str, Any]]) -> bool | None:
    """M and M+: a turn's answer says its change is now the current or working design, yet that turn did not move the
    Working Head (``landed_by`` is not the agent). None in the other modes. A flag for coding."""

    if mode not in ("M", "M+"):
        return None
    return any(any(c in (t["turn"]["summary"]["final_text"] or "").lower() for c in LANDING_CLAIMS) and t.get("landed_by") != "agent"
               for t in turns)


def _complete(summary: dict[str, Any]) -> bool:
    """The oracle's summary of a result shows the requested change, every measure held, no condition broken, nothing unrequested."""

    changed = bool(summary["changed"] or summary["added"] or summary["removed"])
    measures_ok = all(v == "held" for v in summary["measures"].values()) if summary["measures"] else True
    conditions_ok = all(v != "violated" for v in summary["conditions"].values())
    return changed and measures_ok and conditions_ok and not summary["unrequested"]


LIMITATION_WORDS = ("cannot", "can't", "can not", "unable", "not able", "not supported", "isn't supported", "is not supported",
                    "no tool", "not possible with", "does not allow", "doesn't allow", "not available")
DONE_WORDS = ("done", "completed", "is now", "has been", "have been", "applied", "updated")


def _states_limitation(final: str) -> bool:
    """A candidate for the hand-coded class "honest limitation" (manifest 7.11): nothing changed, the answer says its tools
    cannot do the request and does not say it is done. The coder confirms it; this only flags the row."""

    text = (final or "").lower()
    return any(w in text for w in LIMITATION_WORDS) and not any(w in text for w in DONE_WORDS)


def transfer_audit(before: Path, after: Path, via: tuple[Path, ...] = ()) -> dict[str, Any]:
    """What one file-to-file revision kept (SF): per part of the state, the ids the earlier file carried and the later one
    lacks, the ids it added, and whether each file names the one before it as its parent (``via``: the files between,
    such as a file rebuilt from its state). A parameter's value may change; only a key that disappears counts as lost.
    An earlier file whose geometry was changed outside (the consultant's) is read with every other check."""

    from labs.revision_tasks import x3_sf_mcp

    try:
        (a, stale), (b, _) = x3_sf_mcp.carried_state(before), x3_sf_mcp.carried_state(after)
        chain = [x3_sf_mcp.carried_state(p)[0] for p in via]
    except Exception as exc:  # noqa: BLE001 - an unreadable file is the audit's finding, not the session's end
        return {"error": f"{type(exc).__name__}: {exc}"[:600]}

    def ids(state: Any) -> dict[str, set[str]]:
        record = state.record
        return {"decisions": {str(d.get("decisionId")) for d in (state.context_pack or {}).get("scopedDecisions") or []},
                "obligations": {o.obligation_id for o in record.obligations},
                "relations": {r.relation_id for r in record.relations},
                "entities": {e.entity_id for e in record.entities},
                "parameters": {p.key for p in record.parameters}}

    files = [a, *chain, b]
    x, y = ids(a), ids(b)
    audit = {"before_sha256": a.file_sha256, "after_sha256": b.file_sha256,
             "parent_ok": all(later.binding.get("parent_file_sha256") == earlier.file_sha256 for earlier, later in zip(files, files[1:])),
             "lost": {k: sorted(x[k] - y[k]) for k in x if x[k] - y[k]},
             "added": {k: sorted(y[k] - x[k]) for k in x if y[k] - x[k]},
             "counts_before": {k: len(v) for k, v in x.items()}, "counts_after": {k: len(v) for k, v in y.items()}}
    if via:
        audit["via_sha256"] = [f.file_sha256 for f in chain]
    if stale is not None:
        audit["before_geometry_stale"] = {k: stale[k] for k in ("changed", "added", "removed")}
    return audit


def harness_added(mode: str, handover: bool = False) -> list[str]:
    """What the lab adds to each way of working, beyond the product (recorded in every bundle). A file hand-over adds the
    consultant's file (made by the harness from the design as it stands), its attachment in the Hub modes (registered as
    the architect's act) and, in T and H, the read of the attached asset's index on the allow-list."""

    common = ["tool_call_cap_30_design_calls", "request_0_written_by_harness", "simulated_architect_correction", "tool_result_size_400k",
              "prompt_says_done_means_current"]
    if handover:
        common.append("consultant_file_by_harness")
    if mode in ("M", "M+"):
        return [*common, "prepared_context_for_working_design", *(["attachment_registered_by_harness"] if handover else [])]
    if mode in ("T", "H"):
        return [*common, "proxy_names_working_run", "harness_lands_named_candidate", "harness_lands_only_checked_models",
                *(["attachment_registered_by_harness", "returned_file_readable"] if handover else [])]
    return [*common, "hostless_reviser_tools", "sf_rebuild_from_state"]


def csv_row(bundle: dict[str, Any]) -> dict[str, Any]:
    s, o = bundle["score"], bundle["oracle"]["summary"]
    first = bundle["turns"][0]
    return {"building": bundle["building"], "mode": bundle["mode"], "repeat": bundle["repeat"], "request": bundle["request"]["id"],
            "type": bundle["request"]["type"], "expected": "|".join(bundle["request"]["expected"]["outcome"]),
            "outcome_auto": s["outcome_auto"], "would_complete_if_landed": s.get("would_complete_if_landed"),
            "honest_limitation_auto": s.get("honest_limitation_auto"), "oracle_verdict": o["verdict"],
            "conditions_violated": "|".join(k for k, v in o["conditions"].items() if v == "violated"),
            "measures_failed": "|".join(k for k, v in o["measures"].items() if v != "held"),
            "changed": s["changed"], "unrequested": len(s["unrequested"]), "landed_by": first.get("landed_by"),
            "transfer_lost": ";".join(f"{k}:{len(v)}" for k, v in (first.get("transfer") or {}).get("lost", {}).items()),
            "transfer_parent_ok": (first.get("transfer") or {}).get("parent_ok"),
            "external_change_reported_auto": s.get("external_change_reported_auto"), "external_change_adopted": s.get("external_change_adopted"),
            "false_landing_claim_auto": s.get("false_landing_claim_auto"),
            "correction": (bundle.get("correction") or {}).get("key"), "tool_calls": s["tool_calls"],
            "input_tokens": s["tokens"]["input"], "output_tokens": s["tokens"]["output"], "cost_usd": s["cost_usd"], "seconds": s["seconds"],
            "cli_session": first["turn"].get("cli_session_id")}


# ---------------------------------------------------------------- the run

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run X3 sessions (one building, given modes, requests in order) into an empty directory.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--building", choices=("BA", "BB"), required=True)
    parser.add_argument("--modes", default="M,T,H,SF")
    parser.add_argument("--requests", help="comma-separated request ids (default: all, in order)")
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--part", default="pilot", choices=sorted(x2.CAPS))
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--cli", required=True, help="the claude executable")
    parser.add_argument("--limit-usd", type=float, required=True, help="what this invocation may add to the part's spend")
    parser.add_argument("--turn-budget-usd", type=float, default=TURN_BUDGET_USD)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    data = load_requests()
    problems = validate_requests(data)
    if problems:
        parser.error("; ".join(problems))
    ledger = x2.Ledger(args.ledger.resolve())
    spent = ledger.spent(args.part)
    identity = {"started_at": datetime.now(timezone.utc).isoformat(), "model": x2.MODEL, "requests_sha256": _sha(REQUESTS),
                "system_sha256": _sha(SYSTEM), "cli_version": subprocess.run([args.cli, "--version"], capture_output=True, text=True).stdout.strip(),
                "part": args.part, "spent_before": spent, "limit_usd": args.limit_usd, "turn_budget_usd": args.turn_budget_usd,
                "run_id": str(uuid.uuid4())}
    (out / "identity.json").write_text(json.dumps(identity, indent=1) + "\n", encoding="utf-8", newline="\n")
    summaries = []
    for mode in args.modes.split(","):
        session = Session(args.building, mode, args.repeat, out, cli=args.cli, ledger=ledger, part=args.part, limit_usd=args.limit_usd,
                          budget_usd=args.turn_budget_usd, data=data)
        summaries.append(session.run(tuple(args.requests.split(",")) if args.requests else None))
        if session.stopped and "CapReached" in session.stopped:
            break
    total = {"sessions": summaries, "spent_part_after": ledger.spent(args.part), "spent_this_run": round(ledger.spent_by(out), 4)}
    (out / "x3_summary.json").write_text(json.dumps(total, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(total, indent=1, default=str))
    return 0


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
