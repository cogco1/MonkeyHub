"""X4 on the X3 buildings: each BA/BB task starts where its request is made in X3, on the gold main line.

Compile only (producers and compiler, no OCCT): the start's digests, the chain of earlier landed requests, the values
the patch is made against, the earlier requests' measured boxes on the start, and the task's own boxes on the result.
"""

from __future__ import annotations

import fnmatch
import json

import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import fixtures, pipeline, taskspec

REQUESTS = json.loads((taskspec.LAB / "x3_requests.json").read_text(encoding="utf-8"))
# Earlier requests that land in X3's gold main line but cannot be a SET_SCALAR start patch (the task file says so).
NOT_EXPRESSIBLE = {"B2": "the lintel's ends are derived parameters; only the shared lintel_bearing moves them, and it moves all 46 lintels"}


def _tasks():
    return [path for path in taskspec.task_files() if path.stem.startswith(("BA-", "BB-"))]


def _request(building: str, request_id: str) -> dict:
    return next(r for r in REQUESTS["buildings"][building]["requests"] if r["id"] == request_id)


def _apply(record, deltas):
    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator

    for delta in deltas:
        record = apply_state_record_operator(record, StateRecordOperator(
            kind=StateRecordEditKind.SET_SCALAR, base_record_digest=record.digest, base_state_digest=record.state_digest,
            target_ref=delta["target"], key=delta["parameter"], value=delta["to"]))
    return record


def _boxes(record) -> dict[str, tuple[list[float], list[float]]]:
    """Each object's analytic box from the compiler, in the model frame (x, plan, up) the oracle reads."""

    from archflow.state.geometry_program import expected_object_bounds

    program, _, _ = pipeline.compile_record(record)
    return {name: ([b["bbox_min"][0], b["bbox_min"][2], b["bbox_min"][1]], [b["bbox_max"][0], b["bbox_max"][2], b["bbox_max"][1]])
            for name, b in expected_object_bounds(program).items() if b is not None}


def _bbox_problems(measure: dict, boxes: dict) -> list[str] | None:
    """The oracle's ``bbox`` rule on analytic boxes; None when the object has no analytic box."""

    if measure["object"] not in boxes:
        return None
    lo, hi = boxes[measure["object"]]
    tol = float(measure.get("tolerance_m", 0.002))
    return [f"{measure['object']} {side}[{'xyz'[axis]}] {actual[axis]:.4f} != {expected}"
            for side, values, actual in (("min", measure.get("min", [None] * 3), lo), ("max", measure.get("max", [None] * 3), hi))
            for axis, expected in enumerate(values) if expected is not None and abs(actual[axis] - expected) > tol]


def test_the_building_tasks_are_the_requests_with_a_typed_patch():
    assert sorted(p.stem for p in _tasks()) == ["BA-A1-01", "BA-A2-01", "BA-A3-01", "BA-A4-01", "BA-A6-01", "BA-A7-01",
                                                  "BB-B1-01", "BB-B4-01", "BB-B5-01"]


@pytest.mark.parametrize("path", _tasks(), ids=lambda p: p.stem)
def test_each_task_starts_where_its_request_is_made_in_x3(path):
    spec = taskspec.load(path)
    source = spec["x3_request"]
    building, request = source["building"], _request(source["building"], source["request"])
    assert spec["intended_change"]["text_en"] == request["text"] and source["order"] == request["order"]
    assert spec["gold"]["expected_verdict"] == request["expected"]["verdict"]

    # the setup is every earlier request that lands in X3's gold main line, in order, each as its own task's patch
    landed = [r["id"] for r in REQUESTS["buildings"][building]["requests"]
              if r["order"] is not None and r["order"] < request["order"] and r["expected"]["verdict"] == "held" and "complete" in r["expected"]["outcome"]]
    assert source["start_after"] == [r for r in landed if r not in NOT_EXPRESSIBLE]
    patches = [taskspec.load(taskspec.TASKS / f"{building}-{r}-01.json")["intended_change"]["typed_delta"] for r in source["start_after"]]
    assert spec.get("setup", []) == [delta for patch in patches for delta in patch]
    assert spec.get("patch_base") == ("after_setup" if patches else None)
    assert all(r in source.get("start_note", "") for r in landed if r in NOT_EXPRESSIBLE)

    record = taskspec.start_record(spec)
    assert spec["start_state"]["content_digest"] == record.digest
    assert spec["start_state"]["exact_base_digest"] == fixtures.bound(record).state_digest


@pytest.mark.parametrize("path", _tasks(), ids=lambda p: p.stem)
def test_the_start_holds_the_earlier_gold_and_the_patch_gives_the_task_gold(path):
    from archflow.state.state_record import evaluate_parameters

    spec = taskspec.load(path)
    building = spec["x3_request"]["building"]
    start = taskspec.start_record(spec)
    start = start if start.base is not None else fixtures.bound(start)
    values = evaluate_parameters(start)
    assert {d["parameter"]: d["from"] for d in spec["intended_change"]["typed_delta"]} == \
        {d["parameter"]: pytest.approx(values[d["parameter"]]) for d in spec["intended_change"]["typed_delta"]}

    # each earlier request's box measures (x3_requests.json) hold on the start unless a later request in the chain changes
    # that object: after A1 the oculus ring top is 45.80 and the drum's outer radius 28.55; after B1 the lintel spans 8.15-9.85
    after = spec["x3_request"]["start_after"]
    earlier = [m["measure"] for k, r in enumerate(after) for m in _request(building, r)["measures"]
               if m["measure"]["check"] == "bbox" and "*" not in m["measure"]["object"]
               and not any(fnmatch.fnmatchcase(m["measure"]["object"], p) for later in after[k + 1:] for p in _request(building, later)["change"])]
    assert bool(earlier) == bool(after)
    boxes = _boxes(start)
    checked = [_bbox_problems(m, boxes) for m in earlier]
    assert None not in checked and not any(checked), checked

    # the task's own box relations hold on the gold result (start + the typed patch), unless the gold refuses it
    own = [r["measure"] for r in spec["protected_relations"] if r["measure"]["check"] == "bbox"]
    result = _boxes(_apply(start, spec["intended_change"]["typed_delta"]))
    found = [_bbox_problems(m, result) for m in own]
    assert own and None not in found
    if spec["gold"]["expected_verdict"] == "refuse":
        return
    assert not any(found), found
