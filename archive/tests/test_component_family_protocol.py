from __future__ import annotations

from dataclasses import replace
import copy
import unittest

from archive.archflow.realization.sandbox import RealizationStatus, realize_geometry
from archive.archflow.runtime.family_compiler import (
    CompiledFamilyInstance,
    ComponentFamilyCompilationReceipt,
    ComponentFamilyLifecycleReceipt,
    ComponentFamilyRealizationReceipt,
    FamilyCompileStatus,
    FamilyInstanceDisposition,
    FamilyIssueCode,
    FamilyRealizationStatus,
    bind_component_family_realization,
    compile_component_families,
    compile_component_family_lifecycle,
)
from archflow.compilers.geometry import compile_geometry_program
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    compile_semantic_geometry_lifecycle,
)
from archive.archflow.state.component_family import ComponentFamilyError, ComponentFamilyInstance, ComponentFamilyKind, ComponentFamilySet, FamilyAnchorBinding, FamilyParameterRef, FamilySocket
from archflow.state.geometry_program import AffineTransform, AssetReference, CoordinateFrame, GeometryOperation, GeometryOperationKind, GeometryParameter, GeometryParameterKind, GeometryProgramProposal, GeometryTolerance, LengthUnit, SemanticBinding
from archflow.contracts.canonical import canonical_digest
from archive.tests.test_geometry_compiler import COMMITMENT, EVIDENCE, _state
from archive.tests.test_sandbox_realization import _asset_payload, compiled_room
from archive.tests.test_semantic_geometry_lifecycle import (
    _design_state,
    _geometry_proposal,
    _initial,
)


INTERFACE = "interface:outside-to-room"
LIFECYCLE_INTERFACE = "interface:inside-to-outside"
SOURCE = "project-record:family-definition"


def _parameter_ref(program, op_id: str, name: str) -> FamilyParameterRef:
    operation = next(item for item in program.proposal.operations if item.op_id == op_id)
    parameter = next(item for item in operation.parameters if item.name == name)
    return FamilyParameterRef(
        parameter_id=f"{op_id}-{name}",
        operation_id=op_id,
        parameter_name=name,
        parameter_digest=canonical_digest(parameter.to_dict()),
        source_refs=(SOURCE,),
    )


def _family_set(
    state,
    program,
    instance: ComponentFamilyInstance,
    *,
    interfaces: tuple[str, ...] = (INTERFACE,),
) -> ComponentFamilySet:
    return ComponentFamilySet(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        component_tree_digest=(
            state.selected_schematic.option.proposal.proposal_digest
        ),
        geometry_program_digest=program.program_digest,
        available_interface_refs=interfaces,
        instances=(instance,),
    )


def _parametric_fixture():
    state, program, _ = compiled_room()
    instance = ComponentFamilyInstance(
        family_instance_id="parametric-family-instance",
        component_id="building",
        family_id="project-authored-parametric-family",
        family_revision=1,
        kind=ComponentFamilyKind.PARAMETRIC_ASSEMBLY,
        definition_digest="a" * 64,
        predecessor_instance_digest=None,
        frame_id="world",
        native_unit=LengthUnit.METER,
        scale=(1.0, 1.0, 1.0),
        parameter_refs=(_parameter_ref(program, "floor", "origin"),),
        sockets=(
            FamilySocket(
                socket_id="entry-socket",
                object_id="walls",
                frame_id="world",
                interface_refs=(INTERFACE,),
            ),
        ),
        anchors=(
            FamilyAnchorBinding(
                anchor_id="entry-anchor",
                local_socket_id="entry-socket",
                target_object_id="shell",
                target_socket_id="entry-axis",
                interface_refs=(INTERFACE,),
            ),
        ),
        interface_refs=(INTERFACE,),
        dependency_component_ids=(),
        semantic_binding_ids=("room-binding",),
        operation_ids=tuple(
            sorted(item.op_id for item in program.proposal.operations)
        ),
        assembly_ids=("entry",),
        asset_ids=(),
        provenance_refs=(SOURCE,),
    )
    return state, program, _family_set(state, program, instance)


