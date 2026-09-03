from __future__ import annotations

from dataclasses import replace
import unittest

from archflow.compilers.geometry import compile_geometry_program
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    AssetReference,
    CoordinateFrame,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    SemanticBinding,
)
from archive.archflow.realization.sandbox import DerivedVoxelView, HybridScene, RealizationStatus, SandboxArchiveDisposition, SandboxArchiveRecord, SandboxAssetPayload, SandboxRealizationError, SandboxRealizationReceipt, VoxelizationPolicy, derive_voxel_view, realize_geometry
from tests.test_geometry_compiler import (
    COMMITMENT,
    EVIDENCE,
    _state,
)


def _vector(name: str, value: list[float]) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.VECTOR3,
        value=value,
        unit=LengthUnit.METER,
    )


def _points(value: list[list[float]]) -> GeometryParameter:
    return GeometryParameter.create(
        name="points",
        kind=GeometryParameterKind.POINTS3,
        value=value,
        unit=LengthUnit.METER,
    )


def _integer(name: str, value: int) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.INTEGER,
        value=value,
    )


def _solid(op_id: str, output: str, origin, size) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.SOLID,
        output_object_ids=(output,),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            _vector("origin", origin),
            _vector("size", size),
        ),
        semantic_binding_ids=("room-binding",),
    )


def _curve(op_id: str, output: str, points) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.CURVE,
        output_object_ids=(output,),
        input_object_ids=(),
        frame_id="world",
        parameters=(_points(points),),
        semantic_binding_ids=("room-binding",),
    )


def _boolean(
    op_id: str,
    output: str,
    kind: GeometryOperationKind,
    inputs: tuple[str, ...],
    *,
    base_id: str | None = None,
) -> GeometryOperation:
    ordered = tuple(sorted(inputs))
    parameters = ()
    if kind is GeometryOperationKind.BOOLEAN_DIFFERENCE:
        assert base_id is not None
        parameters = (_integer("base_index", ordered.index(base_id)),)
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(output,),
        input_object_ids=ordered,
        frame_id="world",
        parameters=parameters,
        semantic_binding_ids=("room-binding",),
    )


def _asset_payload() -> SandboxAssetPayload:
    return SandboxAssetPayload(
        asset_id="detail-asset",
        vertices=(
            (0.0, 0.0, 0.0),
            (0.5, 0.0, 0.0),
            (0.5, 0.5, 0.0),
            (0.0, 0.5, 0.0),
            (0.0, 0.0, 0.5),
            (0.5, 0.0, 0.5),
            (0.5, 0.5, 0.5),
            (0.0, 0.5, 0.5),
        ),
        faces=(
            (0, 1, 2, 3),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ),
    )


