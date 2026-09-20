"""Explicit real-provider courtyard acceptance through the existing Hub/Monitor.

Synthetic public scene only. The retained P036 project and Hub diagnostic journal
remain under the caller's external output directory, including failed attempts.
This is an opt-in acceptance experiment, not a timing assertion in CI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import site
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.monkeymonitor import run_turn_benchmark as bench
from archflow_studio_api.application.artifacts import ModelSource, list_artifacts
from archflow_studio_api.application.binding import ProjectBinding
from archflow_studio_api.application.drawings import _complete_source, model_view
from archflow_studio_api.settings import StudioSettings
from monkeydiagram.drawing_elevation import read_elevation_source
from monkeymonitor.store import UsageLog
from monkeymonitor.trace import build_traces


RULES = ("This is an isolated design acceptance task, not source development. Use only connected monkeyhub tools. "
         "Keep existing portico-base, portico-cornice, parameters, locks and relations unchanged. "
         "Work on the exact supplied designContext; keep identities and references through revisions. "
         "Batch deterministic changes. Observe actual same-source top/front images when spatial judgment is needed, "
         "then correct any deviation in the same Stage. Never accept or issue a Stage. "
         "Do not read source files, use shell, or call another model. Give a brief result describing what you saw.")
STEPS = (
    ("courtyard", "Add three generic massing prisms in the existing portico component: west-wing "
     "rectangle (20,0)-(24,16), north-wing (24,12)-(38,16), east-wing (38,0)-(42,16), "
     "all ground-based and 12 m high. Together they enclose a courtyard open to the south. "
     "Retain a Reading recording the user requirement: the courtyard stays open to the south, "
     "with a clear rectangle at least 12 m wide by 12 m deep; do not invent later structure/material semantics. "
     "Generate one candidate and inspect the actual top view."),
    ("lower", "Lower only east-wing from 12 to 9 m. Keep every footprint, other height and retained courtyard condition."),
    ("setback", "Set back the top 4 m of north-wing by 2 m from its courtyard-facing south edge. "
     "Keep north-wing identity for its lower 8 m; add north-upper, height 4 m, supported at north-wing-top, "
     "rectangle (24,14)-(38,16). Retain that support reference. Leave both side wings unchanged. "
     "Inspect the actual top and front views of the result."),
    ("trial-repair", "Study moving east-wing toward the west: first make a reversible 4 m trial and inspect "
     "its actual top view. The retained minimum courtyard size takes priority over the trial distance. "
     "If the trial violates it, revise from that exact trial candidate to the largest westward movement "
     "that preserves the courtyard. Inspect the corrected top view. Keep west-wing, north-wing, north-upper "
     "and all their references unchanged. Report the observed trial problem and final movement."),
    ("reopen", "The editing base has explicitly been restored to the supplied candidate, undoing the last "
     "movement study. This is a new provider session; use retained project conditions, not earlier chat. "
     "Move west-wing 0.5 m west, preserving the current east wing and the setback/support above the north wing. "
     "Check the courtyard requirement from the retained Reading and actual top view."),
    ("wall", "From the current massing and retained conditions, add a 0.3 m thick, 3 m high schematic wall "
     "immediately outside the north edge of all three wings, spanning from their outer west to outer east edge. "
     "Derive its extent from current geometry. Name it north-wall; use the existing wall or prism producer, "
     "ground based. Keep all existing masses, the north-upper support reference, locked parameters and courtyard "
     "requirement. Do not add future material or structural assumptions. Inspect top and front views."),
)


@contextmanager
def isolated_services(root):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("ARCHFLOW_STUDIO_") and key != "MONKEYMONITOR_DATA_DIR"}
    environment.update(APPDATA=str(root / "settings"), LOCALAPPDATA=str(root / "local"), PYTHONUTF8="1")
    user_site = site.getusersitepackages()
    if site.ENABLE_USER_SITE and user_site in sys.path:
        environment["PYTHONPATH"] = os.pathsep.join(value for value in (environment.get("PYTHONPATH"), user_site) if value)
    with patch.dict(os.environ, environment, clear=True):
        fixture = bench.project_fixture()
        project = root / "projects" / fixture.PROJECT_ID
        if not project.exists():
            bench.project_fixture().make_project(root / "projects")
        ports = bench.free_ports(3)
        runtime = root / "runtime"
        bench.save_application_settings(runtime, bench.ApplicationSettingsDto(
            projectDir=str(project), referenceRun=fixture.REFERENCE_RUN_ID, cadExport="occt",
            studioPort=ports[1], monitorPort=ports[2]))
        app = bench.create_app(bench.HubSettings(runtime_root=runtime, port=ports[0]))
        server = bench.uvicorn.Server(bench.uvicorn.Config(app, host="127.0.0.1", port=ports[0], log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        base, monitor = f"http://127.0.0.1:{ports[0]}", f"http://127.0.0.1:{ports[2]}"
        try:
            deadline = time.monotonic() + 45
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.1)
            if not server.started:
                raise RuntimeError("Isolated Hub did not start")
            opened = bench.request(base, "/api/runtime/projects/open", {"projectId": fixture.PROJECT_ID, "projectDir": str(project)})
            apps = bench.request(base, "/api/apps/monkeyarch/start?" + urlencode({"projectDir": str(project)}), {})
            while time.monotonic() < deadline:
                rows = bench.request(base, "/api/apps?" + urlencode({"projectDir": str(project)}))
                studio = next(row for row in rows if row["appId"] == "monkeyarch")
                if studio["state"] == "running":
                    break
                if studio["state"] == "error":
                    raise RuntimeError(str(studio.get("error")))
                time.sleep(.2)
            else:
                raise TimeoutError("Isolated Runtime did not start")
            yield app, base, studio["apiUrl"].rstrip("/"), monitor, opened["runtimeId"], project
        finally:
            app.state.chats.shutdown()
            server.should_exit = True
            worker.join(45)
            if worker.is_alive():
                raise RuntimeError("Owned Hub failed to drain")


def retained_record(repository, candidate):
    run = repository.load_run(candidate)
    refs = [ref for ref in repository.list_json(run=run, destination=bench.project_fixture().run_records(candidate))
            if ref.record_kind == bench.STATE_RECORD]
    if len(refs) != 1:
        raise ValueError("Candidate has no unique retained state")
    return repository.load_json(refs[0])


def spatial_readback(project, candidate, state_digest, *, step=None, trial=False, output=None):
    """Independently intersect the certified cold STEP with clear-space volumes.

    Unlike an AABB check this detects material inside the requested courtyard
    and upper setback. Exact source validation is the production drawing reader.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt
    binding = ProjectBinding.open(StudioSettings(project_dir=project, cad_export="occt"))
    models = [row for row in list_artifacts(binding, run_id=candidate).artifacts
              if row.format == "3dm" and row.available and row.design_state_digest == state_digest]
    if len(models) != 1:
        raise ValueError("Candidate lacks one exact complete model")
    model = ModelSource(candidate, state_digest, models[0].sha256)
    source, _ = _complete_source(binding, model, None)
    verified = read_elevation_source(binding.repository, source)
    def overlap(origin, size):
        box = BRepPrimAPI_MakeBox(gp_Pnt(*origin), *size).Shape()
        volume = 0.0
        for row in verified.entries:
            common = BRepAlgoAPI_Common(row.shape, box)
            if not common.IsDone():
                raise ValueError("Exact solid intersection failed")
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(common.Shape(), props)
            volume += abs(props.Mass())
        return volume
    result = {"source_run": candidate, "state_digest": state_digest, "step_sha256": source.step_sha256,
            "court_intrusion_m3": overlap((24, 0, .01), (12, 12, 11.98)),
            "south_access_intrusion_m3": overlap((24, -2, .01), (12, 2, 8.98)),
            "setback_intrusion_m3": overlap((24, 12, 8.01), (14, 2, 3.98))}
    if step:
        # An exact B-rep difference verifies all requested solids, not only
        # their envelopes or the absence of material in the protected void.
        expected = {"portico-base": ((0, 0, 0), (4, 2, .6)),
                    "portico-cornice": ((0, 0, .6), (4, 2, .3)),
                    "west-wing": ((19.5 if step in {"reopen", "wall"} else 20, 0, 0), (4, 16, 12)),
                    "east-wing": ((34 if trial else 36 if step == "trial-repair" else 38, 0, 0),
                                  (4, 16, 12 if step == "courtyard" else 9)),
                    "north-wing": ((24, 12, 0), (14, 4, 12 if step in {"courtyard", "lower"} else 8))}
        if step not in {"courtyard", "lower"}:
            expected["north-upper"] = ((24, 14, 8), (14, 2, 4))
        if step == "wall":
            expected["north-wall"] = ((19.5, 16, 0), (22.5, .3, 3))
        by_name = {row.name: row.shape for row in verified.entries}
        differences = {}
        for name, (origin, size) in expected.items():
            actual = by_name.get("obj-" + name)
            if actual is None:
                differences[name] = None
                continue
            target = BRepPrimAPI_MakeBox(gp_Pnt(*origin), *size).Shape()
            volumes = []
            for first, second in ((actual, target), (target, actual)):
                cut = BRepAlgoAPI_Cut(first, second)
                if not cut.IsDone():
                    raise ValueError("Exact symmetric shape comparison failed")
                props = GProp_GProps()
                BRepGProp.VolumeProperties_s(cut.Shape(), props)
                volumes.append(abs(props.Mass()))
            differences[name] = sum(volumes)
        result.update(exact_shape_differences_m3=differences,
                      unexpected_objects=sorted(set(by_name) - {"obj-" + name for name in expected}),
                      exact_shapes_ok=all(value is not None and value < 1e-7 for value in differences.values())
                      and set(by_name) == {"obj-" + name for name in expected})
    if output:
        for view in ("top", "front"):
            png, _, _ = model_view(binding, model_source=model, view=view)
            (output / f"{candidate}-{view}.png").write_bytes(png)
    return result