def _mesh_fixture():
    state = _state()
    payload = replace(_asset_payload(), asset_id="mesh-asset")
    asset = AssetReference(
        asset_id="mesh-asset",
        uri="project://family-test/assets/mesh-asset",
        media_type="model/example",
        sha256=payload.payload_digest,
        native_unit=LengthUnit.METER,
        sockets=("origin",),
        provenance_refs=(SOURCE,),
    )
    operation = GeometryOperation(
        op_id="mesh-instance-op",
        kind=GeometryOperationKind.ASSET_INSTANCE,
        output_object_ids=("mesh-object",),
        input_object_ids=(),
        frame_id="world",
        parameters=(),
        semantic_binding_ids=("mesh-binding",),
        asset_id=asset.asset_id,
        asset_socket_id="origin",
        asset_scale=(2.0, 2.0, 2.0),
    )
    proposal = GeometryProgramProposal(
        proposal_id="mesh-family-program",
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
                source_refs=(SOURCE,),
            ),
        ),
        assets=(asset,),
        semantic_bindings=(
            SemanticBinding(
                binding_id="mesh-binding",
                component_id="building",
                object_ids=("mesh-object",),
                commitment_refs=(COMMITMENT,),
                evidence_refs=(SOURCE,),
            ),
        ),
        operations=(operation,),
        assemblies=(),
    )
    result = compile_geometry_program(
        state,
        proposal,
        active_commitment_refs=(COMMITMENT,),
        available_asset_digests={asset.asset_id: asset.sha256},
    )
    if result.program is None:
        raise AssertionError(result.receipt.issues)
    instance = ComponentFamilyInstance(
        family_instance_id="external-mesh-instance",
        component_id="building",
        family_id="project-authored-mesh-family",
        family_revision=1,
        kind=ComponentFamilyKind.EXTERNAL_MESH,
        definition_digest=asset.sha256,
        predecessor_instance_digest=None,
        frame_id="world",
        native_unit=LengthUnit.METER,
        scale=(2.0, 2.0, 2.0),
        parameter_refs=(),
        sockets=(
            FamilySocket(
                socket_id="origin",
                object_id="mesh-object",
                frame_id="world",
                interface_refs=(INTERFACE,),
            ),
        ),
        anchors=(),
        interface_refs=(INTERFACE,),
        dependency_component_ids=(),
        semantic_binding_ids=("mesh-binding",),
        operation_ids=("mesh-instance-op",),
        assembly_ids=(),
        asset_ids=("mesh-asset",),
        provenance_refs=(SOURCE,),
    )
    return state, result.program, payload, _family_set(state, result.program, instance)


def _hybrid_fixture():
    state, program, _ = compiled_room(include_asset=True)
    instance = ComponentFamilyInstance(
        family_instance_id="hybrid-family-instance",
        component_id="building",
        family_id="project-authored-hybrid-family",
        family_revision=1,
        kind=ComponentFamilyKind.HYBRID,
        definition_digest="b" * 64,
        predecessor_instance_digest=None,
        frame_id="world",
        native_unit=LengthUnit.METER,
        scale=(1.0, 1.0, 1.0),
        parameter_refs=(_parameter_ref(program, "floor", "origin"),),
        sockets=(
            FamilySocket(
                socket_id="origin",
                object_id="ornament",
                frame_id="world",
                interface_refs=(INTERFACE,),
            ),
        ),
        anchors=(
            FamilyAnchorBinding(
                anchor_id="hybrid-anchor",
                local_socket_id="origin",
                target_object_id="shell",
                target_socket_id="entry-axis",
                interface_refs=(INTERFACE,),
            ),
        ),
        interface_refs=(INTERFACE,),
        dependency_component_ids=(),
        semantic_binding_ids=("room-binding",),
        operation_ids=tuple(
            sorted(item.op_id for item in program.proposal.operations)
        ),
        assembly_ids=("entry",),
        asset_ids=("detail-asset",),
        provenance_refs=(SOURCE,),
    )
    return state, program, _family_set(state, program, instance)


