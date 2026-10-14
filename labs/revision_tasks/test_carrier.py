"""State-bearing files: round trips, hostless revision, and the transport faults the binding must catch.

F3 throughout; every case writes real files through the OCCT exporter (needs
cadquery-ocp and rhino3dm). The oracle judges the revised files in-process here,
on the same meshes the separate oracle process would read.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import carrier, fixtures, oracle, reviser
from archflow.contracts.canonical import canonical_json

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


def _meshes(path: Path) -> dict:
    """The oracle's meshes. It reads no HTML, so a page's embedded GLB is written beside the page and judged."""

    if path.suffix == ".html":
        glb = path.with_suffix(".model.glb")
        glb.write_bytes(carrier.html_glb(carrier.html_blocks(path.read_bytes())))
        path = glb
    return oracle.load_meshes(path)


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
    report = oracle.judge_meshes(TASK, _meshes(revision.written.path), _meshes(start.path))
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


@pytest.mark.parametrize("arm", ["C-full", "C-task"])
def test_the_page_carries_the_3dm_state_and_binding_text(tmp_path, arm):
    """S byte for byte; β byte for byte but for its per-object digests, which are the embedded GLB's (same objects)."""

    pack = None
    if arm == "C-task":
        # Words that would end the script element, and start another, if the page wrote them raw.
        decision = {"decisionId": "d-script", "disposition": "reject", "status": "active", "targetRef": "parameter:door_width",
                    "rawLanguage": "Not 1.4 m. </script><script>alert(1)</script> <!-- <script>"}
        pack = reviser.read_context_pack(fixtures.f3_wall_stair(), root=tmp_path / "ctx0", utterance="Start state.")
        pack = {**pack, "scopedDecisions": [decision]}
    written = {fmt: reviser.write_state_file(fixtures.f3_wall_stair(), arm=arm, fmt=fmt, out_dir=tmp_path / fmt, stem="s0",
                                             parent_sha256=None, context_root=tmp_path / f"ctx-{fmt}", utterance="Start state.",
                                             context_pack=pack)
               for fmt in ("3dm", "html")}
    _, strings = carrier.three_dm_geometry(written["3dm"].path)
    projection = carrier.html_blocks(written["html"].path.read_bytes())[carrier.HTML_PROJECTION_ID]
    carried = json.loads(projection)
    three_dm_binding = json.loads(strings["archflow:binding"])
    assert "<" not in projection
    assert projection == ('{"binding":' + canonical_json(carried["binding"]) + ',"state":' + strings["archflow:state"] + "}").replace("<", "\\u003c")
    assert canonical_json(carried["state"]) == strings["archflow:state"]
    assert set(carried["binding"]["element_geometry_digests"]) == set(three_dm_binding["element_geometry_digests"])
    assert canonical_json({**carried["binding"], "element_geometry_digests": three_dm_binding["element_geometry_digests"]}) == strings["archflow:binding"]
    back = {fmt: reviser.read_state_file(w.path, fmt=fmt) for fmt, w in written.items()}
    assert back["html"].record.digest == back["3dm"].record.digest
    assert back["html"].context_pack == back["3dm"].context_pack


def _projection_element(blocks: dict[str, str]) -> str:
    return f'<script type="application/json" id="{carrier.HTML_PROJECTION_ID}">{blocks[carrier.HTML_PROJECTION_ID]}</script>'


def _edited_state(text: str, blocks: dict[str, str]) -> str:
    carried = json.loads(blocks[carrier.HTML_PROJECTION_ID])
    for parameter in carried["state"]["record"]["parameters"]:
        if parameter["key"] == "lintel_bearing":
            parameter["value"] = 0.05
    return text.replace(blocks[carrier.HTML_PROJECTION_ID], carrier.html_projection(carried["state"], carried["binding"]))


def _edited_model(edit):
    def apply(text: str, blocks: dict[str, str]) -> str:
        document, tail = carrier.glb_split(carrier.html_glb(blocks))
        edit(document)
        return text.replace(blocks[carrier.HTML_MODEL_ID], base64.b64encode(carrier.glb_join(document, tail)).decode("ascii"))
    return apply


def _move_stair(document: dict) -> None:
    for node in document["nodes"]:
        if node.get("name") == "obj-stair":
            node["translation"] = [0.5, 0.0, 0.0]


def _name_another_state(document: dict) -> None:
    document["asset"]["extras"]["archflow"]["design_state_digest"] = "0" * 64


# What another tool, or a hand, does to the page; each is refused with its code.
PAGE_EDITS = {
    "projection removed": (lambda text, blocks: text.replace(_projection_element(blocks), ""), "METADATA_MISSING"),
    "projection cut short": (lambda text, blocks: text.replace(blocks[carrier.HTML_PROJECTION_ID], blocks[carrier.HTML_PROJECTION_ID][:-1]),
                             "METADATA_CORRUPT"),
    "state edited, digest kept": (_edited_state, "STATE_DIGEST_MISMATCH"),
    "object moved in the model": (_edited_model(_move_stair), "GEOMETRY_BINDING_STALE"),
    "model names another state": (_edited_model(_name_another_state), "DESIGN_STATE_REFERENCE_MISMATCH"),
    "a second projection": (lambda text, blocks: text.replace(_projection_element(blocks), _projection_element(blocks) * 2), "FILE_CORRUPT"),
    "model not base64": (lambda text, blocks: text.replace(blocks[carrier.HTML_MODEL_ID], "not base64!"), "FILE_CORRUPT"),
}


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> reviser.Written:
    return _start(tmp_path_factory.mktemp("page"), "C-full", "html")


@pytest.mark.parametrize("edit", sorted(PAGE_EDITS))
def test_an_edited_page_is_refused_not_guessed(page, tmp_path, edit):
    change, code = PAGE_EDITS[edit]
    edited = tmp_path / "edited.html"
    edited.write_text(change(page.path.read_text(encoding="utf-8"), carrier.html_blocks(page.path.read_bytes())), encoding="utf-8", newline="\n")
    assert edited.read_bytes() != page.path.read_bytes()
    revision = _revise(tmp_path, page, "C-full", "html", path=edited)
    assert revision.claim == "refuse" and revision.reasons[0]["code"] == code, revision.reasons


def test_a_revision_keeps_the_decisions_its_file_carried(tmp_path):
    decision = {"decisionId": "d-keep", "disposition": "reject", "status": "active", "targetRef": "parameter:door_width",
                "rawLanguage": "Not 1.4 m."}
    pack = reviser.read_context_pack(fixtures.f3_wall_stair(), root=tmp_path / "ctx0", utterance="Start state.")
    start = reviser.write_state_file(fixtures.f3_wall_stair(), arm="C-task", fmt="3dm", out_dir=tmp_path, stem="s0", parent_sha256=None,
                                     context_root=tmp_path / "ctx", utterance="Start state.",
                                     context_pack={**pack, "scopedDecisions": [decision]})
    first = _revise(tmp_path, start, "C-task", "3dm")
    assert first.claim == "accept"
    assert decision in first.written.context_pack["scopedDecisions"]
    assert decision in reviser.read_state_file(first.written.path, fmt="3dm").context_pack["scopedDecisions"]


def test_carry_decisions_appends_only_missing_ones():
    a, b = {"decisionId": "a"}, {"decisionId": "b"}
    assert reviser.carry_decisions({"scopedDecisions": [a]}, {"scopedDecisions": [a, b]})["scopedDecisions"] == [a, b]
    assert reviser.carry_decisions({"x": 1}, None) == {"x": 1}