def run_turn(app, base, studio, monitor, runtime_id, project, session, source, name, content, output, timeout, cold=False):
    output.mkdir(parents=True, exist_ok=False)
    state = bench.request(studio, "/api/state?" + urlencode({"run": source}))
    message = {"projectId": session["projectId"], "content": RULES + "\n\n" + content,
               "designContext": {"sourceRunId": source, "stateDigest": state["stateDigest"]},
               "contextMode": "project" if cold else "continue"}
    bench.write_json(output / "request.json", message)
    before = bench.request(base, f"/api/runtime/projects/{runtime_id}")
    before_ids = {row["operationId"] for row in before.get("operations", ())}
    posted = bench.request(base, f"/api/chat/sessions/{session['id']}/messages", message)
    turn_id = next(row["id"] for row in reversed(posted["messages"]) if row["role"] == "user")
    print(json.dumps({"step": name, "state": "running", "turn": turn_id}), flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = bench.request(base, f"/api/chat/sessions/{session['id']}")
        if detail["status"] != "running":
            break
        time.sleep(1)
    else:
        bench.request(base, f"/api/chat/sessions/{session['id']}/stop", {})
        raise TimeoutError("Turn timed out; original project/chat/journal retained")
    bench.write_json(output / "chat.json", detail)
    messages = detail["messages"]
    start = next(i for i, row in enumerate(messages) if row["id"] == turn_id)
    after = bench.request(base, f"/api/runtime/projects/{runtime_id}")
    generated = [row for row in after.get("operations", ()) if row["operationId"] not in before_ids
                 and row.get("sessionId") == session["id"] and row.get("candidateId") and row.get("jobId")]
    generated_ids = {row["candidateId"] for row in generated}
    candidates = list(dict.fromkeys(row["candidateId"] for row in messages[start:]
                                   if row.get("candidateId") in generated_ids and row.get("candidateId") != source))
    failures = []
    snapshot = bench.monitor_snapshot(monitor, failures)
    trace = next((row for row in (snapshot or {}).get("traces", ()) if row["turn_id"] == turn_id), None)
    report = {"step": name, "turn_id": turn_id, "source": source, "status": detail["status"],
              "candidates": candidates, "candidate_count": len(candidates), "operations": generated, "trace": trace, "monitor_failures": failures,
              "metrics": bench.measured_metrics(trace) if trace else None}
    bench.write_json(output / "report.json", report)
    if not candidates or detail["status"] != "idle":
        raise RuntimeError("Provider did not produce/read a candidate; inspect retained chat")
    candidate = candidates[-1]
    repository = bench.FilesystemProjectRepository.open(project)
    checks = []
    for run in candidates:
        readback = bench.request(studio, f"/api/candidates/{run}")
        bench.write_json(output / f"{run}.json", readback)
        spatial = spatial_readback(project, run, readback["stateDigest"], step=name,
                                   trial=name == "trial-repair" and run != candidate, output=output)
        checks.append(spatial)
    report["spatial"] = checks
    report["record"] = retained_record(repository, candidate)
    report["checks"] = check_turn(report, messages[start:], source_record=retained_record(repository, source))
    report["task_success"] = all(value for key, value in report["checks"].items()
                                 if key != "actual_model_view_calls" and value is not None)
    saved = app.state.chats._sessions[session["id"]]
    report["provider_session_id"] = saved.acpSessionId or saved.nativeSessionId
    bench.write_json(output / "report.json", report)
    print(json.dumps({"step": name, "state": detail["status"], "candidate": candidate,
                      "checks": report["checks"], "task_success": report["task_success"]}), flush=True)
    if not report["task_success"]:
        raise RuntimeError("Candidate failed independent acceptance; original evidence retained")
    return candidate


def source_fields_preserved(record, source_record, step):
    """Only this scenario's requested height or X translation may change."""
    current = {row["entity_id"]: row for row in record["entities"]}
    target = {"lower": "east-wing", "setback": "north-wing", "trial-repair": "east-wing", "reopen": "west-wing"}.get(step)
    for before in source_record["entities"]:
        after = current.get(before["entity_id"])
        if after is None:
            return False
        if before["entity_id"] != target or before == after:
            if before != after:
                return False
            continue
        revised = deepcopy(after)
        old_params = before.get("fields", {}).get("params", {})
        new_params = revised.get("fields", {}).get("params", {})
        if step in {"lower", "setback"}:
            # Replacing an explicit control binding with a constant loses intent.
            if not all(isinstance(p.get("height"), (int, float)) and not isinstance(p["height"], bool)
                       for p in (old_params, new_params)):
                return False
            new_params["height"] = old_params["height"]
        else:
            old_profile, new_profile = old_params.get("profile", []), new_params.get("profile", [])
            if not old_profile or len(old_profile) != len(new_profile):
                return False
            shifts = []
            for old, new in zip(old_profile, new_profile):
                if len(old) != 2 or len(new) != 2 or old[1] != new[1]:
                    return False
                if old[0] == new[0]:
                    shifts.append(0.0)
                elif all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (old[0], new[0])):
                    shifts.append(new[0] - old[0])
                else:
                    return False
            if any(abs(shift - shifts[0]) > 1e-8 for shift in shifts):
                return False
            default_plane = {"origin": [0, 0, 0], "xAxis": [1, 0, 0], "yAxis": [0, 0, 1], "normal": [0, 1, 0]}
            old_plane, new_plane = old_params.get("work_plane", default_plane), new_params.get("work_plane", default_plane)
            if (set(old_plane) != set(new_plane) or old_plane["origin"][1:] != new_plane["origin"][1:]
                    or any(old_plane[key] != new_plane[key] for key in ("xAxis", "yAxis", "normal"))):
                return False
            new_params["profile"] = old_profile
            if "work_plane" in old_params:
                new_params["work_plane"] = old_plane
            else:
                new_params.pop("work_plane", None)
        if revised != before:
            return False
    return all(all(row in record.get(key, []) for row in source_record.get(key, []))
               for key in ("parameters", "relations", "obligations"))


