"""The oracle on a real F3 door widening, and on eight wrong geometries it must reject.

The candidate is produced through the real pipeline (needs the OCCT kernel and
rhino3dm). Each mutation then edits the read-back meshes the way a faulty
producer or a metadata-only edit would. The first six are the cases a
volume-and-four-rays oracle accepts; the last two must also fail here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from labs.revision_tasks import _bootstrap  # noqa: F401
from labs.revision_tasks import oracle

TASK = Path(__file__).resolve().parent / "tasks" / "F3-T1-01.json"

try:
    from monkeycad.backends.occt.kernel import occt_available
    import rhino3dm  # noqa: F401
    READY = occt_available()
except ImportError:
    READY = False

pytestmark = pytest.mark.skipif(not READY, reason="needs the OCCT kernel (cadquery-ocp) and rhino3dm")


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    from labs.revision_tasks import fixtures, pipeline

    spec = json.loads(TASK.read_text(encoding="utf-8"))
    start = fixtures.bound(fixtures.f3_wall_stair())
    root = tmp_path_factory.mktemp("f3")
    (root / "start").mkdir()
    (root / "candidate").mkdir()
    start_real = pipeline.realize(start, (root / "start").resolve(), "start")
    arm = pipeline.run_archflow_arm(spec, start, start_real, (root / "candidate").resolve())
    assert arm.claim == "accept", arm.reasons
    return spec, oracle.load_meshes(start_real.preview_path), oracle.load_meshes(arm.realization.preview_path)


def _moved(mesh, select, delta):
    v, f = mesh
    v = v.copy()
    rows = select(v)
    v[rows] += np.asarray(delta, dtype=float)
    return v, f


def _box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return v, f


def _interior(v, x):
    return np.isclose(v[:, 0], x, atol=1e-5)


MUTATIONS = {
    "M1_opening_shifted_left_0.08": lambda m: {"obj-wall": _moved(m["obj-wall"], lambda v: _interior(v, 1.2) | _interior(v, 2.8), (-0.08, 0, 0))},
    "M2_opening_same_area_wrong_aspect": lambda m: {"obj-wall": _aspect(m["obj-wall"])},
    "M3_lintel_shifted_x_0.30": lambda m: {"obj-lintel": _moved(m["obj-lintel"], lambda v: np.ones(len(v), bool), (0.30, 0, 0))},
    "M4_left_jamb_short_0.30": lambda m: {"obj-door-frame-wall-door-left": _moved(m["obj-door-frame-wall-door-left"], lambda v: np.isclose(v[:, 2], 2.05, atol=1e-5), (0, 0, -0.30))},
    "M5_leaf_shifted_x_0.20": lambda m: {"obj-door-leaf-wall-door-0": _moved(m["obj-door-leaf-wall-door-0"], lambda v: np.ones(len(v), bool), (0.20, 0, 0))},
    "M6_leaf_out_of_wall_plane_1.0": lambda m: {"obj-door-leaf-wall-door-0": _moved(m["obj-door-leaf-wall-door-0"], lambda v: np.ones(len(v), bool), (0, 1.0, 0))},
    "M7_opening_filled_volume_matched": lambda m: {"obj-wall": _box((0.0, -0.2, 0.0), (6.0, -0.2 + oracle.volume(m["obj-wall"]) / 18.0, 3.0))},
}


def _aspect(mesh):
    """Opening 1.70 wide and 1.6 x 2.1 / 1.70 high: the same area, so the same wall volume."""

    v, f = mesh
    v = v.copy()
    v[_interior(v, 1.2), 0] = 1.15
    v[_interior(v, 2.8), 0] = 2.85
    head = np.isclose(v[:, 2], 2.1, atol=1e-5) & (v[:, 0] > 1.0) & (v[:, 0] < 3.0)
    v[head, 2] = 1.6 * 2.1 / 1.70
    return v, f


def test_valid_candidate_holds_every_relation(models):
    spec, start, candidate = models
    report = oracle.judge_meshes(spec, candidate, start)
    assert report["verdict"] == "held", [r for r in report["relations"] if r["status"] != "held"]


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_wrong_geometry_is_rejected(models, name):
    spec, start, candidate = models
    mutated = copy.copy(candidate)
    mutated.update(MUTATIONS[name](candidate))
    report = oracle.judge_meshes(spec, mutated, start)
    assert report["verdict"] == "violated", name


def test_metadata_only_edit_is_rejected(models):
    """M8: the record says 1.6 m but the geometry is still the 1.2 m start."""

    spec, start, _ = models
    report = oracle.judge_meshes(spec, start, start)
    failing = {r["id"] for r in report["relations"] if r["status"] == "violated"}
    assert report["verdict"] == "violated" and "D1" in failing


def test_shape_signature_ignores_triangulation_but_not_movement(models):
    _, start, _ = models
    wall = start["obj-wall"]
    v, f = wall
    order = np.arange(len(v))[::-1]
    renumbered = (v[order], np.argsort(order)[f][:, [1, 2, 0]])
    assert oracle.same_shape(oracle.signature(wall), oracle.signature(renumbered))
    assert not oracle.same_shape(oracle.signature(wall), oracle.signature(_moved(wall, lambda vv: np.ones(len(vv), bool), (0.001, 0, 0))))


def test_building_checks_read_bb_as_its_spec_states(tmp_path):
    """K3, K4, K6, R7, R8, R9 and the collision list on BB's baseline, then the ground-floor flight moved 0.25 m west."""

    from archflow.state.state_record import StateRecordEditKind, StateRecordOperator, apply_state_record_operator
    from labs.revision_tasks import buildings, fixtures, pipeline

    start = fixtures.bound(buildings.bb_house())
    (tmp_path / "start").mkdir()
    (tmp_path / "moved").mkdir()
    real = pipeline.realize(start, (tmp_path / "start").resolve(), "m")
    moved = apply_state_record_operator(start, StateRecordOperator(kind=StateRecordEditKind.SET_SCALAR, base_record_digest=start.digest,
                                                                   base_state_digest=start.state_digest, target_ref="parameter:f1_x",
                                                                   key="f1_x", value=8.15))
    shifted = pipeline.realize(moved, (tmp_path / "moved").resolve(), "m", prior=real)
    meshes = oracle.load_meshes(real.preview_path)

    def run(measure, on=meshes):
        return oracle.CHECKS[measure["check"]](measure, on, None)

    assert run({"check": "wall_piers", "min_m": 0.5})[1]["min_pier_m"] == 0.55
    assert run({"check": "wall_above_lintels", "min_m": 0.15})[1]["min_wall_above_m"] == 0.3
    for flight in ("F1", "F2", "F3"):
        status, measured = run({"check": "flight", "object": f"obj-stair-{flight}", "risers": 17, "max_rise_m": 0.18, "min_going_m": 0.28,
                                "min_headroom_m": 2.0})
        assert status == "held" and round(measured["headroom_min_m"], 3) == 2.094
        ends = run({"check": "landing_depth", "object": f"obj-stair-{flight}", "min_m": 1.2})[1]["ends"]
        assert [round(e["depth_m"], 4) for e in ends.values()] == [1.6, 1.6]
    pairs = [{"wall": "obj-wall-core-W-L1", "flight": "obj-stair-F1"}, {"wall": "obj-wall-core-E-L2", "flight": "obj-stair-F2"}]
    assert [p["gap_m"] for p in run({"check": "door_beside_flight", "pairs": pairs})[1]["pairs"]] == [0.05, 0.05]
    collisions = {"check": "collisions", "allowed": [["obj-lintel-", "obj-wall-"]]}
    assert run(collisions) == ("held", {"collisions": [], "allowed_embeddings": 46})
    status, measured = run(collisions, oracle.load_meshes(shifted.preview_path))
    assert status == "violated"
    assert [(c["a"], c["b"]) for c in measured["collisions"]] == [("obj-stair-F1", "obj-wall-core-W-L1"), ("obj-slab-L2", "obj-stair-F1")]
    assert abs(measured["collisions"][0]["common_volume_estimate_m3"] - 1.1246) < 0.03


