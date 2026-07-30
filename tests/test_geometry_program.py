from __future__ import annotations

import unittest

from archflow.project import ProjectVersionRef
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
    GeometryProgramError,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    SemanticBinding,
)


EVIDENCE = "evidence:geometry-test"


def _parameter(
    name: str = "size",
    value: float = 1.0,
) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.NUMBER,
        value=value,
        unit=LengthUnit.METER,
    )


def _members(kind: AssemblyKind) -> tuple[AssemblyMember, ...]:
    roles = {
        AssemblyRole.HOST_CUT: ("cut-result",),
        AssemblyRole.FRAME: ("frame",),
        AssemblyRole.HARDWARE: ("hardware",),
        AssemblyRole.CLEARANCE: ("clearance",),
    }
    if kind is AssemblyKind.DOOR:
        roles[AssemblyRole.LEAF] = ("leaf",)
    else:
        roles[AssemblyRole.GLAZING] = ("glazing",)
    return tuple(
        AssemblyMember(role=role, object_ids=object_ids)
        for role, object_ids in sorted(
            roles.items(),
            key=lambda item: item[0].value,
        )
    )


class GeometryProgramContractTests(unittest.TestCase):
    def test_parameters_are_typed_canonical_and_deterministic(self) -> None:
        parameter = GeometryParameter.create(
            name="control-points",
            kind=GeometryParameterKind.POINTS3,
            value=[[0, 0, 0], [1, 2, 3]],
            unit=LengthUnit.METER,
        )
        self.assertEqual(
            parameter.value_json,
            "[[0.0,0.0,0.0],[1.0,2.0,3.0]]",
        )
        self.assertEqual(
            parameter.to_dict()["kind"],
            GeometryParameterKind.POINTS3.value,
        )
        with self.assertRaisesRegex(
            GeometryProgramError,
            "3-vector",
        ):
            GeometryParameter.create(
                name="bad-point",
                kind=GeometryParameterKind.VECTOR3,
                value=[0, 1],
            )

    def test_asset_requires_content_identity_provenance_and_stable_uri(
        self,
    ) -> None:
        asset = AssetReference(
            asset_id="carved-profile",
            uri="project://demo/assets/carved-profile",
            media_type="model/example",
            sha256="a" * 64,
            native_unit=LengthUnit.MILLIMETER,
            sockets=("axis",),
            provenance_refs=(EVIDENCE,),
        )
        self.assertEqual(asset.to_dict()["sha256"], "a" * 64)
        with self.assertRaisesRegex(
            GeometryProgramError,
            "scheme-qualified",
        ):
            AssetReference(
                asset_id="machine-path",
                uri="D:\\assets\\detail.bin",
                media_type="model/example",
                sha256="a" * 64,
                native_unit=LengthUnit.METER,
                sockets=("axis",),
                provenance_refs=(EVIDENCE,),
            )

    def test_hosted_assemblies_require_functional_members(self) -> None:
        door = HostedAssembly(
            assembly_id="entry",
            kind=AssemblyKind.DOOR,
            host_object_id="wall",
            host_socket_id="opening-axis",
            members=_members(AssemblyKind.DOOR),
            interface_refs=("interface:room-to-corridor",),
            semantic_binding_ids=("entry-binding",),
            maturity=DetailMaturity.FUNCTIONAL,
        )
        self.assertEqual(door.objects_for(AssemblyRole.LEAF), ("leaf",))
        window = HostedAssembly(
            assembly_id="window",
            kind=AssemblyKind.WINDOW,
            host_object_id="wall",
            host_socket_id="opening-axis",
            members=_members(AssemblyKind.WINDOW),
            interface_refs=("interface:inside-to-outside",),
            semantic_binding_ids=("window-binding",),
            maturity=DetailMaturity.FUNCTIONAL,
        )
        self.assertEqual(
            window.objects_for(AssemblyRole.GLAZING),
            ("glazing",),
        )
        missing_hardware = tuple(
            item
            for item in _members(AssemblyKind.WINDOW)
            if item.role is not AssemblyRole.HARDWARE
        )
        with self.assertRaisesRegex(
            GeometryProgramError,
            "hardware",
        ):
            HostedAssembly(
                assembly_id="broken-window",
                kind=AssemblyKind.WINDOW,
                host_object_id="wall",
                host_socket_id="opening-axis",
                members=missing_hardware,
                interface_refs=("interface:inside-to-outside",),
                semantic_binding_ids=("window-binding",),
                maturity=DetailMaturity.FUNCTIONAL,
            )

    def test_program_refuses_untethered_or_nondeterministic_operations(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            GeometryProgramError,
            "deterministic names",
        ):
            GeometryOperation(
                op_id="solid",
                kind=GeometryOperationKind.SOLID,
                output_object_ids=("solid",),
                input_object_ids=(),
                frame_id="world",
                parameters=(_parameter("z"), _parameter("a")),
                semantic_binding_ids=("binding",),
            )

        frame = CoordinateFrame(
            frame_id="world",
            parent_frame_id=None,
            transform_from_parent=AffineTransform.identity(),
            source_refs=(EVIDENCE,),
        )
        binding = SemanticBinding(
            binding_id="binding",
            object_ids=("solid",),
            candidate_value_ids=("dimension",),
            commitment_refs=(),
            evidence_refs=(EVIDENCE,),
        )
        operation = GeometryOperation(
            op_id="solid",
            kind=GeometryOperationKind.SOLID,
            output_object_ids=("solid",),
            input_object_ids=(),
            frame_id="world",
            parameters=(_parameter(),),
            semantic_binding_ids=("binding",),
        )
        proposal = GeometryProgramProposal(
            proposal_id="proposal",
            project_id="demo",
            run_id="run",
            base=ProjectVersionRef("demo", 1, "b" * 64),
            candidate_program_digest="c" * 64,
            predecessor_program_digest=None,
            length_unit=LengthUnit.METER,
            tolerance=GeometryTolerance(0.001, 0.001),
            frames=(frame,),
            assets=(),
            semantic_bindings=(binding,),
            operations=(operation,),
            assemblies=(),
        )
        first = proposal.proposal_digest
        second = proposal.proposal_digest
        self.assertEqual(first, second)
        self.assertFalse(proposal.to_dict()["canonical_write_authority"])


if __name__ == "__main__":
    unittest.main()
