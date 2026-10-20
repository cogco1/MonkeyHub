from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Callable

import pytest

from archflow.contracts.canonical import canonical_json
from monkeycad.formats.state_file import (
    BINDING_KEY,
    BINDING_SCHEMA,
    SCHEMA,
    STATE_KEY,
    ObjectRow,
    OutOfBand,
    StateFileError,
    binding_for,
    compare,
    object_rows,
    read_state_file,
    seal_state_file,
)
from monkeycad.formats.three_dm_inspector import inspect_three_dm

rhino3dm = pytest.importorskip("rhino3dm")

RECORD = {
    "schema": "StateRecord@1",
    "project_id": "villa",
    "run_id": "run-001",
    "entities": [{"entity_id": "wall-1", "schema": "Element@1", "fields": {"height_m": 3.0}}],
    "parameters": [
        {"key": "stair_rise", "value": 0.175, "unit": "m"},
        {"key": "stair_going", "value": 0.28, "unit": "m"},
    ],
    "relations": [{"relation_id": "rel-1", "kind": "supports", "subject": "slab-1", "object": "wall-1"}],
    "obligations": [
        {"obligation_id": "obl-egress", "statement": "two exits from the upper floor"},
        {"obligation_id": "obl-light", "statement": "daylight in every bedroom"},
    ],
    "evidence_refs": [],
}
DECISIONS = [{"decision_id": "d-1", "kind": "continue", "by": "architect"}]
BOXES = {"wall": ((0.0, 0.0, 0.0), (4.0, 0.2, 3.0)), "slab": ((0.0, 0.0, -0.2), (4.0, 4.0, 0.0))}


def _box_mesh(low, high, *, triangles: bool = False):
    (x0, y0, z0), (x1, y1, z1) = low, high
    mesh = rhino3dm.Mesh()
    for point in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)):
        mesh.Vertices.Add(*point)
    for a, b, c, d in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        if triangles:
            mesh.Faces.AddFace(a, b, c)
            mesh.Faces.AddFace(a, c, d)
        else:
            mesh.Faces.AddFace(a, b, c, d)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def _write_source(path: Path, strings: dict[str, str] | None = None) -> Path:
    model = rhino3dm.File3dm()
    for name, (low, high) in BOXES.items():
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        model.Objects.AddMesh(_box_mesh(low, high), attributes)
    for key, value in (strings or {}).items():
        model.Strings[key] = value
    assert model.Write(str(path), 8)
    return path


def _rewrite(source: Path, destination: Path, edit: Callable[[object], None]) -> Path:
    model = rhino3dm.File3dm.Read(str(source))
    edit(model)
    assert model.Write(str(destination), 8)
    return destination


def _translate(name: str, dx: float) -> Callable[[object], None]:
    def edit(model) -> None:
        for item in model.Objects:
            if item.Attributes.Name == name:
                assert item.Geometry.Translate(rhino3dm.Vector3d(dx, 0, 0))
    return edit


def _strings(path: Path) -> dict[str, str]:
    return {row["key"]: row["value"] for row in inspect_three_dm(path).document_user_strings}


def _sealed(tmp_path: Path) -> tuple[Path, Path]:
    source = _write_source(tmp_path / "export.3dm", {"archflow:design_state_digest": "abc", "note": "kept"})
    destination = tmp_path / "sealed.3dm"
    seal_state_file(source, destination, record=RECORD, decisions=DECISIONS, parent_file_sha256=None)
    return source, destination


def test_seal_then_read_round_trips_state_and_keeps_the_source(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "export.3dm", {"archflow:design_state_digest": "abc", "note": "kept"})
    source_bytes = source.read_bytes()
    parent = hashlib.sha256(source_bytes).hexdigest()
    destination = tmp_path / "sealed.3dm"

    sealed = seal_state_file(source, destination, record=RECORD, decisions=DECISIONS, parent_file_sha256=parent)

    assert sealed.record == RECORD
    assert sealed.decisions == tuple(DECISIONS)
    assert sealed.out_of_band == ()
    assert sealed.reencoded == ()
    assert sealed.binding["schema"] == BINDING_SCHEMA
    assert sealed.binding["parent_file_sha256"] == parent
    assert sealed.binding == binding_for(RECORD, DECISIONS, object_rows(source), parent)
    assert sorted(sealed.binding["row_digests"]) == [
        "entity:wall-1", "obligation:obl-egress", "obligation:obl-light",
        "parameter:stair_going", "parameter:stair_rise", "record:header", "relation:rel-1",
    ]
    assert [row.name for row in sealed.objects] == ["slab", "wall"]
    assert sealed.objects == object_rows(source)
    assert sealed.file_sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert source.read_bytes() == source_bytes
    strings = _strings(destination)
    assert strings["archflow:design_state_digest"] == "abc"
    assert strings["note"] == "kept"
    assert json.loads(strings[STATE_KEY]) == {"schema": SCHEMA, "record": RECORD, "decisions": DECISIONS}
    assert read_state_file(destination) == sealed


