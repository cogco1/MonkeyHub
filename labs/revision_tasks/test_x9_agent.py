"""DD-agent without a model: the object table it reads, and an answer carrying the right edits passes like DD-det."""

from __future__ import annotations

import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x1, x9, x9_agent

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


def test_the_right_edits_pass_the_oracle_and_match_the_rebuild(tmp_path):
    task = next(t for t in json.loads(x1.TASKS.read_text(encoding="utf-8"))["tasks"] if t["task_id"] == "F3-T2")
    start = x9.family_start("F3", tmp_path)
    rows, state = x9_agent.object_table(start["native"])
    lintel = next(r for r in rows if r["name"] == "obj-lintel")
    assert lintel["primitive"]["kind"] == "extrusion" and json.loads(state)["schema"] == "ArchFlowStateFile@1"
    values, _ = x9.requested_values(task, start["record"])
    det = x9.dd_det(start["native"], values, tmp_path / "det.native.3dm")
    regen = x9.regen(start["bound"], values, tmp_path / "regen")
    (tmp_path / "trials").mkdir()
    answer = {"verdict": "held", "edits": det["edits"], "reason": "lintel bearing 0.3 m"}
    execution = x9_agent.execute(answer, task, start, regen, tmp_path / "trials" / "t")
    scored = x9_agent.score(answer, task, execution)
    assert (scored["edits_applied"], scored["oracle_verdict"], scored["agrees_regen"], scored["unrelated"], scored["missed"]) == (True, "held", True, 0, 0)
    assert scored["verdict_ok_four"] and not scored["false_accept"]
    bad = x9_agent.execute({"verdict": "held", "edits": [{"object": "obj-nowhere", "field": "height", "value": 1}]}, task, start, regen,
                           tmp_path / "trials" / "u")
    assert not bad["applied"] and bad["why"].startswith("EDIT_REFUSED")
