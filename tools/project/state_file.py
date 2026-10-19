"""Start a project from a record, open a state-carrying 3DM as a project, and save its working design back.

    python tools/project/state_file.py init --record record.json --opening opening.json --work-root W --out start.3dm
    python tools/project/state_file.py open --file in.3dm --work-root W
    python tools/project/state_file.py save --project-dir W/<project> --out model.3dm [--parent-file-sha256 S]
                                       [--candidates DIR] [--events hop.jsonl]
    python tools/project/state_file.py parts --file model.3dm
    python tools/project/state_file.py replay (--record record.json --opening opening.json | --start-file F.3dm)
                                         [--events hop-01.jsonl hop-02.jsonl ...]

Each command composes tools and routes MonkeyHub already has (#657, #185). A project is built in the work root
the caller gives: the record and its seats become the project's input, a one-stage workflow is frozen and
opened, and the runner runs and exports it (create, freeze, open stage, run --export). Decisions are recorded
through ``POST /api/decisions`` and files are written through ``POST /api/exports`` with ``withState``, on the
project's own Runtime, started in this process.

``open`` reads the file's state and its seal. What was changed outside MonkeyHub since the seal is handled as
the product does it: rows edited in the file's state are already in the record it builds from; an element whose
objects were all moved by one offset keeps the move, taken into the design as a landed change (a move proposal,
admitted and continued); every other change becomes a condition no check can verify on the parts it touched,
so a revision asks before changing them. The answer lists every outside change, whether it was kept, and
``excerpt``, the words a reviser is given about them.

``save`` writes the working design as a sealed 3DM naming the file it was opened from; with ``--candidates``
it also writes each candidate built since the project was opened, as a plain 3DM, and with ``--events`` the
decisions this hop put into the design, one JSON line each: every change that landed on the working design
(``accept``, with its operator), every design decision recorded (``decision``) and every one withdrawn
(``withdraw``). Nothing else is an event: a change that came back with problems, a question, a limitation or a
stale base changes nothing.

``parts`` answers the seven parts of a file's state, each with its canonical digest and one digest per row:
components, parameters, relations, conditions (the record's obligations and every carried decision that is not a
rejection), rejected options with their reasons, evidence (with the declared option and its rationale), and
dependencies. ``replay`` builds the same parts from
an opening record and its decisions, or from a state file, by applying the events in order through the product's
own operator, and also says, for each change, whether it was made against the content rebuilt so far and made
the content it recorded. Both answer ``parts``; the two are compared part by part. Left out of both, as
bookkeeping: the record's run id, base, predecessor, decision ref, invalidated refs (the last step's history,
not the design), project id and schema, and each carried decision's id and extent (every hop opens a new
project, which records the decisions again under new ids and binds the record to its own run).

Exit code 0 is success, and the answer is one JSON object on stdout; a failure writes its reason to stderr.
Nothing is ever deleted: a project directory or output file that already exists is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tools.dev import source_roots  # noqa: E402

source_roots.put_first(REPO)

from archflow.contracts.canonical import canonical_digest  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository  # noqa: E402
from archflow.state.stage_workflow import DesignPhase, ProjectStage, ProjectStageWorkflow  # noqa: E402
from archflow.state.state_record import (  # noqa: E402
    StateRecord, StateRecordOperator, apply_state_record_operator, part_of_object, resolve_element_bindings,
)
from monkeycad.formats.state_file import BOX_TOLERANCE_M, StateFileError, read_state_file  # noqa: E402
from project_runtime.application.state_files import amount, moved_by, outside_conditions  # noqa: E402

TOOLS = Path(__file__).resolve().parent
STAGE_RUN = "stage-0-001"
WORKFLOW_RUN = "workflow-001"
OPENED = "opened.json"
WAIT_S = 300.0


class StateFileCommandError(RuntimeError):
    """A step of the command failed; the message says which and why."""


# ---- building the project with the existing tools -----------------------------------------------

def _tool(script: str, *args: str) -> str:
    done = subprocess.run([sys.executable, "-X", "utf8", str(TOOLS / script), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        raise StateFileCommandError(f"{script} failed ({done.returncode}): {(done.stderr or done.stdout)[-1500:]}")
    return done.stdout


def _first_json(text: str) -> dict[str, Any]:
    start = text.index("{")
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return json.loads(text[start:index + 1])
    raise StateFileCommandError("a tool answered no JSON object")


def _seats(record: StateRecord) -> dict[str, Any]:
    components = sorted(entity.entity_id for entity in record.entities_of("Component@1"))
    return {"schema": "RunnerSeats@1", "commitment_ref": "commitment:state-file", "branch_id": "runner-v1",
            "provider_identity": {"provider_id": "state-file", "model_id": "deterministic", "provider_version": "1",
                                  "provider_fingerprint": "0" * 64},
            "seats": [{"seat_id": "seat-design", "disciplines": ["structure_support"], "phases": ["design_development"],
                       "owned_component_ids": components, "consumes": [], "reviewer": False}]}


def build_project(record: StateRecord, work_root: Path) -> Path:
    """A new project under ``work_root`` whose stage 0 is ``record``, run and exported by the runner."""

    work_root = Path(work_root)
    project_dir = work_root / record.project_id
    if project_dir.exists():
        raise StateFileCommandError(f"{project_dir} already exists; give a new work root.")
    work_root.mkdir(parents=True, exist_ok=True)
    # The repository writes the project's input record and seats itself.
    FilesystemProjectRepository.initialize(project_dir, project_id=record.project_id,
                                           initial_state={"project_id": record.project_id, "version": 0},
                                           authored_record=record.to_dict(), seat_pack=_seats(record))
    workflow = ProjectStageWorkflow(
        project_id=record.project_id, workflow_id="state-file-workflow",
        stages=(ProjectStage(stage_id="stage-0", stage_index=0, phase=DesignPhase.DESIGN_DEVELOPMENT,
                             required_roles=("geometry-program", "model-inspection"), required_checks=(),
                             close_obligation_id="close-stage-0"),),
        basis_refs=("decision:state-file-workflow",))
    workflow_file = work_root / "workflow.json"
    workflow_file.write_text(json.dumps(workflow.to_dict(), indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    frozen = _first_json(_tool("freeze_project_stage_workflow.py", "--project", str(project_dir), "--run", WORKFLOW_RUN,
                               "--workflow", str(workflow_file), "--create-run"))
    opened = _first_json(_tool("open_stage_run.py", "--project", str(project_dir), "--workflow-ref", frozen["workflow_ref"],
                               "--stage-index", "0", "--run", STAGE_RUN))
    envelope = opened.get("stage_envelope_ref") or opened.get("envelope_ref")
    if not envelope:
        raise StateFileCommandError(f"open_stage_run.py named no stage envelope: {sorted(opened)}")
    _tool("run_project.py", "--project", str(project_dir), "--run", STAGE_RUN, "--workflow-ref", frozen["workflow_ref"],
          "--stage-envelope-ref", envelope, "--export")
    return project_dir


# ---- the project's own Runtime, in this process -------------------------------------------------

@contextmanager
def runtime(project_dir: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient
    from project_runtime.main import create_app
    from project_runtime.settings import StudioSettings

    app = create_app(StudioSettings(cad_export="occt", project_dir=Path(project_dir),
                                    cache_dir=Path(project_dir).parent / "cache"))
    with TestClient(app) as client:
        yield client


def _call(client, method: str, path: str, body: Any = None, *, params: Mapping[str, str] | None = None) -> dict:
    response = client.request(method, path, json=body, params=params)
    if response.status_code >= 400:
        raise StateFileCommandError(f"{method} {path} answered {response.status_code}: {response.text[:800]}")
    return response.json()


def _wait_job(client, job_id: str) -> dict:
    deadline = time.monotonic() + WAIT_S
    while True:
        job = _call(client, "GET", f"/api/jobs/{job_id}")
        if job["status"] in ("succeeded", "failed"):
            return job
        if time.monotonic() > deadline:
            raise StateFileCommandError(f"job {job_id} did not finish in {WAIT_S:.0f} s")
        time.sleep(0.2)


def _head(client) -> tuple[str, str, str | None]:
    """The working design's run and state, and the position's revision; before any Continue, the stage run."""

    source = _call(client, "GET", "/api/working-source")
    head = source.get("head") or {}
    run_id = head.get("runId") or STAGE_RUN
    digest = head.get("stateDigest") or _call(client, "GET", "/api/state", params={"run": run_id})["stateDigest"]
    return run_id, digest, source.get("revisionSha256")


def _decision_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A carried decision in the product's rows, from a file's rows or from opening rows (text, target_ref)."""

    if "rawLanguage" in row:
        return {key: row.get(key) for key in ("disposition", "targetRef", "rawLanguage", "strength")}
    return {"disposition": row["disposition"], "targetRef": row["target_ref"], "rawLanguage": row["text"], "strength": "hard"}


