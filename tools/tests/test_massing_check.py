"""``check_massing`` must accept the exact stage-a-massing study and reject near misses (#419)."""

import tempfile
import unittest
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType, STEPControl_Writer

from monkeycad import occt_backend
from tools.benchmarks.massing_check import EXPECTED_VOLUME, check_massing

NEEDS_OCCT = unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")

GROUND_CORNER = (20.0, 0.0, 0.0)
GROUND_SIZE = (12.0, 8.0, 3.2)
UPPER_CORNER = (20.0, 0.0, 3.2)
UPPER_SIZE = (10.0, 8.0, 3.0)
UPPER_FULL_SIZE = (12.0, 8.0, 3.0)  # flush with the ground block instead of set back
ROOF_CORNER = (19.5, -0.5, 6.2)
ROOF_SIZE = (11.0, 9.0, 0.3)
RECESS_EDGES = (21.5, 24.5, 27.5, 30.5)


def _box(corner, size):
    return BRepPrimAPI_MakeBox(gp_Pnt(*corner), *size).Shape()


def _write_step(path, shapes):
    """Write ``shapes`` the way a plain, generic OCP script would - not the Hub's own ``write_step``.

    OCCT's STEP statics assume millimetre internals (see this repo's
    ``occt_backend`` module docstring): the plain ``STEPControl_Writer``
    rescales by the *difference* between ``xstep.cascade.unit`` (the unit the
    in-memory shape is already in) and ``write.step.unit`` (the unit the file
    declares). Setting only the commonly-documented ``write.step.unit`` and
    leaving ``xstep.cascade.unit`` at its default ("MM") silently writes a
    file that reads back 1000x too small once ``length_unit="meter"`` is
    requested. Setting both to the same unit keeps raw metre-scale
    coordinates unscaled end to end, which is verified below.
    """
    STEPControl_Controller.Init_s()
    if not Interface_Static.SetCVal_s("write.step.unit", "M") or not Interface_Static.SetCVal_s("xstep.cascade.unit", "M"):
        raise AssertionError("OCCT refused the STEP unit statics")
    writer = STEPControl_Writer()
    for shape in shapes:
        if writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs) != IFSelect_RetDone:
            raise AssertionError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise AssertionError("STEP write failed")


def _recess_tool(edge, *, y_start=-0.05, y_extent=0.35):
    """A cutting box for one window recess; padded 0.05 m past the face by default for a clean cut."""
    return _box((edge, y_start, 0.9), (1.2, y_extent, 1.5))


def _study_shapes(*, recesses=True, upper_size=UPPER_SIZE, misplaced_recess=None):
    """The four solids the stage-a-massing task describes, with optional defects."""
    ground = _box(GROUND_CORNER, GROUND_SIZE)
    if recesses:
        for edge in RECESS_EDGES:
            if edge == misplaced_recess:
                # 0.3 m too far from the face: an internal pocket that never reaches the surface.
                tool = _recess_tool(edge, y_start=0.3, y_extent=0.3)
            else:
                tool = _recess_tool(edge)
            ground = BRepAlgoAPI_Cut(ground, tool).Shape()
    upper = _box(UPPER_CORNER, upper_size)
    roof = _box(ROOF_CORNER, ROOF_SIZE)
    return ground, upper, roof


@NEEDS_OCCT
class MassingCheckTests(unittest.TestCase):
    def test_correct_study_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.step"
            _write_step(path, _study_shapes())
            result = check_massing(path)
            self.assertTrue(result["ok"], result)
            # The round trip must read back at the real scale, not 1000x off in either direction.
            self.assertAlmostEqual(result["volume"], EXPECTED_VOLUME, places=2)
            self.assertEqual(result["bounds"], [[19.5, -0.5, 0.0], [32.0, 8.5, 6.5]])

    def test_missing_recesses_fails_on_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.step"
            _write_step(path, _study_shapes(recesses=False))
            result = check_massing(path)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["volume_ok"], result)

    def test_recess_too_far_from_face_fails_on_outside_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.step"
            _write_step(path, _study_shapes(misplaced_recess=21.5))
            result = check_massing(path)
            self.assertFalse(result["ok"], result)
            # Same material removed, just relocated: volume and bounds alone would miss this.
            self.assertTrue(result["volume_ok"], result)
            self.assertFalse(result["outside"]["(22.1, 0.15, 1.65)"], result)

    def test_upper_block_full_length_fails_on_outside_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.step"
            _write_step(path, _study_shapes(upper_size=UPPER_FULL_SIZE))
            result = check_massing(path)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["outside"]["(31.0, 4.0, 4.7)"], result)

    def test_hidden_names_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "study.step"
            # Well east of x = 15 (so it is not filtered out as background), but far outside
            # the study's own bounds and volume - a stand-in for leftover cutter/helper geometry.
            helper = _box((25.0, 20.0, 0.0), (1.0, 1.0, 1.0))
            _write_step(path, (*_study_shapes(), helper))
            helper_name = occt_backend.read_step(path, length_unit="meter")[-1].name

            polluted = check_massing(path)
            self.assertFalse(polluted["ok"], polluted)

            clean = check_massing(path, hidden_names=(helper_name,))
            self.assertTrue(clean["ok"], clean)


if __name__ == "__main__":
    unittest.main()