def check_turn(report, messages, *, source_record):
    name, checks = report["step"], report["spatial"]
    original = bench.StateRecord.from_dict(bench.project_fixture().RECORD_PAYLOAD).to_dict()
    current = {row["entity_id"]: row for row in report["record"]["entities"]}
    keep_ok = all(all(current.get(row["entity_id"], {}).get(key) == row.get(key)
                      for key in ("schema", "parent_id", "fields")) for row in original["entities"])
    keep_ok &= all(all(row in report["record"].get(key, []) for row in original.get(key, []))
                   for key in ("parameters", "relations", "obligations"))
    readings = [row for row in current.values() if row["schema"] == "Reading@1"]
    source_readings = [row for row in source_record["entities"] if row["schema"] == "Reading@1"]
    condition = any("12 m" in row["fields"]["note"] and "south" in row["fields"]["note"].lower()
                    and {"entity:west-wing", "entity:east-wing", "entity:north-wing"}.issubset(row["fields"]["subject_refs"])
                    for row in readings) if name == "courtyard" else bool(source_readings) and all(
                        any(item["entity_id"] == row["entity_id"] and item["fields"] == row["fields"] for item in readings)
                        for row in source_readings)
    view_sources = []
    for index, row in enumerate(messages):
        if row["role"] != "tool" or row.get("status") != "complete" or not row.get("content") or not row["content"].splitlines()[0].endswith("completed"):
            continue
        match = re.search(r"GET (/api/drawings/model-view\?\S+)", row["content"])
        if match:
            view_sources.append((index, parse_qs(urlsplit(match[1]).query)))
    def matches_view(query, item, view="top"):
        return (query.get("runId") == [item["source_run"]] and query.get("stateDigest") == [item["state_digest"]]
                and query.get("view") == [view])
    def viewed(item, view="top"):
        return any(matches_view(query, item, view) for _, query in view_sources)
    upper = current.get("north-upper", {})
    support_ok = name in {"courtyard", "lower"} or upper.get("fields", {}).get("references", {}).get("base") == {"datum": "north-wing-top"}
    operations = {row["candidateId"]: row for row in report.get("operations", ()) if row.get("candidateId")}
    source = report.get("source")
    source_chain_ok = bool(source) and all(
        operations.get(item["source_run"], {}).get("sourceRunId") == (source if index == 0 else checks[index - 1]["source_run"])
        for index, item in enumerate(checks))
    observed_before_repair = None
    if name == "trial-repair":
        final_proposal = operations.get(checks[-1]["source_run"], {}).get("proposalId")
        view_indices = [index for index, query in view_sources if matches_view(query, checks[0])]
        proposal_indices = [index for index, row in enumerate(messages) if final_proposal and row.get("role") == "tool"
                            and row.get("status") == "complete"
                            and row.get("content")
                            and (row.get("content") or "").splitlines()[0].endswith("completed")
                            and re.search(rf"(?m)^proposalId: {re.escape(final_proposal)}$", row.get("content") or "")]
        spans = {row["event_id"]: row for row in (report.get("trace") or {}).get("spans", ())}
        def interval(index):
            # Chat rows are appended at tool start and updated in place. Their
            # list order cannot prove that an image arrived before another call.
            span = spans.get("hub:tool:" + str(messages[index].get("id")), {})
            if span.get("phase") != "tool_call" or span.get("status") != "succeeded":
                return None
            try:
                start, end = (datetime.fromisoformat(span[key].replace("Z", "+00:00")) for key in ("started_at", "ended_at"))
                return (start, end) if start.tzinfo and end.tzinfo and start <= end else None
            except (KeyError, AttributeError, TypeError, ValueError):
                return None
        observed_before_repair = any(view_index < proposal_index and view_time and proposal_time
                                    and view_time[1] <= proposal_time[0]
                                    for view_index in view_indices for proposal_index in proposal_indices
                                    for view_time, proposal_time in [(interval(view_index), interval(proposal_index))])
    return {"original_state_preserved": keep_ok, "retained_condition_preserved": condition,
                        "source_fields_preserved": source_fields_preserved(report["record"], source_record, name),
                        "upper_support_reference_preserved": support_ok,
                        "exact_candidate_source_chain": source_chain_ok,
                        "trial_observed_before_repair": observed_before_repair,
                        "courtyard_batched": len(checks) == 1 if name == "courtyard" else None,
                        "final_exact_shapes": checks[-1]["exact_shapes_ok"],
                        "final_courtyard_clear": checks[-1]["court_intrusion_m3"] < 1e-7,
                        "final_same_source_top_view": name == "lower" or viewed(checks[-1]),
                        "setback_front_view": viewed(checks[-1], "front") if name in {"setback", "wall"} else None,
                        "actual_model_view_calls": len(view_sources),
                        "trial_observed_and_corrected": (len(checks) >= 2 and checks[0]["exact_shapes_ok"]
                            and checks[0]["court_intrusion_m3"] > 1
                            and checks[-1]["court_intrusion_m3"] < 1e-7 and all(viewed(item) for item in (checks[0], checks[-1])))
                            if name == "trial-repair" else None}


