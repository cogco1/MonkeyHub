"""Undeclared relations made explicit: what I1 and I2 find on the fixtures, and that a broken one is noticed."""

from __future__ import annotations

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, infer, pipeline

try:
    from monkeycad.backends.occt.kernel import occt_available
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp)")


def _start(tmp_path, record):
    work = tmp_path / "start"
    work.mkdir()
    return pipeline.realize(record, work.resolve(), "start")


def test_the_undeclared_pediment_support_is_found_and_its_loss_is_noticed(tmp_path):
    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    start = fixtures.bound(fixtures.f1_portico())
    kept, dropped, narrowed = infer.infer_all(start, _start(tmp_path, start))
    assert {(k.layer, k.kind, k.a, k.b) for k in kept} == {("I1", "support", "entablature", "pediment"), ("I2", "support", "entablature", "pediment")}
    assert dropped == [] and narrowed == []
    shallower = apply_state_record_operator(start, StateRecordOperator(
        kind=StateRecordEditKind.SET_SCALAR, base_record_digest=start.digest, base_state_digest=start.state_digest,
        target_ref="parameter:entablature_height", key="entablature_height", value=1.5))
    work = tmp_path / "candidate"
    work.mkdir()
    candidate = pipeline.realize(shallower, work.resolve(), "candidate")
    statuses = {r["status"] for r in infer.check_inferred(infer.with_inferred(shallower, kept), candidate)}
    assert statuses == {"broken"}


def test_the_narrowing_rules_remove_what_the_record_explains_and_what_is_far(tmp_path):
    start = fixtures.bound(fixtures.f3_wall_stair())
    kept, _, narrowed = infer.infer_all(start, _start(tmp_path, start))
    removed = {(n["item"].layer, n["item"].kind, n["item"].a, n["item"].b): n["rule"] for n in narrowed}
    # the stair and the wall both top out on level-2: the record already says so
    assert removed[("I1", "same_level", "stair", "wall")] == "declared common datum"
    # the stair stands 1.5 m from the wall: beyond the role rule's reach
    assert removed[("I2", "non_interference", "wall", "stair")] == "farther apart than 1.0 m"
    assert {(k.layer, k.kind, k.a, k.b) for k in kept} == {("I1", "abutment", "slab-l2", "wall")}


def test_a_roof_against_a_wall_is_not_its_support(tmp_path):
    start = fixtures.bound(fixtures.f2_portico_rotunda())
    kept, dropped, narrowed = infer.infer_all(start, _start(tmp_path, start))
    # both column rows stand on the stylobate's top and share one height parameter: their tops align by declaration
    assert {(n["item"].kind, n["item"].a, n["item"].b): n["rule"] for n in narrowed} == {
        ("same_level", "portico-columns-front", "portico-columns-rear"): "declared common datum"}
    pair = ("support", "rotunda-wall", "transition-roof")
    assert pair in {(k.kind, k.a, k.b) for k in dropped} and pair not in {(k.kind, k.a, k.b) for k in kept}
    assert ("I1", "abutment", "rotunda-wall", "transition-roof") in {(k.layer, k.kind, k.a, k.b) for k in kept}


def test_predicted_boxes_settle_the_pediment_before_execution():
    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    start = fixtures.bound(fixtures.f1_portico())
    item = infer.Inferred("I1", "support", "entablature", "pediment", {"distance_m": 0.0})
    with_one = infer.with_inferred(start, [item])

    def successor(key, value):
        return apply_state_record_operator(with_one, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=with_one.digest, base_state_digest=with_one.state_digest,
            target_ref=f"parameter:{key}", key=key, value=value))

    shallower = infer.predict_inferred(with_one, successor("entablature_height", 1.5), {"entablature"})
    assert [(r["status"], r["method"]) for r in shallower] == [("broken", "predicted_box")]
    four = infer.predict_inferred(with_one, successor("column_count", 4), {"columns", "entablature"})
    assert [(r["status"], r["method"]) for r in four] == [("held", "predicted_box")]


def test_a_crossing_inference_is_the_pre_execution_question():
    record = fixtures.bound(fixtures.f1_portico())
    item = infer.Inferred("I1", "support", "entablature", "pediment", {"distance_m": 0.0})
    with_one = infer.with_inferred(record, [item])
    assert infer.crossing(with_one, {"entablature"}) == [item.obligation().obligation_id]
    assert infer.crossing(with_one, {"entablature", "pediment"}) == []
