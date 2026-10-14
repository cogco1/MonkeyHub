"""X2 batch 5 without a model call: A and B as counted and AG built from them, the IFC renderer (N, N+S), S's prompt,
cap and frozen summary, how each new arm's answer is read, the run's order, and the pre-registered analysis."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import carrier, fixtures, x1, x2, x2_batch5_analysis

# Batch 3's counted A materials: SHA-256 of each family's inputs/<family>/materials-A.txt.
A_SHA256 = {"F1": "d98c9a4452b00e9c512296e53142a8cbcc2cea88e46aab060daa2a183ef27da0",
            "F2": "75c63939cd555c30dfa05cd427e9c370e3a8e9c69686bfdf9a5ca4608ac6c6b3",
            "F3": "39b48ea40f8ac8bc8a408a7ff360c65d3a86a4a6720406c02be1a54667023302"}
S_TEXT = ("Write a structured summary of this design's current state for whoever revises it next. Include whatever a later "
          "revision would need to respect. You will not see the revision requests.")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _events(family: str) -> list:
    return json.loads(x1.HISTORY.read_text(encoding="utf-8"))["families"][family]["events"]


def _small_model(root: Path) -> tuple[Path, Path]:
    """Two named 1 x 1 x 2 m boxes as a 3DM, and that 3DM as the lab's IFC with a state, a binding and object identities."""

    import rhino3dm

    model = rhino3dm.File3dm()
    for name, x0 in (("obj-b", 2.0), ("obj-a", 0.0)):
        mesh = rhino3dm.Mesh()
        for dx, dy, dz in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 2), (1, 0, 2), (1, 1, 2), (0, 1, 2)):
            mesh.Vertices.Add(x0 + dx, dy, dz)
        for quad in ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            mesh.Faces.AddFace(*quad)
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        model.Objects.AddMesh(mesh, attributes)
    three_dm, ifc = root / "small.3dm", root / "small.ifc"
    model.Write(str(three_dm), 7)
    carrier.write_ifc(carrier.ifc_from_three_dm(three_dm), ifc, state={"record": {"parameters": [{"key": "w"}]}},
                      binding_value={"state_digest": "d1"}, design_state_digest="d1")
    return three_dm, ifc


def _prompts(root: Path) -> Path:
    prompts = root / "prompts"
    shutil.copytree(x2.PROMPTS, prompts, ignore=shutil.ignore_patterns("s_summary_*.md"))
    return prompts


# ---------------------------------------------------------------- A, B and AG

def test_a_is_as_counted_and_ag_is_a_then_the_heading_then_b(tmp_path):
    three_dm, _ = _small_model(tmp_path)
    b = x2.render({"objects": x2.readback(three_dm)})
    assert b == ("objects:\n  - name: obj-a\n    min: [0.0, 0.0, 0.0]\n    max: [1.0, 1.0, 2.0]\n"
                 "  - name: obj-b\n    min: [2.0, 0.0, 0.0]\n    max: [3.0, 1.0, 2.0]")
    for family, digest in A_SHA256.items():
        a = x2.render({"events": _events(family)})
        assert _sha(a.encode("utf-8")) == digest
        ag = x2.with_readback(a, b)
        assert ag.encode("utf-8") == a.encode("utf-8") + b"\n\n## Readback of the model file\n\n" + b.encode("utf-8")


def test_the_counted_intros_stay_and_the_new_ones_are_added():
    intros = json.loads((x2.PROMPTS / "materials.json").read_text(encoding="utf-8"))
    assert {k: intros[k] for k in x2.ARMS} == {
        "A": "The complete design history: every event, in order, from the brief to now.",
        "B": "A readback of the model file: each object's name and its bounding box in metres (x along, y depth, z up). "
             "The file carries no other data.",
        "C": "The design state carried in the model file, written out as text.",
        "D": "The design state carried in the model file, written out as text.",
        "F": "The design state exactly as it is embedded in the model file (JSON)."}
    assert all(intros[arm] for arm in x2.BATCH5_ARMS) and intros["N"] == intros["N+S"]