def _lifecycle_family(state, program, *, revision: int, predecessor=None):
    operations = tuple(
        sorted(
            item.op_id
            for item in program.proposal.operations
            if item.semantic_binding_ids == ("building-binding",)
        )
    )
    instance = ComponentFamilyInstance(
        family_instance_id="surface-family-instance",
        component_id="primary-surface",
        family_id="surface-family",
        family_revision=revision,
        kind=ComponentFamilyKind.PARAMETRIC_ASSEMBLY,
        definition_digest=("c" if revision == 1 else "d") * 64,
        predecessor_instance_digest=predecessor,
        frame_id="world",
        native_unit=LengthUnit.METER,
        scale=(1.0, 1.0, 1.0),
        parameter_refs=(_parameter_ref(program, "wall", "width"),),
        sockets=(
            FamilySocket(
                socket_id="entry-socket",
                object_id="frame",
                frame_id="world",
                interface_refs=(LIFECYCLE_INTERFACE,),
            ),
        ),
        anchors=(
            FamilyAnchorBinding(
                anchor_id="entry-anchor",
                local_socket_id="entry-socket",
                target_object_id="wall",
                target_socket_id="entry-axis",
                interface_refs=(LIFECYCLE_INTERFACE,),
            ),
        ),
        interface_refs=(LIFECYCLE_INTERFACE,),
        dependency_component_ids=(),
        semantic_binding_ids=("building-binding",),
        operation_ids=operations,
        assembly_ids=("entry-assembly",),
        asset_ids=(),
        provenance_refs=(SOURCE,),
    )
    return _family_set(
        state,
        program,
        instance,
        interfaces=(LIFECYCLE_INTERFACE,),
    )


class ComponentFamilyContractTests(unittest.TestCase):
    def test_parametric_family_round_trips_and_binds_existing_geometry(self) -> None:
        state, program, family_set = _parametric_fixture()
        receipt = compile_component_families(state, program, family_set)

        self.assertIs(receipt.status, FamilyCompileStatus.COMPILED)
        self.assertEqual(ComponentFamilySet.from_dict(family_set.to_dict()), family_set)
        self.assertEqual(
            ComponentFamilyCompilationReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertEqual(
            receipt.compiled_instances[0].component_id,
            "building",
        )
        self.assertFalse(family_set.to_dict()["component_tree_authority"])
        self.assertFalse(receipt.to_dict()["geometry_generation_authority"])

    def test_external_mesh_is_content_bound_nonparametric_and_realizable(self) -> None:
        state, program, payload, family_set = _mesh_fixture()
        receipt = compile_component_families(state, program, family_set)
        realization = realize_geometry(
            program,
            workspace_id="mesh-family-sandbox",
            asset_payloads=(payload,),
        )

        self.assertIs(receipt.status, FamilyCompileStatus.COMPILED)
        self.assertEqual(
            receipt.compiled_instances[0].asset_digests,
            (("mesh-asset", payload.payload_digest),),
        )
        self.assertIs(realization.receipt.status, RealizationStatus.REALIZED)
        self.assertIsNotNone(realization.scene)
        family_realization = bind_component_family_realization(
            receipt,
            realization.scene,
            realization.receipt,
        )
        self.assertIs(
            family_realization.status,
            FamilyRealizationStatus.REALIZED,
        )
        self.assertEqual(
            ComponentFamilyRealizationReceipt.from_dict(
                family_realization.to_dict()
            ),
            family_realization,
        )
        self.assertEqual(
            family_realization.scene_digest,
            realization.scene.scene_digest,
        )

    def test_family_realization_rejects_stale_sandbox_receipt(self) -> None:
        state, program, family_set = _parametric_fixture()
        compilation = compile_component_families(state, program, family_set)
        realization = realize_geometry(
            program,
            workspace_id="parametric-family-sandbox",
        )
        self.assertIsNotNone(realization.scene)
        stale_receipt = replace(
            realization.receipt,
            geometry_program_digest="9" * 64,
        )

        family_realization = bind_component_family_realization(
            compilation,
            realization.scene,
            stale_receipt,
        )

        self.assertIs(
            family_realization.status,
            FamilyRealizationStatus.REJECTED,
        )
        self.assertIsNone(family_realization.scene_digest)
        self.assertEqual(
            {FamilyIssueCode.REALIZATION_MISMATCH},
            {item.code for item in family_realization.issues},
        )

    def test_hybrid_uses_one_component_binding_for_parameters_and_mesh(self) -> None:
        state, program, family_set = _hybrid_fixture()
        receipt = compile_component_families(state, program, family_set)

        self.assertIs(receipt.status, FamilyCompileStatus.COMPILED)
        compiled = receipt.compiled_instances[0]
        self.assertTrue(compiled.parameter_digests)
        self.assertTrue(compiled.asset_digests)
        self.assertEqual(compiled.component_id, "building")

    def test_mesh_cannot_claim_parameters_and_parametric_scale_is_not_hidden(self) -> None:
        _, _, _, mesh_set = _mesh_fixture()
        mesh = mesh_set.instances[0]
        with self.assertRaises(ComponentFamilyError):
            replace(mesh, parameter_refs=(
                FamilyParameterRef(
                    parameter_id="fake-parameter",
                    operation_id="mesh-instance-op",
                    parameter_name="fake",
                    parameter_digest="f" * 64,
                    source_refs=(SOURCE,),
                ),
            ))
        _, _, parametric_set = _parametric_fixture()
        with self.assertRaises(ComponentFamilyError):
            replace(parametric_set.instances[0], scale=(2.0, 2.0, 2.0))

    def test_parameter_socket_asset_owner_and_stale_binding_fail_typed(self) -> None:
        state, program, family_set = _parametric_fixture()
        instance = family_set.instances[0]
        bad_parameter = replace(
            instance.parameter_refs[0],
            parameter_digest="f" * 64,
        )
        bad = replace(
            family_set,
            instances=(replace(instance, parameter_refs=(bad_parameter,)),),
        )
        receipt = compile_component_families(state, program, bad)
        self.assertIn(FamilyIssueCode.PARAMETER_MISMATCH, {x.code for x in receipt.issues})
        self.assertEqual(receipt.issues[0].component_ids, ("building",))
        self.assertTrue(receipt.issues[0].geometry_object_ids)
        self.assertEqual(receipt.issues[0].source_refs, (SOURCE,))

        payload = copy.deepcopy(family_set.to_dict())
        payload["geometry_program_digest"] = "9" * 64
        payload["family_set_digest"] = canonical_digest(
            {
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "family_set_digest",
                    "component_tree_authority",
                    "persistence_authority",
                    "canonical_write_authority",
                }
            }
        )
        stale = ComponentFamilySet.from_dict(payload)
        stale_receipt = compile_component_families(state, program, stale)
        self.assertIn(
            FamilyIssueCode.EXACT_PROJECT_MISMATCH,
            {x.code for x in stale_receipt.issues},
        )

        mesh_state, mesh_program, _, mesh_set = _mesh_fixture()
        mesh = mesh_set.instances[0]
        bad_socket = replace(mesh.sockets[0], socket_id="missing-socket")
        bad_mesh = replace(mesh_set, instances=(replace(mesh, sockets=(bad_socket,)),))
        mesh_receipt = compile_component_families(
            mesh_state,
            mesh_program,
            bad_mesh,
        )
        self.assertIn(
            FamilyIssueCode.SOCKET_MISMATCH,
            {x.code for x in mesh_receipt.issues},
        )


