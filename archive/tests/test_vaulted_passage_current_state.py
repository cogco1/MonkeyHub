"""Current-state vaulted-passage morphology/continuity regressions."""

from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import rhino3dm

from archflow.adapters.three_dm_inspector import ThreeDmInspection, inspect_three_dm
from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.vaulted_passage_current_state import (
    VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID,
    VaultedPassageCurrentStateContract,
    VaultedPassageFacadeRecord,
    VaultedPassageFacadeState,
    VaultedPassageMorphologyCriteria,
    VaultedPassageMorphologyRole,
    VaultedPassagePathBinding,
    VaultedPassageRoleBinding,
    VaultedPassageStateBasis,
    check_vaulted_passage_current_state,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
FACADE_REF = "facade:south-west"


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="vaulted-passage-current-fixture",
            run_id="run-001",
            base=ProjectVersionRef(
                "vaulted-passage-current-fixture",
                0,
                SHA_A,
            ),
        ),
        branch_id="candidate",
        epoch=2,
    )


def _attributes(name: str) -> rhino3dm.ObjectAttributes:
    attributes = rhino3dm.ObjectAttributes()
    attributes.Name = name
    attributes.SetUserString("archflow:object_ref", f"cad-object:{name}")
    attributes.SetUserString(
        "archflow:operation_ref",
        f"cad-operation:{name}",
    )
    return attributes


