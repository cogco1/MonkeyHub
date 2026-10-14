"""I3 without a model call: proposals are kept only where the start geometry confirms them."""

from __future__ import annotations

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, pipeline, x8

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


def test_only_confirmed_proposals_are_kept(tmp_path):
    record = fixtures.bound(fixtures.f1_portico())
    (tmp_path / "start").mkdir()
    realization = pipeline.realize(record, (tmp_path / "start").resolve(), "start")
    prompt = x8.materials(record, realization)
    assert "- pediment: role roof;" in prompt and "p1-columns-on-stylobate" in prompt
    answer = {"relations": [
        {"a": "entablature", "b": "pediment", "kind": "support", "reason": "the pediment sits on the entablature"},
        {"a": "stylobate", "b": "pediment", "kind": "support", "reason": "wrong: they are 11.4 m apart"},
        {"a": "columns", "b": "entablature", "kind": "support", "reason": "declared already"},
        {"a": "roof-tiles", "b": "pediment", "kind": "support", "reason": "no such element"},
    ]}
    checked = x8.verify(record, realization, answer)
    assert (checked["proposed"], checked["valid"], checked["kept"]) == (4, 2, 1)
    assert [r["why"] for r in checked["rejected"]] == ["already declared", "unknown element or kind"]
    assert x8.recalled("F1", checked["kept_relations"])