def record_decisions(client, project_id: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    from project_runtime.application.state_files import decision_request

    run_id, digest, _ = _head(client)
    return [_call(client, "POST", "/api/decisions", decision_request(_decision_row(row), project_id=project_id,
                                                                    run_id=run_id, state_digest=digest))
            for row in rows]


def export_model(client, run_id: str, state_digest: str, out: Path, *, with_state: bool,
                 parent: str | None = None) -> dict[str, Any]:
    out = Path(out)
    if out.exists():
        raise StateFileCommandError(f"{out} already exists.")
    body: dict[str, Any] = {"targetFormat": "3dm", "projectRevision": {"runId": run_id, "stateDigest": state_digest}}
    if with_state:
        body["withState"] = True
        if parent is not None:
            body["parentFileSha256"] = parent
    started = _call(client, "POST", "/api/exports", body)
    deadline = time.monotonic() + WAIT_S
    while (report := _call(client, "GET", f"/api/exports/{started['exportId']}"))["status"] in ("queued", "running"):
        if time.monotonic() > deadline:
            raise StateFileCommandError(f"export {started['exportId']} did not finish")
        time.sleep(0.1)
    if report["status"] != "succeeded":
        raise StateFileCommandError(f"export of {run_id} failed: {report.get('failureReason')}")
    data = client.get(f"/api/exports/{started['exportId']}/bytes").content
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"file": str(out), "sha256": hashlib.sha256(data).hexdigest(), "runId": run_id, "stateDigest": state_digest}


