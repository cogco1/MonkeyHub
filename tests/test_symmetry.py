"""P074/M075: axial symmetry measurement and stage-gate contracts."""

import glob
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from archflow.evaluation import (
    SymmetryMeasurementError,
    axial_group_offsets,
)


def scene_object(object_id, minimum, maximum, bindings, physical=True):
    return {
        "object_id": object_id,
        "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
        "semantic_binding_ids": list(bindings),
        "physical": physical,
    }


SCENE = [
    scene_object("slab", (0.0, 0.0, 0.0), (20.0, 1.0, 30.0), ["base-b"]),
    scene_object("row", (3.0, 1.0, 2.0), (18.0, 8.0, 4.0), ["row-b"]),
    scene_object("tool", (8.0, 1.0, 0.0), (12.0, 6.0, 2.0), ["cut-b"],
                 physical=False),
]


class AxialGroupOffsetTest(unittest.TestCase):
    def test_symmetric_group_measures_zero(self):
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"base": ["base-b"]},
        )
        self.assertEqual(0.0, findings[0].offset)
        self.assertEqual(("slab",), findings[0].object_ids)

    def test_offset_group_measures_exact_signed_offset(self):
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"row": ["row-b"]},
        )
        self.assertEqual(0.5, findings[0].offset)
        self.assertEqual(10.5, findings[0].center)

    def test_non_physical_objects_are_excluded_by_default(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(
                SCENE, axis_value=10.0, axis_index=0,
                groups={"cut": ["cut-b"]},
            )
        findings = axial_group_offsets(
            SCENE, axis_value=10.0, axis_index=0,
            groups={"cut": ["cut-b"]}, physical_only=False,
        )
        self.assertEqual(0.0, findings[0].offset)

    def test_empty_group_fails_closed(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(
                SCENE, axis_value=10.0, axis_index=0,
                groups={"ghost": ["missing-b"]},
            )

    def test_group_aggregates_multiple_members(self):
        scene = SCENE + [
            scene_object("row-2", (2.0, 1.0, 26.0), (17.0, 8.0, 28.0),
                         ["row-b"]),
        ]
        findings = axial_group_offsets(
            scene, axis_value=10.0, axis_index=0,
            groups={"row": ["row-b"]},
        )
        self.assertEqual(2.0, findings[0].minimum)
        self.assertEqual(18.0, findings[0].maximum)
        self.assertEqual(0.0, findings[0].offset)

    def test_axis_index_selects_dimension(self):
        findings = axial_group_offsets(
            SCENE, axis_value=15.0, axis_index=2,
            groups={"base": ["base-b"]},
        )
        self.assertEqual(0.0, findings[0].offset)

    def test_invalid_requests_are_typed(self):
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=3,
                                groups={"base": ["base-b"]})
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=0,
                                groups={})
        with self.assertRaises(SymmetryMeasurementError):
            axial_group_offsets(SCENE, axis_value=10.0, axis_index=0,
                                groups={"base": []})


def _stage0_objects(rotunda_shift=0.0):
    def solid(object_id, minimum, maximum, binding_id):
        return scene_object(object_id, minimum, maximum, [binding_id])

    return [
        solid("portico-mass-object", (10.0, 62.0, 0.0), (38.0, 74.0, 15.0),
              "portico-binding"),
        solid("plinth-object",
              (0.5 + rotunda_shift, 60.0, 0.0),
              (47.5 + rotunda_shift, 62.0, 62.0),
              "rotunda-binding"),
        solid("dome-shell-object", (1.0, 84.0, 15.0), (47.0, 107.0, 61.0),
              "dome-binding"),
    ]


def _fake_stage_env(objects):
    scene = SimpleNamespace(
        to_dict=lambda: {"schema": "FakeScene@1", "objects": objects},
        workspace_id="gate-drill",
        scene_digest="ab" * 32,
    )
    receipt = SimpleNamespace(
        to_dict=lambda: {"schema": "FakeReceipt@1"},
        receipt_digest="cd" * 32,
        issues=(),
    )
    view = SimpleNamespace(
        to_dict=lambda: {"schema": "FakeView@1"},
        artifact=SimpleNamespace(artifact_id="artifact-1"),
        occupied_cells=[],
    )
    return SimpleNamespace(scene=scene, receipt=receipt), view