def compiled_room(*, include_asset: bool = False):
    state = _state()
    operations = [
        _solid("clearance", "clearance", [0, 1, 2], [2, 2, 1]),
        _curve("frame", "frame", [[0, 1, 2], [0, 3, 2]]),
        _solid("floor", "floor", [0, 0, 0], [5, 1, 5]),
        _curve("hardware", "hardware", [[0, 2, 2], [0, 2, 2.1]]),
        _solid("inner", "inner", [1, 1, 1], [3, 2, 3]),
        _curve("leaf", "leaf", [[0, 1, 2], [0, 3, 2]]),
        _solid("opening-tool", "opening-tool", [0, 1, 2], [1, 2, 1]),
        _solid("outer", "outer", [0, 1, 0], [5, 3, 5]),
        _boolean(
            "shell",
            "shell",
            GeometryOperationKind.BOOLEAN_DIFFERENCE,
            ("inner", "outer"),
            base_id="outer",
        ),
        _boolean(
            "opening",
            "opening",
            GeometryOperationKind.BOOLEAN_INTERSECTION,
            ("opening-tool", "shell"),
        ),
        _boolean(
            "walls",
            "walls",
            GeometryOperationKind.BOOLEAN_DIFFERENCE,
            ("opening", "shell"),
            base_id="shell",
        ),
    ]
    assets = ()
    payloads = ()
    available_assets = {}
    if include_asset:
        payload = _asset_payload()
        assets = (
            AssetReference(
                asset_id=payload.asset_id,
                uri="project://demo/assets/detail-asset",
                media_type="model/example",
                sha256=payload.payload_digest,
                native_unit=LengthUnit.METER,
                sockets=("origin",),
                provenance_refs=(EVIDENCE,),
            ),
        )
        payloads = (payload,)
        available_assets = {payload.asset_id: payload.payload_digest}
        operations.append(
            GeometryOperation(
                op_id="ornament",
                kind=GeometryOperationKind.ASSET_INSTANCE,
                output_object_ids=("ornament",),
                input_object_ids=(),
                frame_id="world",
                parameters=(),
                semantic_binding_ids=("room-binding",),
                asset_id=payload.asset_id,
                asset_socket_id="origin",
                asset_scale=(1.0, 1.0, 1.0),
            )
        )
    operations_tuple = tuple(sorted(operations, key=lambda item: item.op_id))
    object_ids = tuple(
        sorted(
            object_id
            for operation in operations_tuple
            for object_id in operation.output_object_ids
        )
    )
    proposal = GeometryProgramProposal(
        proposal_id="sandbox-room",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=(EVIDENCE,),
            ),
        ),
        assets=assets,
        semantic_bindings=(
            SemanticBinding(
                binding_id="room-binding",
                component_id="building",
                object_ids=object_ids,
                commitment_refs=(COMMITMENT,),
                evidence_refs=(EVIDENCE,),
            ),
        ),
        operations=operations_tuple,
        assemblies=(
            HostedAssembly(
                assembly_id="entry",
                kind=AssemblyKind.DOOR,
                host_object_id="shell",
                host_socket_id="entry-axis",
                members=(
                    AssemblyMember(AssemblyRole.CLEARANCE, ("clearance",)),
                    AssemblyMember(AssemblyRole.FRAME, ("frame",)),
                    AssemblyMember(AssemblyRole.HARDWARE, ("hardware",)),
                    AssemblyMember(AssemblyRole.HOST_CUT, ("opening",)),
                    AssemblyMember(AssemblyRole.LEAF, ("leaf",)),
                ),
                interface_refs=("interface:outside-to-room",),
                semantic_binding_ids=("room-binding",),
                maturity=DetailMaturity.FUNCTIONAL,
            ),
        ),
    )
    compiled = compile_geometry_program(
        state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
        available_asset_digests=available_assets,
    )
    if compiled.program is None:
        raise AssertionError(compiled.receipt.issues)
    return state, compiled.program, payloads


