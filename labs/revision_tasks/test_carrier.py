"""State-bearing files: round trips, hostless revision, and the transport faults the binding must catch.

F3 throughout; every case writes real files through the OCCT exporter (needs
cadquery-ocp and rhino3dm). The oracle judges the revised files in-process here,
on the same meshes the separate oracle process would read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import carrier, fixtures, oracle, reviser

try:
    from monkeycad.backends.occt.kernel import occt_available
    import rhino3dm
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp) and rhino3dm")

DOOR = [{"target": "parameter:door_width", "parameter": "door_width", "from": 1.2, "to": 1.6}]
TASK = json.loads((Path(__file__).resolve().parent / "tasks" / "F3-T1-01.json").read_text(encoding="utf-8"))


def _start(tmp_path: Path, arm: str, fmt: str) -> reviser.Written:
    return reviser.write_state_file(fixtures.f3_wall_stair(), arm=arm, fmt=fmt, out_dir=tmp_path, stem="s0", parent_sha256=None,
                                    context_root=tmp_path / "ctx", utterance="Start state.")


def _revise(tmp_path: Path, start: reviser.Written, arm: str, fmt: str, path: Path | None = None, **kwargs) -> reviser.Revision:
    return reviser.revise(path or start.path, fmt=fmt, arm=arm, deltas=DOOR, out_dir=tmp_path, stem="s1", context_root=tmp_path / "ctx",
                          utterance="Widen the door opening from 1.2 m to 1.6 m.", sidecar=start.sidecar, **kwargs)


def _rewrite_3dm(source: Path, target: Path, *, move: str | None = None, drop: tuple[str, ...] = ()) -> Path:
    """What another tool does to the file: read it with rhino3dm, optionally move one object or drop strings, save."""

    model = rhino3dm.File3dm.Read(str(source))
    if move is not None:
        for obj in list(model.Objects):
            if obj.Attributes.Name == move:
                geometry, attributes = obj.Geometry, obj.Attributes
                geometry.Translate(rhino3dm.Vector3d(0.5, 0.0, 0.0))
                model.Objects.Delete(attributes.Id)
                model.Objects.AddMesh(geometry, attributes)
    for key in drop:
        model.Strings.Delete(key)
    assert model.Write(str(target), 8)
    return target


@pytest.mark.parametrize("fmt", carrier.FORMATS)
@pytest.mark.parametrize("arm", carrier.ARMS)
def test_every_arm_reads_back_and_revises(tmp_path, arm, fmt):
    start = _start(tmp_path, arm, fmt)
    back = reviser.read_state_file(start.path, fmt=fmt, sidecar=start.sidecar)
    assert back.record.digest == start.record.digest
    assert (back.context_pack is not None) == (arm == "C-task")
    assert (start.sidecar is not None) == (arm == "D")
    revision = _revise(tmp_path, start, arm, fmt)
    assert revision.claim == "accept", revision.reasons
    assert revision.written.binding["parent_file_sha256"] == back.file_sha256
    report = oracle.judge_meshes(TASK, oracle.load_meshes(revision.written.path), oracle.load_meshes(start.path))
    assert report["verdict"] == "held", [r for r in report["relations"] if r["status"] != "held"]


def test_d_arm_file_carries_only_references(tmp_path):
    start = _start(tmp_path, "D", "3dm")
    _, strings = carrier.three_dm_geometry(start.path)
    assert "archflow:state" not in strings and "archflow:binding" not in strings
    assert strings["archflow:design_state_digest"] == start.binding["state_digest"]


def test_a_plain_resave_keeps_the_binding(tmp_path):
    start = _start(tmp_path, "C-full", "3dm")
    resaved = _rewrite_3dm(start.path, tmp_path / "resaved.3dm")
    assert reviser.read_state_file(resaved, fmt="3dm").record.digest == start.record.digest


@pytest.mark.parametrize("arm", carrier.ARMS)
def test_a_foreign_geometry_edit_is_caught_unless_unbound(tmp_path, arm):
    start = _start(tmp_path, arm, "3dm")
    edited = _rewrite_3dm(start.path, tmp_path / "edited.3dm", move="obj-stair")
    revision = _revise(tmp_path, start, arm, "3dm", path=edited)
    if arm == "C-unbound":
        # No geometry binding: the edit goes unnoticed and the rebuild silently discards it.
        assert revision.claim == "accept"
    else:
        assert revision.claim == "refuse" and revision.reasons[0]["code"] == "GEOMETRY_BINDING_STALE"


def test_stripped_state_is_refused_not_guessed(tmp_path):
    start = _start(tmp_path, "C-full", "3dm")
    stripped = _rewrite_3dm(start.path, tmp_path / "stripped.3dm", drop=("archflow:state", "archflow:binding"))
    revision = _revise(tmp_path, start, "C-full", "3dm", path=stripped)
    assert revision.claim == "refuse" and revision.reasons[0]["code"] == "METADATA_MISSING"


def test_stripped_glb_extras_are_refused(tmp_path):
    start = _start(tmp_path, "C-full", "glb")
    document, tail = carrier.glb_split(start.path.read_bytes())
    document["asset"].pop("extras")
    stripped = tmp_path / "stripped.glb"
    stripped.write_bytes(carrier.glb_join(document, tail))
    revision = _revise(tmp_path, start, "C-full", "glb", path=stripped)
    assert revision.claim == "refuse" and revision.reasons[0]["code"] == "METADATA_MISSING"


def test_a_change_made_against_another_file_is_stale(tmp_path):
    start = _start(tmp_path, "C-full", "3dm")
    revision = _revise(tmp_path, start, "C-full", "3dm", expected_file_sha256="0" * 64)
    assert revision.claim == "refuse" and revision.reasons[0]["code"] == "STALE_BASE"


def test_an_edited_state_without_a_new_digest_is_refused(tmp_path):
    start = _start(tmp_path, "C-full", "glb")
    document, tail = carrier.glb_split(start.path.read_bytes())
    state = document["asset"]["extras"]["archflow"]["state"]
    for parameter in state["record"]["parameters"]:
        if parameter["key"] == "lintel_bearing":
            parameter["value"] = 0.05
    edited = tmp_path / "edited.glb"
    edited.write_bytes(carrier.glb_join(document, tail))
    revision = _revise(tmp_path, start, "C-full", "glb", path=edited)
    assert revision.claim == "refuse" and revision.reasons[0]["code"] == "STATE_DIGEST_MISMATCH"
