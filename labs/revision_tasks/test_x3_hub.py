"""X3 driver pieces that need neither a model nor a running Hub: MCP config, CLI command, environments, the stream
summary, the ledger gate and the choice of a run's preview model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x2, x3_hub


def test_mcp_config_is_the_external_binding_of_monkeyhubs_own_server():
    config = x3_hub.mcp_config(hub_url="http://127.0.0.1:5000", project_dir=Path("D:/p/revision-tasks"), session_id="x3-abc",
                               python="python.exe")
    server = config["mcpServers"]["monkeyhub"]
    assert list(config["mcpServers"]) == ["monkeyhub"]
    assert server["command"] == "python.exe" and server["alwaysLoad"] is True
    args = server["args"]
    assert Path(args[0]) == x3_hub.MCP_SERVER and x3_hub.MCP_SERVER.is_file()
    assert args[1] == "--mcp"
    pairs = dict(zip(args[2::2], args[3::2]))
    assert pairs == {"--hub-url": "http://127.0.0.1:5000", "--project-dir": str(Path("D:/p/revision-tasks")),
                     "--source-session-id": "x3-abc", "--provider": "claude"}
    assert "--chat-id" not in args          # the external binding, not a Hub-owned conversation


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_command_carries_the_fairness_flags_and_monkeyhubs_tools_only(tmp_path):
    argv = x3_hub.command("claude.exe", tmp_path / "mcp.json", max_turns=12)
    assert argv[:2] == ["claude.exe", "-p"]
    assert _flag(argv, "--model") == "claude-opus-5-5"
    assert _flag(argv, "--output-format") == "stream-json" and "--verbose" in argv
    assert _flag(argv, "--permission-mode") == "dontAsk"
    assert _flag(argv, "--setting-sources") == "project,local"
    assert "--disable-slash-commands" in argv and "--strict-mcp-config" in argv
    assert _flag(argv, "--tools") == ""                      # no built-in tool
    assert _flag(argv, "--disallowedTools") == "WebSearch,WebFetch"
    assert _flag(argv, "--mcp-config") == str(tmp_path / "mcp.json")
    assert _flag(argv, "--max-turns") == "12" and _flag(argv, "--max-budget-usd") == "3"
    allowed = _flag(argv, "--allowedTools").split(",")
    assert set(allowed) == {f"mcp__monkeyhub__{t}" for t in ("presentation_bind", "chat_present", "studio_schema", "studio_request",
                                                               "visual_review", "attachment_read", "fab_request")}
    assert "--no-session-persistence" in argv and "--resume" not in argv and "--append-system-prompt-file" not in argv


def test_a_resumed_or_persisted_turn_keeps_its_session(tmp_path):
    resumed = x3_hub.command("claude.exe", tmp_path / "mcp.json", resume="sess-1", system_prompt_file=tmp_path / "sys.md")
    assert _flag(resumed, "--resume") == "sess-1" and "--no-session-persistence" not in resumed
    assert _flag(resumed, "--append-system-prompt-file") == str(tmp_path / "sys.md")
    persisted = x3_hub.command("claude.exe", tmp_path / "mcp.json", persist=True)
    assert "--no-session-persistence" not in persisted and "--resume" not in persisted


def test_environments_are_isolated_and_hide_cuda(tmp_path):
    parent = {"PATH": "p", "APPDATA": "C:/Users/u/AppData/Roaming", "ARCHFLOW_STUDIO_PORT": "1", "MONKEYMONITOR_DATA_DIR": "x",
              "CLAUDE_CODE_TERMINAL_MCP_TOOLS": "mcp__host__done", "CLAUDE_CONFIG_DIR": "C:/elsewhere"}
    services = x3_hub.service_environment(parent, tmp_path)
    assert services["APPDATA"] == str(tmp_path / "settings") and services["LOCALAPPDATA"] == str(tmp_path / "local")
    assert services["CUDA_VISIBLE_DEVICES"] == "" and services["PYTHONUTF8"] == "1"
    assert "ARCHFLOW_STUDIO_PORT" not in services and "MONKEYMONITOR_DATA_DIR" not in services
    cli = x3_hub.cli_environment(parent)
    assert cli["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and cli["CUDA_VISIBLE_DEVICES"] == ""
    assert cli["APPDATA"] == parent["APPDATA"] and cli["PATH"] == "p"          # the CLI keeps its own sign-in
    assert not {"ARCHFLOW_STUDIO_PORT", "MONKEYMONITOR_DATA_DIR", "CLAUDE_CODE_TERMINAL_MCP_TOOLS", "CLAUDE_CONFIG_DIR"} & set(cli)
    assert x3_hub.cli_environment(parent, tmp_path / "cfg")["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")


def test_summary_lists_tool_calls_with_paths_and_refusals():
    events = [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-opus-5-5", "permissionMode": "dontAsk",
         "tools": ["mcp__monkeyhub__studio_request"], "mcp_servers": [{"name": "monkeyhub", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Binding."},
                                                      {"type": "tool_use", "id": "t1", "name": "mcp__monkeyhub__presentation_bind", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "{\"chatId\": \"c\"}"}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "mcp__monkeyhub__studio_request",
                                                       "input": {"method": "POST", "path": "/api/admissions", "taskClass": "deterministic_edit",
                                                                 "body": {"results": []}}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": [
            {"type": "text", "text": json.dumps({"code": "ADMISSION_GATE_REFUSED", "detail": "C3", "httpStatus": 409})}]}]}},
        {"type": "result", "subtype": "success", "session_id": "s1", "num_turns": 3, "result": "Done.", "is_error": False,
         "total_cost_usd": 0.5, "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]
    summary = x3_hub.summarize(events)
    assert summary["session_id"] == "s1" and summary["num_turns"] == 3 and summary["final_text"] == "Done."
    assert summary["tool_call_count"] == 2 and summary["tool_errors"] == 1
    bind, admit = summary["tool_calls"]
    assert bind["name"] == "presentation_bind" and bind["server"] == "monkeyhub" and bind["is_error"] is False
    assert admit["method"] == "POST" and admit["path"] == "/api/admissions" and admit["taskClass"] == "deterministic_edit"
    assert admit["body_keys"] == ["results"] and admit["error"]["code"] == "ADMISSION_GATE_REFUSED"
    assert summary["assistant_text"] == ["Binding."]


def test_a_turn_is_refused_before_anything_runs(tmp_path):
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    ledger.add({"part": "pilot", "cost_usd": x2.CAPS["pilot"] - 2.0})
    with pytest.raises(x2.CapReached):          # 2 left of the cap, but a turn may spend 3
        x3_hub.run_turn("r", hub_url="http://127.0.0.1:1", project_dir=tmp_path, session_id="s", workdir=tmp_path / "t",
                        cli="no-such-cli", ledger=ledger, part="pilot")
    with pytest.raises(x3_hub.X3Error):          # a resumable turn needs its own config directory
        x3_hub.run_turn("r", hub_url="http://127.0.0.1:1", project_dir=tmp_path, session_id="s", workdir=tmp_path / "t",
                        cli="no-such-cli", ledger=ledger, part="pilot", resume="sess-1")
    assert not (tmp_path / "t").exists() and len(ledger.path.read_text(encoding="utf-8").splitlines()) == 1


def test_the_result_is_the_working_design_then_an_admitted_then_a_named_candidate():
    choose = x3_hub.choose_result
    assert choose(True, "c2", ["c1", "c2"], [], "") == ("c2", "working_design")
    assert choose(False, "stage-0-001", ["c1", "c2", "c3"], ["c2"], "see c3") == ("c2", "admitted_candidate")
    # the pilot: the edit candidate, then a no-op baseline candidate the answer does not offer as its result
    assert choose(False, "stage-0-001", ["edit-1", "baseline-2"], [], "It's in a candidate run (`edit-1`)") == ("edit-1", "candidate_named_in_answer")
    assert choose(False, "stage-0-001", ["c1", "c2"], ["other-chat"], "") == ("c2", "latest_candidate")
    assert choose(False, "stage-0-001", [], [], "") == ("stage-0-001", "unchanged")


def test_the_harness_lands_only_a_candidate_with_a_checked_model():
    land = x3_hub.choose_landing
    checked = {"c1", "c3"}.__contains__
    # the named candidate failed its readback: the rules go on to the latest candidate with a model
    assert land("head-0", ["c1", "c2"], "see c2", checked) == ("c1", "latest_candidate", ["c2"])
    assert land("head-0", ["c1", "c2", "c3"], "see c3", checked) == ("c3", "candidate_named_in_answer", ["c2"])
    # no candidate has a model: nothing is landed
    assert land("head-0", ["c2"], "see c2", checked) == (None, "unchanged", ["c2"])
    assert land("head-0", [], "", checked) == (None, "unchanged", [])


def test_the_preview_model_is_the_runs_certified_3dm_checked_on_disk(tmp_path):
    model = tmp_path / "runs" / "c1" / "m.preview.3dm"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"3dm bytes")
    sha = hashlib.sha256(b"3dm bytes").hexdigest()
    rows = [{"runId": "c1", "format": "step", "representation": "exact", "available": True, "relativePath": "runs/c1/m.step", "sha256": "0"},
            {"runId": "c0", "format": "3dm", "representation": "preview", "available": True, "relativePath": "runs/c0/a.preview.3dm", "sha256": "1",
             "designStateDigest": "d0"},
            {"runId": "c1", "format": "3dm", "representation": "preview", "available": True, "relativePath": "runs/c1/m.preview.3dm",
             "sha256": sha, "designStateDigest": "d1", "fileName": "m.preview.3dm", "objectCount": 2}]
    found = x3_hub.preview_model(rows, "c1", "d1", tmp_path)
    assert found["path"] == str(model) and found["verified"] is True and found["object_count"] == 2
    assert x3_hub.preview_model(rows, "c1", "d1", tmp_path, asset_sha256=sha)["sha256"] == sha
    assert x3_hub.preview_model(rows, "c1", "other", tmp_path) is None
    assert x3_hub.preview_model(rows, "c1", None, tmp_path, asset_sha256="f" * 64) is None
    model.write_bytes(b"changed")
    assert x3_hub.preview_model(rows, "c1", "d1", tmp_path)["verified"] is False


def test_an_attached_model_is_an_external_asset_of_these_exact_bytes(tmp_path, monkeypatch):
    import base64

    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(json.dumps({"project_id": "revision-tasks"}), encoding="utf-8")
    model = tmp_path / "returned.3dm"
    model.write_bytes(b"3D Geometry File Format consultant")
    sha = hashlib.sha256(model.read_bytes()).hexdigest()
    services = x3_hub.HubServices("http://hub", "http://runtime", "http://monitor", project, "rt-1", tmp_path)
    sent = []
    answer = {"artifactId": sha, "runId": f"studio-model-{sha}", "fileName": "returned.3dm", "sha256": sha, "sizeBytes": 34, "objectCount": 1,
              "representation": "external", "designStateDigest": None, "available": True, "receiptRef": "project://p/r", "modelSource": None}
    monkeypatch.setattr(x3_hub, "studio_write", lambda hub, runtime, method, path, body: sent.append((hub, runtime, method, path, body)) or answer)
    attached = x3_hub.attach_model(services, model, "returned.3dm")
    [(hub, runtime, method, path, body)] = sent
    assert (hub, runtime, method, path) == ("http://hub", "rt-1", "POST", "/api/model-assets")
    assert body == {"projectId": "revision-tasks", "fileName": "returned.3dm", "contentBase64": base64.b64encode(model.read_bytes()).decode("ascii")}
    assert attached["sha256"] == sha and attached["runId"] == f"studio-model-{sha}" and "modelSource" not in attached
    assert x3_hub.attachment_line(attached) == f"(Attached: returned.3dm, model asset {sha}, run studio-model-{sha}.)"
    monkeypatch.setattr(x3_hub, "studio_write", lambda *args: {**answer, "sha256": "0" * 64})
    with pytest.raises(x3_hub.X3Error, match="not external"):
        x3_hub.attach_model(services, model, "returned.3dm")
    monkeypatch.setattr(x3_hub, "studio_write", lambda *args: {**answer, "representation": "composed"})
    with pytest.raises(x3_hub.X3Error):
        x3_hub.attach_model(services, model, "returned.3dm")


def test_gh_names_the_sessions_rhino_by_its_pid_on_calls_that_take_a_slot():
    listed = {"result": {"content": [{"type": "text", "text": json.dumps({"payload": [{"slotId": "aardvark", "pid": 25200, "adopted": True},
                                                                                     {"slotId": "otter", "pid": 4242}]})}]}}
    assert x3_hub.rhino_slot(listed, 4242) == "otter" and x3_hub.rhino_slot(listed, "25200") == "aardvark"
    assert x3_hub.rhino_slot(listed, 1) is None
    assert x3_hub.rhino_slot({"result": {"content": [{"type": "text", "text": "not json"}]}}, 4242) is None
    assert x3_hub.with_slot("run_python", {"script": "x"}, "otter", {"run_python"}) == ({"script": "x", "slot": "otter"}, True)
    assert x3_hub.with_slot("run_python", {"script": "x", "slot": "mine"}, "otter", {"run_python"})[1] is False
    assert x3_hub.with_slot("list_slots", {}, "otter", {"run_python"}) == ({}, False)
    assert x3_hub.with_slot("run_python", {}, None, {"run_python"}) == ({}, False)
    assert x3_hub.rhino_lost('{"error":{"code":"rhino_closed","message":"m"}}') and x3_hub.rhino_lost('{"error":{"code":"slot_not_found"}}')
    assert not x3_hub.rhino_lost('{"payload":"rhino_closed is a word"}')


def test_the_unrestricted_and_restricted_proxies_refuse_as_before_without_gh_settings():
    m = x3_hub.proxy_settings({"command": "python", "args": []}, log=Path("calls.jsonl"), restricted=False)
    assert "deny" not in m and "rhino_pid" not in m and "stderr" not in m
    assert x3_hub.proxy_refusal(m, "anything", {"method": "DELETE", "path": "/x"}, 1) is None
    t = x3_hub.proxy_settings({"command": "python", "args": []}, log=Path("calls.jsonl"), restricted=True, user_message="u")
    assert x3_hub.proxy_refusal(t, "studio_request", {"method": "GET", "path": "/api/construction/model"}, 1) is None
    assert x3_hub.proxy_refusal(t, "studio_request", {"method": "GET", "path": "/api/memory"}, 1)["code"] == "X3_ACTION_UNAVAILABLE"