def _block(lo, hi):
    """A closed box mesh, outward faces, as the oracle reads one."""

    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return v, f


def test_a_point_s_distance_to_a_surface():
    box = _block((0, 0, 0), (1, 1, 1))
    points = np.array([[0.5, 0.5, 0.5], [0.5, 0.5, 0.9], [2.0, 0.5, 0.5], [2.0, 2.0, 0.5], [2.0, 2.0, 2.0]])
    assert oracle.surface_distance(box, points) == pytest.approx([0.5, 0.1, 1.0, 2 ** 0.5, 3 ** 0.5])
    assert abs(oracle.volume(box) - 1.0) < 1e-12


def test_collisions_without_seams_are_as_before():
    meshes = {"obj-a": _block((0, 0, 0), (1, 1, 1)), "obj-b": _block((0.9995, 0, 0), (2, 1, 1))}      # a 0.5 mm sliver
    status, measured = oracle.check_collisions({"check": "collisions"}, meshes, None)
    assert status == "violated" and set(measured) == {"collisions", "allowed_embeddings"}            # the result as it always was
    assert measured == {"collisions": [{"a": "obj-a", "b": "obj-b", "common_volume_estimate_m3": round(14 ** 3 * 0.0005 / 14 ** 3, 6)}],
                        "allowed_embeddings": 0}
    other = oracle.check_collisions({"check": "collisions", "seams": [["obj-c", "obj-d"]], "seam_depth_m": 0.002}, meshes, None)
    assert other[0] == "violated" and other[1]["collisions"] == measured["collisions"] and other[1]["seam_points_within_tolerance"] == []
    assert oracle.check_collisions({"check": "collisions", "seams": [], "seam_depth_m": 0.002}, meshes, None) == (status, measured)