def test_the_counted_command_is_unchanged_and_s_appends_no_system_prompt(tmp_path):
    counted = x2.command("claude", tmp_path)
    assert counted == ["claude", "-p", "--model", "claude-opus-5-5", "--output-format", "stream-json", "--verbose",
                       "--permission-mode", "dontAsk", "--setting-sources", "project,local", "--disable-slash-commands",
                       "--max-turns", "1", "--tools", "", "--disallowedTools", "WebSearch,WebFetch", "--strict-mcp-config",
                       "--mcp-config", str(tmp_path / "mcp.json"), "--append-system-prompt-file",
                       str(x2.PROMPTS / "system_common.md"), "--settings", str(tmp_path / "settings.json"),
                       "--no-session-persistence", "--max-budget-usd", "1.0"]
    summary = x2.command("claude", tmp_path, system=None)
    assert summary == [a for a in counted if a not in ("--append-system-prompt-file", str(x2.PROMPTS / "system_common.md"))]


# ---------------------------------------------------------------- N and N+S

def test_the_ifc_renderer_writes_structure_products_relations_and_boxes(tmp_path):
    three_dm, ifc = _small_model(tmp_path)
    out = x2.ifc_materials(ifc, tmp_path / "native.ifc", [o["name"] for o in x2.readback(three_dm)])
    lines = out["N"].splitlines()
    assert lines[:9] == ["schema: IFC4", "spatial_structure:", "  - class: IfcProject", "    Name: lab model",
                         f"    GlobalId: {carrier._guid('project')}", "    parts:", "      - class: IfcSite", "        Name: site",
                         f"        GlobalId: {carrier._guid('site')}"]
    site = f"IfcSite site ({carrier._guid('site')})"
    assert lines[9:23] == ["products:", "  - class: IfcBuildingElementProxy", "    Name: obj-a",
                           f"    GlobalId: {carrier._guid('object', 'obj-a')}", "    Tag: obj-a", f"    spatial_container: {site}",
                           "    relations:", "      - class: IfcRelContainedInSpatialStructure",
                           f"        GlobalId: {carrier._guid('contains')}", "        role: RelatedElements",
                           f"        RelatingStructure: {site}", "    bounding_box:", "      min: [0.0, 0.0, 0.0]", "      max: [1.0, 1.0, 2.0]"]
    assert lines[23:25] == ["  - class: IfcBuildingElementProxy", "    Name: obj-b"]           # sorted by Name
    assert lines[-2:] == ["      min: [2.0, 0.0, 0.0]", "      max: [3.0, 1.0, 2.0]"]
    assert out["global_ids"] == {carrier._guid("object", "obj-a"): "obj-a", carrier._guid("object", "obj-b"): "obj-b"}
    assert x2.ifc_materials(ifc, tmp_path / "again.ifc", ["obj-a", "obj-b"])["N"] == out["N"]       # deterministic


def test_a_box_follows_the_placement_and_a_body_that_is_not_a_mesh_is_read_through_its_shape(tmp_path):
    import ifcopenshell

    _, path = _small_model(tmp_path)
    model = ifcopenshell.open(str(path))
    site = model.by_type("IfcSite")[0]
    context = model.by_type("IfcGeometricRepresentationContext")[0]

    def placed(x: float, y: float) -> object:
        return model.createIfcLocalPlacement(site.ObjectPlacement, model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((x, y, 0.0)), None, None))

    mesh = model.by_type("IfcTriangulatedFaceSet")[0]                                  # obj-a's 1 x 1 x 2 m box, moved 10 m in y
    moved = model.createIfcBuildingElementProxy(carrier._guid("object", "obj-d"), None, "obj-d", None, None, placed(0.0, 10.0),
                                                model.createIfcProductDefinitionShape(None, None, [model.createIfcShapeRepresentation(
                                                    context, "Body", "Tessellation", [mesh])]), "obj-d", None)
    solid = model.createIfcExtrudedAreaSolid(model.createIfcRectangleProfileDef("AREA", None, None, 1.0, 1.0), None,
                                             model.createIfcDirection((0.0, 0.0, 1.0)), 3.0)
    extruded = model.createIfcBuildingElementProxy(carrier._guid("object", "obj-c"), None, "obj-c", None, None, placed(5.0, 0.0),
                                                   model.createIfcProductDefinitionShape(None, None, [model.createIfcShapeRepresentation(
                                                       context, "Body", "SweptSolid", [solid])]), "obj-c", None)
    boxes = x2._boxes(model, [moved, extruded])
    assert boxes[moved.GlobalId] == {"min": [0.0, 10.0, 0.0], "max": [1.0, 11.0, 2.0]}
    assert boxes[extruded.GlobalId] == {"min": [4.5, -0.5, 0.0], "max": [5.5, 0.5, 3.0]}


