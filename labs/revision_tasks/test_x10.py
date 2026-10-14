"""X10 without a model call: the page's DATA, the four levels and their checks, the score, the dry run and the cap refusal."""

from __future__ import annotations

import csv
import json
import sys

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x2, x10


def _prepared(paths):
    return x10.prepare(paths["extract"], paths["tasks"], paths["deny"], x10.CUTOFF, x10.MATERIALS, None)


def _argv(paths, out, *more):
    return ["--out", str(out), "--extract", str(paths["extract"]), "--tasks", str(paths["tasks"]), "--deny-words", str(paths["deny"]), *more]


def _edit_extract(paths, change):
    extract = json.loads(paths["extract"].read_text(encoding="utf-8"))
    change(extract)
    paths["extract"].write_text(json.dumps(extract), encoding="utf-8", newline="\n")


def test_data_is_read_from_a_const_script_or_a_json_script_without_the_glb(tmp_path):
    paths = x10.write_synthetic(tmp_path / "inputs")
    data, report = x10.read_l1({"html": "page.html"}, paths["page"].parent)
    assert [c["n"] for c in data["components"]][:2] == ["footing-1", "post-1"] and data["model"] == {} and len(data["notes"]) == 6
    assert [(s["at"], s["type"]) for s in report["stripped"]] == [("DATA.model.glb", "model/gltf-binary"), ("page", "model/gltf-binary")]
    assert all(s["chars"] > 100 for s in report["stripped"]) and "Z2xURg" not in json.dumps(data)
    assert report["data_chars"] > report["kept_chars"]
    raw, _ = x10.page_data(paths["page"].read_text(encoding="utf-8"))
    (tmp_path / "json.html").write_text('<script type="application/json" id="config">{"theme": "light"}</script>\n'
                                        f'<script type="application/json" id="page-data">{json.dumps(raw)}</script>\n',
                                        encoding="utf-8", newline="\n")
    again, _ = x10.read_l1({"html": "json.html", "id": "page-data"}, tmp_path)
    assert again == data
    with pytest.raises(ValueError, match="PAGE_DATA_NOT_FOUND"):
        x10.read_l1({"html": "json.html"}, tmp_path)          # two JSON scripts and no id to choose


def test_levels_nest_and_a_broken_nesting_is_caught(tmp_path):
    prepared = _prepared(x10.write_synthetic(tmp_path / "inputs"))
    nesting = prepared["manifest"]["checks"]["nesting"]
    assert nesting["ok"] and nesting["L1<L2"]["adds"] == {"relations": 7, "decisions": 2}
    assert nesting["L2<L3"]["adds"] == {"obligations": 4, "evidence": 3}
    assert "notes.md" not in prepared["texts"]["L3"] + prepared["texts"]["H"] and "relations:" not in prepared["texts"]["L1"]
    assert "Block infill was dropped as too heavy." in prepared["texts"]["L1"]           # notes stay in L1 as the page was built
    broken = json.loads(json.dumps(prepared["data"]))
    broken["L2"]["page"]["components"][3]["m"] = "mat-block"
    check = x10.nesting(broken, {m: x2.render(broken[m]) for m in x10.MATERIALS})
    assert not check["ok"] and check["L1<L2"]["first_changed"] == ["page.components[3]"] and not check["L1<L2"]["text_prefix"]
    assert not check["L2<L3"]["ok"]


def test_a_late_or_undated_item_fails_and_gold_strings_are_reported(tmp_path):
    paths = x10.write_synthetic(tmp_path / "inputs")
    clean = _prepared(paths)["manifest"]["checks"]["leakage"]
    assert clean["ok"] and clean["dated_items"] == 2 + 7 + 2 + 4 + 3 + 5 and not clean["later_dates_in_text"]
    assert clean["gold_in_materials"]["T6"]["L1"] == ["panel-1", "panel-2"] and "T3" not in clean["gold_in_materials"]

    def later(extract):
        extract["history"].append({"date": "2026-10-20", "event": "a later event", "source": "notes.md#history"})
        del extract["decisions"][0]["date"]
        extract["evidence"][1]["as_of"] = "2026-11-01"

    _edit_extract(paths, later)
    late = _prepared(paths)["manifest"]["checks"]["leakage"]
    assert not late["ok"] and late["undated"] == ["decisions:dec-infill"]
    assert late["late"] == [{"item": "evidence:ev-stud-price", "as_of": "2026-11-01"}, {"item": "history[5]", "date": "2026-10-20"}]
    assert late["later_dates_in_text"] == {"L3": ["2026-11-01"], "H": ["2026-10-20"]}
    out = tmp_path / "run"
    assert x10.run(out, extract=paths["extract"], tasks=paths["tasks"], deny=paths["deny"], repeats=1)["stopped"] == "CHECKS_FAILED: leakage"
    assert not (out / "x10.csv").exists()