def _box_mesh(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> rhino3dm.Mesh:
    result = rhino3dm.Mesh()
    low = minimum
    high = maximum
    for x, y, z in (
        (low[0], low[1], low[2]),
        (high[0], low[1], low[2]),
        (high[0], high[1], low[2]),
        (low[0], high[1], low[2]),
        (low[0], low[1], high[2]),
        (high[0], low[1], high[2]),
        (high[0], high[1], high[2]),
        (low[0], high[1], high[2]),
    ):
        result.Vertices.Add(x, y, z)
    for face in (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ):
        result.Faces.AddFace(*face)
    return result


def _continuous_curved_vault() -> rhino3dm.NurbsSurface:
    return rhino3dm.Sphere(
        rhino3dm.Point3d(3.0, 2.0, 2.5),
        2.0,
    ).ToNurbsSurface()


def _faceted_planar_vault() -> rhino3dm.Mesh:
    """One joined twelve-panel shell: continuous-looking but all planar."""

    mesh = rhino3dm.Mesh()
    panel_count = 12
    for y in (0.0, 4.0):
        for index in range(panel_count + 1):
            angle = math.pi * index / panel_count
            mesh.Vertices.Add(
                3.0 + 2.0 * math.cos(angle),
                y,
                1.0 + 3.0 * math.sin(angle),
            )
    offset = panel_count + 1
    for index in range(panel_count):
        mesh.Faces.AddFace(
            index,
            index + 1,
            offset + index + 1,
            offset + index,
        )
    return mesh


@lru_cache(maxsize=None)
def inspection_fixture(
    *,
    faceted: bool = False,
    include_path: bool = True,
    path_end_y: float = 4.0,
    host_min_y: float = 3.5,
    host_max_y: float = 4.5,
    host_min_x: float = 1.0,
    host_max_x: float = 5.0,
    include_infill: bool = False,
) -> ThreeDmInspection:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "current-vaulted-passage.3dm"
        model = rhino3dm.File3dm()
        model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
        layer = rhino3dm.Layer()
        layer.Name = "Current Vaulted Passage"
        layer_index = model.Layers.Add(layer)

        portal_attributes = _attributes("exterior-portal")
        portal_attributes.LayerIndex = layer_index
        model.Objects.AddMesh(
            _box_mesh((1.0, -0.5, 0.5), (5.0, 0.5, 4.5)),
            portal_attributes,
        )

        vault_attributes = _attributes("passage-vault")
        vault_attributes.LayerIndex = layer_index
        if faceted:
            model.Objects.AddMesh(_faceted_planar_vault(), vault_attributes)
        else:
            model.Objects.AddSurface(_continuous_curved_vault(), vault_attributes)

        host_attributes = _attributes("host-opening")
        host_attributes.LayerIndex = layer_index
        model.Objects.AddMesh(
            _box_mesh(
                (host_min_x, host_min_y, 0.5),
                (host_max_x, host_max_y, 4.5),
            ),
            host_attributes,
        )

        if include_infill:
            infill_attributes = _attributes("historical-infill")
            infill_attributes.LayerIndex = layer_index
            model.Objects.AddMesh(
                _box_mesh((1.0, -0.05, 0.5), (5.0, 0.15, 4.5)),
                infill_attributes,
            )

        if include_path:
            path_attributes = _attributes("full-passage-path")
            path_attributes.LayerIndex = layer_index
            model.Objects.AddCurve(
                rhino3dm.LineCurve(
                    rhino3dm.Point3d(3.0, 0.0, 2.5),
                    rhino3dm.Point3d(3.0, path_end_y, 2.5),
                ),
                path_attributes,
            )

        if not model.Write(str(source), 8):
            raise RuntimeError("failed to write current vaulted-passage fixture")
        return inspect_three_dm(source)


def role_binding(role: VaultedPassageMorphologyRole) -> VaultedPassageRoleBinding:
    name = {
        VaultedPassageMorphologyRole.EXTERIOR_PORTAL: "exterior-portal",
        VaultedPassageMorphologyRole.PASSAGE_VAULT: "passage-vault",
        VaultedPassageMorphologyRole.HOST_OPENING: "host-opening",
        VaultedPassageMorphologyRole.HISTORICAL_INFILL: "historical-infill",
    }[role]
    return VaultedPassageRoleBinding(
        role=role,
        component_ref=f"component:{name}",
        readback_object_ref=f"cad-object:{name}",
        readback_operation_ref=f"cad-operation:{name}",
    )


def path_binding() -> VaultedPassagePathBinding:
    return VaultedPassagePathBinding(
        component_ref="component:full-passage-path",
        readback_object_ref="cad-object:full-passage-path",
        readback_operation_ref="cad-operation:full-passage-path",
    )


def criteria() -> VaultedPassageMorphologyCriteria:
    return VaultedPassageMorphologyCriteria(
        criteria_ref="criteria:vaulted-passage-current",
        interface_tolerance=0.01,
        minimum_section_overlap_ratio=0.95,
        minimum_host_cut_depth=0.2,
        maximum_path_sample_gap=0.5,
        minimum_path_samples=9,
        evidence_refs=("evidence:measured-passage-section",),
        adoption_refs=("adoption:passage-morphology-criteria",),
    )


def facade_record(
    state: VaultedPassageFacadeState,
    *,
    facade_ref: str = FACADE_REF,
    state_basis: VaultedPassageStateBasis = (
        VaultedPassageStateBasis.DIRECT_EVIDENCE
    ),
    propagated_from_facade_ref: str | None = None,
    propagation_evidence_refs: tuple[str, ...] = (),
) -> VaultedPassageFacadeRecord:
    if state is VaultedPassageFacadeState.PARKED:
        return VaultedPassageFacadeRecord(
            facade_ref=facade_ref,
            state=state,
            state_basis=VaultedPassageStateBasis.UNRESOLVED,
        )
    roles = list(
        (
            VaultedPassageMorphologyRole.EXTERIOR_PORTAL,
            VaultedPassageMorphologyRole.PASSAGE_VAULT,
            VaultedPassageMorphologyRole.HOST_OPENING,
        )
    )
    if state is VaultedPassageFacadeState.CLOSED:
        roles.append(VaultedPassageMorphologyRole.HISTORICAL_INFILL)
    return VaultedPassageFacadeRecord(
        facade_ref=facade_ref,
        state=state,
        state_basis=state_basis,
        state_evidence_refs=(f"evidence:{facade_ref}-current-state",),
        state_adoption_refs=(f"adoption:{facade_ref}-current-state",),
        propagated_from_facade_ref=propagated_from_facade_ref,
        propagation_evidence_refs=propagation_evidence_refs,
        role_bindings=tuple(role_binding(role) for role in roles),
        path_binding=(
            path_binding()
            if state is VaultedPassageFacadeState.OPEN
            else None
        ),
    )


def contract(
    inspection: ThreeDmInspection,
    *,
    state: VaultedPassageFacadeState = VaultedPassageFacadeState.OPEN,
    facades: tuple[VaultedPassageFacadeRecord, ...] | None = None,
    expected_facade_refs: tuple[str, ...] = (FACADE_REF,),
) -> VaultedPassageCurrentStateContract:
    return VaultedPassageCurrentStateContract(
        contract_id="current-passage-001",
        branch=branch(),
        scope_digest=SHA_B,
        stage_subject_digest=SHA_C,
        inspection_artifact_ref="artifact:current-vaulted-passage-3dm",
        inspection_model_sha256=inspection.file_sha256,
        inspection_digest=canonical_digest(inspection.to_dict()),
        expected_facade_refs=expected_facade_refs,
        facades=(facade_record(state),) if facades is None else facades,
        criteria=criteria(),
    )


class VaultedPassageCurrentStateTests(unittest.TestCase):
    def test_open_current_passage_passes_and_round_trips(self) -> None:
        inspection = inspection_fixture()
        value = contract(inspection)

        receipt = check_vaulted_passage_current_state(
            value,
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(
            receipt.checker_id,
            VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID,
        )
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertEqual(
            VaultedPassageCurrentStateContract.from_dict(value.to_dict()),
            value,
        )
        self.assertEqual(CheckReceiptEnvelope.from_dict(receipt.to_dict()), receipt)

    def test_faceted_planar_wedge_shell_cannot_claim_continuous_vault(self) -> None:
        inspection = inspection_fixture(faceted=True)

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-faceted-planar-shell",
            {item.code for item in receipt.findings},
        )

    def test_short_center_ray_is_not_a_full_portal_to_host_path(self) -> None:
        inspection = inspection_fixture(path_end_y=1.0)

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=inspection,
        )

        codes = {item.code for item in receipt.findings}
        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-full-path-endpoints-disconnected",
            codes,
        )
        self.assertIn("vaulted-passage-blind-end", codes)

    def test_host_cut_must_have_adopted_through_depth(self) -> None:
        inspection = inspection_fixture(host_min_y=3.95, host_max_y=4.05)

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-host-cut-insufficient",
            {item.code for item in receipt.findings},
        )

    def test_host_section_must_match_vault_section(self) -> None:
        inspection = inspection_fixture(host_min_x=2.9, host_max_x=3.1)

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-section-mismatch",
            {item.code for item in receipt.findings},
        )

    def test_closed_facade_requires_inspected_historical_infill(self) -> None:
        inspection = inspection_fixture(include_path=False, include_infill=True)

        receipt = check_vaulted_passage_current_state(
            contract(inspection, state=VaultedPassageFacadeState.CLOSED),
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.PASS)

    def test_parked_facade_is_explicit_unknown_not_a_pass(self) -> None:
        inspection = inspection_fixture(include_path=False)

        receipt = check_vaulted_passage_current_state(
            contract(inspection, state=VaultedPassageFacadeState.PARKED),
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "vaulted-passage-facade-state-parked",
            {item.code for item in receipt.findings},
        )

    def test_facade_cannot_be_omitted_from_current_state_denominator(self) -> None:
        inspection = inspection_fixture()
        value = contract(
            inspection,
            facades=(),
            expected_facade_refs=(FACADE_REF,),
        )

        receipt = check_vaulted_passage_current_state(
            value,
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-facade-state-missing",
            {item.code for item in receipt.findings},
        )

    def test_symmetry_propagation_without_evidence_is_rejected(self) -> None:
        inspection = inspection_fixture()
        direct = facade_record(
            VaultedPassageFacadeState.OPEN,
            facade_ref="facade:south-west",
        )
        propagated = facade_record(
            VaultedPassageFacadeState.OPEN,
            facade_ref="facade:north-east",
            state_basis=VaultedPassageStateBasis.AUTHORIZED_PROPAGATION,
            propagated_from_facade_ref="facade:south-west",
            propagation_evidence_refs=(),
        )
        value = contract(
            inspection,
            facades=(direct, propagated),
            expected_facade_refs=("facade:north-east", "facade:south-west"),
        )

        receipt = check_vaulted_passage_current_state(
            value,
            inspection=inspection,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-unsupported-symmetry-propagation",
            {item.code for item in receipt.findings},
        )

    def test_agent_contract_without_controller_inspection_cannot_pass(self) -> None:
        inspection = inspection_fixture()

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=None,
        )

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "vaulted-passage-controller-inspection-missing",
            {item.code for item in receipt.findings},
        )

    def test_handwritten_geometry_claim_cannot_replace_inspector_analysis(self) -> None:
        inspection = inspection_fixture()
        stripped = replace(inspection, object_geometry_analysis=())

        receipt = check_vaulted_passage_current_state(
            contract(inspection),
            inspection=stripped,
        )

        codes = {item.code for item in receipt.findings}
        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn("vaulted-passage-inspection-digest-contradiction", codes)
        self.assertIn("vaulted-passage-inspected-geometry-contradiction", codes)


if __name__ == "__main__":
    unittest.main()