def test_n_has_no_archflow_property_set_and_n_plus_s_keeps_them_as_stored(tmp_path):
    three_dm, ifc = _small_model(tmp_path)
    out = x2.ifc_materials(ifc, tmp_path / "native.ifc", ["obj-a", "obj-b"])
    assert "ArchFlow_" not in out["N"] and "property_sets" not in out["N"]
    with_state = out["N+S"].splitlines()
    assert with_state[4:10] == [f"    GlobalId: {carrier._guid('project')}", "    property_sets:", "      ArchFlow_State:",
                                '        binding: {"state_digest":"d1"}', "        design_state_digest: d1",
                                '        state: {"record":{"parameters":[{"key":"w"}]}}']
    assert "      ArchFlow_Object:" in with_state and "        object_ref: cad-object:obj-a" in with_state
    assert out["report"]["removed"] == {"ArchFlow_Object": {"on": {"IfcBuildingElementProxy": 2}, "properties": ["object_ref"]},
                                        "ArchFlow_State": {"on": {"IfcProject": 1}, "properties": ["binding", "design_state_digest", "state"]}}
    assert out["report"]["remaining"] == [] and out["report"]["kept_identity"] == []
    assert out["report"]["names"]["match_readback"]
    import ifcopenshell

    assert not [p for p in ifcopenshell.open(str(tmp_path / "native.ifc")).by_type("IfcPropertySet")]


def test_a_product_whose_name_is_not_its_object_name_keeps_only_its_identity(tmp_path):
    _, ifc = _small_model(tmp_path)
    out = x2.ifc_materials(ifc, tmp_path / "native.ifc", ["obj-a"])
    assert out["report"]["kept_identity"] == [{"product": "obj-b", "GlobalId": carrier._guid("object", "obj-b"), "kept": ["object_ref"]}]
    assert out["report"]["remaining"] == ["ArchFlow_Object"] and not out["report"]["names"]["match_readback"]
    assert out["report"]["names"]["products_not_in_readback"] == ["obj-b"]
    assert out["N"].count("ArchFlow_Object:") == 1 and "object_ref: cad-object:obj-b" in out["N"] and "ArchFlow_State" not in out["N"]


# ---------------------------------------------------------------- S

def test_the_s_prompt_is_the_given_text_the_cap_and_a(tmp_path):
    assert (x2.PROMPTS / "s_summary.md").read_bytes() == (S_TEXT + " Keep it under {cap} characters.\n\n{materials}\n").encode("utf-8")
    prompts = _prompts(tmp_path)
    path, prompt = x2.summary_prompt("F1", "events:\n  - e", 21586, prompts)
    assert prompt == S_TEXT + " Keep it under 21586 characters.\n\nevents:\n  - e\n"
    assert path.read_bytes() == (S_TEXT + " Keep it under 21586 characters.\n\n{materials}\n").encode("utf-8")
    with pytest.raises(x2.SummaryRefused, match="has changed"):
        x2.summary_prompt("F1", "events:\n  - e", 21000, prompts)


def test_a_summary_over_the_cap_is_cut_at_a_line_boundary():
    assert x2.cap_summary("one\ntwo\nthree", 20) == ("one\ntwo\nthree", False)
    assert x2.cap_summary("one\ntwo\nthree", 9) == ("one\ntwo", True)          # the cut falls inside "three"
    assert x2.cap_summary("one\ntwo\nthree", 7) == ("one\ntwo", True)          # the cut falls on the line end
    assert x2.cap_summary("a single long line", 8) == ("a single", True)
    assert x2.estimated_tokens("x" * 9) == 3


def _fake_call(text: str, seen: list):
    def call(prompt, trial, *, cli, config, system):
        seen.append({"prompt": prompt, "system": system, "cli": cli})
        return {"exit_code": 0, "stderr": "", "seconds": 2.0, "init": {"tools": []}, "text": text,
                "result": {"usage": {"input_tokens": 1000, "output_tokens": 100}, "total_cost_usd": 0.01}}
    return call