def _land_as_person(client, project_id: str, run_id: str, label: str) -> None:
    """Admit a built candidate and continue the working design onto it, as the person opening the file."""

    _call(client, "POST", "/api/admissions", {"projectId": project_id, "task": {"kind": "ui"},
                                               "results": [{"runId": run_id, "outcome": "admitted", "label": label[:240],
                                                            "summary": label}]})
    _, _, revision = _head(client)
    _call(client, "PUT", "/api/working-draft", {"projectId": project_id, "runId": run_id, "baseRevisionSha256": revision})


# ---- what changed outside MonkeyHub ---------------------------------------------------------------

def outside_plan(record: StateRecord, found) -> tuple[list[dict[str, Any]], list[Any]]:
    # Each move to keep also names its findings, so a refused one becomes a condition like any other change.
    """(moves to keep by element, the other changes) of a file's differences from its seal."""

    elements = resolve_element_bindings(record)
    objects_of: dict[str, list[str]] = {}
    for row in found.objects:
        element = part_of_object(row.name, elements)
        if element is not None:
            objects_of.setdefault(element, []).append(row.name)
    moved: dict[str, list[Any]] = {}
    others = []
    for finding in found.out_of_band:
        if finding.kind == "object_moved":
            element = part_of_object(finding.ref.removeprefix("object:"), elements)
            offset = moved_by(finding.detail)
            if element is not None and offset is not None:
                moved.setdefault(element, []).append((finding, offset))
                continue
        others.append(finding)
    keep = []
    for element, rows in sorted(moved.items()):
        offsets = [offset for _, offset in rows]
        whole = sorted(name for name in objects_of.get(element, ())) == sorted(f.ref.removeprefix("object:") for f, _ in rows)
        same = all(max(abs(a - b) for a, b in zip(offset, offsets[0])) <= BOX_TOLERANCE_M for offset in offsets)
        if whole and same:
            keep.append({"element": element, "offset": offsets[0], "objects": [f.ref for f, _ in rows],
                         "findings": [f for f, _ in rows]})
        else:
            others.extend(f for f, _ in rows)
    return keep, others


