"""X2 arm G pieces without a model or a Hub: the request, the prepared context (X3 M's), the answer, the score with
its landing classes, which model is judged against which start, and the budget refusals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, x1, x2, x2_g, x3, x3_hub

TASKS = {t["task_id"]: t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]}
START = {"record": fixtures.f1_portico(), "lab_start": Path("lab-start.3dm"),
         "objects_by_element": {"stylobate": ("stylobate",), "columns": tuple(f"columns-{i}" for i in range(6)),
                                "entablature": ("entablature",), "pediment": ("pediment",)},
         "component_elements": {"portico-base": ["stylobate"], "portico-columns": ["columns"], "portico-entablature": ["entablature"],
                                "portico-roof": ["pediment"]}}


# ---------------------------------------------------------------- the request

def test_the_request_is_x2s_template_with_gs_intro_and_the_prepared_context():
    prompt = x2_g.request_prompt("Make the columns more slender.", "CONTEXT")
    intro = x2_g.INTRO.read_text(encoding="utf-8").strip()
    assert prompt == x2.prompt_for("D", "Make the columns more slender.", "CONTEXT").replace(
        json.loads((x2.PROMPTS / "materials.json").read_text(encoding="utf-8"))["D"], intro)
    assert prompt.startswith("# Request\n\nMake the columns more slender.\n\n# Materials\n\n" + intro + "\n\nCONTEXT")
    # the system prompt is every arm's; only the intro line says what differs
    assert x2_g.SYSTEM == x2.PROMPTS / "system_common.md"
    assert "cannot use tools" in x2_g.SYSTEM.read_text(encoding="utf-8") and "for this request you have MonkeyHub's own tools" in intro
    assert "g_intro.md" in x2.prompt_files_sha256()
    assert "\r" not in x2_g.INTRO.read_text(encoding="utf-8")


def test_the_prepared_context_is_x3_modes_m(monkeypatch, tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"project_id": "revision-tasks"}), encoding="utf-8")
    seen = []

    def call(base, path, method="GET", body=None, **_):
        seen.append((path, method, body))
        return {"contextPack": "ContextPack@1", "source": {"runId": "stage-0-001"}, "scopedDecisions": [{"rawLanguage": "rejected"}]}

    monkeypatch.setattr(x3_hub, "_call", call)
    services = x3_hub.HubServices("http://hub", "http://runtime", "http://monitor", tmp_path, "rt-1", tmp_path)
    head = {"run_id": "stage-0-001", "state_digest": "d" * 64}
    block, info = x2_g.prepared_context(services, "Make the columns more slender.", head)
    prompt, _ = x3.Session.prepared_prompt(x2_g._HubView(services), "Make the columns more slender.", head)
    assert prompt == "Make the columns more slender.\n\n" + block
    assert seen[0] == ("/api/intents/context", "POST", {"utterance": "Make the columns more slender.", "projectId": "revision-tasks",
                                                         "sourceRunId": "stage-0-001", "stateDigest": "d" * 64})
    assert info == {"source_run": "stage-0-001", "chars": len(block)}


def test_g_runs_x2s_tasks():
    assert [t["task_id"] for t in x2_g.tasks(("F1",))] == [t for t in TASKS if t.startswith("F1-")]
    assert [t["task_id"] for t in x2_g.tasks(("F1", "F3"), ("F1-T2", "F3-T1"))] == ["F1-T2", "F3-T1"]


def test_the_answer_is_read_as_every_arms():
    final = 'Built and landed.\n```json\n{"verdict": "held", "changes": {"column_radius": 0.405}, "affected": ["portico-columns"]}\n```'
    assert x2.parse_answer(final) == {"verdict": "held", "changes": {"column_radius": 0.405}, "affected": ["portico-columns"]}


# ---------------------------------------------------------------- score

def _judged(verdict: str, model: bool = True) -> dict:
    return {"model": {"path": "m.3dm", "source": "monkeyhub_export"} if model else None, "oracle_verdict": verdict}


HELD = {"verdict": "held", "changes": {"column_radius": 0.405}, "affected": ["portico-columns"],
        "conditions": ["p1-columns-on-stylobate", "p2-entablature-on-columns", "k1-central-clear"]}


def test_a_landed_correct_change_is_a_usable_successor():
    s = x2_g.score(HELD, TASKS["F1-T2"], "F1", _judged("held"), "working_design", START)
    assert s["verdict_ok"] and s["category_ok"] and s["verdict_category"] == "held"
    assert s["usable_successor"] and s["executed"] and not s["false_accept"]
    assert s["landing"] == "landed" and s["landed"] and s["correct_not_landed"] is False and s["would_complete_if_landed"] is None
    assert s["affected"] == ["columns"] and s["affected_recall"] == 1.0 and s["recheck_recall"] == 1.0   # a geometry id reads as its element


def test_a_correct_candidate_left_unlanded_is_its_own_class():
    s = x2_g.score(HELD, TASKS["F1-T2"], "F1", _judged("held"), "latest_candidate", START)
    assert s["landing"] == "correct_not_landed" and s["correct_not_landed"] and s["would_complete_if_landed"] is True
    assert s["usable_successor"]                       # as x2 counts the harness's candidate; landing says it did not land
    wrong = x2_g.score(HELD, TASKS["F1-T2"], "F1", _judged("violated"), "admitted_candidate", START)
    assert wrong["landing"] == "candidate_not_landed" and wrong["would_complete_if_landed"] is False and wrong["false_accept"]
    unjudged = x2_g.score(HELD, TASKS["F1-T2"], "F1", _judged("unavailable"), "latest_candidate", START)
    assert unjudged["would_complete_if_landed"] is None and unjudged["landing"] == "candidate_not_landed"


def test_only_a_held_or_violated_answer_counts_its_model_as_executed():
    refuse = {"verdict": "refuse", "changes": None, "affected": [], "conditions": ["p5-clear-spacing"]}
    s = x2_g.score(refuse, TASKS["F1-T6"], "F1", _judged("violated"), "latest_candidate", START)
    assert s["verdict_ok"] and s["category_ok"] and not s["executed"] and s["oracle_verdict"] == "unavailable"
    assert s["left_model_oracle_verdict"] == "violated" and not s["usable_successor"] and not s["false_accept"]
    # manifest 7.2: violated and refuse are one category ("blocked")
    violated = {**refuse, "verdict": "violated"}
    s = x2_g.score(violated, TASKS["F1-T6"], "F1", _judged("unavailable", model=False), "unchanged", START)
    assert not s["verdict_ok"] and s["category_ok"] and s["verdict_category"] == "blocked"
    assert s["landing"] == "no_change" and s["left_model_oracle_verdict"] is None and not s["executed"]


def test_a_held_answer_on_a_task_that_does_not_hold_is_a_false_accept():
    s = x2_g.score({**HELD, "changes": {"column_radius": 0.65}}, TASKS["F1-T3"], "F1", _judged("violated"), "working_design", START)
    assert s["false_accept"] and not s["category_ok"] and not s["usable_successor"]
    s = x2_g.score(None, TASKS["F1-T3"], "F1", _judged("unavailable", model=False), "unchanged", START)
    assert not s["parsed"] and s["verdict_category"] is None and not s["category_ok"] and not s["false_accept"]


def test_parameter_changes_and_the_answers_statement():
    start = {"column_radius": 0.45, "column_clear_spacing": 1.14, "column_count": 6.0}
    result = {"column_radius": 0.405, "column_clear_spacing": 1.23, "column_count": 6.0}
    assert x2_g.parameter_changes(start, result) == {"column_clear_spacing": [1.14, 1.23], "column_radius": [0.45, 0.405]}
    assert x2_g.parameter_changes(start, None) is None
    assert x2_g.answer_mismatch({"changes": {"parameter:column_radius": 0.405}}, result) == []
    assert x2_g.answer_mismatch({"changes": {"column_radius": 0.4, "capital": 1}}, result) == ["capital", "column_radius"]
    assert x2_g.answer_mismatch({"changes": None}, result) is None


# ---------------------------------------------------------------- which model, against which start

def test_the_left_model_is_judged_against_a_start_from_the_same_exporter(monkeypatch, tmp_path):
    judged = []
    monkeypatch.setattr(x2_g.runner, "run_oracle", lambda spec, candidate, start, out: judged.append((spec.name, candidate, start)) or
                        {"verdict": "violated", "relations": [{"id": "K1", "status": "violated"}, {"id": "P1", "status": "held"}]})
    monkeypatch.setattr(x3_hub, "lab_realization", lambda project, run_id, out: {"path": str(tmp_path / "lab.3dm"), "sha256": "a",
                                                                                 "source": "lab pipeline.realize"})
    head = tmp_path / "head.3dm"
    exported = {"how": "working_design", "run_id": "c1", "model": {"path": str(tmp_path / "c1.3dm"), "verified": True, "sha256": "b"}}
    out = x2_g.judge_left_model(exported, TASKS["F1-T3"], START, head, tmp_path, tmp_path)
    assert out["model"]["source"] == "monkeyhub_export" and judged[-1] == ("F1-T3-01.json", tmp_path / "c1.3dm", head)
    assert out["oracle_verdict"] == "violated" and out["oracle_violated"] == ["K1"]
    unexported = {"how": "latest_candidate", "run_id": "c2", "model": None}
    out = x2_g.judge_left_model(unexported, TASKS["F1-T3"], START, head, tmp_path, tmp_path)
    assert out["model"]["source"] == "lab_realization" and judged[-1] == ("F1-T3-01.json", tmp_path / "lab.3dm", START["lab_start"])
    assert x2_g.judge_left_model(unexported, TASKS["F1-T5"], START, head, tmp_path, tmp_path)["why"] == "no oracle task"
    assert x2_g.judge_left_model({"how": "unchanged", "run_id": "stage-0-001"}, TASKS["F1-T3"], START, head, tmp_path, tmp_path)["model"] is None
    assert len(judged) == 2


# ---------------------------------------------------------------- budget

def test_a_round_is_refused_before_it_could_cross_a_cap(tmp_path):
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    x2_g.check_round(ledger, "pilot", 0.0, 2.0, 1.0)
    with pytest.raises(x2.CapReached, match="this run's limit"):
        x2_g.check_round(ledger, "pilot", 1.2, 2.0, 1.0)
    ledger.add({"part": "x2_g", "cost_usd": x2.CAPS["x2_g"] - 2.0, "out": "elsewhere"})
    with pytest.raises(x2.CapReached, match="part x2_g"):       # 2 left of the part's cap, a round may spend 3
        x2_g.check_round(ledger, "x2_g", 0.0, 50.0, x2_g.TURN_BUDGET_USD)


def test_this_runs_spend_is_its_own_rows_in_the_shared_ledger(tmp_path):
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    out = tmp_path / "run-1"
    ledger.add({"part": "pilot", "cost_usd": 0.5, "out": str(out / "rounds" / "F1-T2-r0" / "turn")})
    ledger.add({"part": "pilot", "cost_usd": 4.0, "out": str(tmp_path / "run-10" / "rounds" / "F1-T2-r0" / "turn")})
    ledger.add({"part": "pilot", "cost_usd": 7.0, "out": "another session"})
    assert ledger.spent_by(out) == 0.5


def test_a_run_over_its_limit_starts_no_hub(tmp_path):
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    out = tmp_path / "out"
    out.mkdir()

    def no_turn(*args, **kwargs):
        raise AssertionError("no turn may run")

    summary = x2_g.run(out, families=("F1",), repeats=1, first_repeat=0, part="pilot", ledger=ledger, cli=None, limit_usd=0.5,
                       task_ids=("F1-T2",), budget_usd=1.0, turn=no_turn)
    assert summary["stopped"].startswith("this run's limit") and not (out / "rounds").exists() and not (out / "inputs").exists()
    identity = json.loads((out / "identity.json").read_text(encoding="utf-8"))
    assert identity["tasks"] == ["F1-T2"] and identity["max_tool_calls"] == 20 and identity["max_turns"] == 24


def test_g_keeps_its_own_20_calls_when_x3s_cap_changes():
    settings = x2_g.proxy_settings({"command": "python", "args": []}, Path("calls.jsonl"))
    assert x2_g.G_MAX_CALLS == 20 and settings["max_calls"] == 20 and x3_hub.MAX_TOOL_CALLS != x2_g.G_MAX_CALLS
    assert "tools" not in settings                                          # every tool, as before
    assert x3_hub.proxy_refusal(settings, "studio_request", {"method": "GET", "path": "/api/construction/model"}, 20) is None
    assert x3_hub.proxy_refusal(settings, "studio_request", {"method": "GET", "path": "/api/construction/model"}, 21)["code"] == "X3_TOOL_BUDGET_SPENT"
    assert x2_g.MAX_TURNS == 24 and "at most 20 tool calls" in x2_g.INTRO.read_text(encoding="utf-8")