def test_an_object_moved_after_sealing_is_out_of_band_and_a_millimetre_is_not(tmp_path: Path) -> None:
    _, sealed = _sealed(tmp_path)

    moved = read_state_file(_rewrite(sealed, tmp_path / "moved.3dm", _translate("wall", 0.35)))
    assert [(item.kind, item.ref) for item in moved.out_of_band] == [("object_moved", "object:wall")]
    detail = moved.out_of_band[0].detail
    assert detail["moved_m"] == pytest.approx(0.35, abs=1e-6)
    assert detail["before"] == {"min": [0.0, 0.0, 0.0], "max": [4.0, pytest.approx(0.2), 3.0]}
    assert detail["after"]["min"][0] == pytest.approx(0.35, abs=1e-6)

    nudged = read_state_file(_rewrite(sealed, tmp_path / "nudged.3dm", _translate("wall", 0.001)))
    assert nudged.out_of_band == ()
    assert nudged.reencoded == ("wall",)


def test_reencoded_geometry_inside_the_box_is_tolerated(tmp_path: Path) -> None:
    _, sealed = _sealed(tmp_path)
    original = rhino3dm.File3dm.Read(str(sealed))
    copy_model = rhino3dm.File3dm()
    for item in original.Objects:
        name = item.Attributes.Name
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        geometry = _box_mesh(*BOXES["wall"], triangles=True) if name == "wall" else item.Geometry
        copy_model.Objects.AddMesh(geometry, attributes)
    for index in range(len(original.Strings)):
        key, value = original.Strings[index]
        copy_model.Strings[key] = value
    round_trip = tmp_path / "round-trip.3dm"
    assert copy_model.Write(str(round_trip), 8)

    found = read_state_file(round_trip)

    assert found.out_of_band == ()
    assert found.reencoded == ("wall",)
    assert found.objects[1].geometry_sha256 != read_state_file(sealed).objects[1].geometry_sha256


def _with_state(record: dict, decisions: list[dict]) -> Callable[[object], None]:
    def edit(model) -> None:
        model.Strings[STATE_KEY] = canonical_json({"schema": SCHEMA, "record": record, "decisions": decisions})
    return edit


def test_state_rows_and_decisions_changed_outside_the_seal(tmp_path: Path) -> None:
    _, sealed = _sealed(tmp_path)
    record = copy.deepcopy(RECORD)
    record["parameters"][0]["value"] = 0.19
    record["obligations"] = [item for item in record["obligations"] if item["obligation_id"] != "obl-light"]
    record["entities"].append({"entity_id": "stair-1", "schema": "Element@1", "fields": {}})

    rows = read_state_file(_rewrite(sealed, tmp_path / "rows.3dm", _with_state(record, DECISIONS)))
    assert rows.out_of_band == (
        OutOfBand("row_added", "entity:stair-1", {}),
        OutOfBand("row_removed", "obligation:obl-light", {}),
        OutOfBand("row_changed", "parameter:stair_rise", {"changes": [{"path": "value", "before": 0.175, "after": 0.19}]}),
    )
    assert rows.binding == read_state_file(sealed).binding

    decisions = [*DECISIONS, {"decision_id": "d-2", "kind": "endorse", "by": "architect"}]
    changed = read_state_file(_rewrite(sealed, tmp_path / "decisions.3dm", _with_state(RECORD, decisions)))
    assert changed.out_of_band == (OutOfBand("decisions_changed", "decisions", {"added": [decisions[1]], "removed": []}),)
    # Whatever the file now says, the seal keeps what MonkeyHub wrote.
    assert (rows.sealed_record, rows.sealed_decisions) == (RECORD, tuple(DECISIONS))
    assert (changed.sealed_record, changed.sealed_decisions) == (RECORD, tuple(DECISIONS))


