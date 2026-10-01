"""P089: the geometry compiler over the spine's own design state.

The state is the projection of an authored ``StateRecord@1``
(``spine_fixture.py``), which is what ``monkeyarch.application.project_runner`` hands the
compiler. The frozen digests below are that state's, computed once.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from archflow.state.geometry_program import (
    AssetSubstitutionReceipt,
)
from monkeyarch.compilation.geometry import (
    GeometryCompileStatus,
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state.geometry_program import (
    AssemblyRole,
    AssetReference,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    LengthUnit,
    ObjectRevisionPrecondition,
)
from spine_fixture import COMMITMENT, EVIDENCE, _codes, _number, _operation, _proposal, _state


class GeometryCompilerTests(unittest.TestCase):
    def test_canonical_owner_freezes_the_payload_digest(self) -> None:
        state = _state()
        result = compile_geometry_program(
            state,
            _proposal(state),
            active_commitment_refs=(COMMITMENT,),
        )
        assert result.program is not None

        self.assertEqual(
            "bb6d0daf925f94521d2af6bd62b1b8047086a89177fb2bf6fcb690f2235f5ed0",
            result.program.program_digest,
        )
        self.assertEqual(
            {
                "schema": "GeometryCompilationReceipt@1",
                "proposal_digest": (
                    "ba917964badd5253d9050c209cfe004ed8dea318e8adaacdf4e35b6ea6c3b874"
                ),
                "status": "compiled",
                "compiled_program_digest": (
                    "bb6d0daf925f94521d2af6bd62b1b8047086a89177fb2bf6fcb690f2235f5ed0"
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
                    "ab2d44fbffe1e86dfc505ef81ab316233527ab8126b34ebb9f10c052c02306f5"
                ),
            },
            result.program.objects[0].to_dict(),
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


class ConstructionOwnershipTests(unittest.TestCase):
    """#419: only delivered geometry needs a design identity; a host cut relates to its host by construction."""

    def setUp(self) -> None:
        self.state = _state()

    def _compile(self, proposal):
        return compile_geometry_program(self.state, proposal, active_commitment_refs=(COMMITMENT,))

    def _without_owner(self, proposal, *object_ids):
        binding = proposal.semantic_bindings[0]
        owned = tuple(item for item in binding.object_ids if item not in object_ids)
        return replace(proposal, semantic_bindings=(replace(binding, object_ids=owned),))

    def _block(self) -> GeometryOperation:
        return _operation(op_id="block", kind=GeometryOperationKind.SOLID, output="block", parameter=_number("width", 2.0))

    def _cutter(self, *, retained: bool = False) -> GeometryOperation:
        parameters = [_number("width", 0.5)]
        if retained:
            parameters.append(GeometryParameter.create(
                name="retain_for_inspection", kind=GeometryParameterKind.BOOLEAN, value=True))
        return GeometryOperation(
            op_id="niche-cutter", kind=GeometryOperationKind.SOLID, output_object_ids=("niche-cutter",),
            input_object_ids=(), frame_id="world",
            parameters=tuple(sorted(parameters, key=lambda item: item.name)), semantic_binding_ids=(),
        )

    def _niche(self) -> GeometryOperation:
        return _operation(op_id="niche", kind=GeometryOperationKind.BOOLEAN_DIFFERENCE, output="niche-block",
                          inputs=("block", "niche-cutter"))

    def _host_cut(self, object_id: str):
        proposal = _proposal(self.state)
        assembly = proposal.assemblies[0]
        members = tuple(
            replace(member, object_ids=(object_id,)) if member.role is AssemblyRole.HOST_CUT else member
            for member in assembly.members
        )
        return replace(proposal, assemblies=(replace(assembly, members=members),))

    def test_a_consumed_intermediate_needs_no_design_identity(self) -> None:
        proposal = self._without_owner(
            _proposal(self.state, extra_operations=(self._block(), self._cutter(), self._niche())), "niche-cutter")
        result = self._compile(proposal)
        self.assertIs(result.receipt.status, GeometryCompileStatus.COMPILED, result.receipt.issues)
        assert result.program is not None
        self.assertIn("niche-cutter", {item.object_id for item in result.program.objects})

    def test_an_unbound_delivered_object_is_refused(self) -> None:
        proposal = self._without_owner(_proposal(self.state, extra_operations=(self._cutter(),)), "niche-cutter")
        issues = [item for item in self._compile(proposal).receipt.issues if item.code is GeometryIssueCode.UNOWNED_OBJECT]
        self.assertEqual([(item.subject_id, item.detail) for item in issues],
                         [("niche-cutter", "delivered geometry object has no design identity binding")])

    def test_a_retained_intermediate_needs_a_design_identity(self) -> None:
        proposal = self._without_owner(
            _proposal(self.state, extra_operations=(self._block(), self._cutter(retained=True), self._niche())),
            "niche-cutter")
        self.assertIn(GeometryIssueCode.UNOWNED_OBJECT, _codes(self._compile(proposal)))

    def test_a_host_cut_region_consumed_with_its_host_is_accepted(self) -> None:
        result = self._compile(self._host_cut("opening-tool"))
        self.assertIs(result.receipt.status, GeometryCompileStatus.COMPILED, result.receipt.issues)

    def test_a_host_cut_related_to_its_host_by_no_operation_is_refused(self) -> None:
        issues = self._compile(self._host_cut("unrelated-axis")).receipt.issues
        self.assertEqual([item.detail for item in issues if item.code is GeometryIssueCode.INVALID_ASSEMBLY],
                         ["host-cut region is related to its named host by no operation"])


if __name__ == "__main__":
    unittest.main()
