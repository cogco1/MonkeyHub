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


def test_ba_matches_its_spec_and_raising_the_order_hits_the_second_pediment(tmp_path):
    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    start = fixtures.bound(buildings.ba_pantheon())
    assert len(start.entities_of("Element@1")) == 62          # 61 design rows and the hidden bronze-door void
    real = _realize(start, tmp_path / "start")
    # the spec's 44 solids, with the attic, the dome and the oculus ring in the drum's eight pieces each
    assert len(pipeline.visible_objects(real)) == 65

    def box(name):
        row = real.receipt.readback[name]["bbox"]
        return tuple(round(v, 4) for v in (*row["min"], *row["max"]))

    assert {name: box(name) for name in ("obj-podium", "obj-portico-col-F1", "obj-entablature-front", "obj-pediment-front",
                                         "obj-portico-roof", "obj-intermediate-pediment")} == {
        "obj-podium": (-17.1, 32.85, -1.32, 17.1, 48.35, 0.0),
        "obj-portico-col-F1": (-16.77, 46.61, 0.0, -15.29, 48.09, 14.15),
        "obj-entablature-front": (-17.1, 46.15, 14.15, 17.1, 48.35, 17.5),
        "obj-pediment-front": (-17.1, 46.85, 17.5, 17.1, 48.35, 25.23),
        "obj-portico-roof": (-17.1, 32.85, 17.5, 17.1, 46.85, 25.23),
        "obj-intermediate-pediment": (-17.1, 32.85, 21.9, 17.1, 33.45, 29.16)}
    assert round(box("obj-portico-col-front-E-0")[0] - box("obj-portico-col-front-W-2")[3], 4) == 3.4     # K1, the two central columns
    assert max(box(name)[5] for name in pipeline.visible_objects(real)) == box("obj-oculus-ring-0")[5] == 44.8   # K2
    assert all(c["status"] == "held" for c in pipeline.declared_checks(start, real))
    assert all(o["status"] == "held" for o in pipeline.obligation_checks(start))
    assert pipeline.penetrating_pairs(real) == {}
    # A4: the order 1.0 m taller, the link block untouched. Every declared check still holds; the one new penetration is J3,
    # which nothing declares. Spec 2.3 gives 5.415 m3 taking the notch slope equal to the roof's; at the stated notch
    # half-width 8.473 it is 0.00002 flatter, and the overlap is 9.0240 m2 x 0.60.
    raised = apply_state_record_operator(start, StateRecordOperator(
        kind=StateRecordEditKind.SET_SCALAR, base_record_digest=start.digest, base_state_digest=start.state_digest,
        target_ref="parameter:column_height", key="column_height", value=15.15))
    moved = _realize(raised, tmp_path / "a4", prior=real)
    assert {pair: round(volume, 4) for pair, volume in pipeline.penetrating_pairs(moved).items()} == {
        ("obj-intermediate-pediment", "obj-portico-roof"): 5.4144}
    assert all(c["status"] == "held" for c in pipeline.declared_checks(raised, moved))
