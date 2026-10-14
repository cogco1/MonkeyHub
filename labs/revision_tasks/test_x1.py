"""The history agrees with the fixture records, and the reference decision answers a few tasks as the gold does."""

from __future__ import annotations

import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x1

TASKS = {t["task_id"]: t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"]}

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False


@pytest.mark.parametrize("family", ["F1", "F2", "F3"])
def test_history_and_record_agree_on_every_parameter(family):
    context = x1.history_context(family)
    assert context.notes["parameter_mismatches"] == []
    assert context.notes["dangling"] == []


def test_the_task_table_is_complete():
    assert sorted(TASKS) == sorted([f"{f}-T{n}" for f in ("F1", "F2", "F3") for n in range(1, 9)] + ["F1-T4b"])
    for task in TASKS.values():
        assert set(task["gold"]["verdicts"]) <= {"held", "violated", "refuse", "unknown", "flag_rejected"}


def test_rejections_live_only_in_the_history():
    h = x1.history_context("F3")
    record_only = x1.Context("SF", h.record, set(), h.evidence)
    assert x1.decide(h, TASKS["F3-T7"], None)["verdict"] == "flag_rejected"
    assert x1.decide(record_only, TASKS["F3-T7"], None)["verdict"] == "unknown"


def test_a_contradiction_is_refused_before_any_geometry():
    h = x1.history_context("F3")
    assert x1.decide(h, TASKS["F3-T6"], None)["verdict"] == "refuse"


def test_current_evidence_decides_and_superseded_does_not():
    h = x1.history_context("F3")
    answer = x1.decide(h, TASKS["F3-T8"], None)
    assert answer["verdict"] == "held" and answer["changes"] == {"storey_height": 3.0} and answer["affected"] == []


def test_a_geometry_only_file_knows_nothing_it_has_to_check():
    geometry = x1.Context("GF", None)
    answer = x1.decide(geometry, TASKS["F3-T3"], None)
    assert answer["verdict"] == "held" and answer["recheck"] == []


@pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")
def test_a_page_decides_as_the_3dm_does(tmp_path):
    record = x1.FAMILY_FIXTURE["F3"]()
    contexts = {}
    for fmt in ("3dm", "html"):
        written = x1.reviser.write_state_file(record, arm="C-full", fmt=fmt, out_dir=tmp_path / fmt, stem="state", parent_sha256=None,
                                              context_root=tmp_path / f"ctx-{fmt}", utterance="Current design state.")
        contexts[fmt] = x1.file_context(f"SF[C-full,{fmt}]", written.path, fmt, "F3")
    assert contexts["html"].state_bytes == contexts["3dm"].state_bytes
    assert (contexts["html"].evidence, contexts["html"].decisions) == (contexts["3dm"].evidence, contexts["3dm"].decisions)
    for task in (t for t in TASKS.values() if t["family"] == "F3"):
        assert x1.decide(contexts["html"], task, None, execute=False) == x1.decide(contexts["3dm"], task, None, execute=False), task["task_id"]


@pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")
def test_a_local_change_rebuilds_only_what_it_reaches(tmp_path):
    h = x1.history_context("F3")
    answer = x1.decide(h, TASKS["F3-T2"], tmp_path)
    assert answer["verdict"] == "held" and answer["affected"] == ["lintel"]
