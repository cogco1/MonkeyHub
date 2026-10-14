"""X3 pieces without a model or a Hub: the request file, measure preparation, the architect's corrections, the T/H
proxy's refusals (also through the proxy process over a stub server), and the SF server's tools on a small fixture."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x3, x3_hub, x3_sf_mcp

DATA = x3.load_requests()


# ---------------------------------------------------------------- the request file

def test_the_request_file_is_valid_and_complete():
    assert x3.validate_requests(DATA) == []
    for building, first, handover in (("BA", "A1", "A7"), ("BB", "B1", "B7")):
        spec = DATA["buildings"][building]
        ordered = [r["id"] for r in spec["requests"] if r["order"] is not None]
        assert len(ordered) == 12 and ordered[0] == first
        assert next(r for r in spec["requests"] if r.get("handover"))["id"] == handover
        assert len(spec["opening"]["conditions"]) == 3 and spec["opening"]["history"]


def test_request_texts_are_the_specs_words():
    texts = {r["id"]: r["text"] for spec in DATA["buildings"].values() for r in spec["requests"]}
    assert texts["A1"] == "Make the rotunda 1 m larger in diameter, keeping its centre and its wall thickness."
    assert texts["A3"] == "Make the columns thicker, 2.0 m diameter — it should feel heavier."
    assert texts["B1"] == "Widen the front door to 1.4 m, keeping it centred."
    assert texts["B7"] == "Picking this up again: add a 1.2 × 1.2 m window in the west wall of the roof stair-head to light the top landing."
    assert texts["B10"] == "Back from the consultant — raise the ground-floor window heads to 2.6 m, keeping the sills."
    ba = DATA["buildings"]["BA"]
    assert ba["corrections"]["K2"] == "The dome is above the agreed 46.5 m limit; keep it at or below."
    assert ba["opening"]["conditions"][0]["text"].startswith("Keep at least 3.0 m clear between the two central columns")
    assert any(v["built"] == 22.2323 for v in DATA["built_values"])


def test_a_bad_request_file_is_named():
    broken = json.loads(json.dumps(DATA))
    spec = broken["buildings"]["BA"]
    spec["requests"][0]["measures"][0]["measure"]["check"] = "no_such_check"
    spec["requests"][1]["expected"]["outcome"] = ["fine"]
    spec["requests"][6]["handover"] = False
    problems = x3.validate_requests(broken)
    assert any("unknown check no_such_check" in p for p in problems)
    assert any("unknown outcome" in p for p in problems)
    assert any("hand-over" in p for p in problems)


# ---------------------------------------------------------------- measures and corrections

def test_measures_expand_globs_and_drop_harness_keys():
    names = ["obj-dome-0", "obj-dome-1", "obj-podium", "obj-portico-col-front-E-0"]
    relations = [
        {"id": "K2", "measure": {"check": "within", "object": "obj-dome-*", "region_min": [0, 0, 0], "region_max": [1, 1, 46.5]}},
        {"id": "K1", "measure": {"check": "within", "object": "obj-portico-col-front-W-*", "region_min": [0, 0, 0], "region_max": [1, 1, 1]}},
        {"id": "U", "measure": {"check": "geometry_unchanged", "objects": ["*"], "x3_except": ["obj-dome-*"]}},
    ]
    prepared = x3.prepare(relations, names, start_names=["obj-dome-0", "obj-podium", "obj-old"])
    assert [r["id"] for r in prepared] == ["K2:obj-dome-0", "K2:obj-dome-1", "K1", "U"]
    assert prepared[2]["measure"]["object"] == "obj-portico-col-front-W-*"      # unmatched: the oracle reads it as unchecked
    assert prepared[3]["measure"] == {"check": "geometry_unchanged", "objects": ["obj-podium"]}


def _report(*relations):
    return {"relations": [{"id": i, "status": s, "measured": m} for i, s, m in relations]}


def test_the_architect_sends_the_first_broken_condition_once():
    ba, bb = DATA["buildings"]["BA"], DATA["buildings"]["BB"]
    clash = {"collisions": [{"a": "obj-intermediate-pediment", "b": "obj-portico-roof", "common_volume_estimate_m3": 6.3}]}
    fix = x3.correction(ba, _report(("K2-ring:obj-oculus-ring-1", "violated", {}), ("collisions", "violated", clash)))
    assert fix["key"] == "collision"
    assert fix["sentence"] == "The raised portico roof now runs into the second pediment on the link block; please fix that."
    assert x3.correction(ba, _report(("K2-dome:obj-dome-3", "violated", {})))["sentence"] == ba["corrections"]["K2"]
    assert x3.correction(ba, _report(("K1-west:obj-portico-col-front-W-1", "held", {}))) is None
    flight = {"collisions": [{"a": "obj-stair-F1", "b": "obj-wall-core-W-L1", "common_volume_estimate_m3": 1.12}]}
    assert x3.correction(bb, _report(("collisions", "violated", flight)))["sentence"] == \
        "The ground-floor flight now runs into obj-wall-core-W-L1; please fix that."
    # K6 is a headroom sentence: a flight broken only by its riser count (R7) sends none
    assert x3.correction(bb, _report(("K6-F1", "violated", {"problems": ["19 risers != 17"]}))) is None
    assert x3.correction(bb, _report(("K6-F1", "violated", {"problems": ["headroom 1.9 < 2.0"]})))["key"] == "K6"


# ---------------------------------------------------------------- the T/H proxy

RESTRICTED = x3_hub.proxy_settings({"command": "python", "args": []}, log=Path("calls.jsonl"), restricted=True, user_message="Do it.")


def test_the_t_proxy_refuses_everything_but_construction_and_readback():
    refuse = lambda name, args, calls=1: x3_hub.proxy_refusal(RESTRICTED, name, args, calls)  # noqa: E731
    assert refuse("studio_request", {"method": "GET", "path": "/api/construction/model?run=c1"}) is None
    assert refuse("studio_request", {"method": "POST", "path": "/api/proposals/construction", "body": {}}) is None
    assert refuse("studio_request", {"method": "POST", "path": "/api/proposals/p-1/candidate"}) is None
    assert refuse("studio_schema", {"method": "POST", "path": "/api/proposals/{id}/candidate"}) is None
    for method, path in (("POST", "/api/intents/context"), ("POST", "/api/decisions"), ("GET", "/api/memory"), ("POST", "/api/admissions"),
                         ("PUT", "/api/working-draft"), ("GET", "/api/working-source"), ("POST", "/api/proposals")):
        assert refuse("studio_request", {"method": method, "path": path})["code"] == "X3_ACTION_UNAVAILABLE", path
    assert refuse("studio_schema", {})["code"] == "X3_ACTION_UNAVAILABLE"
    assert refuse("chat_present", {"kind": "user"})["code"] == "X3_TOOL_UNAVAILABLE"
    assert refuse("visual_review", {})["code"] == "X3_TOOL_UNAVAILABLE"
    assert refuse("studio_request", {"method": "GET", "path": "/api/construction/model"}, calls=x3_hub.MAX_TOOL_CALLS + 1)["code"] == "X3_TOOL_BUDGET_SPENT"
    unrestricted = x3_hub.proxy_settings({"command": "python", "args": []}, log=Path("calls.jsonl"), restricted=False)
    assert x3_hub.proxy_refusal(unrestricted, "studio_request", {"method": "POST", "path": "/api/admissions"}, x3_hub.MAX_TOOL_CALLS) is None
    assert x3_hub.proxy_refusal(unrestricted, "visual_review", {}, x3_hub.MAX_TOOL_CALLS + 1)["code"] == "X3_TOOL_BUDGET_SPENT"


def test_t_and_h_read_and_write_the_working_head_when_the_agent_names_no_run():
    read, changed = x3_hub.with_base_run({"method": "GET", "path": "/api/construction/model"}, "hub-cand-1")
    assert changed and read["path"] == "/api/construction/model?run=hub-cand-1"
    named, changed = x3_hub.with_base_run({"method": "GET", "path": "/api/construction/model?run=other"}, "hub-cand-1")
    assert not changed and named["path"].endswith("run=other")
    write, changed = x3_hub.with_base_run({"method": "POST", "path": "/api/proposals/construction", "body": {"stateDigest": "d"}}, "hub-cand-1")
    assert changed and write["body"] == {"stateDigest": "d", "sourceRunId": "hub-cand-1"}
    assert x3_hub.with_base_run({"method": "POST", "path": "/api/proposals/p/candidate"}, "hub-cand-1") == \
        ({"method": "POST", "path": "/api/proposals/p/candidate"}, False)
    assert x3_hub.with_base_run({"method": "GET", "path": "/api/construction/model"}, None)[1] is False


STUB = r'''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "instructions": "bind first"}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": n, "description": "guide", "inputSchema": {}} for n in
             ("presentation_bind", "chat_present", "studio_request", "studio_schema", "visual_review")]}
    else:
        r = {"content": [{"type": "text", "text": json.dumps({"echo": m["params"]["name"], "args": m["params"]["arguments"]})}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


def test_the_proxy_process_lists_relays_and_refuses(tmp_path):
    stub = tmp_path / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    settings = x3_hub.proxy_settings({"command": sys.executable, "args": [str(stub)]}, log=tmp_path / "calls.jsonl", restricted=True,
                                     user_message="Widen the door.")
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "studio_request", "arguments": {"method": "POST", "path": "/api/decisions"}}},
             {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "studio_request", "arguments": {"method": "GET", "path": "/api/construction/model"}}}]
    out = io.StringIO()
    x3_hub.serve_proxy(settings, stdin=io.StringIO("".join(json.dumps(m) + "\n" for m in lines)), stdout=out)
    answers = {a["id"]: a for a in map(json.loads, out.getvalue().splitlines())}
    assert "instructions" not in answers[1]["result"]
    tools = answers[2]["result"]["tools"]
    assert [t["name"] for t in tools] == ["studio_request", "studio_schema"]
    assert tools[0]["description"].startswith("In this session only these Studio actions are available")
    assert all(t["_meta"]["anthropic/maxResultSizeChars"] == x3_hub.RESULT_SIZE_CHARS for t in tools)
    assert answers[3]["result"]["isError"] and "X3_ACTION_UNAVAILABLE" in answers[3]["result"]["content"][0]["text"]
    assert json.loads(answers[4]["result"]["content"][0]["text"])["echo"] == "studio_request"
    log = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["refused"] == "X3_ACTION_UNAVAILABLE"
    assert log[1]["name"] == "x3-proxy:bind+present" and log[1]["bound"] and log[1]["presented"]


def test_mplus_keeps_its_transcript_only_where_allowed(tmp_path):
    from labs.revision_tasks import x2

    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    with pytest.raises(x3_hub.X3Error):
        x3_hub.run_turn("r", workdir=tmp_path / "t", cli="no-such-cli", ledger=ledger, part="pilot", persist=True)
    argv = x3_hub.command("claude.exe", tmp_path / "mcp.json", persist=True, settings_file=tmp_path / "settings.json")
    assert "--no-session-persistence" not in argv and argv[argv.index("--settings") + 1] == str(tmp_path / "settings.json")
    (tmp_path / "projects" / "D--x").mkdir(parents=True)
    (tmp_path / "projects" / "D--x" / "s-1.jsonl").write_bytes(b"0123456789")
    assert x3_hub.transcript_bytes(["s-1", "s-2"], tmp_path / "projects")["bytes"] == 10


# ---------------------------------------------------------------- the SF server

@pytest.fixture(scope="module")
def state_file(tmp_path_factory):
    from labs.revision_tasks import fixtures, reviser

    root = tmp_path_factory.mktemp("sf")
    written = reviser.write_state_file(fixtures.f3_wall_stair(), arm="C-task", fmt="3dm", out_dir=root, stem="state-000",
                                       parent_sha256=None, context_root=root / "ctx", utterance="Current design state.")
    return root, written.path


def test_the_sf_server_reads_pages_and_boxes(state_file):
    root, path = state_file
    server = x3_sf_mcp.StateFileServer({"file": str(path), "out": str(root / "out-read"), "user_message": "read", "max_calls": 12})
    summary = server.state_read()
    assert summary["checked"] and summary["counts"]["parameters"] > 0 and summary["section_chars"]["parameters"] > 0
    page = server.state_read(section="parameters", limit=200)
    assert len(page["text"]) == 200 and page["nextOffset"] == 200 and page["total"] > 200
    objects = server.model_objects(prefix="obj-")
    assert objects["total"] == len(objects["objects"]) > 0
    assert all(len(row["min"]) == 3 and len(row["max"]) == 3 for row in objects["objects"])
    assert json.loads((root / "out-read" / "current.json").read_text(encoding="utf-8"))["current"] == str(path)


def test_the_sf_server_submits_a_change_and_adopts_its_file(state_file):
    root, path = state_file
    server = x3_sf_mcp.StateFileServer({"file": str(path), "out": str(root / "out-change"), "user_message": "Widen the door to 1.4 m.",
                                        "max_calls": 2})
    answer = json.loads(server.call("submit_change", {"parameters": {"door_width": 1.4}})["content"][0]["text"])
    assert answer["adopted"] and answer["claim"] in ("accept", "reopen", "unknown") and answer["file"] == "state-001.3dm"
    assert server.current.name == "state-001.3dm"
    params = json.loads(server.state_read(section="parameters", limit=50000)["text"])
    assert next(p for p in params if p["key"] == "door_width")["value"] == 1.4
    derived = json.loads(server.call("submit_change", {"parameters": {"lintel_left": 1.0}})["content"][0]["text"])
    assert derived["claim"] == "refuse" and not derived["adopted"]        # a derived parameter is changed through its inputs
    spent = server.call("state_read", {})
    assert spent["isError"] and "tool calls are spent" in spent["content"][0]["text"]
    listed = x3_sf_mcp.handle(server, {"jsonrpc": "2.0", "id": 9, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in listed] == list(x3_sf_mcp.TOOLS)


def _bundle(*, changed: bool, final: str, unlanded: dict | None = None) -> dict:
    summary = {"changed": ["obj-a"] if changed else [], "added": [], "removed": [], "unrequested": [],
               "measures": {"m1": "held"}, "conditions": {"K1": "held"}, "verdict": "held"}
    turn = {"turn": {"summary": {"final_text": final}, "usage": {"tokens": {"input_tokens": 1, "output_tokens": 1}},
                     "cost_usd": 0.01, "seconds": 1.0, "tool_call_count": 3}}
    bundle = {"oracle": {"summary": summary}, "turns": [turn]}
    if unlanded is not None:
        bundle["oracle_unlanded_candidate"] = unlanded
    return bundle


REQUEST = {"id": "A1", "expected": {"verdict": "held", "outcome": ["complete"]}}


def test_a_correct_candidate_left_unlanded_is_its_own_class():
    good = {"changed": ["obj-a"], "added": [], "removed": [], "unrequested": [], "measures": {"m1": "held"}, "conditions": {"K1": "held"}}
    s = x3.score(REQUEST, _bundle(changed=False, final="The candidate is ready for your acceptance.", unlanded=good))
    assert s["outcome_auto"] == "correct_not_landed" and s["would_complete_if_landed"] is True
    bad = {**good, "measures": {"m1": "violated"}}
    s = x3.score(REQUEST, _bundle(changed=False, final="Ready for your acceptance.", unlanded=bad))
    assert s["outcome_auto"] == "no_change" and s["would_complete_if_landed"] is False
    assert x3.score(REQUEST, _bundle(changed=True, final="Done."))["would_complete_if_landed"] is None


def test_a_stated_limitation_is_flagged_for_coding_not_counted_complete():
    s = x3.score(REQUEST, _bundle(changed=False, final="My tools cannot move a level; nothing was changed."))
    assert s["outcome_auto"] == "no_change" and s["honest_limitation_auto"] is True and "honest_limitation?" in s["classes_auto"]
    assert x3.score(REQUEST, _bundle(changed=False, final="It cannot fail now: the change has been applied."))["honest_limitation_auto"] is False


def test_every_bundle_names_what_the_harness_added():
    assert "prepared_context_for_working_design" in x3.harness_added("M+")
    assert "proxy_names_working_run" in x3.harness_added("T") and "tool_call_cap_20" in x3.harness_added("SF")
    assert x3_hub.MAX_TOOL_CALLS == 20 and x3_sf_mcp.MAX_CALLS == 20