def test_a_planted_deny_word_stops_the_run_before_anything_holds_it(tmp_path):
    paths = x10.write_synthetic(tmp_path / "inputs")
    _edit_extract(paths, lambda e: e["relations"][0].update(statement="footing-1 carries post-1 (Zorblax detail; zorblaxian joint)"))
    out = tmp_path / "run"
    summary = x10.run(out, extract=paths["extract"], tasks=paths["tasks"], deny=paths["deny"], repeats=1)
    assert summary["stopped"].startswith("ANONYMITY")
    assert sorted(p.name for p in out.iterdir()) == ["x10_manifest.json", "x10_summary.json"]
    hits = {h["in"]: h for h in json.loads((out / "x10_manifest.json").read_text(encoding="utf-8"))["checks"]["anonymity"]["hits"]}
    assert hits["material L2"] == {"in": "material L2", "word_line": 2, "whole_only": False, "whole_word": 1, "substring": 2}
    assert "material L3" in hits and "material L1" not in hits and "material H" not in hits
    assert all("zorblax" not in p.read_text(encoding="utf-8").casefold() for p in out.iterdir())
    # a deny word that reaches only an output file's name (a task id) is caught by the file scan, and redacted where reported
    paths = x10.write_synthetic(tmp_path / "inputs2")
    tasks = json.loads(paths["tasks"].read_text(encoding="utf-8"))
    tasks["tasks"][7]["task_id"] = "T8-zorblax"
    paths["tasks"].write_text(json.dumps(tasks), encoding="utf-8", newline="\n")
    out = tmp_path / "run2"
    summary = x10.run(out, extract=paths["extract"], tasks=paths["tasks"], deny=paths["deny"], repeats=1)
    manifest_text = (out / "x10_manifest.json").read_text(encoding="utf-8")
    assert summary["stopped"] == "CHECKS_FAILED: anonymity" and "zorblax" not in manifest_text.casefold()
    files = json.loads(manifest_text)["checks"]["anonymity"]["file_hits"]
    assert {"in": "name trials/X10-L1-T8-<deny:2>-r0.prompt.md", "word_line": 2, "whole_only": False, "whole_word": 1,
            "substring": 1} in files


def test_an_equals_line_matches_whole_words_only(tmp_path):
    (tmp_path / "deny.txt").write_text("# names\n=Thing\nthing\n=\n", encoding="utf-8", newline="\n")
    words = x10.deny_words(tmp_path / "deny.txt")
    assert words == [(2, "Thing", True), (3, "thing", False)]
    hits = x10.scan({"a": "the THING; something; thing_x", "b": "something only"}, words)
    assert hits == [{"in": "a", "word_line": 2, "whole_only": True, "whole_word": 1, "substring": 3},
                    {"in": "a", "word_line": 3, "whole_only": False, "whole_word": 1, "substring": 3},
                    {"in": "b", "word_line": 3, "whole_only": False, "whole_word": 0, "substring": 1}]
    assert x10._redact("this thing, Thingy", words[:1]) == "this <deny:2>, Thingy"


def test_scores_use_four_categories_names_and_value_tolerance():
    task = {"task_id": "T", "gold": {"verdicts": ["violated"], "affected": ["panel-1", "Cap-1"], "recheck": ["budget-total", "sample-size"],
                                     "values": {"panel-1.height": {"value": 1100, "tol": 0.5}, "cap-1.height": {"value": 60, "tol": 0}}}}
    s = x10.score({"verdict": "refuse", "changes": {"PANEL-1.height": 1100.5, "cap-1.height": 60.0},
                   "affected": ["PANEL-1", "cap-1", "post-1"], "conditions": ["budget-total"]}, task)
    assert (s["verdict_ok"], s["category"], s["category_ok"], s["false_accept"]) == (False, "blocked", True, False)
    assert (s["affected_precision"], s["affected_recall"], s["recheck_recall"]) == (2 / 3, 1.0, 0.5)
    assert s["value_hits"] == {"panel-1.height": True, "cap-1.height": True} and s["values_ok"]
    s = x10.score({"verdict": "held", "changes": {"panel-1.height": 1100.6, "cap-1.height": True}, "affected": "panel-1"}, task)
    assert s["false_accept"] and not s["category_ok"] and s["value_hits"] == {"panel-1.height": False, "cap-1.height": False}
    assert (s["affected_precision"], s["affected_recall"], s["recheck_recall"]) == (None, 0.0, 0.0)
    assert x10.score({"verdict": "violated", "affected": ["panel 1"]}, task)["affected_precision"] == 0.0   # exact apart from case
    assert x10.score(None, task)["parsed"] is False and x10.score({"verdict": "ask"}, task)["category"] is None
    assert len({x10.category(v) for v in x2.VERDICTS}) == 4 and x10.category("violated") == x10.category("refuse") == "blocked"
    unknown = {"task_id": "U", "gold": {"verdicts": ["unknown"], "affected": [], "recheck": []}}
    s = x10.score({"verdict": "flag_rejected", "affected": []}, unknown)
    assert not s["category_ok"] and not s["false_accept"] and s["values_ok"] is None and s["affected_recall"] is None