def _keep_move(client, project_id: str, move: Mapping[str, Any]) -> str:
    """Take one outside move into the design: a move proposal of its element, built, admitted and continued."""

    run_id, digest, _ = _head(client)
    dx, dy, dz = move["offset"]
    # The file is Z-up (x east, y north, z up); the modeling frame is Y-up with plan (x, z).
    proposal = _call(client, "POST", "/api/proposals/transform", {
        "projectId": project_id, "stateDigest": digest, "sourceRunId": run_id, "elementId": move["element"],
        "kind": "move", "translation": [dx, dz, dy]})
    started = _call(client, "POST", f"/api/proposals/{proposal['proposalId']}/candidate")
    job = _wait_job(client, started["jobId"])
    if job["status"] != "succeeded":
        raise StateFileCommandError(f"the move of {move['element']} did not build: {job.get('error')}")
    _land_as_person(client, project_id, started["candidateId"], _move_words(move))
    return started["candidateId"]


def _move_words(move: Mapping[str, Any]) -> str:
    return f"Outside MonkeyHub: {move['element']} was moved by {amount(move['offset'])}; kept in the design."


def _refusal(reason: str) -> str:
    """A refused move's reason in its route's own words, without the HTTP wrapping."""

    try:
        return str(json.loads(reason[reason.index("{"):])["detail"])[:240]
    except (ValueError, KeyError, TypeError):
        return reason[:240]


def _finding_words(finding) -> str:
    if finding.kind == "object_moved":
        offset = moved_by(finding.detail)
        return f"{finding.ref.removeprefix('object:')} was moved by " + (
            amount(offset) if offset is not None else f"{float(finding.detail.get('moved_m', 0.0)):.3f} m")
    return {"object_added": f"{finding.ref} was added", "object_removed": f"{finding.ref} was removed",
            "object_duplicate": f"{finding.ref} was duplicated", "row_added": f"{finding.ref} was added in the record",
            "row_removed": f"{finding.ref} was removed from the record", "row_changed": f"{finding.ref} was changed in the record",
            "decisions_changed": "the decisions were changed", "unsealed": "the file carries no seal"}.get(finding.kind, finding.kind)


def excerpt(kept: Sequence[Mapping[str, Any]], others: Sequence[Any], conditions: Sequence[Any],
            refused: Sequence[Mapping[str, Any]] = ()) -> str:
    """What a reviser is told about the changes made outside MonkeyHub, in words."""

    if not kept and not others:
        return ""
    lines = ["Changes made outside MonkeyHub since this file was last saved by it. Name each one, with its amount, "
             "in your answer:"]
    lines += [f"- {_move_words(move)}" for move in kept]
    lines += [f"- {_move_words(move).removesuffix(' kept in the design.').rstrip(';')}. It could not be kept as it "
              f"stands ({_refusal(move['reason'])}); the design still has it where it was." for move in refused]
    moved_refused = {id(finding) for move in refused for finding in move["findings"]}
    for finding in others:
        if id(finding) in moved_refused:
            continue
        if finding.kind.startswith("row_") or finding.kind == "decisions_changed":
            lines.append(f"- {_finding_words(finding)}; the design starts from the record as found.")
        elif finding.kind == "unsealed":
            lines.append("- The file carries no seal, so what changed in it cannot be told.")
        else:
            lines.append(f"- {_finding_words(finding)}; the design record does not have this change.")
    asking = [row for row in conditions if row.status.value in ("open", "blocked")]
    if asking:
        lines.append("Ask the architect before changing what these touched: "
                     + ", ".join(sorted({ref for row in asking for ref in row.subject_refs})) + ".")
    return "\n".join(lines)


# ---- the commands -------------------------------------------------------------------------------

def _runs(project_dir: Path) -> list[str]:
    return sorted(path.name for path in (Path(project_dir) / "runs").iterdir() if path.is_dir())