def test_a_generated_summary_is_booked_capped_frozen_and_reused(tmp_path, monkeypatch):
    prompts, seen = _prompts(tmp_path), []
    monkeypatch.setattr(x2, "call_model", _fake_call("line one\nline two\nline three\n", seen))
    ledger = x2.Ledger(tmp_path / "ledger.jsonl")
    first = tmp_path / "run-1"
    args = dict(history="events:\n  - e", cap=17, cli="claude", config=tmp_path / "config", ledger=ledger, part="x2_single", prompts=prompts)
    made = x2.frozen_summary("F1", folder=first / "summaries", out=first, **args)
    assert seen == [{"prompt": S_TEXT + " Keep it under 17 characters.\n\nevents:\n  - e\n", "system": None, "cli": "claude"}]
    assert made["text"] == "line one\nline two" and made["truncated"] and (made["chars_generated"], made["chars"]) == (28, 17)
    assert made["appended_system_prompt"] is None and "--append-system-prompt-file" not in made["command"]
    assert (first / "summaries" / "F1.md").read_bytes() == b"line one\nline two"
    assert made["sha256"] == _sha(b"line one\nline two") == json.loads((first / "summaries" / "F1.json").read_text(encoding="utf-8"))["sha256"]
    rows = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["part"], r["trial_id"], r["cost_usd"]) for r in rows] == [("x2_single", "X2-S-summary-F1", 0.01)]
    # a resumed run reuses the frozen text and makes no call
    monkeypatch.setattr(x2, "call_model", lambda *a, **k: pytest.fail("a frozen summary was generated again"))
    second = tmp_path / "run-2"
    reused = x2.frozen_summary("F1", folder=second / "summaries", out=second, reuse=first / "summaries", **args)
    assert reused["text"] == made["text"] and reused["reused_from"] == str(first / "summaries")
    assert (second / "summaries" / "F1.md").read_bytes() == b"line one\nline two" and len(rows) == 1
    # and refuses one whose text no longer matches its SHA-256
    (first / "summaries" / "F1.md").write_bytes(b"line one\nline 2")
    with pytest.raises(x2.SummaryRefused, match="SHA-256"):
        x2.frozen_summary("F1", folder=tmp_path / "run-3" / "summaries", out=tmp_path / "run-3", reuse=first / "summaries", **args)


def test_a_dry_run_freezes_a_marked_stand_in_that_a_counted_run_refuses(tmp_path, monkeypatch):
    prompts = _prompts(tmp_path)
    monkeypatch.setattr(x2, "call_model", lambda *a, **k: pytest.fail("a dry run made a call"))
    args = dict(history="events:\n  - e", cap=500, config=tmp_path / "config", ledger=None, part="x2_single", prompts=prompts)
    dry = x2.frozen_summary("F2", folder=tmp_path / "dry" / "summaries", out=tmp_path / "dry", cli=None, **args)
    assert dry["stand_in"] and dry["text"].startswith("[DRY-RUN STAND-IN, NOT A SUMMARY") and "F2" in dry["text"]
    with pytest.raises(x2.SummaryRefused, match="stand-in"):
        x2.frozen_summary("F2", folder=tmp_path / "real" / "summaries", out=tmp_path / "real", cli="claude",
                          reuse=tmp_path / "dry" / "summaries", **args)


# ---------------------------------------------------------------- answers

def test_each_new_arm_reads_changes_as_its_counted_arm():
    record = fixtures.f1_portico()
    assert x2.READ_AS == {"AG": "A", "S": "A", "N": "B", "N+S": "D"}
    for changes in ({"portico.column_diameter": 0.81}, {"column_count": 4}, {"parameter:column_count": 4}, {"nothing": 1}):
        for arm, counted in x2.READ_AS.items():
            assert x2.map_changes(changes, arm, "F1", record) == x2.map_changes(changes, counted, "F1", record)
    assert x2.map_changes({"portico.column_diameter": 0.81}, "S", "F1", record) == ({"column_radius": 0.405}, None)
    assert x2.map_changes({"column_count": 4}, "N", "F1", record)[1] == "UNKNOWN_PARAMETER: column_count"
    assert x2.map_changes({"column_count": 4}, "N+S", "F1", record) == ({"column_count": 4.0}, None)


def test_n_reads_a_product_global_id_as_its_name_and_other_arms_are_untouched():
    inputs = {"objects_by_element": {"col-1": ("obj-col-1",)}, "global_ids": {"0GID": "obj-col-1"}}
    answer = {"verdict": "held", "affected": ["0GID", "obj-col-1"]}
    for arm in ("N", "N+S"):
        assert x2._element_ids(x2.read_names(answer, arm, inputs)["affected"], inputs) == ["col-1"]
    for arm in (*x2.ARMS, "AG", "S"):
        assert x2.read_names(answer, arm, inputs) is answer


# ---------------------------------------------------------------- the run

