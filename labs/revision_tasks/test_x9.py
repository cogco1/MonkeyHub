"""X9: the native editable 3DM reproduces the mesh export, and editing its numbers gives what the OCCT rebuild gives."""

from __future__ import annotations

import csv
import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import carrier, fixtures, native3dm, pipeline, x1, x9

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

needs_occt = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


@needs_occt
@pytest.mark.parametrize("family", ["F1", "F2", "F3"])
def test_the_native_file_reproduces_the_mesh_export(tmp_path, family):
    record = fixtures.bound(x1.FAMILY_FIXTURE[family]())
    (tmp_path / "start").mkdir()
    realization = pipeline.realize(record, (tmp_path / "start").resolve(), "start")
    export = native3dm.write_native(record, tmp_path / "start.native.3dm")
    readback = native3dm.readback_numbers(realization)
    boxes = native3dm.compare_numbers(native3dm.geometry_boxes(export.path), readback)
    assert boxes["agree"] and not boxes["missing"] and not boxes["extra"], boxes
    assert boxes["max_box_dev_m"] <= 1e-6 and boxes["max_volume_rel_dev"] <= 1e-6
    assert native3dm.compare_numbers(native3dm.mesh_model(export.path, tmp_path / "mesh.3dm"), readback)["agree"]
    assert export.mesh_ids == (["obj-rotunda-dome"] if family == "F2" else [])
    read = native3dm.read_native(export.path)
    assert read.record.digest == record.digest
    assert all(native3dm.same_primitive(read.primitives[k], p) for k, p in export.primitives.items())


@needs_occt
def test_editing_the_numbers_holds_and_agrees_with_the_rebuild(tmp_path):
    summary = x9.run(tmp_path, families=("F1", "F3"), task_ids=("F1-T2", "F3-T1"), repeats=1)
    assert summary["by_class"]["full"] == ["F1-T2", "F3-T1"]
    verdicts = summary["oracle"]["applicable"]["verdicts_dd_regen_expected"]
    assert verdicts == {"F1-T2": ["held", "held", "held"], "F3-T1": ["held", "held", "held"]}
    assert summary["dd_agrees_regen"] == 2 and summary["unrelated_changed"] == 0
    rows = {row["task_id"]: row for row in csv.DictReader((tmp_path / "x9.csv").open(encoding="utf-8"))}
    assert rows["F3-T1"]["edits"].count("profile:obj-wall;") == 1 and "obj-slab-l2" not in rows["F3-T1"]["edits"]
    assert rows["F1-T2"]["edited_elements"] == "columns" and rows["F1-T2"]["edited_outside_gold_affected"] == ""


def test_a_dome_change_is_partial_and_says_why(tmp_path):
    record = fixtures.bound(fixtures.f2_portico_rotunda())
    start = native3dm.write_native(record, tmp_path / "start.native.3dm")
    result = x9.dd_det(start.path, {"rotunda_radius": 10.5}, tmp_path / "candidate.native.3dm")
    assert result["classification"] == "partial"
    assert "obj-rotunda-dome regenerated" in result["reason"]
    after = native3dm.read_native(tmp_path / "candidate.native.3dm")
    assert after.record.digest == result["record"].digest
    edited = {e["object"] for e in result["edits"]}
    untouched = [n for n in start.primitives if n not in edited]
    assert untouched and all(n.startswith("obj-portico-") for n in untouched)
    assert all(native3dm.same_primitive(after.primitives[n], start.primitives[n]) for n in untouched)


def test_the_edit_hook_writes_numbers_and_an_unsealed_edit_is_refused(tmp_path):
    import rhino3dm

    start = native3dm.write_native(fixtures.bound(fixtures.f1_portico()), tmp_path / "start.native.3dm")
    read = native3dm.read_native(start.path)
    applied = native3dm.apply_edits(read.model, [
        {"object": "obj-columns-0", "field": "regular_polygon", "value": {"center": [0.0, 0.0], "radius": 0.405, "segments": 24}},
        {"object": "obj-columns-1", "field": "translate", "value": [0.5, 0.0, 0.0]}])
    assert [a["mode"] for a in applied] == ["replaced", "in_place"]
    assert read.model.Write(str(tmp_path / "edited.3dm"), 8)
    objects = {o.Attributes.Name: o for o in rhino3dm.File3dm.Read(str(tmp_path / "edited.3dm")).Objects}
    stated = json.loads(objects["obj-columns-0"].Attributes.GetUserString("archflow:primitive"))
    assert stated["form"]["regular_polygon"]["radius"] == 0.405 and stated["source"]["op"] == "columns-0"
    box = objects["obj-columns-1"].Geometry.GetBoundingBox()
    assert abs(box.Min.X - 2.49 - 0.5) < 1e-9 and abs(box.Max.X - 3.39 - 0.5) < 1e-9
    with pytest.raises(carrier.CarrierRefused) as refused:
        native3dm.read_native(tmp_path / "edited.3dm")
    assert refused.value.code == "GEOMETRY_BINDING_STALE"