class SandboxRealizationTests(unittest.TestCase):
    def test_thin_aabb_uses_positive_cell_overlap_not_center_only(self) -> None:
        from archive.archflow.realization.sandbox import (
            AxisAlignedBounds,
            SceneObject,
            SceneRepresentation,
            _contains,
            _intersects_cell,
        )
        from archflow.contracts.canonical import canonical_json as _canonical_json

        bounds = AxisAlignedBounds((0.0, 0.0, 0.0), (2.0, 0.2, 2.0))
        slab = SceneObject(
            object_id="thin-slab",
            producer_op_id="thin-slab-op",
            source_object_digest="a" * 64,
            representation=SceneRepresentation.ANALYTIC,
            geometry_json=_canonical_json(
                {"kind": "aabb", "bounds": bounds.to_dict()}
            ),
            bounds=bounds,
            semantic_binding_ids=("binding-slab",),
            physical=True,
        )
        objects = {slab.object_id: slab}

        self.assertFalse(_contains(slab.object_id, (0.5, 0.5, 0.5), objects))
        self.assertTrue(
            _intersects_cell(slab.object_id, (0, 0, 0), 1.0, objects)
        )
        self.assertFalse(
            _intersects_cell(slab.object_id, (2, 0, 0), 1.0, objects)
        )

    def test_mesh_occupancy_samples_actual_mesh_not_bounding_box(
        self,
    ) -> None:
        from archive.archflow.realization.sandbox import (
            AxisAlignedBounds,
            SceneObject,
            SceneRepresentation,
            _contains,
        )
        from archflow.contracts.canonical import canonical_json as _canonical_json

        geometry = {
            "kind": "mesh",
            "requested_asset_id": "asset-detail",
            "resolved_asset_id": "asset-detail",
            "asset_payload_digest": "a" * 64,
            "socket_id": "origin",
            "vertices": [
                [0.0, 0.0, 0.0],
                [4.0, 0.0, 0.0],
                [0.0, 4.0, 0.0],
                [0.0, 0.0, 4.0],
            ],
            "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
        }
        item = SceneObject(
            object_id="detail-mesh",
            producer_op_id="asset-detail-op",
            source_object_digest="b" * 64,
            representation=SceneRepresentation.MESH,
            geometry_json=_canonical_json(geometry),
            bounds=AxisAlignedBounds(
                (0.0, 0.0, 0.0),
                (4.0, 4.0, 4.0),
            ),
            semantic_binding_ids=("binding-detail",),
            physical=True,
        )
        objects = {"detail-mesh": item}

        # Inside the tetrahedron: occupied.
        self.assertTrue(
            _contains("detail-mesh", (0.5, 0.5, 0.5), objects)
        )
        # Inside the bounding box but outside the tetrahedron: the old
        # AABB shortcut reported this cell as definitively occupied.
        self.assertFalse(
            _contains("detail-mesh", (3.5, 3.5, 3.5), objects)
        )
        # Outside the bounding box entirely.
        self.assertFalse(
            _contains("detail-mesh", (5.0, 5.0, 5.0), objects)
        )

    def test_scene_is_deterministic_and_reloads_without_platform(self) -> None:
        _, program, payloads = compiled_room()
        first = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        second = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )

        self.assertIs(first.receipt.status, RealizationStatus.REALIZED)
        self.assertIsNotNone(first.scene)
        assert first.scene is not None
        self.assertEqual(first.scene.scene_digest, second.scene.scene_digest)
        self.assertFalse(first.scene.object("leaf").physical)
        self.assertEqual(
            first.scene.object("leaf").producer_op_id,
            "leaf",
        )
        self.assertEqual(
            HybridScene.from_dict(first.scene.to_dict()),
            first.scene,
        )
        self.assertEqual(
            SandboxRealizationReceipt.from_dict(first.receipt.to_dict()),
            first.receipt,
        )
        self.assertFalse(first.scene.to_dict()["voxel_is_canonical"])
        self.assertFalse(
            first.receipt.to_dict()["canonical_write_authority"]
        )

    def test_voxel_view_is_exactly_bound_and_keeps_scene_canonical(self) -> None:
        _, program, payloads = compiled_room()
        result = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert result.scene is not None
        view = derive_voxel_view(
            result.scene,
            result.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        observation = view.to_observation()

        self.assertTrue(view.occupied_cells)
        self.assertTrue(view.walkable_cells)
        self.assertTrue(view.opening_cells)
        self.assertEqual(observation.source_artifact_sha256, view.view_digest)
        self.assertEqual(DerivedVoxelView.from_dict(view.to_dict()), view)
        self.assertEqual(view.scene_digest, result.scene.scene_digest)
        self.assertTrue(
            view.to_dict()["canonical_geometry_retained"]
        )
        with self.assertRaisesRegex(
            SandboxRealizationError,
            "exact realized scene",
        ):
            derive_voxel_view(
                result.scene,
                replace(result.receipt, scene_digest="f" * 64),
                policy=VoxelizationPolicy(default_resolution=1.0),
            )

    def test_mesh_detail_uses_local_resolution_without_replacing_mesh(
        self,
    ) -> None:
        _, program, payloads = compiled_room(include_asset=True)
        result = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert result.scene is not None
        ornament = result.scene.object("ornament")
        view = derive_voxel_view(
            result.scene,
            result.receipt,
            policy=VoxelizationPolicy(
                default_resolution=1.0,
                local_resolutions=(("ornament", 0.25),),
            ),
        )

        self.assertEqual(ornament.representation.value, "mesh")
        self.assertEqual(len(ornament.geometry["vertices"]), 8)
        self.assertEqual(
            view.local_detail_samples[0].resolution,
            0.25,
        )
        self.assertEqual(view.resolution, 1.0)
        self.assertIn(
            "mesh_validation_uses_bounded_sampling",
            view.loss_codes,
        )

    def test_grounded_scene_passes_the_support_hard_gate(self) -> None:
        from archive.archflow.compilers.voxel_program import compile_building_program
        from archive.archflow.validation.usability import (
            UsabilityGate,
            validate_usability,
        )

        _, program, payloads = compiled_room()
        result = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert result.scene is not None
        view = derive_voxel_view(
            result.scene,
            result.receipt,
            policy=VoxelizationPolicy(default_resolution=1.0),
        )
        observation = view.to_observation()

        self.assertEqual(observation.unsupported_cells, ())
        receipt = validate_usability(
            compile_building_program(
                {
                    "use": "sandbox-room",
                    "width_blocks": 5,
                    "depth_blocks": 5,
                    "required_spaces": ["room"],
                }
            ),
            observation,
            use_zones=(),
        )
        self.assertFalse(
            [
                finding
                for finding in receipt.findings
                if finding.gate is UsabilityGate.SUPPORT
            ]
        )

    def test_floating_component_triggers_the_support_hard_gate(self) -> None:
        from archive.archflow.adapters.voxel_observation import VoxelBounds
        from archive.archflow.realization.sandbox import _support_relations
        from archive.archflow.compilers.voxel_program import compile_building_program
        from archive.archflow.validation.usability import validate_usability

        grounded_slab = tuple(
            (x, 0, z) for x in range(2) for z in range(2)
        )
        floating_plate = tuple(
            (x, 2, z) for x in range(2) for z in range(2)
        )
        occupied = tuple(sorted((*grounded_slab, *floating_plate)))
        view = DerivedVoxelView(
            scene_digest="a" * 64,
            realization_receipt_digest="b" * 64,
            policy_digest="c" * 64,
            resolution=1.0,
            bounds=VoxelBounds((0, 0, 0), (1, 2, 1)),
            occupied_cells=occupied,
            walkable_cells=(),
            opening_cells=(),
            connected_regions=(),
            support_relations=_support_relations(set(occupied)),
            local_detail_samples=(),
            loss_codes=(),
            project_id="support-probe",
            version=0,
            workspace_id="support-workspace",
        )
        observation = view.to_observation()

        self.assertEqual(observation.unsupported_cells, floating_plate)
        receipt = validate_usability(
            compile_building_program(
                {
                    "use": "support-probe",
                    "width_blocks": 2,
                    "depth_blocks": 2,
                    "required_spaces": ["room"],
                }
            ),
            observation,
            use_zones=(),
        )
        self.assertIn(
            "usability.support.floating_component",
            [finding.code for finding in receipt.findings],
        )
        # The persisted record schema and digest are unchanged: the analysis
        # runs only at observation-derivation time.
        self.assertEqual(DerivedVoxelView.from_dict(view.to_dict()), view)

    def test_malformed_operation_returns_explicit_rejection(self) -> None:
        design_state, program, _ = compiled_room()
        unsupported = GeometryOperation(
            op_id="malformed-loft",
            kind=GeometryOperationKind.LOFT,
            output_object_ids=("unsupported-object",),
            input_object_ids=(),
            frame_id="world",
            parameters=(),
            semantic_binding_ids=("room-binding",),
        )
        binding = replace(
            program.proposal.semantic_bindings[0],
            object_ids=tuple(
                sorted(
                    (
                        *program.proposal.semantic_bindings[0].object_ids,
                        "unsupported-object",
                    )
                )
            ),
        )
        proposal = replace(
            program.proposal,
            proposal_id="malformed-proposal",
            semantic_bindings=(binding,),
            operations=tuple(
                sorted(
                    (*program.proposal.operations, unsupported),
                    key=lambda item: item.op_id,
                )
            ),
        )
        compiled = compile_geometry_program(
            design_state,
            proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        assert compiled.program is not None

        result = realize_geometry(
            compiled.program,
            workspace_id="sandbox-workspace",
        )
        self.assertIsNone(result.scene)
        self.assertIs(result.receipt.status, RealizationStatus.REJECTED)
        self.assertEqual(
            result.receipt.issues[0].code,
            "sandbox.operation_failed",
        )

    def test_downstream_dispositions_reload_without_granting_authority(
        self,
    ) -> None:
        _, program, payloads = compiled_room()
        realized = realize_geometry(
            program,
            workspace_id="sandbox-workspace",
            asset_payloads=payloads,
        )
        assert realized.scene is not None
        for disposition in SandboxArchiveDisposition:
            record = SandboxArchiveRecord(
                archive_id=f"archive-{disposition.value}",
                disposition=disposition,
                geometry_program_digest=program.program_digest,
                realization_receipt_digest=realized.receipt.receipt_digest,
                scene_digest=realized.scene.scene_digest,
                decision_receipt_digest="9" * 64,
                evidence_refs=(f"decision:{disposition.value}",),
            )
            self.assertEqual(
                SandboxArchiveRecord.from_dict(record.to_dict()),
                record,
            )
            self.assertFalse(record.to_dict()["decision_authority"])


if __name__ == "__main__":
    unittest.main()
