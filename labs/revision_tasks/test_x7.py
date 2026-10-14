"""The scale chain on a tiny grid: every revision is accepted, both arms carry the same state, the summary compares them."""

from __future__ import annotations

import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x7

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


def test_a_short_chain_on_both_arms(tmp_path):
    summary = x7.run(tmp_path, sizes=(4,), revisions=3, arms=("C-full", "D"), formats=("3dm",))
    cell = summary["n4-3dm"]
    assert abs(cell["C-full"]["state_bytes"] - cell["D"]["state_bytes"]) < 64     # the same state, in or beside the file
    assert cell["D"]["file_bytes"] < cell["C-full"]["file_bytes"]
    assert "p" in cell["tost_total"]
    rows = (tmp_path / "x7.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 2 * 4 and all(",accept," in row for row in rows[1:] if ",start," not in row)
    assert json.loads((tmp_path / "x7_summary.json").read_text(encoding="utf-8")) == summary