def validate_saved(output):
    """Recheck retained evidence without a provider call or project write."""
    project = output / "projects" / bench.project_fixture().PROJECT_ID
    repository = bench.FilesystemProjectRepository.open(project)
    events, monitor_warnings = UsageLog(output / "runtime/diagnostics/monkeymonitor").read()
    projected = {trace["turn_id"]: trace for trace in build_traces([event.to_dict() for event in events])["traces"]}
    results = {}
    provider_ids = []
    for name, _ in STEPS:
        directory = output / name
        if name == "wall" and not directory.exists():
            continue  # Older pilot: retain its explicitly narrower five-step scope.
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        detail = json.loads((directory / "chat.json").read_text(encoding="utf-8"))
        # Earlier pilot exports can include a read of their input candidate.
        # Exclude it explicitly; validation never promotes an input to output.
        candidates = [item for item in report["candidates"] if item != report["source"]]
        if not candidates:
            raise ValueError(f"{name}: no new retained candidate")
        checks = []
        for candidate in candidates:
            readback = json.loads((directory / f"{candidate}.json").read_text(encoding="utf-8"))
            checks.append(spatial_readback(project, candidate, readback["stateDigest"], step=name,
                          trial=name == "trial-repair" and candidate != candidates[-1], output=directory))
        report["spatial"] = checks
        report["record"] = retained_record(repository, candidates[-1])
        if "operations" not in report:
            # Read the existing Hub admission journal for older pilot exports;
            # no inferred mutation chain or replacement telemetry is invented.
            operations = []
            for path in (output / "runtime/runtime/operations").glob("*.json"):
                journal = json.loads(path.read_text(encoding="utf-8"))
                operations.extend(item["record"] for item in journal["operations"]
                                  if item["record"].get("candidateId") in candidates)
            report["operations"] = operations
        messages = detail["messages"]
        start = next(i for i, row in enumerate(messages) if row["id"] == report["turn_id"])
        tests = check_turn(report, messages[start:], source_record=retained_record(repository, report["source"]))
        results[name] = {"checks": tests, "spatial": checks, "candidate_count": len(candidates),
                         "passed": all(value for key, value in tests.items() if key != "actual_model_view_calls" and value is not None),
                         "metrics": bench.measured_metrics(projected[report["turn_id"]]) if report["turn_id"] in projected else None}
        provider_ids.append(report.get("provider_session_id"))
    cold = bool(provider_ids[4]) and all(provider_ids[4] != item for item in provider_ids[:4])
    wall_cold = bool(provider_ids[5]) and provider_ids[5] not in provider_ids[:5] if len(provider_ids) == 6 else None
    authority_path = output / "initial-authority.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8")) if authority_path.exists() else None
    unchanged = (hashlib.sha256((project / "HEAD").read_bytes()).hexdigest() == authority["head_sha256"]
                 and repository.read_design_branches() == authority["branches"]) if authority else None
    result = {"steps": results, "reopened_provider_is_new": cold, "wall_provider_is_new": wall_cold,
              "canonical_authority_unchanged": unchanged,
              "metrics_source": "retained_usage_journal", "monitor_warnings": monitor_warnings,
              "all_passed": cold and wall_cold is True and unchanged is True
              and len(results) == len(STEPS) and all(item["passed"] for item in results.values()),
              "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    bench.write_json(output / "validation.json", result)
    print(json.dumps({"validation": str(output / "validation.json"), "all_passed": result["all_passed"],
                      "checks": {name: row["checks"] for name, row in results.items()}}, ensure_ascii=False), flush=True)
    if not result["all_passed"]:
        raise RuntimeError("Retained design loop failed acceptance; see validation.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--summarize", action="store_true", help="Validate a retained run without calling a provider")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.summarize:
        validate_saved(args.output)
        return
    if not args.model or not args.output.is_absolute() or args.output.exists():
        parser.error("Use a new absolute nonproject output directory; failed runs are never overwritten")
    args.output.mkdir(parents=True)
    diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=bench.ROOT)
    bench.write_json(args.output / "build.json", {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=bench.ROOT, text=True).strip(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(), "model": args.model,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    try:
        with isolated_services(args.output) as services:
            app, base, studio, monitor, runtime_id, project = services
            session = bench.request(base, "/api/chat/sessions", {"projectDir": str(project), "provider": "codex", "model": args.model})
            before_head = (project / "HEAD").read_bytes()
            before_branches = bench.FilesystemProjectRepository.open(project).read_design_branches()
            bench.write_json(args.output / "initial-authority.json", {
                "head_sha256": hashlib.sha256(before_head).hexdigest(), "branches": before_branches})
            source, results = "run-001", {}
            for name, content in STEPS[:4]:
                source = run_turn(*services, session, source, name, content, args.output / name, args.timeout)
                results[name] = source
            assert (project / "HEAD").read_bytes() == before_head, "Unrequested Stage/issue mutation"
            assert bench.FilesystemProjectRepository.open(project).read_design_branches() == before_branches, "Unrequested Stage acceptance"
        # A new Hub/Runtime/Monitor and provider session must read the retained
        # setback candidate. No earlier chat is sent to this fresh session.
        with isolated_services(args.output) as services:
            app, base, studio, monitor, runtime_id, project = services
            source = results["setback"]
            for name, content in STEPS[4:]:
                session = bench.request(base, "/api/chat/sessions", {"projectDir": str(project), "provider": "codex", "model": args.model})
                source = run_turn(*services, session, source, name, content, args.output / name, args.timeout, cold=True)
                results[name] = source
            assert (project / "HEAD").read_bytes() == before_head, "Unrequested Stage/issue mutation"
            assert bench.FilesystemProjectRepository.open(project).read_design_branches() == before_branches, "Unrequested Stage acceptance"
        bench.write_json(args.output / "results.json", results)
        validate_saved(args.output)
    except Exception as exc:
        detail = exc.read().decode("utf-8", errors="replace") if isinstance(exc, HTTPError) else str(exc)
        bench.write_json(args.output / "failure.json", {"type": type(exc).__name__, "detail": detail})
        raise


if __name__ == "__main__":
    main()