def test_a_batch5_run_rotates_its_four_arms_and_touches_no_counted_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(x2, "PROMPTS", _prompts(tmp_path))

    def inputs(family, root, arms):
        (root / "inputs" / family).mkdir(parents=True)
        materials = {"A": "events:\n  - e", "B": "objects:\n  - name: o", "C": "c", "D": "d" * 400, "F": "{}", "N": "n", "N+S": "ns"}
        return {"materials": {**materials, "AG": x2.with_readback(materials["A"], materials["B"])}}

    monkeypatch.setattr(x2, "family_inputs", inputs)
    out = tmp_path / "run"
    out.mkdir()
    x2.run(out, families=("F1",), arms=x2.BATCH5_ARMS, repeats=2, part="x2_single", ledger=None, cli=None, task_ids=("F1-T1", "F1-T2"))
    with (out / "x2.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["arm"] for r in rows] == ["AG", "S", "N", "N+S", "S", "N", "N+S", "AG", "S", "N", "N+S", "AG", "N", "N+S", "AG", "S"]
    assert {r["order_slot"] for r in rows} == {"0", "1", "2", "3"}
    s_prompt = (out / "trials" / "X2-S-F1-T1-r0.prompt.md").read_text(encoding="utf-8")
    assert "[DRY-RUN STAND-IN, NOT A SUMMARY" in s_prompt
    report = json.loads((out / "batch5_inputs.json").read_text(encoding="utf-8"))
    cell = report["families"]["F1"]
    assert cell["s_cap_chars"] == 400 and cell["s_summary"]["stand_in"] and cell["materials"]["D"] == {"chars": 400, "estimated_tokens": 100}
    assert report["s_truncated_families"] == {"count": 0, "families": []}


# ---------------------------------------------------------------- the analysis

def _csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trial_id", "task_id", "arm", "verdict", "gold_verdicts", "verdict_ok", "false_accept"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_the_analysis_counts_arms_and_bootstraps_the_three_contrasts(tmp_path):
    tasks = json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]

    def rows(arm: str, right) -> list[dict]:
        out = []
        for i, task in enumerate(tasks):
            gold = task["gold"]["verdicts"]
            for r in range(5):
                verdict = gold[0] if right(i, r) else ("held" if "held" not in gold else "unknown")
                out.append({"trial_id": f"X2-{arm}-{task['task_id']}-r{r}", "task_id": task["task_id"], "arm": arm, "verdict": verdict,
                            "gold_verdicts": "|".join(gold), "verdict_ok": "True", "false_accept": str(verdict == "held" and "held" not in gold)})
        return out

    batch3 = _csv(tmp_path / "b3.csv", rows("D", lambda i, r: True) + rows("A", lambda i, r: False))
    batch5 = _csv(tmp_path / "b5.csv", rows("AG", lambda i, r: i % 2 == 0) + rows("S", lambda i, r: True) + rows("N", lambda i, r: False)
                  + rows("N+S", lambda i, r: r < 4) + [{"trial_id": "X2-D-x", "task_id": "F1-T1", "arm": "D", "verdict": "held",
                                                           "gold_verdicts": "held", "verdict_ok": "", "false_accept": ""}])
    result = x2_batch5_analysis.analyse([batch3], [batch5])
    assert set(result["arms"]) == {"D", "AG", "S", "N", "N+S"}                     # batch 3's A and batch 5's D stay out
    assert [(a, c["correct"], c["rows"], c["complete"]) for a, c in result["arms"].items()] == [
        ("AG", 65, 125, True), ("D", 125, 125, True), ("N", 0, 125, True), ("N+S", 100, 125, True), ("S", 125, 125, True)]
    assert sum(t["rows"] for t in result["arms"]["N"]["by_type"].values()) == 125
    contrasts = {c["contrast"]: c for c in result["contrasts"]}
    assert contrasts["D - S"]["difference"] == 0.0 and contrasts["D - S"]["ci95"] == [0.0, 0.0]
    assert contrasts["N+S - N"]["difference"] == pytest.approx(0.8) and contrasts["N+S - N"]["ci95"] == pytest.approx([0.8, 0.8])
    low, high = contrasts["D - AG"]["ci95"]
    assert contrasts["D - AG"]["difference"] == pytest.approx(60 / 125) and low < 60 / 125 < high and contrasts["D - AG"]["tasks"] == 25
    assert x2_batch5_analysis.analyse([batch3], [batch5]) == result                 # seeded