class StageGateDrillTest(unittest.TestCase):
    """M075: the criterion sits on the acceptance path, fail-closed."""

    def _run_gate(self, objects, tmp):
        from tools.projects.pantheon import monument_support as monument
        from archflow.project import (
            FilesystemProjectRepository,
            bootstrap_raw_request_project,
        )

        realization, view = _fake_stage_env(objects)
        root = Path(tmp) / "gate-drill"
        bootstrapped = bootstrap_raw_request_project(
            root,
            project_id="gate-drill",
            prompt="stage-gate fail-closed drill",
            run_id="drill-001",
            synthetic_test=True,
        )
        repository = FilesystemProjectRepository.open(root)
        program = SimpleNamespace(program_digest="ef" * 32)
        state = SimpleNamespace(base=None, state_digest="12" * 32)
        patches = (
            mock.patch.object(
                monument, "realize_geometry",
                lambda program, workspace_id: realization,
            ),
            mock.patch.object(
                monument, "derive_voxel_view",
                lambda scene, receipt, policy: view,
            ),
            mock.patch.object(
                monument, "CandidateSubmission",
                lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            mock.patch.object(
                monument, "CandidateDelta",
                lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            mock.patch.object(
                monument, "CanonicalState",
                lambda **kwargs: SimpleNamespace(**kwargs),
            ),
            mock.patch.object(
                monument, "validate_submission",
                lambda state, submission, validators: SimpleNamespace(
                    passed=True
                ),
            ),
            mock.patch.object(
                monument, "_validation_payload",
                lambda validation: {"schema": "FakeValidation@1"},
            ),
        )
        for patch in patches:
            patch.start()
        try:
            return (
                monument,
                repository,
                bootstrapped.run,
                lambda: monument._persist_stage(
                    repository,
                    run=bootstrapped.run,
                    state=state,
                    program=program,
                    stage=0,
                ),
                root,
            )
        finally:
            self.addCleanup(lambda: [patch.stop() for patch in patches])

    def _archives(self, root):
        pattern = str(
            root / "runs" / "drill-001" / "records"
            / "p065-stage-0-sandbox-archive-*.json"
        )
        return [
            json.loads(Path(item).read_text(encoding="utf-8"))
            for item in glob.glob(pattern)
        ]

    def test_off_axis_stage_is_rejected_and_never_accepted(self):
        from tools.projects.pantheon import monument_support as monument

        with tempfile.TemporaryDirectory() as tmp:
            _, repository, run, persist, root = self._run_gate(
                _stage0_objects(rotunda_shift=0.5), tmp
            )
            with self.assertRaises(monument.StageGateError):
                persist()
            archives = self._archives(root)
            self.assertEqual(1, len(archives))
            self.assertEqual("rejected", archives[0]["disposition"])
            gates = glob.glob(
                str(root / "runs" / "drill-001" / "records"
                    / "p065-stage-0-symmetry-gate-*.json")
            )
            gate = json.loads(Path(gates[0]).read_text(encoding="utf-8"))
            self.assertEqual("fail", gate["status"])
            self.assertAlmostEqual(0.5, gate["max_abs_offset"])

    def test_symmetric_stage_accepts_and_cites_the_gate_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, repository, run, persist, root = self._run_gate(
                _stage0_objects(rotunda_shift=0.0), tmp
            )
            persist()
            archives = self._archives(root)
            self.assertEqual(1, len(archives))
            self.assertEqual("accepted", archives[0]["disposition"])
            self.assertTrue(
                any(
                    "symmetry-gate" in ref
                    for ref in archives[0]["evidence_refs"]
                )
            )


class DerivedRowOriginTest(unittest.TestCase):
    def test_monument_rows_center_on_the_axis(self):
        from tools.projects.pantheon.monument_support import (
            CENTER_X,
            _axial_row_origin,
        )
        for count, step, width in ((8, 3.5, 2), (8, 3.5, 3), (9, 3.3, 1.4)):
            origin = _axial_row_origin(count, step, width)
            span_min = origin
            span_max = origin + (count - 1) * step + width
            self.assertAlmostEqual(CENTER_X, (span_min + span_max) / 2)


if __name__ == "__main__":
    unittest.main()
