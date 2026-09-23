"""Opt-in real-provider feedback acceptance through Hub's ordinary chat tool.

Synthetic public geometry and one fixture PDF only. Reuses the #32 services and
Monitor trace; output is explicit, external, and retains failed attempts.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.monkeymonitor import run_turn_benchmark as bench
from tests.monkeymonitor.run_design_loop import isolated_services, retained_record
from archflow.project.record_kinds import STUDIO_CANDIDATE_DELTA

RULES = ("Use only connected monkeyhub tools for this isolated synthetic project. "
         "Do not use shell, read source code, call another model, accept a Stage, issue or change locks. "
         "Inspect the specified source/result with the native image tool. Give a short factual result.\n")


def request(base, path, body=None, *, method=None, binary=False):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  method=method, headers={"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(req, timeout=180) as response:
        return response.read() if binary else json.load(response)


def page_ref(document):
    return {key: document.get(key) for key in ("runId", "assetSha256", "revisionRef")} | {"pageIndex": 0}


def context(studio, project_id, source, domain="design"):
    state = request(studio, "/api/state?" + urlencode({"run": source}))
    return request(studio, "/api/intents/context", {
        "projectId": project_id, "sourceRunId": source, "stateDigest": state["stateDigest"],
        "utterance": "Continue this review", "decisionContext": {"domain": domain},
    })


def seed(services, output):
    _, base, studio, _, runtime_id, project = services
    fixture = bench.project_fixture()
    proxy = f"/api/runtime/projects/{runtime_id}/studio"
    state = request(studio, f"/api/state?run={fixture.REFERENCE_RUN_ID}")
    proposal = request(base, proxy + "/api/proposals", {
        "projectId": fixture.PROJECT_ID, "sourceRunId": fixture.REFERENCE_RUN_ID,
        "stateDigest": state["stateDigest"], "targetComponentId": "portico", "elementId": "portico-cornice",
        "utterance": "set height to 0.35",
    })
    started = request(base, proxy + f"/api/proposals/{proposal['proposalId']}/candidate", {})
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = request(studio, f"/api/jobs/{started['jobId']}")
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(.5)
    if job["status"] != "succeeded":
        raise RuntimeError(f"Fixture geometry failed: {job}")
    candidate = request(studio, f"/api/candidates/{started['candidateId']}")
    model = next(row["modelSource"] for row in candidate["artifacts"] if row["format"] == "3dm")
    # A visible offending example is an uploaded fixture, not a new drawing algorithm.
    from reportlab.pdfgen import canvas
    stream = BytesIO()
    page = canvas.Canvas(stream, pagesize=(420, 300))
    page.setFont("Helvetica", 13)
    page.drawString(25, 268, "Portico review / original feedback source")
    page.drawString(25, 244, "Synergy unlocks potential through seamless composition.")
    page.rect(30, 55, 230, 125)
    page.setLineWidth(.6)
    for x in range(30, 251, 10):
        page.line(x, 55, min(x + 80, 260), 55 + min(80, 260 - x))
    page.drawString(280, 120, "Diagonal hatch")
    page.save()
    uploaded = request(base, proxy + "/api/documents", {"projectId": fixture.PROJECT_ID,
        "fileName": "Feedback source.pdf", "mimeType": "application/pdf",
        "contentBase64": base64.b64encode(stream.getvalue()).decode()})
    result = {"source": candidate["candidateId"], "modelSource": model, "document": uploaded,
              "head": (project / "HEAD").read_text(),
              "branches": bench.FilesystemProjectRepository.open(project).read_design_branches()}
    bench.write_json(output / "setup.json", result)
    return result


def turn(services, output, name, source, words, args):
    app, base, studio, monitor, runtime_id, project = services
    folder = output / name
    if args.resume and (folder / "report.json").exists():
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        if report["status"] == "idle":
            return report
    folder.mkdir(exist_ok=True)
    session = request(base, "/api/chat/sessions", {"projectDir": str(project), "provider": "codex", "model": args.model})
    state = request(studio, "/api/state?" + urlencode({"run": source}))
    content = RULES + words
    if len(content) > 2000:
        raise ValueError("Feedback fixture must fit the real raw-language limit without truncating user words")
    posted = request(base, f"/api/chat/sessions/{session['id']}/messages", {
        "projectId": session["projectId"], "content": content, "contextMode": "project",
        "designContext": {"sourceRunId": source, "stateDigest": state["stateDigest"]},
    })
    message_id = next(row["id"] for row in reversed(posted["messages"]) if row["role"] == "user")
    print(json.dumps({"step": name, "status": "running", "session": session["id"]}), flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        detail = request(base, f"/api/chat/sessions/{session['id']}")
        if detail["status"] != "running":
            break
        time.sleep(1)
    else:
        request(base, f"/api/chat/sessions/{session['id']}/stop", {})
        raise TimeoutError(f"{name}: original project and chat retained")
    bench.write_json(folder / "chat.json", detail)
    failures = []
    snapshot = bench.monitor_snapshot(monitor, failures)
    trace = next((row for row in (snapshot or {}).get("traces", []) if row["turn_id"] == message_id), None)
    saved = app.state.chats._sessions[session["id"]]
    runtime = request(base, f"/api/runtime/projects/{runtime_id}")
    operations = [row for row in runtime.get("operations", []) if row.get("sessionId") == session["id"]]
    result = {"step": name, "status": detail["status"], "session_id": session["id"], "message_id": message_id,
              "provider_session_id": saved.acpSessionId or saved.nativeSessionId,
              "source": source, "operations": operations, "trace": trace, "monitor_failures": failures,
              "decisions": request(studio, "/api/decisions")["decisions"],
              "documents": request(studio, "/api/documents")["documents"],
              "user_corrections_after_initial_feedback": 0,
              "human_acceptance": "not evaluated; scripted acceptance fixture"}
    bench.write_json(folder / "report.json", result)
    print(json.dumps({"step": name, "status": detail["status"], "provider_session": result["provider_session_id"]}), flush=True)
    if detail["status"] != "idle":
        raise RuntimeError(f"{name}: provider failed: {detail.get('error')}")
    return result


def observed_sheet(services, output, name, report, *, banned=(), required=None):
    studio = services[2]
    documents = [row for row in report["documents"] if row.get("drawingId") == "arch364-technical"]
    if not documents:
        raise AssertionError(f"{name}: no technical sheet was produced")
    # Identify this turn's exact artifact by its operation response, not by recency.
    shas = {row.get("result", {}).get("assetSha256") for row in report["operations"]}
    matching = [row for row in documents if row["assetSha256"] in shas]
    if matching:
        documents = matching
    # A fresh fixture has one technical sheet until the explicit revocation study.
    if required:
        documents = [row for row in documents if required.lower() in " ".join(row.get("viewRecipe", {}).get("notes", [])).lower()]
    assert len(documents) == 1, f"Ambiguous output pages: {[page_ref(row) for row in documents]}"
    document = documents[0]
    query = {"runId": document["runId"]}
    if document.get("revisionRef"):
        query["revisionRef"] = document["revisionRef"]
    data = request(studio, f"/api/documents/{document['assetSha256']}/bytes?" + urlencode(query), binary=True)
    import fitz
    with fitz.open(stream=data, filetype="pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        pdf[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(output / name / "observed.png")
    (output / name / "observed.pdf").write_bytes(data)
    (output / name / "observed.txt").write_text(text, encoding="utf-8")
    assert all(word.lower() not in text.lower() for word in banned), text
    if required:
        assert required.lower() in text.lower(), text
    assert "board/export" in (output / name / "chat.json").read_text(encoding="utf-8"), "Provider did not observe its registered page"
    return {"source": page_ref(document), "text": text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.output.is_absolute() or (args.output.exists() and not args.resume):
        parser.error("Use a new absolute external directory, or explicitly resume its retained evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    import subprocess
    bench.write_json(args.output / "build.json", {"revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=bench.ROOT, text=True).strip(), "model": args.model})
    (args.output / "source.diff").write_bytes(subprocess.check_output(["git", "diff", "HEAD"], cwd=bench.ROOT))
    try:
        with isolated_services(args.output) as services:
            setup = json.loads((args.output / "setup.json").read_text(encoding="utf-8")) if args.resume and (args.output / "setup.json").exists() else seed(services, args.output)
            source, page = setup["source"], json.dumps(page_ref(setup["document"]))
            copy = turn(services, args.output, "save-copy", source,
                f"Inspect this registered page: {page}. For this project's future drawing notes, avoid the words 'synergy', 'unlock potential', and 'seamless'. Use short facts about visible geometry. Remember this feedback; do not generate the replacement yet.", args)
            copy_decision = next(row for row in copy["decisions"] if row["scope"]["domain"] == "copy" and row["disposition"] == "avoid")
            assert copy_decision["messageSource"] == {"sessionId": copy["session_id"], "messageId": copy["message_id"]}
        # Reopen all services. The next provider gets no earlier conversation or correction.
        with isolated_services(args.output) as services:
            app, base, studio, monitor, runtime_id, project = services
            copied = turn(services, args.output, "fresh-copy-fit", source,
                "Produce an arch364-technical review sheet of the current exact model with two short notes about the base and cornice. Choose a readable scale that fits its fixed paper size; inspect the registered result page.", args)
            assert copied["provider_session_id"] and copied["provider_session_id"] != copy["provider_session_id"]
            copy_output = observed_sheet(services, args.output, "fresh-copy-fit", copied, banned=("synergy", "unlock potential", "seamless"))
            keep = turn(services, args.output, "save-keep", source,
                "Inspect this model's front view. In this project's later design revisions, keep portico-base's shape and position unchanged. The cornice may still change. Remember this feedback without modifying geometry now.", args)
            keep_decision = next(row for row in keep["decisions"] if row["disposition"] == "keep" and row["targetRef"] == "entity:portico-base")
            kept = turn(services, args.output, "fresh-keep", source,
                "Make the existing two-part portico assembly 0.20 m taller by choosing an editable height that respects retained project decisions. Produce one reversible candidate and inspect its front view.", args)
            candidates = [row["candidateId"] for row in kept["operations"] if row.get("candidateId") and row.get("jobId")]
            assert candidates, "No real next candidate"
            repository = bench.FilesystemProjectRepository.open(project)
            before, after = retained_record(repository, source), retained_record(repository, candidates[-1])
            objects = lambda record: {row["entity_id"]: row for row in record["entities"]}
            assert objects(before)["portico-base"] == objects(after)["portico-base"]
            assert abs(objects(after)["portico-cornice"]["fields"]["params"]["height"] - .55) < 1e-8
            assert before["parameters"] == after["parameters"] and before["relations"] == after["relations"]
            chat_text = (args.output / "fresh-keep" / "chat.json").read_text(encoding="utf-8")
            delta_refs = [ref for ref in repository.list_json(run=repository.load_run(candidates[-1]),
                destination=bench.project_fixture().run_records(candidates[-1])) if ref.record_kind == STUDIO_CANDIDATE_DELTA]
            assert len(delta_refs) == 1
            delta = repository.load_json(delta_refs[0])
            assert keep_decision["targetRef"] in delta["operator"]["protected"]
            assert 'model-view' in chat_text
            # Independently exercise the actual existing owner, with the saved ref.
            state = request(studio, "/api/state?" + urlencode({"run": source}))
            conflict = request(base, f"/api/runtime/projects/{runtime_id}/studio/api/proposals", {
                "projectId": keep_decision["projectId"], "sourceRunId": source, "stateDigest": state["stateDigest"],
                "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 0.8",
                "keep": [keep_decision["targetRef"]],
            })
            assert conflict["status"] == "conflict" and keep_decision["targetRef"] in conflict["impact"]["conflicts"]
            bench.write_json(args.output / "keep-owner-refusal.json", conflict)
            hatch = turn(services, args.output, "hatch-defer", source,
                f"Inspect this registered page: {page}. For this project's future drawing output, avoid the diagonal hatch shown here. Remember this feedback. Check the public drawing request schema and report whether it can control that hatch; if unsupported, explicitly defer the change and do not claim success from an unhatchable default sheet.", args)
            hatch_decision = next(row for row in hatch["decisions"] if row["targetRef"] == "drawing:hatch")
            assert hatch_decision["messageSource"]["messageId"] == hatch["message_id"]
            design_ids = {row["decisionId"] for row in context(studio, keep_decision["projectId"], source)["scopedDecisions"]}
            assert keep_decision["decisionId"] in design_ids and copy_decision["decisionId"] not in design_ids and hatch_decision["decisionId"] not in design_ids
            revoked = turn(services, args.output, "revoke-copy", source,
                "Withdraw only my earlier feedback about the wording of this project's drawing notes. Keep the separate design and hatch feedback. Do not modify geometry or generate a drawing.", args)
            revoked_copy = next(row for row in revoked["decisions"] if row["decisionId"] == copy_decision["decisionId"])
            assert revoked_copy["status"] == "revoked" and revoked_copy["messageSource"] == copy_decision["messageSource"]
            assert revoked_copy["revisionMessageSource"]["messageId"] == revoked["message_id"]
            assert not context(studio, keep_decision["projectId"], source, "copy")["scopedDecisions"]
            next_copy = turn(services, args.output, "after-revoke", source,
                "Produce an arch364-technical sheet of this exact model with the single note exactly: Synergy unlocks potential. Choose a readable scale that fits; inspect the registered result page.", args)
            after_revoke = observed_sheet(services, args.output, "after-revoke", next_copy, required="Synergy unlocks potential.")
            assert (project / "HEAD").read_text() == setup["head"]
            assert repository.read_design_branches() == setup["branches"]
            bench.write_json(args.output / "result.json", {"copy": copy_output, "keep_candidate": candidates[-1],
                "keep_owner_refused_conflicting_edit": True, "hatch": "deferred: public sheet API has no hatch control",
                "scope_mismatch_excluded": True, "revoked_copy": after_revoke,
                "head_and_stages_unchanged": True, "parameter_locks_unchanged": True,
                "fresh_provider_sessions": [copy["provider_session_id"], copied["provider_session_id"], kept["provider_session_id"], next_copy["provider_session_id"]],
                "scripted_repeat_corrections": 0, "human_acceptance": "not evaluated"})
    except Exception as exc:
        detail = exc.read().decode("utf-8", errors="replace") if isinstance(exc, HTTPError) else str(exc)
        bench.write_json(args.output / "failure.json", {"type": type(exc).__name__, "detail": detail})
        raise


if __name__ == "__main__":
    main()
