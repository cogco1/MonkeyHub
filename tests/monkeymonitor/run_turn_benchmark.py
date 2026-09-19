"""Run an explicitly requested real CLI benchmark in disposable Hub/Studio services.

Uses the installed provider authentication. No user project/service/configuration
is altered. Results are the same Monitor traces, with their fixture/build context.
Invoke manually; this paid/provider-dependent measurement is not a CI threshold.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
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


def request(base, path, body=None):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response)


def expected_geometry(readback, scenario):
    if not readback or not readback.get("objects"):
        return False
    bounds = {"portico-base": ([0, 0, 0], [4, 2, 0.6]),
              "portico-cornice": ([0, 0, 0.6], [4, 2, 1.1 if scenario == "incremental-edit" else 0.9])}
    if scenario == "simple-create":
        bounds["monitor-benchmark-block"] = ([12, 0, 0], [16, 3, 2.5])
    objects = {row.get("producerOp"): row for row in readback["objects"]}
    if set(objects) != set(bounds):
        return False
    for name, (minimum, maximum) in bounds.items():
        row = objects[name]
        if row.get("lengthUnit") != "meter" or row.get("upAxis") != "Z-up" or not row.get("bbox"):
            return False
        for actual, expected in zip(row["bbox"]["min"] + row["bbox"]["max"], minimum + maximum, strict=True):
            if abs(actual - expected) > 0.001:
                return False
    return True


def candidate_for_readback(detail, runtime):
    # A partial readback intentionally has no fully-read candidate card. Its
    # admitted operation still names the run to inspect independently. Accept
    # only one candidate identity for this fresh, single-turn benchmark chat.
    candidates = {row["candidateId"] for row in runtime.get("operations", [])
                  if row.get("sessionId") == detail["id"] and row.get("candidateId")}
    return next(iter(candidates)) if len(candidates) == 1 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("simple-create", "incremental-edit"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="Explicit nonproject directory for this benchmark's trace/report")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--model", help="Pin the installed provider's model for comparable runs")
    parser.add_argument("--hold-seconds", type=int, default=0, help="Keep isolated services available briefly for a separate headless preview check")
    parser.add_argument("--no-preview", action="store_true", help="Only measure provider/runtime on a machine without headless Chrome; first visible remains unknown")
    parser.add_argument("--context-pack", action="store_true",
                        help="Send the same incremental-edit prompt with this fixture's known source/digest/focus as designContext, so the turn is prepared before the provider starts")
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("--output must be an absolute nonproject directory")
    if args.context_pack and args.scenario != "incremental-edit":
        parser.error("--context-pack names one existing object to edit; it applies to incremental-edit only")
    config = json.loads(Path(__file__).with_name("benchmarks.json").read_text())
    if args.model:
        config["model"] = args.model
    scenario = next(row for row in config["scenarios"] if row["id"] == args.scenario)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    fixture_revision = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", config["fixture"]], cwd=ROOT, text=True).strip()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="monkeyhub-turn-benchmark-") as temporary, ExitStack() as stack:
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
        repository, _ = fixture.make_project(root / "projects")
        project = root / "projects" / fixture.PROJECT_ID
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
            state_digest = fixture.runner_state_digest(repository, fixture.REFERENCE_RUN_ID)
            message = {"projectId": fixture.PROJECT_ID,
                       "content": scenario["prompt"].replace("{state_digest}", state_digest)}
            if args.context_pack:
                # The same unchanged prompt, plus what this fixture's scenario
                # already states in words: which run, which state, which object.
                # Nothing is inferred here; a wrong name is refused by Studio.
                message["designContext"] = {
                    "sourceRunId": fixture.REFERENCE_RUN_ID, "stateDigest": state_digest,
                    "targetComponentId": "portico", "elementId": "portico-cornice",
                }
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
                raise TimeoutError("The benchmark provider did not finish within its configured timeout")
            runtime_snapshot = request(base, f"/api/runtime/projects/{opened['runtimeId']}")
            candidate = candidate_for_readback(detail, runtime_snapshot)
            readback = request(studio["apiUrl"].rstrip("/"), f"/api/candidates/{candidate}") if candidate else None
            readback_ok = bool(readback and readback["status"] == "succeeded" and readback["seatExecutionComplete"]
                               and readback.get("objects") and not readback.get("objectReadbackError"))
            geometry_ok = expected_geometry(readback, scenario["id"])
            preview_url = studio["url"] + "&candidate=" + (candidate or "")
            connection = {"monitor_url": monitor, "preview_url": preview_url, "turn_id": turn_id, "candidate_id": candidate}
            (args.output / "connection.json").write_text(json.dumps(connection, indent=2), encoding="utf-8")
            print(json.dumps({"state": detail["status"], **connection}), flush=True)
            if args.hold_seconds:
                time.sleep(args.hold_seconds)
            preview_code = preview.wait(timeout=100) if preview else None
            snapshot = request(monitor, "/api/traces")
            trace = next(row for row in snapshot["traces"] if row["turn_id"] == turn_id)
            report = {"benchmark": {**{key: config[key] for key in ("fixture", "fixture_revision", "provider", "model")},
                                    "scenario": scenario["id"], "build_revision": revision, "fixture_last_change": fixture_revision,
                                    # Which condition this run was: the scenario and its
                                    # prompt are the same either way, so the two reports
                                    # are comparable and say which is which.
                                    "context_mode": "context_pack" if args.context_pack else "none"},
                      "observed_live": observed_live, "candidate_id": candidate, "candidate_readback_ok": readback_ok,
                      "expected_geometry_ok": geometry_ok,
                      "preview_status": "not_requested" if preview is None else "succeeded" if preview_code == 0 else "failed",
                      "canonical_head_unchanged": repository.layout.head.read_bytes() == head_before, "trace": trace}
            (args.output / "trace.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"report": str(args.output / "trace.json"), "status": trace["status"],
                              "summary": trace["summary"], "usage": trace["usage"], "candidate": candidate}), flush=True)
            if detail["status"] != "idle" or not readback_ok:
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
