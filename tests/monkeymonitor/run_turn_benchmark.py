"""Run an explicitly requested real CLI benchmark in disposable Hub/Studio services.

Uses the installed provider authentication. No user project/service/configuration
is altered. Results are the same Monitor traces, with their fixture/build context.
Invoke manually; this paid/provider-dependent measurement is not a CI threshold.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import site
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "apps/monkeyhub/api", ROOT / "apps/monkeyhub/api/tests", ROOT / "apps/archflow-studio/api"):
    sys.path.insert(0, str(folder))

import uvicorn
from test_monkeyhub_lifecycle import free_ports, project_fixture
from monkeyhub_api.main import HubSettings, create_app
from archflow_studio_api.settings import save_application_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto
from archflow.project.archive import archive_manifest, restore_project_archive, write_project_archive
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.refs import RunRef
from archflow.project.record_kinds import STATE_RECORD
from archflow.state.state_record import StateRecord


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def source_identity(repository):
    """Compare the retained inputs, not just HEAD (which candidates do not move)."""
    return archive_manifest(repository)["transfer"]


def measured_metrics(trace):
    """Project existing Monitor observations; missing counters remain unknown."""
    summary, spans = trace["summary"], trace["spans"]
    return {
        "wall_ms": summary["elapsed_ms"],
        **trace["usage"]["tokens"],
        "model_calls": summary["model_rounds"],
        "agent_activity_intervals": summary["provider_rounds"],
        "tool_calls": summary["tool_rounds"],
        "cad_builds": sum(row["phase"] == "geometry_build" for row in spans),
        "observed_failed_tools": sum(row["phase"] == "tool_call" and row["status"] == "failed" for row in spans),
        "observed_retry_spans": sum((row["details"].get("retry_attempt") or 0) > 0 for row in spans),
        "provider_internal_retries": None,
        "reported_models": sorted({row["model"] for row in spans if row.get("model_call") and row.get("model")}),
    }


def paired_benchmark(args, config):
    """Two retained copies, two real histories, one pinned request and build."""
    if not args.model:
        raise ValueError("A paired measurement requires --model")
    args.output.mkdir(parents=True, exist_ok=True)
    prepared = args.output / "prepared.json"
    fixture = project_fixture()
    if not prepared.exists():
        repository, _ = fixture.make_project(args.output / "seed")
        archive = args.output / "input.zip"
        write_project_archive(repository, archive)
        identity = source_identity(repository)
        for condition in ("continue", "project"):
            copied, _ = restore_project_archive(args.output / condition / "projects" / fixture.PROJECT_ID, archive)
            if source_identity(copied) != identity:
                raise ValueError("Restored benchmark inputs differ")
        write_json(prepared, {"source": identity, "scenario": args.scenario, "model": args.model,
                              "provider": config["provider"], "order": args.order})
    saved = json.loads(prepared.read_text(encoding="utf-8"))
    if any(saved[key] != value for key, value in (("scenario", args.scenario), ("model", args.model), ("order", args.order))):
        raise ValueError("Use a new output directory for a different scenario/model/order")
    # Refuse a used pair; never silently reuse candidate-populated projects.
    for condition in ("continue", "project"):
        repository = FilesystemProjectRepository.open(args.output / condition / "projects" / fixture.PROJECT_ID)
        if source_identity(repository) != saved["source"] or (args.output / condition / "trace.json").exists():
            raise ValueError("This pair has already run or its input changed; use a new output directory")
    if args.prepare_only:
        print(json.dumps({"prepared": str(prepared), "identical_retained_inputs": True, "live_model_calls": 0}))
        return
    for condition in args.order.split(","):
        command = [sys.executable, str(Path(__file__).resolve()), "--scenario", args.scenario,
                   "--model", args.model, "--timeout", str(args.timeout), "--no-preview",
                   "--condition", condition, "--output", str(args.output / condition),
                   "--retained-root", str(args.output / condition)]
        # Continue to the other arm even if one fails; failures are measurements.
        result = subprocess.run(command, cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode and not (args.output / condition / "trace.json").exists():
            raise RuntimeError(f"{condition} failed before exporting a trace (exit {result.returncode})")
    reports = {name: json.loads((args.output / name / "trace.json").read_text(encoding="utf-8"))
               for name in ("continue", "project")}
    a, b = reports.values()
    equal = {key: a["benchmark"][key] == b["benchmark"][key] for key in
             ("build_revision", "source_diff_sha256", "provider", "model", "scenario", "prompt_sha256", "input_source")}
    models_known_equal = bool(a["metrics"]["reported_models"]) and a["metrics"]["reported_models"] == b["metrics"]["reported_models"]
    success = all(row["task_success"] for row in reports.values())
    report = {"matching_inputs": equal, "reported_models_match": models_known_equal,
              "both_tasks_succeeded": success,
              "comparable_successful_pair": all(equal.values()) and models_known_equal and success,
              "order": saved["order"], "samples_per_condition": 1,
              "conditions": {name: {"metrics": row["metrics"], "task_success": row["task_success"],
                                    "trace": str(args.output / name / "trace.json"),
                                    "project_dir": row["project_dir"]} for name, row in reports.items()},
              "limitations": ["Synthetic two-object project; not architectural design-quality evidence.",
                              "Histories share a primer request but are generated independently; retained transcripts record any differences.",
                              "No browser preview measurement. Provider internal retries and account billing are unknown.",
                              "One ordered pair cannot establish causality or a latency distribution."]}
    write_json(args.output / "comparison.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


def request(base, path, body=None):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response)


def expected_geometry(readback, scenario):
    if not readback or not readback.get("objects"):
        return False
    base_height = 0.8 if scenario == "assembly-edit" else 0.6
    top = 1.3 if scenario == "assembly-edit" else 1.1 if scenario == "incremental-edit" else 0.9
    bounds = {"portico-base": ([0, 0, 0], [4, 2, base_height]),
              "portico-cornice": ([0, 0, base_height], [4, 2, top])}
    if scenario == "simple-create":
        bounds["monitor-benchmark-block"] = ([12, 0, 0], [16, 3, 2.5])
    objects = {row.get("producerOp"): row for row in readback["objects"]}
    if set(objects) != set(bounds) or len(objects) != len(readback["objects"]):
        return False
    for name, (minimum, maximum) in bounds.items():
        row = objects[name]
        if row.get("lengthUnit") != "meter" or row.get("upAxis") != "Z-up" or not row.get("bbox"):
            return False
        coordinates = row["bbox"]["min"] + row["bbox"]["max"]
        if len(coordinates) != 6:
            return False
        for actual, expected in zip(coordinates, minimum + maximum, strict=True):
            if not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual - expected) > 0.001:
                return False
    return True


def expected_authored_fields(repository, candidate, scenario, fixture):
    """Geometry alone cannot prove the support/keep conditions survived."""
    if not candidate or scenario == "simple-create":
        return None
    run = RunRef(fixture.PROJECT_ID, candidate, repository.read_head())
    records = [repository.load_json(ref) for ref in repository.list_json(
        run=run, destination=fixture.run_records(candidate)) if ref.record_kind == STATE_RECORD]
    if len(records) != 1:
        return False
    actual = records[0]
    expected = StateRecord.from_dict(fixture.RECORD_PAYLOAD).to_dict()
    for row in expected["entities"]:
        if row["entity_id"] == "portico-cornice":
            row["fields"]["params"]["height"] = 0.5
        if row["entity_id"] == "portico-base" and scenario == "assembly-edit":
            row["fields"]["params"]["height"] = 0.8
    # New provenance may be appended. Authored field values, identity, parent,
    # controls and relationships must stay exactly as the task requested.
    def entities(record):
        return {row["entity_id"]: {key: row.get(key) for key in ("schema", "parent_id", "fields")}
                for row in record["entities"]}
    return entities(actual) == entities(expected) and all(
        actual.get(key, []) == expected.get(key, []) for key in ("parameters", "relations", "obligations"))


def candidate_for_readback(detail, runtime):
    # A partial readback intentionally has no fully-read candidate card. Its
    # admitted operation still names the run to inspect independently. Accept
    # only one candidate identity for this fresh, single-turn benchmark chat.
    candidates = {row["candidateId"] for row in runtime.get("operations", [])
                  if row.get("sessionId") == detail["id"] and row.get("candidateId") and row.get("jobId")}
    return next(iter(candidates)) if len(candidates) == 1 else None


def prime_session(base, session, message, timeout, output):
    """Establish genuine provider continuity without modifying the paired input."""
    primer = {**message, "content": (
        "This is an isolated benchmark preparation turn. Only use connected monkeyhub tools. "
        "Read the supplied exact project context and inspect its existing portico-base and portico-cornice "
        "facts, references and support relationship. Briefly summarize the two heights, shared footprint "
        "and support datum for a subsequent revision. Make no changes or proposals. Do not read repository "
        "files, use shell tools, or call another model. Do not request rendered views: the seed has no retained STEP."
    )}
    request(base, f"/api/chat/sessions/{session['id']}/messages", primer)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = request(base, f"/api/chat/sessions/{session['id']}")
        if detail["status"] != "running":
            write_json(output / "primer-chat.json", detail)
            if detail["status"] != "idle":
                raise RuntimeError("Preparation turn failed; inspect primer-chat.json")
            return
        time.sleep(1)
    request(base, f"/api/chat/sessions/{session['id']}/stop", {})
    raise TimeoutError("Preparation turn timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("simple-create", "incremental-edit", "assembly-edit"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="Explicit nonproject directory for this benchmark's trace/report")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--model", help="Pin the installed provider's model for comparable runs")
    parser.add_argument("--hold-seconds", type=int, default=0, help="Keep isolated services available briefly for a separate headless preview check")
    parser.add_argument("--no-preview", action="store_true", help="Only measure provider/runtime on a machine without headless Chrome; first visible remains unknown")
    parser.add_argument("--context-pack", action="store_true",
                        help="Send the same incremental-edit prompt with this fixture's known source/digest/focus as designContext, so the turn is prepared before the provider starts")
    parser.add_argument("--session-pair", action="store_true", help="Compare old-session continuation with a project-state session; retain both projects and diagnostics")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare and verify a --session-pair without starting services or calling a model")
    parser.add_argument("--order", choices=("continue,project", "project,continue"), default="continue,project")
    parser.add_argument("--condition", choices=("continue", "project"), help=argparse.SUPPRESS)
    parser.add_argument("--retained-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("--output must be an absolute nonproject directory")
    if args.context_pack and args.scenario != "incremental-edit":
        parser.error("--context-pack names one existing object to edit; it applies to incremental-edit only")
    if args.prepare_only and not args.session_pair:
        parser.error("--prepare-only requires --session-pair")
    config = json.loads(Path(__file__).with_name("benchmarks.json").read_text())
    if args.model:
        config["model"] = args.model
    if args.session_pair:
        paired_benchmark(args, config)
        return
    scenario = next(row for row in config["scenarios"] if row["id"] == args.scenario)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_diff = subprocess.check_output(["git", "diff", "HEAD", "--", "apps", "archflow", "monkeyarch", "monkeymonitor", "tests/monkeymonitor", "tools/benchmark_intent_context.py"], cwd=ROOT)
    fixture_revision = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", config["fixture"]], cwd=ROOT, text=True).strip()
    args.output.mkdir(parents=True, exist_ok=True)
    root_context = nullcontext(str(args.retained_root)) if args.retained_root else tempfile.TemporaryDirectory(prefix="monkeyhub-turn-benchmark-")
    with root_context as temporary, ExitStack() as stack:
        root = Path(temporary)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"}
        environment.update(APPDATA=str(root / "settings"), LOCALAPPDATA=str(root / "local"), PYTHONUTF8="1")
        user_site = site.getusersitepackages()
        if site.ENABLE_USER_SITE and user_site in sys.path:
            environment["PYTHONPATH"] = os.pathsep.join(value for value in (environment.get("PYTHONPATH"), user_site) if value)
        # Preserve the configured provider authentication and installed native
        # CLI location; APPDATA above isolates only the application settings.
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        ports = free_ports(3)
        runtime = root / "runtime"
        fixture = project_fixture()
        project = root / "projects" / fixture.PROJECT_ID
        if args.retained_root:
            if not args.retained_root.is_absolute():
                raise ValueError("retained root must be absolute")
            repository = FilesystemProjectRepository.open(project)
        else:
            repository, _ = fixture.make_project(root / "projects")
        input_source = source_identity(repository)
        head_before = repository.layout.head.read_bytes()
        save_application_settings(runtime, ApplicationSettingsDto(
            projectDir=str(project), referenceRun=fixture.REFERENCE_RUN_ID, cadExport="occt",
            studioPort=ports[1], monitorPort=ports[2],
        ))
        app = create_app(HubSettings(runtime_root=runtime, port=ports[0]))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[0], log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        preview = None
        base = f"http://127.0.0.1:{ports[0]}"
        monitor = f"http://127.0.0.1:{ports[2]}"
        try:
            end = time.monotonic() + 40
            while not server.started and time.monotonic() < end and worker.is_alive():
                time.sleep(0.1)
            if not server.started:
                raise RuntimeError("Isolated Hub did not start")
            opened = request(base, "/api/runtime/projects/open", {"projectId": fixture.PROJECT_ID, "projectDir": str(project)})
            request(base, "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), {})
            ready_deadline = time.monotonic() + 40
            while time.monotonic() < ready_deadline:
                apps = request(base, "/api/apps?" + urlencode({"projectDir": str(project)}))
                studio = next(row for row in apps if row["appId"] == "monkeyarch")
                if studio["state"] == "running":
                    break
                if studio["state"] == "error":
                    raise RuntimeError("Isolated Studio did not start: " + str(studio.get("error")))
                time.sleep(0.2)
            else:
                raise TimeoutError("Isolated Studio did not become ready")
            session = request(base, "/api/chat/sessions", {"projectDir": str(project), "provider": config["provider"], "model": config["model"]})
            state = request(studio["apiUrl"].rstrip("/"), "/api/state?" + urlencode({"run": fixture.REFERENCE_RUN_ID}))
            state_digest = state["stateDigest"]
            write_json(args.output / "input-state.json", state)
            message = {"projectId": fixture.PROJECT_ID,
                       "content": scenario["prompt"].replace("{state_digest}", state_digest)}
            if args.context_pack or args.condition:
                # The same unchanged prompt, plus what this fixture's scenario
                # already states in words: which run, which state, which object.
                # Nothing is inferred here; a wrong name is refused by Studio.
                message["designContext"] = {
                    "sourceRunId": fixture.REFERENCE_RUN_ID, "stateDigest": state_digest,
                    "targetComponentId": "portico", "elementId": "portico-cornice",
                }
                if args.scenario == "assembly-edit":
                    message["designContext"].pop("elementId")
                    message["designContext"]["elementIds"] = ["portico-base", "portico-cornice"]
            primer_session_id = None
            if args.condition:
                prime_session(base, session, message, args.timeout, args.output)
                if source_identity(repository) != input_source:
                    raise RuntimeError("Primer changed the project; pair is invalid")
                saved_session = app.state.chats._sessions[session['id']]
                primer_session_id = saved_session.acpSessionId or saved_session.nativeSessionId
                message["contextMode"] = args.condition
            request_started = time.monotonic()
            posted = request(base, f"/api/chat/sessions/{session['id']}/messages", message)
            turn_id = next(row["id"] for row in reversed(posted["messages"]) if row["role"] == "user")
            if not args.no_preview:
                preview = subprocess.Popen([shutil.which("node") or "node", str(Path(__file__).with_name("benchmark_preview.mjs")),
                                            base, session["id"], monitor, str(args.output)],
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(json.dumps({"state": "running", "scenario": scenario["id"], "turn_id": turn_id, "monitor_url": monitor}), flush=True)
            deadline = time.monotonic() + args.timeout
            observed_live, last_count = False, -1
            while time.monotonic() < deadline:
                detail = request(base, f"/api/chat/sessions/{session['id']}")
                snapshot = request(monitor, "/api/traces")
                trace = next((row for row in snapshot["traces"] if row["turn_id"] == turn_id), None)
                if trace:
                    observed_live |= trace["status"] == "running"
                    if len(trace["spans"]) != last_count:
                        last_count = len(trace["spans"])
                        print(json.dumps({"state": trace["status"], "spans": last_count, "tools": trace["summary"]["tool_rounds"]}), flush=True)
                if detail["status"] != "running":
                    break
                time.sleep(1)
            else:
                request(base, f"/api/chat/sessions/{session['id']}/stop", {})
                # Drain before reading the final trace; preserve failures too.
                stop_deadline = time.monotonic() + 30
                while time.monotonic() < stop_deadline:
                    detail = request(base, f"/api/chat/sessions/{session['id']}")
                    if detail["status"] != "running":
                        break
                    time.sleep(0.2)
            observed_wall_ms = round((time.monotonic() - request_started) * 1000)
            write_json(args.output / "chat.json", detail)
            runtime_snapshot = request(base, f"/api/runtime/projects/{opened['runtimeId']}")
            candidate = candidate_for_readback(detail, runtime_snapshot)
            readback = request(studio["apiUrl"].rstrip("/"), f"/api/candidates/{candidate}") if candidate else None
            write_json(args.output / "candidate.json", readback)
            readback_ok = bool(readback and readback["status"] == "succeeded" and readback["seatExecutionComplete"]
                               and readback.get("objects") and not readback.get("objectReadbackError"))
            geometry_ok = expected_geometry(readback, scenario["id"])
            authored_ok = expected_authored_fields(repository, candidate, scenario["id"], fixture)
            preview_url = studio["url"] + "&candidate=" + (candidate or "")
            connection = {"monitor_url": monitor, "preview_url": preview_url, "turn_id": turn_id, "candidate_id": candidate}
            (args.output / "connection.json").write_text(json.dumps(connection, indent=2), encoding="utf-8")
            print(json.dumps({"state": detail["status"], **connection}), flush=True)
            if args.hold_seconds:
                time.sleep(args.hold_seconds)
            preview_code = preview.wait(timeout=100) if preview else None
            snapshot = request(monitor, "/api/traces")
            trace = next(row for row in snapshot["traces"] if row["turn_id"] == turn_id)
            saved_session = app.state.chats._sessions[session['id']]
            native_session_id = saved_session.acpSessionId or saved_session.nativeSessionId
            continuity_ok = (bool(primer_session_id and native_session_id) and
                             ((primer_session_id == native_session_id) == (args.condition == "continue"))) if args.condition else True
            task_success = (detail["status"] == "idle" and readback_ok and geometry_ok and authored_ok is not False and continuity_ok
                            and repository.layout.head.read_bytes() == head_before)
            report = {"benchmark": {**{key: config[key] for key in ("fixture", "fixture_revision", "provider", "model")},
                                    "scenario": scenario["id"], "build_revision": revision, "fixture_last_change": fixture_revision,
                                    "source_diff_sha256": hashlib.sha256(source_diff).hexdigest(),
                                    "prompt_sha256": hashlib.sha256(message["content"].encode()).hexdigest(),
                                    "input_source": input_source,
                                    # Which condition this run was: the scenario and its
                                    # prompt are the same either way, so the two reports
                                    # are comparable and say which is which.
                                    "context_mode": args.condition or ("context_pack" if args.context_pack else "none")},
                      "task_success": task_success, "metrics": measured_metrics(trace),
                      "observed_wall_ms": observed_wall_ms, "project_dir": str(project), "runtime_root": str(runtime),
                      "session_id": session['id'], "primer_provider_session_id": primer_session_id,
                      "provider_session_id": native_session_id, "session_continuity_ok": continuity_ok,
                      "observed_live": observed_live, "candidate_id": candidate, "candidate_readback_ok": readback_ok,
                      "expected_geometry_ok": geometry_ok,
                      "expected_authored_fields_ok": authored_ok,
                      "preview_status": "not_requested" if preview is None else "succeeded" if preview_code == 0 else "failed",
                      "canonical_head_unchanged": repository.layout.head.read_bytes() == head_before, "trace": trace}
            (args.output / "trace.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"report": str(args.output / "trace.json"), "status": trace["status"],
                              "summary": trace["summary"], "usage": trace["usage"], "candidate": candidate}), flush=True)
            if not task_success:
                raise RuntimeError("Provider turn did not return a successful candidate with retained object readback; inspect the exported trace")
            if not geometry_ok:
                raise RuntimeError("Candidate geometry did not match the fixed benchmark scene; inspect the exported trace")
            if preview_code not in (None, 0):
                raise RuntimeError("Headless preview did not complete; provider/runtime trace was still exported")
        finally:
            app.state.chats.shutdown()
            server.should_exit = True
            worker.join(45)
            if worker.is_alive():
                raise RuntimeError("Isolated Hub did not finish draining")
            if preview and preview.poll() is None:
                preview.terminate()
                preview.wait(timeout=10)


if __name__ == "__main__":
    main()
