from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

import archflow.compilers as compiler_api
import archflow.compilers.geometry as canonical_geometry_compiler
import archflow.runtime.geometry_compiler as runtime_geometry_compiler
from archflow.compilers.geometry import (
    AssetSubstitutionReceipt,
    GeometryCompileStatus,
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state.developed_design import DevelopedDesignState
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
    ObjectRevisionPrecondition,
    SemanticBinding,
)


EVIDENCE = "evidence:geometry-compiler"
COMMITMENT = "commitment:maintain-egress"
from tests.test_design_development import _coordinated_state


def _state(*, width: float = 6.0) -> DevelopedDesignState:
    state = _coordinated_state()[3]
    if width == 6.0:
        return state
    proposal = state.selected_schematic.option.proposal
    components = tuple(
        replace(
            item,
            revision=item.revision + 1,
            intent=f"{item.intent} Width decision {width}.",
        )
        if item.component_id == "building"
        else item
        for item in proposal.components
    )
    option = replace(
        state.selected_schematic.option,
        proposal=replace(proposal, components=components),
    )
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            option=option,
        ),
    )


def _number(name: str, value: float) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.NUMBER,
        value=value,
        unit=LengthUnit.METER,
    )


def _operation(
    *,
    op_id: str,
    kind: GeometryOperationKind,
    output: str,
    inputs: tuple[str, ...] = (),
    parameter: GeometryParameter | None = None,
    asset_id: str | None = None,
    responds: tuple[str, ...] = (),
    responds_to_bindings: tuple[str, ...] = (),
) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(output,),
        input_object_ids=inputs,
        frame_id="world",
        parameters=(parameter,) if parameter is not None else (),
        semantic_binding_ids=("building-binding",),
        asset_id=asset_id,
        asset_socket_id="origin" if asset_id is not None else None,
        asset_scale=(1.0, 1.0, 1.0) if asset_id is not None else None,
        responds_to_object_ids=responds,
        responds_to_binding_ids=responds_to_bindings,
    )


def _proposal(
    state: DevelopedDesignState,
    *,
    wall_width: float = 6.0,
    predecessor: str | None = None,
    revisions: tuple[ObjectRevisionPrecondition, ...] = (),
    respond_to_dependencies: bool = False,
    assets: tuple[AssetReference, ...] = (),
    extra_operations: tuple[GeometryOperation, ...] = (),
) -> GeometryProgramProposal:
    dependency_response = (
        {
            "cut": ("wall",),
            "frame": ("cut-result",),
            "leaf": ("cut-result",),
            "hardware": ("cut-result",),
            "clearance": ("cut-result",),
        }
        if respond_to_dependencies
        else {}
    )
    operations = (
        _operation(
            op_id="clearance",
            kind=GeometryOperationKind.SOLID,
            output="clearance",
            inputs=("cut-result",),
            parameter=_number("depth", 1.2),
            responds=dependency_response.get("clearance", ()),
        ),
        _operation(
            op_id="cut",
            kind=GeometryOperationKind.BOOLEAN_DIFFERENCE,
            output="cut-result",
            inputs=("opening-tool", "wall"),
            responds=dependency_response.get("cut", ()),
        ),
        _operation(
            op_id="frame",
            kind=GeometryOperationKind.SWEEP,
            output="frame",
            inputs=("cut-result",),
            parameter=_number("thickness", 0.08),
            responds=dependency_response.get("frame", ()),
        ),
        _operation(
            op_id="hardware",
            kind=GeometryOperationKind.SOLID,
            output="hardware",
            inputs=("cut-result",),
            parameter=_number("placeholder-size", 0.05),
            responds=dependency_response.get("hardware", ()),
        ),
        _operation(
            op_id="leaf",
            kind=GeometryOperationKind.SOLID,
            output="leaf",
            inputs=("cut-result",),
            parameter=_number("thickness", 0.04),
            responds=dependency_response.get("leaf", ()),
        ),
        _operation(
            op_id="opening-tool",
            kind=GeometryOperationKind.SOLID,
            output="opening-tool",
            parameter=_number("width", 0.9),
        ),
        _operation(
            op_id="unrelated",
            kind=GeometryOperationKind.CURVE,
            output="unrelated-axis",
            parameter=_number("length", 2.0),
        ),
        _operation(
            op_id="wall",
            kind=GeometryOperationKind.SOLID,
            output="wall",
            parameter=_number("width", wall_width),
        ),
        *extra_operations,
    )
    object_ids = tuple(
        sorted(
            {
                object_id
                for operation in operations
                for object_id in operation.output_object_ids
            }
        )
    )
    binding = SemanticBinding(
        binding_id="building-binding",
        component_id="building",
        object_ids=object_ids,
        commitment_refs=(COMMITMENT,),
        evidence_refs=(EVIDENCE,),
    )
    assembly = HostedAssembly(
        assembly_id="entry-assembly",
        kind=AssemblyKind.DOOR,
        host_object_id="wall",
        host_socket_id="entry-axis",
        members=(
            AssemblyMember(AssemblyRole.CLEARANCE, ("clearance",)),
            AssemblyMember(AssemblyRole.FRAME, ("frame",)),
            AssemblyMember(AssemblyRole.HARDWARE, ("hardware",)),
            AssemblyMember(AssemblyRole.HOST_CUT, ("cut-result",)),
            AssemblyMember(AssemblyRole.LEAF, ("leaf",)),
        ),
        interface_refs=("interface:inside-to-outside",),
        semantic_binding_ids=("building-binding",),
        maturity=DetailMaturity.FUNCTIONAL,
    )
    return GeometryProgramProposal(
        proposal_id="geometry-proposal",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=predecessor,
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
        semantic_bindings=(binding,),
        operations=tuple(sorted(operations, key=lambda item: item.op_id)),
        assemblies=(assembly,),
        revisions=revisions,
    )