class ComponentFamilyLifecycleTests(unittest.TestCase):
    def _compiled_lifecycle(self):
        before_state, before_program = _initial()
        after_state = _design_state(1)
        semantic = compile_semantic_geometry_lifecycle(
            transaction_id="family-surface-revision",
            predecessor_state=before_state,
            current_state=after_state,
            predecessor_proposal=(
                before_state.selected_schematic.option.proposal
            ),
            current_proposal=after_state.selected_schematic.option.proposal,
            prior_program=before_program,
            geometry_proposal=_geometry_proposal(
                after_state,
                before_program,
                stage=1,
            ),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            semantic.receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        assert semantic.geometry_program is not None
        before = _lifecycle_family(before_state, before_program, revision=1)
        after_instance = _lifecycle_family(
            after_state,
            semantic.geometry_program,
            revision=2,
            predecessor=before.instances[0].instance_digest,
        )
        before_receipt = compile_component_families(
            before_state,
            before_program,
            before,
        )
        after_receipt = compile_component_families(
            after_state,
            semantic.geometry_program,
            after_instance,
        )
        return before, after_instance, before_receipt, after_receipt, semantic

    def test_exact_p055_family_revision_compiles_and_round_trips(self) -> None:
        before, after, before_receipt, after_receipt, semantic = (
            self._compiled_lifecycle()
        )
        receipt = compile_component_family_lifecycle(
            before,
            after,
            before_receipt,
            after_receipt,
            semantic.receipt,
        )

        self.assertIs(receipt.status, FamilyCompileStatus.COMPILED)
        self.assertIs(
            receipt.transitions[0].disposition,
            FamilyInstanceDisposition.REVISED,
        )
        self.assertEqual(
            ComponentFamilyLifecycleReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertFalse(receipt.to_dict()["successor_authority"])

        stale = compile_component_family_lifecycle(
            before,
            after,
            replace(before_receipt, run_id="stale-run"),
            after_receipt,
            semantic.receipt,
        )
        self.assertIs(stale.status, FamilyCompileStatus.REJECTED)
        self.assertIn(
            FamilyIssueCode.LIFECYCLE_MISMATCH,
            {item.code for item in stale.issues},
        )

    def test_same_revision_content_change_and_missing_predecessor_reject(self) -> None:
        before, after, before_receipt, _, semantic = self._compiled_lifecycle()
        changed = replace(
            after.instances[0],
            family_revision=1,
            predecessor_instance_digest=None,
        )
        after = replace(after, instances=(changed,))
        current_receipt = ComponentFamilyCompilationReceipt(
            project_id=after.project_id,
            run_id=after.run_id,
            base=after.base,
            family_set_digest=after.family_set_digest,
            design_state_digest=after.design_state_digest,
            component_tree_digest=after.component_tree_digest,
            geometry_program_digest=after.geometry_program_digest,
            status=FamilyCompileStatus.COMPILED,
            compiled_instances=(
                replace(
                    before_receipt.compiled_instances[0],
                    instance_digest=changed.instance_digest,
                    definition_digest=changed.definition_digest,
                ),
            ),
            issues=(),
        )
        receipt = compile_component_family_lifecycle(
            before,
            after,
            before_receipt,
            current_receipt,
            semantic.receipt,
        )
        codes = {item.code for item in receipt.issues}
        self.assertIs(receipt.status, FamilyCompileStatus.REJECTED)
        self.assertIn(FamilyIssueCode.IMMUTABLE_REVISION_CHANGED, codes)
        self.assertIn(FamilyIssueCode.LIFECYCLE_MISMATCH, codes)

    def test_exact_replacement_and_retirement_are_distinct(self) -> None:
        before, after, before_receipt, _, semantic = self._compiled_lifecycle()
        replacement = replace(
            after.instances[0],
            family_id="replacement-surface-family",
            family_revision=1,
            definition_digest="e" * 64,
            predecessor_instance_digest=before.instances[0].instance_digest,
        )
        replaced_set = replace(after, instances=(replacement,))
        replaced_compilation = compile_component_families(
            _design_state(1),
            semantic.geometry_program,
            replaced_set,
        )
        replaced = compile_component_family_lifecycle(
            before,
            replaced_set,
            before_receipt,
            replaced_compilation,
            semantic.receipt,
        )
        self.assertIs(replaced.status, FamilyCompileStatus.COMPILED)
        self.assertIs(
            replaced.transitions[0].disposition,
            FamilyInstanceDisposition.REPLACED,
        )

        retired_set = replace(after, instances=())
        retired_compilation = compile_component_families(
            _design_state(1),
            semantic.geometry_program,
            retired_set,
        )
        retired = compile_component_family_lifecycle(
            before,
            retired_set,
            before_receipt,
            retired_compilation,
            semantic.receipt,
        )
        self.assertIs(retired.status, FamilyCompileStatus.COMPILED)
        self.assertIs(
            retired.transitions[0].disposition,
            FamilyInstanceDisposition.RETIRED,
        )

    def test_impacted_dependency_cannot_remain_silently_preserved(self) -> None:
        before_state = _design_state(0)
        before_proposal = self._with_support_parameter(
            _geometry_proposal(before_state, stage=0)
        )
        before_geometry = compile_geometry_program(
            before_state,
            before_proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        assert before_geometry.program is not None
        before_program = before_geometry.program
        after_state = _design_state(1)
        after_proposal = self._with_support_parameter(
            _geometry_proposal(after_state, before_program, stage=1)
        )
        semantic = compile_semantic_geometry_lifecycle(
            transaction_id="family-dependency-change",
            predecessor_state=before_state,
            current_state=after_state,
            predecessor_proposal=(
                before_state.selected_schematic.option.proposal
            ),
            current_proposal=after_state.selected_schematic.option.proposal,
            prior_program=before_program,
            geometry_proposal=after_proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            semantic.receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        assert semantic.geometry_program is not None
        before_surface = _lifecycle_family(
            before_state,
            before_program,
            revision=1,
        ).instances[0]
        after_surface = _lifecycle_family(
            after_state,
            semantic.geometry_program,
            revision=2,
            predecessor=before_surface.instance_digest,
        ).instances[0]
        support = self._support_family(before_program)
        before_set = ComponentFamilySet(
            project_id=before_state.project_id,
            run_id=before_state.run_id,
            base=before_state.base,
            design_state_digest=before_state.state_digest,
            component_tree_digest=(
                before_state.selected_schematic.option.proposal.proposal_digest
            ),
            geometry_program_digest=before_program.program_digest,
            available_interface_refs=tuple(
                sorted((LIFECYCLE_INTERFACE, "interface:support-link"))
            ),
            instances=tuple(
                sorted(
                    (before_surface, support),
                    key=lambda item: item.family_instance_id,
                )
            ),
        )
        after_set = replace(
            before_set,
            design_state_digest=after_state.state_digest,
            component_tree_digest=(
                after_state.selected_schematic.option.proposal.proposal_digest
            ),
            geometry_program_digest=semantic.geometry_program.program_digest,
            instances=tuple(
                sorted(
                    (after_surface, support),
                    key=lambda item: item.family_instance_id,
                )
            ),
        )
        before_compilation = compile_component_families(
            before_state,
            before_program,
            before_set,
        )
        after_compilation = compile_component_families(
            after_state,
            semantic.geometry_program,
            after_set,
        )
        self.assertIs(before_compilation.status, FamilyCompileStatus.COMPILED)
        self.assertIs(after_compilation.status, FamilyCompileStatus.COMPILED)
        receipt = compile_component_family_lifecycle(
            before_set,
            after_set,
            before_compilation,
            after_compilation,
            semantic.receipt,
        )
        self.assertIs(receipt.status, FamilyCompileStatus.REJECTED)
        self.assertIn(
            FamilyIssueCode.LOCAL_DEPENDENCY_NOT_INVALIDATED,
            {item.code for item in receipt.issues},
        )

        independent_support = replace(
            support,
            dependency_component_ids=(),
        )
        independent_before = replace(
            before_set,
            instances=tuple(
                sorted(
                    (before_surface, independent_support),
                    key=lambda item: item.family_instance_id,
                )
            ),
        )
        independent_after = replace(
            after_set,
            instances=tuple(
                sorted(
                    (after_surface, independent_support),
                    key=lambda item: item.family_instance_id,
                )
            ),
        )
        independent_before_compilation = compile_component_families(
            before_state,
            before_program,
            independent_before,
        )
        independent_after_compilation = compile_component_families(
            after_state,
            semantic.geometry_program,
            independent_after,
        )
        preserved = compile_component_family_lifecycle(
            independent_before,
            independent_after,
            independent_before_compilation,
            independent_after_compilation,
            semantic.receipt,
        )
        self.assertIs(preserved.status, FamilyCompileStatus.COMPILED)
        support_transition = next(
            item
            for item in preserved.transitions
            if item.component_id == "primary-support"
        )
        self.assertIs(
            support_transition.disposition,
            FamilyInstanceDisposition.PRESERVED,
        )
        self.assertEqual(
            support_transition.predecessor_instance_digest,
            support_transition.current_instance_digest,
        )

    @staticmethod
    def _with_support_parameter(proposal):
        parameter = GeometryParameter.create(
            name="support-offset",
            kind=GeometryParameterKind.NUMBER,
            value=0.0,
            unit=LengthUnit.METER,
        )
        return replace(
            proposal,
            operations=tuple(
                replace(item, parameters=(parameter,))
                if item.op_id == "portico-mass"
                else item
                for item in proposal.operations
            ),
        )

    @staticmethod
    def _support_family(program) -> ComponentFamilyInstance:
        return ComponentFamilyInstance(
            family_instance_id="support-family-instance",
            component_id="primary-support",
            family_id="support-family",
            family_revision=1,
            kind=ComponentFamilyKind.PARAMETRIC_ASSEMBLY,
            definition_digest="f" * 64,
            predecessor_instance_digest=None,
            frame_id="world",
            native_unit=LengthUnit.METER,
            scale=(1.0, 1.0, 1.0),
            parameter_refs=(
                _parameter_ref(program, "portico-mass", "support-offset"),
            ),
            sockets=(
                FamilySocket(
                    socket_id="support-socket",
                    object_id="portico-object",
                    frame_id="world",
                    interface_refs=("interface:support-link",),
                ),
            ),
            anchors=(),
            interface_refs=("interface:support-link",),
            dependency_component_ids=("primary-surface",),
            semantic_binding_ids=("portico-binding",),
            operation_ids=("portico-mass",),
            assembly_ids=(),
            asset_ids=(),
            provenance_refs=(SOURCE,),
        )


if __name__ == "__main__":
    unittest.main()
