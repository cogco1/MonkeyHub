"""BB builds as its spec states, and its undeclared interface shows up only as a collision."""

from __future__ import annotations

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import buildings, fixtures, pipeline

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


def _realize(record, work, prior=None):
    work.mkdir()
    return pipeline.realize(record, work.resolve(), "model", prior=prior)


def test_bb_matches_its_spec_and_moving_a_flight_hits_the_core_wall(tmp_path):
    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    start = fixtures.bound(buildings.bb_house())
    assert len(start.entities_of("Element@1")) == 89          # 86 design rows and three hidden stair-hole voids
    real = _realize(start, tmp_path / "start")
    assert len(pipeline.visible_objects(real)) == 118
    volumes = {name: round(real.receipt.readback[name]["volume"], 4) for name in ("obj-stair-F1", "obj-slab-L1", "obj-slab-L2")}
    assert volumes == {"obj-stair-F1": 8.316, "obj-slab-L1": 39.672, "obj-slab-L2": 38.762}
    assert all(c["status"] == "held" for c in pipeline.declared_checks(start, real))
    baseline = pipeline.penetrating_pairs(real)
    assert len(baseline) == 46 and all(any(o.startswith("obj-lintel-") for o in pair) for pair in baseline)
    shifted = apply_state_record_operator(start, StateRecordOperator(
        kind=StateRecordEditKind.SET_SCALAR, base_record_digest=start.digest, base_state_digest=start.state_digest,
        target_ref="parameter:f1_x", key="f1_x", value=8.15))
    moved = _realize(shifted, tmp_path / "b5", prior=real)
    new = {pair: round(volume, 4) for pair, volume in pipeline.penetrating_pairs(moved).items() if pair not in baseline}
    assert new == {("obj-stair-F1", "obj-wall-core-W-L1"): 1.1246, ("obj-slab-L2", "obj-stair-F1"): 0.0094}
    assert all(c["status"] == "held" for c in pipeline.declared_checks(shifted, moved))