def test_collisions_at_a_declared_seam_count_only_points_deeper_than_its_depth():
    sliver = {"obj-a": _block((0, 0, 0), (1, 1, 1)), "obj-b": _block((0.9995, 0, 0), (2, 1, 1))}
    seam = {"check": "collisions", "seams": [["obj-b", "obj-a"]], "seam_depth_m": 0.002}               # matched either way, as allowed is
    status, measured = oracle.check_collisions(seam, sliver, None)
    assert status == "held" and measured["collisions"] == [] and measured["seam_depth_m"] == 0.002
    [kept] = measured["seam_points_within_tolerance"]                                                  # reported, not hidden
    assert (kept["a"], kept["b"], kept["points"]) == ("obj-a", "obj-b", 14 ** 3) and kept["max_depth_m"] <= 0.00025 + 1e-9
    assert kept["volume_estimate_m3"] == pytest.approx(0.0005, abs=1e-6)
    deep = {"obj-a": _block((0, 0, 0), (1, 1, 1)), "obj-b": _block((0.7, 0, 0), (2, 1, 1))}            # a 0.3 m overlap at the seam
    status, measured = oracle.check_collisions(seam, deep, None)
    assert status == "violated" and measured["collisions"][0]["common_volume_estimate_m3"] == pytest.approx(0.3, abs=1e-6)
    assert measured["seam_points_within_tolerance"] == []
    status, measured = oracle.check_collisions({**seam, "seam_depth_m": 0.0001}, sliver, None)               # a stricter depth
    [kept] = measured["seam_points_within_tolerance"]
    assert status == "violated" and kept["points"] == 6 * 14 * 14                    # the 6 of 14 layers within 0.1 mm of a face
    assert measured["collisions"][0]["common_volume_estimate_m3"] == pytest.approx(0.0005 * 8 / 14, abs=1e-6)
