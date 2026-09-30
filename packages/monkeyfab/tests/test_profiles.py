import math

import pytest

from monkeyfab.profiles import get_profile


def test_working_volume_deducts_two_xy_edges_and_only_one_top_clearance():
    profile = get_profile("h2d-right")
    assert profile.working_volume(0, 0) == profile.usable_volume_mm
    assert profile.working_volume(3, 7) == (319, 314, 318)
    assert profile.working_volume() == (315, 310, 320)


def test_x1c_rectangle_avoids_cutter_zone_and_respects_default_height():
    profile = get_profile("x1c")
    origin = profile.usable_origin_mm
    upper = tuple(a + b for a, b in zip(origin, profile.usable_volume_mm))
    assert origin == (18, 0, 0)
    assert upper == (256, 256, 250)
    assert upper[2] < profile.nominal_volume_mm[2]


def test_h2s_uses_its_full_envelope_and_default_margins():
    profile = get_profile("h2s")
    assert profile.key == "h2s"
    assert profile.label == "Bambu Lab H2S"
    assert profile is not get_profile("h2d")
    assert profile.nominal_volume_mm == (340, 320, 340)
    assert profile.usable_origin_mm == (0, 0, 0)
    assert profile.usable_volume_mm == (340, 320, 340)
    assert profile.working_volume(0, 0) == (340, 320, 340)
    assert profile.working_volume() == (330, 310, 335)


def test_h2d_shared_envelope_is_the_intersection_of_both_nozzles():
    left = get_profile("h2d-left")
    right = get_profile("h2d-right")
    shared = get_profile("h2d-dual")
    assert left.usable_volume_mm[2] == 320
    assert right.usable_volume_mm[2] == 325
    assert left.usable_origin_mm[0] == 0
    assert right.usable_origin_mm[0] == 25
    for axis in range(3):
        lower = max(left.usable_origin_mm[axis], right.usable_origin_mm[axis])
        upper = min(
            left.usable_origin_mm[axis] + left.usable_volume_mm[axis],
            right.usable_origin_mm[axis] + right.usable_volume_mm[axis],
        )
        assert shared.usable_origin_mm[axis] == lower
        assert shared.usable_volume_mm[axis] == upper - lower
    assert get_profile("h2d") is shared


@pytest.mark.parametrize(
    ("xy_margin", "z_clearance"),
    [(-1, 0), (0, -1), (math.nan, 0), (0, math.nan), (math.inf, 0), (0, math.inf)],
)
def test_invalid_margin_values_are_rejected(xy_margin, z_clearance):
    with pytest.raises(ValueError, match="finite and non-negative"):
        get_profile("x1c").working_volume(xy_margin, z_clearance)


@pytest.mark.parametrize(("xy_margin", "z_clearance"), [(119, 0), (120, 0), (0, 250), (0, 251)])
def test_margins_must_leave_positive_dimensions(xy_margin, z_clearance):
    with pytest.raises(ValueError, match="positive working volume"):
        get_profile("x1c").working_volume(xy_margin, z_clearance)


def test_unknown_printer_names_are_not_silently_substituted():
    with pytest.raises(ValueError, match="unknown printer profile"):
        get_profile("h2d-full-bed")