def test_the_dry_run_writes_everything_and_scores_canned_answers_without_a_call(tmp_path, monkeypatch):
    paths = x10.write_synthetic(tmp_path / "inputs")
    monkeypatch.setattr(x2, "call_model", lambda *a, **k: pytest.fail("a dry run made a call"))
    out = tmp_path / "run"
    assert x10.main(_argv(paths, out, "--dry-run")) == 0
    manifest = json.loads((out / "x10_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] and manifest["trials_planned"] == 96
    assert set(manifest["checks"]) == {"nesting", "leakage", "gold_ids", "anonymity", "sizes"}
    sizes = manifest["checks"]["sizes"]["by_material"]
    assert sizes["L1"]["chars"] < sizes["L2"]["chars"] < sizes["L3"]["chars"] and sizes["L1"]["est_tokens"] == -(-sizes["L1"]["chars"] // 4)
    assert len(list((out / "trials").glob("*.prompt.md"))) == 96 and len(list((out / "trials").glob("*.bundle.json"))) == 96
    with (out / "x10.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 192
    summary = json.loads((out / "x10_summary.json").read_text(encoding="utf-8"))
    gold, wrong = summary["by_source"]["canned-gold"], summary["by_source"]["canned-wrong"]
    for m in x10.MATERIALS:
        assert gold[m]["trials"] == gold[m]["verdict_ok"] == gold[m]["category_ok"] == 24 and gold[m]["false_accept"] == 0
        assert gold[m]["affected_recall"]["mean"] == gold[m]["recheck_recall"]["mean"] == 1.0 and gold[m]["values_ok"] == gold[m]["values_trials"] == 12
        assert wrong[m]["category_ok"] == 0 and wrong[m]["false_accept"] == 15 and wrong[m]["values_ok"] == 0
        assert wrong[m]["affected_recall"]["mean"] < 1 and wrong[m]["recheck_recall"]["mean"] < 1
    prompt = (out / "trials" / "X10-H-T5-r0.prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("# Request\n\nUse block infill for both panels.\n\n# Materials\n\nThe project's design history")
    assert "2026-09-18" in prompt and "components:" not in prompt
    # the key legend follows the intro, the same in all four prompts, and is in no material
    legend = x10.legend_block(json.loads(paths["extract"].read_text(encoding="utf-8"))["l1"])
    assert legend.startswith("Key legend:\n  n: name\n  m: material\n")
    for m in x10.MATERIALS:
        text = (out / "trials" / f"X10-{m}-T5-r0.prompt.md").read_text(encoding="utf-8")
        intro = json.loads(x10.INTROS.read_text(encoding="utf-8"))[m]
        assert f"\n\n# Materials\n\n{intro}\n\n{legend}\n\n" in text
        assert "Key legend" not in (out / "materials" / f"{m}.txt").read_text(encoding="utf-8")
    assert manifest["checks"]["sizes"]["legend"]["chars"] == len(legend)
    identity = json.loads((out / "identity.json").read_text(encoding="utf-8"))
    assert "--append-system-prompt-file" in identity["command"] and "x10_materials.json" in identity["prompt_files_sha256"]
    assert summary["anonymity_after"]["ok"]
    assert not [p for p in out.rglob("*") if p.is_file() and b"\r\n" in p.read_bytes()]


def test_a_real_run_is_refused_without_the_x10_cap(tmp_path, monkeypatch):
    paths = x10.write_synthetic(tmp_path / "inputs")
    monkeypatch.setattr(x2, "call_model", lambda *a, **k: pytest.fail("a refused run made a call"))
    monkeypatch.delitem(x2.CAPS, "x10", raising=False)
    out, ledger = tmp_path / "run", tmp_path / "ledger.jsonl"
    assert x10.main(_argv(paths, out, "--cli", "claude", "--ledger", str(ledger))) == 2
    with pytest.raises(x2.CapReached, match="no cap"):
        x10.run(out, extract=paths["extract"], tasks=paths["tasks"], deny=paths["deny"], dry_run=False, cli="claude", ledger=x2.Ledger(ledger))
    assert not out.exists() and not ledger.exists()


def test_with_the_cap_each_call_is_booked_and_scored(tmp_path, monkeypatch):
    paths = x10.write_synthetic(tmp_path / "inputs")
    monkeypatch.setitem(x2.CAPS, "x10", 5.0)
    calls = []

    def stand_in(prompt, trial, *, cli, config, system=None):
        calls.append(trial.name)
        answer = {"verdict": "held", "changes": {"cap-1.height": 60}, "affected": ["cap-1"], "conditions": ["budget-total"], "reason": "r"}
        return {"exit_code": 0, "stderr": "", "seconds": 0.1, "init": {}, "text": json.dumps(answer),
                "result": {"usage": {"input_tokens": 1000, "output_tokens": 100}, "total_cost_usd": 0.0}}

    monkeypatch.setattr(x2, "call_model", stand_in)
    ledger = tmp_path / "ledger.jsonl"
    argv = _argv(paths, tmp_path / "run", "--cli", sys.executable, "--ledger", str(ledger), "--task-ids", "T1", "--materials", "L1,H", "--repeats", "1")
    assert x10.main(argv) == 0 and calls == ["X10-L1-T1-r0", "X10-H-T1-r0"]
    booked = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [(r["experiment"], r["part"]) for r in booked] == [("X10", "x10")] * 2 and abs(booked[0]["cost_usd"] - 0.006) < 1e-9
    summary = json.loads((tmp_path / "run" / "x10_summary.json").read_text(encoding="utf-8"))
    assert summary["by_source"]["model"]["L1"]["category_ok"] == summary["by_source"]["model"]["H"]["values_ok"] == 1


def test_notes_are_classified_for_the_report_only_and_the_legend_is_checked(tmp_path):
    paths = x10.write_synthetic(tmp_path / "inputs")
    prepared = _prepared(paths)
    notes = prepared["manifest"]["inputs"]["l1"]["notes"]
    assert notes == {"notes": 6, "classified": 5, "unclassified": [5], "by_class": {"descriptive": 1, "relational": 1, "decision": 1,
                                                                                    "obligation": 1, "evidence": 1, "mixed": 0}}
    assert prepared["manifest"]["inputs"]["l1"]["legend_entries"] == 5
    bad = ((lambda e: e["l1"]["notes_classification"].append({"note": 6, "class": "mixed", "source": "s", "date": "2026-10-08"}),
            "outside DATA.notes"),
           (lambda e: e["l1"]["notes_classification"][0].update({"class": "rule"}), "class is not one of"),
           (lambda e: e["l1"]["notes_classification"][1].update({"note": 0}), "new integer index"),
           (lambda e: e["l1"]["legend"].pop("entries"), "l1.legend must be"))
    for index, (change, message) in enumerate(bad):
        paths = x10.write_synthetic(tmp_path / f"bad-{index}")
        _edit_extract(paths, change)
        with pytest.raises(ValueError, match=message):
            _prepared(paths)
    # the legend is dated like every item and scanned like every prompt
    paths = x10.write_synthetic(tmp_path / "late")
    _edit_extract(paths, lambda e: e["l1"]["legend"].update(date="2026-10-30"))
    assert _prepared(paths)["manifest"]["checks"]["leakage"]["late"] == [{"item": "l1.legend", "date": "2026-10-30"}]
    paths = x10.write_synthetic(tmp_path / "named")
    _edit_extract(paths, lambda e: e["l1"]["legend"]["entries"].update(z="zorblax rating"))
    summary = x10.run(tmp_path / "run", extract=paths["extract"], tasks=paths["tasks"], deny=paths["deny"], repeats=1)
    hits = json.loads((tmp_path / "run" / "x10_manifest.json").read_text(encoding="utf-8"))["checks"]["anonymity"]["hits"]
    assert summary["stopped"].startswith("ANONYMITY") and "legend" in {h["in"] for h in hits}
    assert not [h for h in hits if h["in"].startswith("material")]
