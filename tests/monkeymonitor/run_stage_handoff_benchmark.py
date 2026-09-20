"""Manual #185 accepted-Stage comparison: two pairs, continue/stage then stage/continue.

Public synthetic data only. --prepare-only runs deterministic OCCT/Runtime
preflight without a provider; the normal invocation explicitly starts paid
provider turns. Raw chats, failed turns and Monitor traces remain outside Git.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import site
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.monkeymonitor import run_turn_benchmark as harness
from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app as create_studio
from archflow_studio_api.settings import StudioSettings

PROJECT_ID = "demo-project"
LOCKS = ("mass_x0", "mass_x1", "mass_y0", "mass_y1", "mass_z0", "mass_height")
ENTRANCE_NOTE = (
    "South entrance: retain a centered 1.0 m clear gap, X=1.5..2.5 m. "
    "Two straight walls must lie outside the mass, Y=-0.2..0 m, with thickness 0.2 m "
    "and height 2.4 m above level-ground. Keep the retained hall mass unchanged. "
    "Massing is an occupied-volume placeholder; it does not prove circulation or code compliance."
)
TASK = (
    "This is an isolated design benchmark. Only use connected monkeyhub tools. "
    "Continue the explicitly confirmed massing Stage in the supplied exact context. "
    "Add two wall producers named entrance-west and entrance-east in component portico, "
    "following the retained entrance-condition Reading. Leave the centered gap open to the full wall height. "
    "Preserve every existing entity, field, parameter, lock and binding; do not replace the mass with walls. "
    "Add Reading wall-review with subject_refs naming hall-mass, entrance-west and entrance-east, "
    "recording the entrance condition and the remaining circulation/code review. "
    "Use one semantic proposal, execute with awaitSeconds:60 and verify actual readback. "
    "Never accept or issue a Stage. Give one short result sentence. "
    "Do not use shell or repository files, call other models, or change application settings."
)
# Six substantive reviews replace the previous single short primer. Their
# generated length is measured, not assumed to constitute a universal 'long'
# context. Rejected alternatives need not travel into the final task's pack.
HISTORY = (
    "Audit the massing coordinate convention and reconstruct all six extents from retained facts. "
    "Explain how each bound control changes a specific face, and what its lock will and will not protect. "
    "Identify all consumers and references that a future facade task must preserve.",
    "Read the exact source's top model view. Check its footprint against all four plan controls, "
    "explain the south/front coordinate convention and distinguish the placeholder boundary from a wall face. "
    "Describe where the retained centered entrance belongs and identify any discrepancy between facts and view.",
    "Read the exact source's front model view. Check the 3.0 m mass height and base elevation against "
    "the retained controls. Explain where the 2.4 m walls will end, the remaining 0.6 m above them, "
    "and why the occupied-volume placeholder does not establish a roof, structure, headroom or door lintel.",
    "Review the entrance-condition Reading. Compare a centered opening with west-offset and east-offset "
    "alternatives for this exact four-metre frontage. Work through their wall lengths, symmetry, clear gap "
    "and relation to the mass boundary. Reject the offset alternatives because the retained condition is centered.",
    "Prepare a constructible coordinate schedule for the two wall segments under the retained condition. "
    "Explain start/end points, inward normal, base level, wall thickness and height, and the difference "
    "between their actual solids and the occupied-volume placeholder. Check overlaps and full-height gap.",
    "Review the earlier reasoning as the last massing discussion. Separate retained decisions from "
    "discarded alternatives, list what the next wall task can recover from StateRecord alone, "
    "and describe which affected items need review if the entrance brief later widens from 1.0 to 1.4 m. "
    "Do not change the current 1.0 m condition and do not infer approval of any new design.",
)
LIMITATIONS = [
    "Two counterbalanced pairs are a bounded case study, not a universal cost or latency claim.",
    "The same review prompts produce independent histories; actual lengths and preparation costs are retained.",
    "This fixture locks six existing massing controls and bindings. Whole-object topology, arbitrary future fields and undeclared dependencies are not frozen.",
    "The mass remains an occupied-volume placeholder. Exact wall extents and entrance gap do not prove circulation or building-code compliance.",
    "Upstream review is measured in a disposable clone that accepts the verified wall candidate only to settle a baseline. The measured project keeps its unaccepted wall candidate and its two massing Stages; neither the provider nor any measured arm accepted a wall Stage.",
    "The review lists are derived from declared dependencies and Reading subjects. They are prompts for a person, not validation that the widened entrance is buildable.",
    "No browser timing, account billing, or provider-internal retry count is inferred.",
]


def api(target, path, body=None):
    if isinstance(target, str):
        return harness.request(target, path, body)
    response = target.get(path) if body is None else target.post(path, json=body)
    response.raise_for_status()
    return response.json()


def state_source(target, run_id, stage_ref=None):
    query = {"run": run_id}
    if stage_ref:
        query["sourceStageRef"] = stage_ref
    state = api(target, "/api/state?" + urlencode(query))
    return {"sourceRunId": run_id, "stateDigest": state["stateDigest"],
            **({"sourceStageRef": stage_ref} if stage_ref else {})}


def context(target, source):
    return api(target, "/api/intents/context", {**source, "utterance": TASK, "projectId": PROJECT_ID})


def candidate(target, source, edit=None, *, lock=False):
    path = "/api/proposals/parameter-locks" if lock else "/api/proposals"
    body = {**source, **({"parameterKeys": list(LOCKS), "action": "lock"} if lock else {"semanticEdit": edit})}
    proposal = api(target, path, body)
    submitted = api(target, f"/api/proposals/{proposal['proposalId']}/candidate", {})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = api(target, f"/api/jobs/{submitted['jobId']}")
        if job["status"] not in ("queued", "running"):
            if job["status"] != "succeeded":
                raise RuntimeError(f"Deterministic candidate failed: {job}")
            return api(target, f"/api/candidates/{submitted['candidateId']}")
        time.sleep(0.1)
    raise TimeoutError("Deterministic candidate did not finish in 120 seconds")


def retained_record(repository, run_id):
    fixture = harness.project_fixture()
    run = repository.load_run(run_id)
    records = [repository.load_json(ref) for ref in repository.list_json(
        run=run, destination=fixture.run_records(run_id)) if ref.record_kind == harness.STATE_RECORD]
    if len(records) != 1:
        raise ValueError(f"Expected one exact StateRecord for {run_id}, found {len(records)}")
    return records[0]


def massing_payload():
    fixture = harness.project_fixture()
    payload = deepcopy(fixture.RECORD_PAYLOAD)
    payload["entities"] = [row for row in payload["entities"] if row["schema"] != "Element@1"]
    payload["entities"].extend([
        {"entity_id": "hall-mass", "schema": "Element@1", "parent_id": "portico", "basis_refs": [fixture.EVIDENCE],
         "fields": {"component_id": "portico", "producer": "prism",
                    "references": {"base": {"elevation": "@mass_z0"}},
                    "params": {"height": "@mass_height", "profile": [
                        ["@mass_x0", "@mass_y0"], ["@mass_x1", "@mass_y0"],
                        ["@mass_x1", "@mass_y1"], ["@mass_x0", "@mass_y1"]]}}},
        {"entity_id": "entrance-condition", "schema": "Reading@1",
         "fields": {"subject_refs": ["entity:hall-mass"], "note": ENTRANCE_NOTE}},
    ])
    payload["parameters"] = [{"key": key, "value": value, "unit": "m", "epistemic_status": "declared"}
                             for key, value in zip(LOCKS, (0, 4, 0, 2, 0, 3), strict=True)]
    payload["relations"] = []
    return payload


def wall_edit():
    """Deterministic preflight only; live provider receives TASK, not this edit."""
    entities = []
    for name, x0, x1 in (("entrance-west", 0, 1.5), ("entrance-east", 2.5, 4)):
        entities.append({"entity_id": name, "schema": "Element@1", "parent_id": "portico",
                         "fields": {"component_id": "portico", "producer": "wall",
                                    "references": {"base": {"level": "level-ground"},
                                                   "line": {"from": {"point": [x0, 0]}, "to": {"point": [x1, 0]}, "inward": [0, -1]}},
                                    "params": {"height": 2.4, "thickness": 0.2}}})
    entities.append({"entity_id": "wall-review", "schema": "Reading@1", "fields": {
        "subject_refs": ["entity:hall-mass", "entity:entrance-west", "entity:entrance-east"],
        "note": ENTRANCE_NOTE + " Remaining: circulation and code review."}})
    return {"summary": "Build the two south wall segments with the retained centered gap", "entities": entities}


def geometry_ok(readback, *, walls):
    expected = {"hall-mass": ([0, 0, 0], [4, 2, 3])}
    if walls:
        expected.update({"entrance-west": ([0, -0.2, 0], [1.5, 0, 2.4]),
                         "entrance-east": ([2.5, -0.2, 0], [4, 0, 2.4])})
    rows = readback.get("objects") or []
    by_name = {row.get("producerOp"): row for row in rows}
    if len(rows) != len(by_name) or set(by_name) != set(expected):
        return False
    for name, (minimum, maximum) in expected.items():
        row = by_name[name]
        if row.get("lengthUnit") != "meter" or row.get("upAxis") != "Z-up":
            return False
        bbox = row.get("bbox") or {}
        actual = bbox.get("min", []) + bbox.get("max", [])
        if len(actual) != 6 or any(not isinstance(a, (float, int)) or not math.isfinite(a)
                                   or abs(a - e) > 0.001 for a, e in zip(actual, minimum + maximum)):
            return False
    return bool(readback.get("seatExecutionComplete") and not readback.get("objectReadbackError"))


def authored_checks(before, after):
    def entities(record):
        return {row["entity_id"]: {key: row.get(key) for key in ("schema", "parent_id", "fields", "basis_refs")}
                for row in record["entities"]}
    old, new = entities(before), entities(after)
    review = new.get("wall-review", {}).get("fields", {})
    walls = [new.get(name, {}).get("fields", {}) for name in ("entrance-west", "entrance-east")]
    parameters = {row["key"]: row for row in after["parameters"]}
    return {"existing_entities_unchanged": all(new.get(key) == value for key, value in old.items()),
            "existing_parameters_and_locks_unchanged": all(parameters.get(row["key"]) == row for row in before["parameters"]),
            "all_six_controls_locked": set(LOCKS).issubset({row["key"] for row in after["parameters"] if row.get("lock_authority")}),
            "existing_relations_and_obligations_unchanged": all(row in after.get(key, []) for key in ("relations", "obligations") for row in before.get(key, [])),
            "real_wall_producers": all(row.get("producer") == "wall" for row in walls),
            "wall_review_scoped": set(review.get("subject_refs", [])) == {"entity:hall-mass", "entity:entrance-west", "entity:entrance-east"},
            "wall_review_note_retained": bool(review.get("note"))}


def impact_changes(pack):
    """The derived change summary one ContextPack carries, or nothing."""
    return (pack.get("confirmedStage") or {}).get("changes") or {}


def upstream_impact_checks(before, after):
    """A reopened upstream condition must raise reviews that are actually new.

    Comparing a settled Stage against a pack that already listed the new walls
    proves nothing: the mass would already be affected and wall-review, being
    itself new, would already need review. Every downstream claim here is a
    before/after transition, so the baseline has to be quiet first.
    """
    old, new = impact_changes(before), impact_changes(after)
    summaries = [pack.get("confirmedStage") or {} for pack in (before, after)]
    return {
        "settled_baseline_stage": summaries[0].get("isSource") is True and not (
            old.get("changedRefs") or old.get("needsReviewRefs") or old.get("affectedRefs")),
        "only_the_condition_changed": new.get("changedRefs") == ["entity:entrance-condition"],
        "wall_review_not_itself_edited": "entity:wall-review" not in new.get("changedRefs", []),
        "wall_review_newly_needs_review": ("entity:wall-review" not in old.get("needsReviewRefs", [])
                                           and "entity:wall-review" in new.get("needsReviewRefs", [])),
        "retained_mass_newly_affected": ("entity:hall-mass" not in old.get("affectedRefs", [])
                                         and "entity:hall-mass" in new.get("affectedRefs", [])),
        "no_omitted_summary": not any(row.get("omittedCounts") for row in summaries),
    }


def impact_probe(target, repository, wall_run, stage, output):
    """Reopen the upstream entrance brief above a settled wall Stage.

    The measured project keeps its unaccepted wall candidate and its two
    massing Stages. Only this disposable P036 clone accepts the already
    verified wall candidate, and only so that the condition edit is measured
    against a quiet baseline. Neither the provider nor any measured arm ever
    accepted a wall Stage.
    """
    output.mkdir(parents=True, exist_ok=True)
    measured_head = repository.layout.head.read_bytes()
    started = time.monotonic()
    archive = output / "impact-input.zip"
    harness.write_project_archive(repository, archive)
    clone, _ = harness.restore_project_archive(output / "projects" / PROJECT_ID, archive)
    settings = StudioSettings(project_dir=clone.layout.root, cad_export="occt")
    with TestClient(create_studio(settings)) as client:
        wall_stage = api(client, f"/api/candidates/{wall_run}/accept", {
            "projectId": PROJECT_ID, "expectedHeadStageRef": stage["stageRef"],
            "label": "Disposable clone-only wall Stage"})
    # Explicitly close/reopen so the baseline is read from committed history.
    with TestClient(create_studio(settings)) as client:
        clone_head = clone.layout.head.read_bytes()
        source = state_source(client, wall_run, wall_stage["stageRef"])
        before = context(client, source)
        preparation_ms = round((time.monotonic() - started) * 1000)
        # Only the note moves; subject_refs and every geometry row are retained.
        note = ENTRANCE_NOTE.replace("1.0 m clear gap, X=1.5..2.5 m", "1.4 m clear gap, X=1.3..2.7 m")
        reading = {"entity_id": "entrance-condition", "fields": {"subject_refs": ["entity:hall-mass"], "note": note}}
        change_started = time.monotonic()
        changed = candidate(client, source, {"summary": "Reopen the upstream entrance brief for review only",
                                             "entities": [reading]})
        change_ms = round((time.monotonic() - change_started) * 1000)
        after = context(client, state_source(client, changed["candidateId"], wall_stage["stageRef"]))
        stages = {"impact_clone": len(api(client, "/api/design-history")["stages"]),
                  "measured_project": len(api(target, "/api/design-history")["stages"])}
        checks = upstream_impact_checks(before, after)
        checks.update({
            "unaccepted_source": (after.get("confirmedStage") or {}).get("isSource") is False,
            "unchanged_mass_and_wall_geometry": geometry_ok(changed, walls=True),
            "impact_clone_head_unchanged": clone.layout.head.read_bytes() == clone_head,
            "measured_project_head_unchanged": repository.layout.head.read_bytes() == measured_head,
            "test_wall_stage_only_in_clone": stages == {"impact_clone": 3, "measured_project": 2}})
    return {"checks": checks, "before": before, "after": after, "stage_counts": stages,
            "candidate_id": changed["candidateId"], "impact_clone": {
                "project_dir": str(clone.layout.root), "archive": str(archive),
                "measured_project_dir": str(repository.layout.root),
                "accepted_wall_candidate_id": wall_run, "test_wall_stage_ref": wall_stage["stageRef"],
                "wall_stage_accepted_only_in_this_disposable_clone": True},
            "clone_preparation_ms": preparation_ms, "condition_change_ms": change_ms}


def prepare(output):
    """Real export/lock/accept/reopen preflight, then identical unaccepted copies."""
    output.mkdir(parents=True, exist_ok=True)
    if (output / "prepared.json").exists():
        saved = json.loads((output / "prepared.json").read_text(encoding="utf-8"))
        if saved.get("history_turns") != len(HISTORY):
            raise ValueError("Prepared history plan changed; use a new output directory")
        return saved
    fixture = harness.project_fixture()
    repository = harness.FilesystemProjectRepository.initialize(output / "seed" / PROJECT_ID,
        project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0})
    payload = massing_payload()
    fixture.write_runner_record(repository, payload)
    fixture.write_runner_seats(repository)
    run = repository.create_run(fixture.REFERENCE_RUN_ID)
    fixture.retain_runner_receipt(repository, run, record_payload=payload,
        design_state_digest=fixture.runner_state_digest(repository, run.run_id, payload))
    started = time.monotonic()
    settings = StudioSettings(project_dir=repository.layout.root, cad_export="occt")
    with TestClient(create_studio(settings)) as client:
        # Materialize the exact authored mass through the real runner and CAD.
        mass = candidate(client, state_source(client, run.run_id), {
            "summary": "Retain the reviewed entrance brief and export the synthetic mass",
            "entities": [{"entity_id": "entrance-condition", "fields": payload["entities"][-1]["fields"]}]})
        if not geometry_ok(mass, walls=False):
            raise RuntimeError("Massing readback did not match the six controlled extents")
        model = next(row["modelSource"] for row in mass["artifacts"] if row.get("modelSource"))
        initial = api(client, "/api/design-stages/initialize", {"projectId": PROJECT_ID, "modelSource": model, "label": "Massing baseline"})
        locked = candidate(client, state_source(client, mass["candidateId"], initial["stageRef"]), lock=True)
        if not geometry_ok(locked, walls=False):
            raise RuntimeError("Locking changed the massing geometry")
    archive = output / "input.zip"
    harness.write_project_archive(repository, archive)
    source = harness.source_identity(repository)
    preflight, _ = harness.restore_project_archive(output / "preflight" / PROJECT_ID, archive)
    with TestClient(create_studio(StudioSettings(project_dir=preflight.layout.root, cad_export="occt"))) as client:
        stage = api(client, f"/api/candidates/{locked['candidateId']}/accept", {
            "projectId": PROJECT_ID, "expectedHeadStageRef": initial["stageRef"], "label": "Confirmed massing"})
    # Explicitly close/reopen before reading the accepted context and building walls.
    with TestClient(create_studio(StudioSettings(project_dir=preflight.layout.root, cad_export="occt"))) as client:
        confirmed = context(client, state_source(client, locked["candidateId"], stage["stageRef"]))
        summary = confirmed.get("confirmedStage") or {}
        if summary.get("isSource") is not True or set(summary.get("lockedParameterKeys", [])) != set(LOCKS):
            raise RuntimeError("Cold accepted Stage did not expose all six locked massing controls")
        head_before = preflight.layout.head.read_bytes()
        walls = candidate(client, state_source(client, locked["candidateId"], stage["stageRef"]), wall_edit())
        checks = authored_checks(retained_record(preflight, locked["candidateId"]), retained_record(preflight, walls["candidateId"]))
        checks["all_conditions_in_context"] = confirmed["context"]["coverage"]["omittedConditionCount"] == 0
        checks["actual_wall_geometry"] = geometry_ok(walls, walls=True)
        impact = impact_probe(client, preflight, walls["candidateId"], stage, output / "impact-check")
        checks.update(impact["checks"])
        checks["canonical_head_unchanged"] = preflight.layout.head.read_bytes() == head_before
        checks["no_wall_stage_accepted"] = len(api(client, "/api/design-history")["stages"]) == 2
        harness.write_json(output / "preflight.json", {"checks": checks, "confirmed_context": confirmed, "impact": impact})
        if not all(checks.values()):
            raise RuntimeError(f"Preflight failed: {checks}")
    preparation_ms = round((time.monotonic() - started) * 1000)
    for pair in ("pair-1", "pair-2"):
        for mode in ("continue", "stage"):
            copied, _ = harness.restore_project_archive(output / pair / mode / "projects" / PROJECT_ID, archive)
            if harness.source_identity(copied) != source:
                raise RuntimeError("Cloned retained inputs differ")
    saved = {"source": source, "initial_stage": initial, "locked_candidate_id": locked["candidateId"],
             "orders": [["continue", "stage"], ["stage", "continue"]], "pairs": 2, "history_turns": len(HISTORY),
             "deterministic_preparation_ms": preparation_ms, "live_model_calls": 0,
             "limitations": LIMITATIONS}
    harness.write_json(output / "prepared.json", saved)
    return saved


@contextmanager
def isolated_services(root):
    """Use the existing benchmark's isolated Hub/managed Runtime lifecycle."""
    with ExitStack() as stack:
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"}
        environment.update(APPDATA=str(root / "settings"), LOCALAPPDATA=str(root / "local"), PYTHONUTF8="1")
        user_site = site.getusersitepackages()
        if site.ENABLE_USER_SITE and user_site in sys.path:
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (environment.get("PYTHONPATH"), user_site)))
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        ports = harness.free_ports(3)
        project, runtime = root / "projects" / PROJECT_ID, root / "runtime"
        harness.save_application_settings(runtime, harness.ApplicationSettingsDto(projectDir=str(project),
            referenceRun="run-001", cadExport="occt", studioPort=ports[1], monitorPort=ports[2]))
        app = harness.create_app(harness.HubSettings(runtime_root=runtime, port=ports[0]))
        server = harness.uvicorn.Server(harness.uvicorn.Config(app, host="127.0.0.1", port=ports[0], log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{ports[0]}"
        try:
            deadline = time.monotonic() + 40
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.1)
            if not server.started:
                raise RuntimeError("Isolated Hub did not start")
            opened = api(base, "/api/runtime/projects/open", {"projectId": PROJECT_ID, "projectDir": str(project)})
            api(base, "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), {})
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                apps = api(base, "/api/apps?" + urlencode({"projectDir": str(project)}))
                studio = next(row for row in apps if row["appId"] == "monkeyarch")
                if studio["state"] == "running":
                    break
                if studio["state"] == "error":
                    raise RuntimeError(str(studio.get("error")))
                time.sleep(0.2)
            else:
                raise TimeoutError("Isolated Runtime did not start")
            yield app, base, studio["apiUrl"].rstrip("/"), f"http://127.0.0.1:{ports[2]}", opened
        finally:
            app.state.chats.shutdown()
            server.should_exit = True
            worker.join(45)
            if worker.is_alive():
                raise RuntimeError("Isolated Hub did not finish draining")


def provider_identity(app, session_id):
    session = app.state.chats._sessions[session_id]
    return session.acpSessionId or session.nativeSessionId


def total_metrics(turns):
    """Sum recorded observations only; one unknown counter makes its total unknown."""
    metrics = [row.get("metrics") or {} for row in turns]
    keys = {key for row in metrics for key, value in row.items() if value is None or type(value) in (int, float)}
    return {key: sum(row[key] for row in metrics) if all(type(row.get(key)) in (int, float) for row in metrics) else None
            for key in sorted(keys)}


def provider_turn(base, monitor, session_id, message, output, timeout):
    """Keep every attempted turn; unknown Monitor counters remain unknown."""
    output.mkdir(parents=True, exist_ok=True)
    harness.write_json(output / "request.json", message)
    started = time.monotonic()
    posted = api(base, f"/api/chat/sessions/{session_id}/messages", message)
    turn_id = next(row["id"] for row in reversed(posted["messages"]) if row["role"] == "user")
    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        detail = api(base, f"/api/chat/sessions/{session_id}")
        if detail["status"] != "running":
            break
        if time.monotonic() >= deadline:
            api(base, f"/api/chat/sessions/{session_id}/stop", {})
            timed_out = True
            break
        time.sleep(1)
    if timed_out:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            detail = api(base, f"/api/chat/sessions/{session_id}")
            if detail["status"] != "running":
                break
            time.sleep(0.2)
    harness.write_json(output / "chat.json", detail)
    failures, snapshot = [], None
    for _ in range(5):
        snapshot = harness.monitor_snapshot(monitor, failures)
        if snapshot is not None:
            break
        time.sleep(0.2)
    trace = next((row for row in (snapshot or {}).get("traces", []) if row["turn_id"] == turn_id), None)
    report = {"turn_id": turn_id, "status": detail["status"], "timed_out": timed_out,
              "observed_wall_ms": round((time.monotonic() - started) * 1000), "trace": trace,
              "metrics": harness.measured_metrics(trace) if trace else None, "monitor_read_failures": failures}
    harness.write_json(output / "trace.json", report)
    if timed_out or detail["status"] != "idle" or trace is None:
        raise RuntimeError("Provider turn incomplete; raw chat, diagnostics and failure remain retained")
    return detail, report


def run_arm(args, prepared):
    root, mode = args.output, args.condition
    build_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff_command = ["git", "diff", "HEAD", "--", "apps", "archflow", "monkeyarch", "monkeymonitor", "tests/monkeymonitor"]
    source_diff = subprocess.check_output(diff_command, cwd=ROOT)
    repository = harness.FilesystemProjectRepository.open(root / "projects" / PROJECT_ID)
    if (root / "started.json").exists() or harness.source_identity(repository) != prepared["source"]:
        raise ValueError("This arm was already attempted or changed; use a new prepared output directory")
    harness.write_json(root / "started.json", {"condition": mode, "model": args.model, "provider": args.provider})
    preparation = []
    with isolated_services(root) as (app, base, runtime, monitor, _):
        session = api(base, "/api/chat/sessions", {"projectDir": str(repository.layout.root), "provider": args.provider, "model": args.model})
        source = state_source(runtime, prepared["locked_candidate_id"], prepared["initial_stage"]["stageRef"])
        for index, discussion in enumerate(HISTORY, 1):
            message = {"projectId": PROJECT_ID, "designContext": source, "contextMode": "continue",
                       "content": "Read-only massing review for a later wall task. Only use connected monkeyhub tools. "
                       "Use the exact supplied source and retained facts. Do not propose, execute, accept or issue anything. "
                       "Do not read repository files, use shell tools or call another model. " + discussion +
                       " Write a useful 350-500 word review; do not pad or invent missing facts."}
            detail, report = provider_turn(base, monitor, session["id"], message, root / f"history-{index}", args.timeout)
            preparation.append(report)
            if harness.source_identity(repository) != prepared["source"]:
                raise RuntimeError("Preparation changed the project; arm is invalid")
        old_provider = provider_identity(app, session["id"])
        acceptance_started = time.monotonic()
        stage = api(runtime, f"/api/candidates/{prepared['locked_candidate_id']}/accept", {
            "projectId": PROJECT_ID, "expectedHeadStageRef": prepared["initial_stage"]["stageRef"], "label": "Confirmed massing"})
        acceptance_ms = round((time.monotonic() - acceptance_started) * 1000)
        harness.write_json(root / "accepted-stage.json", stage)
        history_size = {"messages": len(detail["messages"]), "characters": sum(len(row.get("content", "")) for row in detail["messages"]),
                        "provider_history_tokens": None}
        restart_started = time.monotonic()
    # A real Hub/Runtime restart preserves visible chat and old native identity.
    with isolated_services(root) as (app, base, runtime, monitor, opened):
        restart_ms = round((time.monotonic() - restart_started) * 1000)
        source = state_source(runtime, prepared["locked_candidate_id"], stage["stageRef"])
        context_started = time.monotonic()
        pack = context(runtime, source)
        context_ms = round((time.monotonic() - context_started) * 1000)
        harness.write_json(root / "confirmed-context.json", pack)
        if not (pack.get("confirmedStage") or {}).get("isSource"):
            raise RuntimeError("Restart did not read the exact accepted Stage")
        before = retained_record(repository, prepared["locked_candidate_id"])
        head_before = repository.layout.head.read_bytes()
        detail, report = provider_turn(base, monitor, session["id"], {
            "projectId": PROJECT_ID, "designContext": source, "contextMode": mode, "content": TASK}, root / "final", args.timeout)
        runtime_snapshot = api(base, f"/api/runtime/projects/{opened['runtimeId']}")
        run_id = harness.candidate_for_readback(detail, runtime_snapshot)
        readback = api(runtime, f"/api/candidates/{run_id}") if run_id else None
        harness.write_json(root / "candidate.json", readback)
        checks = authored_checks(before, retained_record(repository, run_id)) if run_id else {}
        # Retries and CAD calls are measured metrics; they do not make several
        # generated candidates one. This only claims the readback one succeeded.
        checks["successful_wall_candidate"] = bool(readback and readback.get("status") == "succeeded")
        checks["actual_wall_geometry"] = geometry_ok(readback or {}, walls=True)
        new_provider = provider_identity(app, session["id"])
        checks["provider_continuity"] = bool(old_provider and new_provider) and ((old_provider == new_provider) == (mode == "continue"))
        checks["canonical_head_unchanged"] = repository.layout.head.read_bytes() == head_before
        checks["only_massing_stage_accepted"] = len(api(runtime, "/api/design-history")["stages"]) == 2
        checks["all_conditions_in_context"] = pack["context"]["coverage"]["omittedConditionCount"] == 0
        impact = impact_probe(runtime, repository, run_id, stage, root / "impact-check") if all(checks.values()) else None
        if impact:
            harness.write_json(root / "upstream-impact.json", impact)
            checks.update(impact["checks"])
        checks["source_unchanged_during_arm"] = (source_diff == subprocess.check_output(diff_command, cwd=ROOT)
            and build_revision == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
        result = {"condition": mode, "provider": args.provider, "model": args.model,
                  "build_revision": build_revision,
                  "source_diff_sha256": hashlib.sha256(source_diff).hexdigest(), "input_source": prepared["source"],
                  "prompt_sha256": hashlib.sha256(TASK.encode()).hexdigest(), "history_prompts_sha256": hashlib.sha256(json.dumps(HISTORY).encode()).hexdigest(),
                  "accepted_record_digest": stage["recordDigest"], "checks": checks, "task_success": all(checks.values()),
                  "metrics": report["metrics"], "preparation": preparation, "history_size": history_size,
                  "preparation_metrics": total_metrics(preparation), "all_turn_metrics": total_metrics([*preparation, report]),
                  "stage_acceptance_ms": acceptance_ms, "restart_ms": restart_ms, "context_rebuild_ms": context_ms,
                  "primer_provider_session_id": old_provider, "provider_session_id": new_provider,
                  "project_dir": str(repository.layout.root), "limitations": LIMITATIONS}
        harness.write_json(root / "trace.json", result)
        if not result["task_success"]:
            raise RuntimeError(f"Final candidate did not pass: {checks}")


def summarize(output, prepared):
    pairs = []
    for index, order in enumerate(prepared["orders"], 1):
        reports = {}
        for mode in order:
            path = output / f"pair-{index}" / mode / "trace.json"
            reports[mode] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "task_success": False, "missing_report": str(path), "raw_run_log": str(path.with_name("run.log"))}
        a, b = reports["continue"], reports["stage"]
        equal = {key: key in a and key in b and a[key] == b[key] for key in (
            "build_revision", "source_diff_sha256", "provider", "model", "input_source", "prompt_sha256",
            "history_prompts_sha256", "accepted_record_digest")}
        models = [row.get("metrics", {}).get("reported_models") for row in (a, b)]
        pairs.append({"order": order, "matching_inputs": equal,
                      "comparable_successful_pair": all(equal.values()) and bool(models[0]) and models[0] == models[1]
                      and a["task_success"] and b["task_success"], "conditions": reports})
    result = {"pairs": pairs, "samples_per_condition": 2, "deterministic_preparation_ms": prepared["deterministic_preparation_ms"],
              "limitations": LIMITATIONS}
    harness.write_json(output / "comparison.json", result)
    print(json.dumps({"report": str(output / "comparison.json"),
                      "comparable_pairs": sum(row["comparable_successful_pair"] for row in pairs)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--condition", choices=("continue", "stage"), help=argparse.SUPPRESS)
    parser.add_argument("--prepared", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    paths = json.loads(subprocess.check_output([sys.executable, "tools/workspace.py", "paths"], cwd=ROOT, text=True))
    temp_root = (Path(paths["workspaceRoot"]) / "temp").resolve()
    if not args.output.is_absolute() or not args.output.resolve().is_relative_to(temp_root):
        parser.error(f"--output must be an explicit directory under configured temp root {temp_root}")
    if args.condition:
        run_arm(args, json.loads(args.prepared.read_text(encoding="utf-8")))
        return
    if not args.prepare_only and not args.summarize and not args.model:
        parser.error("Pin --model for the paid two-pair comparison")
    prepared = prepare(args.output)
    if args.prepare_only:
        print(json.dumps({"prepared": str(args.output / "prepared.json"), "live_model_calls": 0, "preflight_passed": True}))
        return
    if not args.summarize:
        if any((args.output / f"pair-{index}" / mode / "started.json").exists()
               for index, order in enumerate(prepared["orders"], 1) for mode in order):
            parser.error("This comparison already attempted provider calls; --summarize it or use a new prepared output directory")
        for index, order in enumerate(prepared["orders"], 1):
            for mode in order:
                root = args.output / f"pair-{index}" / mode
                command = [sys.executable, str(Path(__file__).resolve()), "--output", str(root), "--condition", mode,
                           "--prepared", str(args.output / "prepared.json"), "--model", args.model,
                           "--provider", args.provider, "--timeout", str(args.timeout)]
                with (root / "run.log").open("w", encoding="utf-8") as log:
                    outcome = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if outcome.returncode:
                    harness.write_json(root / "failure.json", {"exit_code": outcome.returncode, "automatic_retries": 0})
    summarize(args.output, prepared)


if __name__ == "__main__":
    main()
