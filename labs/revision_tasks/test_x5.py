"""Short transport chains: a hop through a tool that does not know the state is refused; a plain resave is not."""

from __future__ import annotations

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import x5

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


@pytest.mark.parametrize(("arm", "chain", "expected"), [
    ("C-full", "3dm-glb", [("refuse", ["METADATA_MISSING"], "correct_refuse")] * 2),
    ("D", "3dm-ifc", [("refuse", ["GEOMETRY_BINDING_STALE"], "correct_refuse")] * 2),
    ("C-full", "control", [("accept", [], "correct_accept")]),
])
def test_what_a_hop_loses_is_refused(tmp_path, arm, chain, expected):
    rows = x5.run_chain("F1", arm, chain, tmp_path / "case")
    assert [(r["claim"], r["codes"], r["class"]) for r in rows] == expected
    assert all(r["identity"] == "kept" for r in rows)