def test_a_seal_whose_kept_record_was_edited_does_not_match_itself(tmp_path: Path) -> None:
    _, sealed = _sealed(tmp_path)

    def edit(model) -> None:
        binding = json.loads(model.Strings[BINDING_KEY])
        binding["record"]["parameters"][0]["value"] = 0.19
        model.Strings[BINDING_KEY] = canonical_json(binding)

    forged = read_state_file(_rewrite(sealed, tmp_path / "forged.3dm", edit))
    assert (forged.binding, forged.sealed_record) == (None, None), "a seal that does not match itself vouches for nothing"
    assert [row.kind for row in forged.out_of_band] == ["seal_broken"]
    assert "does not match" in forged.out_of_band[0].detail["reason"]


def test_a_state_string_removed_or_emptied_is_read_from_the_copy_the_seal_keeps(tmp_path: Path) -> None:
    _, sealed = _sealed(tmp_path)

    def empty(model) -> None:
        model.Strings[STATE_KEY] = "{}"

    found = read_state_file(_rewrite(sealed, tmp_path / "emptied.3dm", empty))
    assert (found.record, found.decisions) == (RECORD, tuple(DECISIONS))
    assert [row.kind for row in found.out_of_band] == ["state_replaced"]


def _ell_mesh():
    """An L in plan, 4 by 4 m and 1 m high: turned half round or mirrored, it keeps its box and not its vertices."""

    plan = ((0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4))
    mesh = rhino3dm.Mesh()
    for z in (0.0, 1.0):
        for x, y in plan:
            mesh.Vertices.Add(float(x), float(y), z)
    for i in range(6):
        j = (i + 1) % 6
        mesh.Faces.AddFace(i, j, j + 6, i + 6)
    mesh.Compact()
    return mesh


def test_a_turn_mirror_or_resize_and_an_unnamed_object_are_out_of_band(tmp_path: Path) -> None:
    model = rhino3dm.File3dm()
    for name, mesh in (("ell", _ell_mesh()), ("wall", _box_mesh(*BOXES["wall"]))):
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = name
        model.Objects.AddMesh(mesh, attributes)
    assert model.Write(str(tmp_path / "export.3dm"), 8)
    sealed = tmp_path / "sealed.3dm"
    seal_state_file(tmp_path / "export.3dm", sealed, record=RECORD, decisions=DECISIONS, parent_file_sha256=None)

    def change(name, transform):
        def edit(model) -> None:
            for item in model.Objects:
                if item.Attributes.Name == name:
                    assert item.Geometry.Transform(transform)
        return edit

    centre = rhino3dm.Point3d(2, 2, 0.5)
    turned = read_state_file(_rewrite(sealed, tmp_path / "turned.3dm", change(
        "ell", rhino3dm.Transform.Rotation(3.141592653589793, rhino3dm.Vector3d(0, 0, 1), centre))))
    assert [(row.kind, row.ref) for row in turned.out_of_band] == [("object_reshaped", "object:ell")]
    mirrored = read_state_file(_rewrite(sealed, tmp_path / "mirrored.3dm", change(
        "ell", rhino3dm.Transform.Mirror(centre, rhino3dm.Vector3d(1, -1, 0)))))
    assert [(row.kind, row.ref) for row in mirrored.out_of_band] == [], "an L mirrored on its diagonal is the same L"
    flipped = read_state_file(_rewrite(sealed, tmp_path / "flipped.3dm", change(
        "ell", rhino3dm.Transform.Mirror(centre, rhino3dm.Vector3d(1, 0, 0)))))
    assert [(row.kind, row.ref) for row in flipped.out_of_band] == [("object_reshaped", "object:ell")]
    stretched = read_state_file(_rewrite(sealed, tmp_path / "stretched.3dm", change(
        "wall", rhino3dm.Transform.Scale(rhino3dm.Point3d(0, 0, 0), 2.0))))
    assert [(row.kind, row.ref) for row in stretched.out_of_band] == [("object_transformed", "object:wall")]

    def add_unnamed(model) -> None:
        model.Objects.AddMesh(_box_mesh((10, 10, 0), (11, 11, 1)))

    unnamed = read_state_file(_rewrite(sealed, tmp_path / "unnamed.3dm", add_unnamed))
    assert [(row.kind, row.detail) for row in unnamed.out_of_band] == [("object_unnamed", {"before": 0, "after": 1})]