def _codes(result) -> set[GeometryIssueCode]:
    return {item.code for item in result.receipt.issues}


class GeometryCompilerTests(unittest.TestCase):
    def test_canonical_owner_freezes_payload_digest_and_facade_identity(
        self,
    ) -> None:
        state = _state()
        result = compile_geometry_program(
            state,
            _proposal(state),
            active_commitment_refs=(COMMITMENT,),
        )
        assert result.program is not None

        self.assertEqual(
            "39fc0581cc23010aee7ce3781d087e79d0115b844a0c7cc99f625b05dc704fe6",
            result.program.program_digest,
        )
        self.assertEqual(
            "f731496b9fbc2b455d1ec20566d6aafeb6f875e67906eb529305c1ca6dadc836",
            result.receipt.receipt_digest,
        )
        self.assertEqual(
            {
                "schema": "GeometryCompilationReceipt@1",
                "proposal_digest": (
                    "e9a07293fb9be4a326e1171a18cc3c3985e8254f2aabf461f3618967f460a309"
                ),
                "status": "compiled",
                "compiled_program_digest": (
                    "39fc0581cc23010aee7ce3781d087e79d0115b844a0c7cc99f625b05dc704fe6"
                ),
                "operation_order": [
                    "opening-tool",
                    "unrelated",
                    "wall",
                    "cut",
                    "clearance",
                    "frame",
                    "hardware",
                    "leaf",
                ],
                "issues": [],
                "asset_substitutions": [],
                "execution_authority": False,
                "hard_gate_authority": False,
                "canonical_write_authority": False,
            },
            result.receipt.to_dict(),
        )
        self.assertEqual(
            {
                "schema": "CompiledGeometryObject@1",
                "object_id": "clearance",
                "producer_op_id": "clearance",
                "object_digest": (
                    "a72e184cf3747fd89684b624e369e977c9291bd5617e2e981edc58266f6230c3"
                ),
            },
            result.program.objects[0].to_dict(),
        )
        self.assertEqual(
            canonical_geometry_compiler.__all__,
            runtime_geometry_compiler.__all__,
        )
        for symbol in canonical_geometry_compiler.__all__:
            self.assertIs(
                getattr(canonical_geometry_compiler, symbol),
                getattr(runtime_geometry_compiler, symbol),
                symbol,
            )
            self.assertIs(
                getattr(canonical_geometry_compiler, symbol),
                getattr(compiler_api, symbol),
                symbol,
            )

    def test_graph_compiles_in_dependency_order_with_stable_objects(self) -> None:
        state = _state()
        proposal = _proposal(state)
        result = compile_geometry_program(
            state,
            proposal,
            active_commitment_refs=(COMMITMENT,),
        )

        self.assertIs(result.receipt.status, GeometryCompileStatus.COMPILED)
        self.assertIsNotNone(result.program)
        assert result.program is not None
        order = result.program.operation_order
        self.assertLess(order.index("wall"), order.index("cut"))
        self.assertLess(order.index("cut"), order.index("frame"))
        self.assertEqual(
            result.program.program_digest,
            result.receipt.compiled_program_digest,
        )
        self.assertFalse(
            result.program.to_dict()["canonical_write_authority"]
        )

    def test_changed_host_invalidates_dependents_until_acknowledged(
        self,
    ) -> None:
        state = _state()
        initial = compile_geometry_program(
            state,
            _proposal(state),
            active_commitment_refs=(COMMITMENT,),
        )
        assert initial.program is not None

        unacknowledged = compile_geometry_program(
            state,
            _proposal(
                state,
                wall_width=7.0,
                predecessor=initial.program.program_digest,
            ),
            active_commitment_refs=(COMMITMENT,),
            prior_program=initial.program,
        )
        self.assertIs(
            unacknowledged.receipt.status,
            GeometryCompileStatus.REJECTED,
        )
        self.assertIn(
            GeometryIssueCode.UNACKNOWLEDGED_DEPENDENCY_CHANGE,
            _codes(unacknowledged),
        )
        self.assertIn(
            GeometryIssueCode.MISSING_REVISION_PRECONDITION,
            _codes(unacknowledged),
        )

        revision_ids = tuple(
            item.object_id
            for item in initial.program.objects
            if item.object_id not in {"opening-tool", "unrelated-axis"}
        )
        revisions = tuple(
            ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=initial.program.object_digest(object_id),
                reason_refs=("decision:increase-width",),
            )
            for object_id in sorted(revision_ids)
        )
        acknowledged_proposal = _proposal(
            state,
            wall_width=7.0,
            predecessor=initial.program.program_digest,
            revisions=revisions,
            respond_to_dependencies=True,
        )
        acknowledged = compile_geometry_program(
            state,
            acknowledged_proposal,
            active_commitment_refs=(COMMITMENT,),
            prior_program=initial.program,
        )
        self.assertIs(
            acknowledged.receipt.status,
            GeometryCompileStatus.COMPILED,
        )
        assert acknowledged.program is not None
        self.assertEqual(
            initial.program.object_digest("unrelated-axis"),
            acknowledged.program.object_digest("unrelated-axis"),
        )
        self.assertNotEqual(
            initial.program.object_digest("wall"),
            acknowledged.program.object_digest("wall"),
        )
        self.assertNotEqual(
            initial.program.object_digest("frame"),
            acknowledged.program.object_digest("frame"),
        )

    def test_changed_semantics_cannot_reuse_old_geometry_silently(self) -> None:
        initial_state = _state(width=6.0)
        initial = compile_geometry_program(
            initial_state,
            _proposal(initial_state),
            active_commitment_refs=(COMMITMENT,),
        )
        assert initial.program is not None
        changed_state = _state(width=8.0)
        changed_proposal = _proposal(
            changed_state,
            predecessor=initial.program.program_digest,
        )
        result = compile_geometry_program(
            changed_state,
            changed_proposal,
            active_commitment_refs=(COMMITMENT,),
            prior_program=initial.program,
        )
        self.assertIn(
            GeometryIssueCode.UNACKNOWLEDGED_SEMANTIC_CHANGE,
            _codes(result),
        )
        self.assertIn(
            GeometryIssueCode.MISSING_REVISION_PRECONDITION,
            _codes(result),
        )

    def test_missing_asset_is_red_and_lossy_substitution_is_explicit(
        self,
    ) -> None:
        state = _state()
        requested = AssetReference(
            asset_id="requested-detail",
            uri="project://demo/assets/requested-detail",
            media_type="model/example",
            sha256="e" * 64,
            native_unit=LengthUnit.MILLIMETER,
            sockets=("origin",),
            provenance_refs=(EVIDENCE,),
        )
        replacement = AssetReference(
            asset_id="replacement-detail",
            uri="project://demo/assets/replacement-detail",
            media_type="model/example",
            sha256="f" * 64,
            native_unit=LengthUnit.MILLIMETER,
            sockets=("origin",),
            provenance_refs=(EVIDENCE,),
        )
        detail_operation = _operation(
            op_id="ornament",
            kind=GeometryOperationKind.ASSET_INSTANCE,
            output="ornament",
            asset_id=requested.asset_id,
        )
        proposal = _proposal(
            state,
            assets=(replacement, requested),
            extra_operations=(detail_operation,),
        )
        missing = compile_geometry_program(
            state,
            proposal,
            active_commitment_refs=(COMMITMENT,),
            available_asset_digests={
                replacement.asset_id: replacement.sha256,
            },
        )
        self.assertIn(GeometryIssueCode.MISSING_ASSET, _codes(missing))

        substitution = AssetSubstitutionReceipt(
            requested_asset_id=requested.asset_id,
            requested_sha256=requested.sha256,
            replacement_asset_id=replacement.asset_id,
            replacement_sha256=replacement.sha256,
            loss_codes=("reduced-detail",),
            evidence_refs=("evidence:reviewed-substitution",),
        )
        compiled = compile_geometry_program(
            state,
            proposal,
            active_commitment_refs=(COMMITMENT,),
            available_asset_digests={
                replacement.asset_id: replacement.sha256,
            },
            asset_substitutions=(substitution,),
        )
        self.assertIs(
            compiled.receipt.status,
            GeometryCompileStatus.COMPILED,
        )
        self.assertEqual(
            compiled.receipt.asset_substitutions,
            (substitution,),
        )

    def test_parametric_profile_and_array_remain_generic_operations(
        self,
    ) -> None:
        state = _state()
        profile = GeometryOperation(
            op_id="detail-profile",
            kind=GeometryOperationKind.CURVE,
            output_object_ids=("detail-profile",),
            input_object_ids=(),
            frame_id="world",
            parameters=(
                GeometryParameter.create(
                    name="points",
                    kind=GeometryParameterKind.POINTS3,
                    value=[[0, 0, 0], [0.1, 0.05, 0], [0.2, 0, 0]],
                    unit=LengthUnit.METER,
                ),
            ),
            semantic_binding_ids=("building-binding",),
        )
        repeated = GeometryOperation(
            op_id="detail-array",
            kind=GeometryOperationKind.ARRAY,
            output_object_ids=("detail-array",),
            input_object_ids=("detail-profile",),
            frame_id="world",
            parameters=(
                GeometryParameter.create(
                    name="count",
                    kind=GeometryParameterKind.INTEGER,
                    value=8,
                ),
            ),
            semantic_binding_ids=("building-binding",),
        )
        result = compile_geometry_program(
            state,
            _proposal(
                state,
                extra_operations=(repeated, profile),
            ),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(result.receipt.status, GeometryCompileStatus.COMPILED)
        assert result.program is not None
        self.assertLess(
            result.program.operation_order.index("detail-profile"),
            result.program.operation_order.index("detail-array"),
        )

    def test_unresolved_operation_is_an_explicit_rejection_receipt(
        self,
    ) -> None:
        state = _state()
        broken = _operation(
            op_id="broken",
            kind=GeometryOperationKind.TRANSFORM,
            output="broken-output",
            inputs=("missing-input",),
        )
        result = compile_geometry_program(
            state,
            _proposal(state, extra_operations=(broken,)),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            result.receipt.status,
            GeometryCompileStatus.REJECTED,
        )
        self.assertIsNone(result.program)
        self.assertIn(GeometryIssueCode.UNKNOWN_OBJECT, _codes(result))
        self.assertFalse(
            result.receipt.to_dict()["canonical_write_authority"]
        )

    def test_kernel_contains_no_project_or_platform_answer_routes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (
            (root / "archflow/state/geometry_program.py")
            .read_text(encoding="utf-8")
            .lower()
            + (
                root / "archflow/compilers/geometry.py"
            ).read_text(encoding="utf-8").lower()
            + (
                root / "archflow/runtime/geometry_compiler.py"
            ).read_text(encoding="utf-8").lower()
        )
        forbidden = (
            "pantheon",
            "minecraft",
            "rhino",
            "revit",
            "greek_order",
            "gothic",
        )
        self.assertEqual(
            {token for token in forbidden if token in source},
            set(),
        )
        self.assertNotIn("if building_type", source)
        self.assertNotIn("if style", source)


if __name__ == "__main__":
    unittest.main()