def _write_opened(work_root: Path, project_dir: Path, file_sha256: str | None,
                  decisions: Sequence[Mapping[str, Any]] = ()) -> None:
    (Path(work_root) / OPENED).write_text(json.dumps({"projectDir": str(project_dir), "runs": _runs(project_dir),
                                                      "decisions": list(decisions), "fileSha256": file_sha256},
                                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def _decisions(client) -> list[dict[str, Any]]:
    """The project's active design decisions, each with its id."""

    from project_runtime.application.state_files import file_decisions
    from project_runtime.binding import bound_project

    return file_decisions(bound_project(client.app.state))


# ---- the state's parts, and the history that rebuilds them ---------------------------------------

PARTS = ("components", "parameters", "relations", "conditions", "rejected", "evidence", "dependencies")
_REJECTING = ("reject", "avoid")


def _rows(rows: Sequence[Mapping[str, Any]], key) -> dict[str, str]:
    """One canonical digest per row, under the row's own name (a repeated name is numbered)."""

    named: dict[str, str] = {}
    for row in rows:
        name = base = str(key(row))
        count = 1
        while name in named:
            count += 1
            name = f"{base}#{count}"
        named[name] = canonical_digest(dict(row))
    return named


def state_parts(record: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The seven parts of a state, each with its canonical digest and one digest per row."""

    built = StateRecord.from_dict(dict(record))
    record = built.to_dict()  # the rows as the product writes them, whoever wrote this copy
    carried = [_decision_row(row) for row in decisions]
    decided = lambda row: f"{row['disposition']}:{row['targetRef']}:{canonical_digest(row)[:12]}"  # noqa: E731
    edges = [{"upstream": edge.upstream_ref, "downstream": edge.downstream_ref, "relation": edge.relation,
              "source": edge.source_ref, "effect": edge.effect.value} for edge in built.dependency_edges()]
    rows = {
        "components": _rows(record.get("entities") or (), lambda row: row["entity_id"]),
        "parameters": _rows(record.get("parameters") or (), lambda row: row["key"]),
        "relations": _rows(record.get("relations") or (), lambda row: row["relation_id"]),
        "conditions": {**_rows(record.get("obligations") or (), lambda row: f"obligation:{row['obligation_id']}"),
                       **_rows([row for row in carried if row["disposition"] not in _REJECTING], decided)},
        "rejected": _rows([row for row in carried if row["disposition"] in _REJECTING], decided),
        "evidence": {**_rows([{"evidence_refs": sorted(record.get("evidence_refs") or ()),
                               "basis_refs": sorted(record.get("basis_refs") or ())}], lambda row: "record"),
                     **_rows([{"option": record.get("option") or {}}], lambda row: "option")},
        "dependencies": _rows(edges, lambda row: f"{row['upstream']}>{row['downstream']}:{row['relation']}"),
    }
    return {name: {"sha256": canonical_digest(rows[name]), "rows": rows[name]} for name in PARTS}


def _bound(record: StateRecord) -> StateRecord:
    """A record that can be cited: one without a base (an authored record) is bound to a stand-in run."""

    if record.base is not None:
        return record
    from archflow.project.refs import ProjectVersionRef, RunRef

    return record.bound_to(RunRef(record.project_id, "replay", ProjectVersionRef(record.project_id, 0, None)))


def replay(start_record: Mapping[str, Any], start_decisions: Sequence[Mapping[str, Any]],
           events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The parts the history rebuilds: each accepted change applied in order by the product's operator.

    A change is applied to the design content the history has rebuilt so far. Its run and base are bookkeeping
    (every hop is opened as a new project), so only the content is compared: each step says whether the content
    it was made against, and the content it made, are the ones rebuilt here.
    """

    record = StateRecord.from_dict(dict(start_record))
    decisions = [_decision_row(row) for row in start_decisions]
    steps = []
    for number, event in enumerate(events, 1):
        kind = event.get("kind")
        if kind == "accept":
            record = _bound(record)
            operator = StateRecordOperator.from_dict(event["operator"])
            step = {"event": number, "kind": kind, "runId": event.get("runId"),
                    "baseMatches": operator.base_record_digest == record.digest}
            try:
                record = apply_state_record_operator(record, replace(operator, base_record_digest=record.digest,
                                                                     base_state_digest=record.state_digest))
            except Exception as exc:  # the product refuses the change on this content: the history does not rebuild
                raise StateFileCommandError(f"event {number} ({event.get('runId')}) does not apply: {exc}") from exc
            steps.append({**step, "resultMatches": record.digest == event.get("resultRecordDigest")})
        elif kind == "decision":
            decisions.append(_decision_row(event["row"]))
            steps.append({"event": number, "kind": kind})
        elif kind == "withdraw":
            row = _decision_row(event["row"])
            if row not in decisions:
                raise StateFileCommandError(f"event {number} withdraws a decision the history does not have")
            decisions.remove(row)
            steps.append({"event": number, "kind": kind})
        else:
            raise StateFileCommandError(f"event {number} is not an accept, a decision or a withdrawal: {kind!r}")
    return {"parts": state_parts(record.to_dict(), decisions), "steps": steps}


def _hop_events(client, opened: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What this hop put into the design: changes landed on the working design, and decisions recorded or withdrawn."""

    from project_runtime.binding import bound_project

    binding = bound_project(client.app.state)
    before = set(opened.get("runs") or ())
    lineage = (_call(client, "GET", "/api/working-source").get("head") or {}).get("lineage") or ()
    events = []
    for run_id in reversed(lineage):  # oldest first
        delta = None if run_id in before else binding.candidate_delta(run_id)
        if delta is not None:
            events.append({"kind": "accept", "runId": run_id, "summary": delta.get("request"),
                           "operator": delta["operator"], "resultRecordDigest": delta.get("result_record_digest")})
    now = _decisions(client)
    active = {row["decisionId"] for row in now}
    known = {row["decisionId"] for row in opened.get("decisions") or ()}
    events += [{"kind": "withdraw", "row": row} for row in opened.get("decisions") or () if row["decisionId"] not in active]
    events += [{"kind": "decision", "row": row} for row in now if row["decisionId"] not in known]
    return events


def init(record_file: Path, opening_file: Path, work_root: Path, out: Path) -> dict[str, Any]:
    record = StateRecord.from_dict(json.loads(Path(record_file).read_text(encoding="utf-8")))
    opening = json.loads(Path(opening_file).read_text(encoding="utf-8"))
    rows = opening.get("decisions", []) if isinstance(opening, dict) else opening
    project_dir = build_project(record, work_root)
    with runtime(project_dir) as client:
        recorded = record_decisions(client, record.project_id, rows)
        run_id, digest, _ = _head(client)
        saved = export_model(client, run_id, digest, out, with_state=True)
        decisions = _decisions(client)
    _write_opened(work_root, project_dir, saved["sha256"], decisions)
    return {"projectDir": str(project_dir), "decisions": len(recorded), **saved}


def open_file(file: Path, work_root: Path) -> dict[str, Any]:

    try:
        found = read_state_file(Path(file))
    except StateFileError as exc:
        raise StateFileCommandError(f"{file} is not a state file MonkeyHub can open: {exc}") from exc
    record = StateRecord.from_dict(found.record)
    kept, others = outside_plan(record, found)
    refused: list[dict[str, Any]] = []
    root = Path(work_root)
    while True:
        # A move the design cannot take as it stands (it would tear a declared relation) is not kept: it
        # becomes a condition, which lives in the record, so the project is built again with it.
        unkept = [*others, *(finding for move in refused for finding in move["findings"])]
        conditions = outside_conditions(record, unkept, found.decisions, file_sha256=found.file_sha256, kept=kept)
        project_dir = build_project(replace(record, obligations=(*record.obligations, *conditions)), root)
        landed, failed = [], []
        with runtime(project_dir) as client:
            recorded = record_decisions(client, record.project_id, found.decisions)
            for move in kept:
                try:
                    landed.append({**move, "runId": _keep_move(client, record.project_id, move)})
                except StateFileCommandError as exc:
                    failed.append({**move, "reason": str(exc)[:600]})
            run_id, digest, _ = _head(client)
            decisions = _decisions(client)
        if not failed:
            break
        if refused:
            raise StateFileCommandError("a move kept in the first build was refused when the project was built again: "
                                        + "; ".join(row["reason"] for row in failed))
        refused = failed
        dropped = {row["element"] for row in failed}
        kept = [move for move in kept if move["element"] not in dropped]
        root = root / "open-again"
    _write_opened(project_dir.parent, project_dir, found.file_sha256, decisions)
    return {"projectDir": str(project_dir), "fileSha256": found.file_sha256, "sealed": found.binding is not None,
            "workingRunId": run_id, "stateDigest": digest, "decisions": len(recorded),
            "outside": [{"kind": f.kind, "ref": f.ref, "detail": f.detail} for f in found.out_of_band],
            "kept": [{"element": row["element"], "offset": list(row["offset"]), "runId": row["runId"]} for row in landed],
            "notKept": [{"element": row["element"], "offset": list(row["offset"]), "reason": row["reason"]} for row in refused],
            "conditions": [row.to_dict() for row in conditions if row.status.value in ("open", "blocked")],
            "outsideRows": [row.to_dict() for row in conditions], "reencoded": list(found.reencoded),
            "excerpt": excerpt(landed, unkept, conditions, refused)}


def save(project_dir: Path, out: Path, parent: str | None, candidates: Path | None,
         events: Path | None = None) -> dict[str, Any]:
    project_dir = Path(project_dir)
    opened_file = project_dir.parent / OPENED
    opened = json.loads(opened_file.read_text(encoding="utf-8")) if opened_file.is_file() else {}
    if parent is None:
        parent = opened.get("fileSha256")
    with runtime(project_dir) as client:
        run_id, digest, _ = _head(client)
        saved = export_model(client, run_id, digest, out, with_state=True, parent=parent)
        written = []
        if candidates is not None:
            from project_runtime.binding import bound_project

            binding = bound_project(client.app.state)
            before = set(opened.get("runs") or ())
            for candidate in [run for run in _runs(project_dir) if run not in before]:
                if binding.candidate_delta(candidate) is None:
                    continue  # not a candidate: an export, a decision log, an admission record
                state = client.get("/api/state", params={"run": candidate})
                if state.status_code != 200:
                    written.append({"runId": candidate, "unavailable": state.text[:400]})
                    continue
                try:
                    written.append(export_model(client, candidate, state.json()["stateDigest"],
                                                Path(candidates) / f"{candidate}.3dm", with_state=False))
                except StateFileCommandError as exc:
                    written.append({"runId": candidate, "unavailable": str(exc)[:400]})
        hop = _hop_events(client, opened) if events is not None else None
    if hop is not None:
        if Path(events).exists():
            raise StateFileCommandError(f"{events} already exists.")
        Path(events).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in hop), encoding="utf-8",
                                newline="\n")
    return {**saved, "parentFileSha256": parent, "candidates": written,
            **({} if hop is None else {"events": str(events), "eventCount": len(hop)})}


def replay_command(args) -> dict[str, Any]:
    if args.start_file:
        found = read_state_file(Path(args.start_file))
        record, decisions = found.record, found.decisions
    elif args.record and args.opening:
        record = json.loads(Path(args.record).read_text(encoding="utf-8"))
        opening = json.loads(Path(args.opening).read_text(encoding="utf-8"))
        decisions = opening.get("decisions", []) if isinstance(opening, dict) else opening
    else:
        raise StateFileCommandError("replay starts from --record with --opening, or from --start-file")
    events = [json.loads(line) for path in args.events for line in Path(path).read_text(encoding="utf-8").splitlines()
              if line.strip()]
    return replay(record, decisions, events)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    started = commands.add_parser("init", help="start a project from a record and its opening decisions")
    started.add_argument("--record", required=True)
    started.add_argument("--opening", required=True)
    started.add_argument("--work-root", required=True)
    started.add_argument("--out", required=True)
    opened = commands.add_parser("open", help="open a state-carrying 3DM as a project")
    opened.add_argument("--file", required=True)
    opened.add_argument("--work-root", required=True)
    saved = commands.add_parser("save", help="save the working design as a sealed 3DM")
    saved.add_argument("--project-dir", required=True)
    saved.add_argument("--out", required=True)
    saved.add_argument("--parent-file-sha256")
    saved.add_argument("--candidates")
    saved.add_argument("--events")
    parted = commands.add_parser("parts", help="the seven parts of a state file, each with its digests")
    parted.add_argument("--file", required=True)
    replayed = commands.add_parser("replay", help="the parts an opening record and its events rebuild")
    replayed.add_argument("--record")
    replayed.add_argument("--opening")
    replayed.add_argument("--start-file")
    replayed.add_argument("--events", nargs="*", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            answer = init(Path(args.record), Path(args.opening), Path(args.work_root), Path(args.out))
        elif args.command == "open":
            answer = open_file(Path(args.file), Path(args.work_root))
        elif args.command == "save":
            answer = save(Path(args.project_dir), Path(args.out), args.parent_file_sha256,
                          None if args.candidates is None else Path(args.candidates),
                          None if args.events is None else Path(args.events))
        elif args.command == "parts":
            found = read_state_file(Path(args.file))
            answer = {"parts": state_parts(found.record, found.decisions)}
        else:
            answer = replay_command(args)
    except (StateFileCommandError, StateFileError, OSError, ValueError, KeyError) as exc:
        print(f"state_file {args.command}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(answer, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