def test_an_extrusion_turned_inside_its_box_is_out_of_band(tmp_path: Path) -> None:
    # The L as an extrusion, with the render mesh Rhino keeps and a reader takes its box from (rhino3dm cannot give
    # one a render mesh without failing on exit, so the file is kept); its vertices are read as a Brep's.
    sealed = tmp_path / "sealed.3dm"
    seal_state_file(Path(__file__).with_name("fixtures") / "ell-extrusion.3dm", sealed, record=RECORD,
                    decisions=DECISIONS, parent_file_sha256=None)
    assert read_state_file(sealed).out_of_band == ()
    turn = rhino3dm.Transform.Rotation(3.141592653589793, rhino3dm.Vector3d(0, 0, 1), rhino3dm.Point3d(2, 2, 0.5))

    def turned(model) -> None:
        for item in model.Objects:
            assert item.Geometry.Transform(turn)

    found = read_state_file(_rewrite(sealed, tmp_path / "turned.3dm", turned))
    assert [(row.kind, row.ref) for row in found.out_of_band] == [("object_reshaped", "object:ell")]


def test_compare_names_added_removed_and_duplicate_objects() -> None:
    wall = ObjectRow("wall", (0.0, 0.0, 0.0), (4.0, 0.2, 3.0), "a" * 64)
    binding = binding_for(RECORD, DECISIONS, (wall,), None)
    door = ObjectRow("door", (1.0, 0.0, 0.0), (2.0, 0.2, 2.1), "b" * 64)
    twin = ObjectRow("door", (3.0, 0.0, 0.0), (4.0, 0.2, 2.1), "c" * 64)

    found, reencoded = compare(binding, RECORD, DECISIONS, (twin, door))

    assert found == (
        OutOfBand("object_added", "object:door", {"after": {"min": [1.0, 0.0, 0.0], "max": [2.0, 0.2, 2.1]}}),
        OutOfBand("object_duplicate", "object:door", {"count": 2}),
        OutOfBand("object_removed", "object:wall", {"before": {"min": [0.0, 0.0, 0.0], "max": [4.0, 0.2, 3.0]}}),
    )
    assert reencoded == ()


def test_refusals(tmp_path: Path) -> None:
    bare = _write_source(tmp_path / "bare.3dm")
    with pytest.raises(StateFileError) as missing:
        read_state_file(bare)
    assert missing.value.code == "STATE_MISSING"

    corrupt = _write_source(tmp_path / "corrupt.3dm", {STATE_KEY: "{not json"})
    with pytest.raises(StateFileError) as broken:
        read_state_file(corrupt)
    assert broken.value.code == "STATE_CORRUPT"

    foreign = _write_source(tmp_path / "foreign.3dm", {STATE_KEY: canonical_json({"schema": "Other@9"})})
    with pytest.raises(StateFileError) as unsupported:
        read_state_file(foreign)
    assert unsupported.value.code == "SCHEMA_UNSUPPORTED"

    state = canonical_json({"schema": SCHEMA, "record": RECORD, "decisions": DECISIONS})
    unsealed = read_state_file(_write_source(tmp_path / "unsealed.3dm", {STATE_KEY: state}))
    assert unsealed.binding is None
    assert unsealed.record == RECORD
    assert unsealed.out_of_band == (OutOfBand("unsealed", "file", {}),)

    # A seal emptied or made unreadable is read as none, and said so.
    for name, text in (("empty", "{}"), ("list", "[]"), ("null", "null"), ("broken", "{not json")):
        found = read_state_file(_write_source(tmp_path / f"bad-binding-{name}.3dm", {STATE_KEY: state, BINDING_KEY: text}))
        assert (found.binding, found.sealed_record, found.record) == (None, None, RECORD)
        assert [row.kind for row in found.out_of_band] == ["seal_broken"]

    source = _write_source(tmp_path / "export.3dm")
    taken = tmp_path / "taken.3dm"
    taken.write_bytes(b"already here")
    for destination in (taken, source):
        with pytest.raises(StateFileError) as refused:
            seal_state_file(source, destination, record=RECORD, decisions=DECISIONS, parent_file_sha256=None)
        assert refused.value.code == "WRITE_FAILED"
    assert taken.read_bytes() == b"already here"
