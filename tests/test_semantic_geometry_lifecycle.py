from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.runtime.geometry_compiler import compile_geometry_program
from archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleIssueCode,
    SemanticGeometryLifecycleStatus,
    bind_initial_semantic_geometry,
    compile_semantic_geometry_lifecycle,
)
from archflow.state import ComponentMaturity, DesignComponent
from archflow.state.geometry_program import (
    GeometryOperationKind,
    ObjectRetirement,
    ObjectRevisionPrecondition,
    SemanticBinding,
)
from tests.test_design_portfolio import EVIDENCE
from tests.test_geometry_compiler import (
    COMMITMENT,
    _operation,
    _proposal,
    _state,
)


def _design_state(stage: int):
    state = _state()
    proposal = state.selected_schematic.option.proposal
    components = {
        item.component_id: item for item in proposal.components
    }
    surface = components["primary-surface"]
    if stage == 0:
        components["primary-surface"] = replace(
            surface,
            intent="Establish the coarse dome shell.",
            maturity=ComponentMaturity.SCHEMATIC,
            unresolved_child_roles=("oculus", "coffers"),
        )
    elif stage == 1:
        components["primary-surface"] = replace(
            surface,
            intent="Resolve the dome shell and oculus.",
            maturity=ComponentMaturity.DEVELOPED,
            revision=1,
            unresolved_child_roles=("coffers",),
        )
        components["oculus"] = DesignComponent(
            component_id="oculus",
            parent_component_id="primary-surface",
            semantic_kind="oculus",
            intent="Open the dome crown.",
            maturity=ComponentMaturity.DEVELOPED,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=(EVIDENCE,),
        )
    elif stage == 2:
        components["primary-surface"] = replace(
            surface,
            intent="Resolve the dome shell, oculus, and coffering.",
            maturity=ComponentMaturity.DETAILED,
            revision=2,
            unresolved_child_roles=(),
        )
        components["oculus"] = DesignComponent(
            component_id="oculus",
            parent_component_id="primary-surface",
            semantic_kind="oculus",
            intent="Open the dome crown.",
            maturity=ComponentMaturity.DEVELOPED,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=(EVIDENCE,),
        )
        components["coffers"] = DesignComponent(
            component_id="coffers",
            parent_component_id="primary-surface",
            semantic_kind="coffer-system",
            intent="Articulate the inside of the dome shell.",
            maturity=ComponentMaturity.DETAILED,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=(EVIDENCE,),
        )
    else:
        raise ValueError("unsupported stage")
    current_proposal = replace(
        proposal,
        components=tuple(
            sorted(components.values(), key=lambda item: item.component_id)
        ),
    )
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            option=replace(
                state.selected_schematic.option,
                proposal=current_proposal,
            ),
        ),
    )


def _geometry_proposal(state, prior=None, *, stage: int):
    extras = [
        _operation(
            op_id="portico-mass",
            kind=GeometryOperationKind.SOLID,
            output="portico-object",
        )
    ]
    if stage >= 1:
        extras.append(
            _operation(
                op_id="oculus-opening",
                kind=GeometryOperationKind.SOLID,
                output="oculus-object",
            )
        )
    if stage >= 2:
        extras.append(
            _operation(
                op_id="coffer-array",
                kind=GeometryOperationKind.ARRAY,
                output="coffer-object",
            )
        )
    revisions = ()
    if prior is not None:
        surface_binding = next(
            item
            for item in prior.proposal.semantic_bindings
            if item.component_id == "primary-surface"
        )
        revisions = tuple(
            ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=prior.object_digest(object_id),
                reason_refs=("decision:dome-refinement",),
            )
            for object_id in surface_binding.object_ids
        )
    proposal = _proposal(
        state,
        wall_width=6.0 + stage,
        predecessor=None if prior is None else prior.program_digest,
        revisions=revisions,
        respond_to_dependencies=prior is not None,
        extra_operations=tuple(extras),
    )
    operations = []
    ownership: dict[str, list[str]] = {
        "building-binding": [],
        "portico-binding": [],
    }
    if stage >= 1:
        ownership["oculus-binding"] = []
    if stage >= 2:
        ownership["coffer-binding"] = []
    for operation in proposal.operations:
        if operation.op_id == "portico-mass":
            binding_id = "portico-binding"
        elif operation.op_id == "oculus-opening":
            binding_id = "oculus-binding"
        elif operation.op_id == "coffer-array":
            binding_id = "coffer-binding"
        else:
            binding_id = "building-binding"
        ownership[binding_id].extend(operation.output_object_ids)
        operations.append(
            replace(
                operation,
                semantic_binding_ids=(binding_id,),
                responds_to_object_ids=(
                    operation.input_object_ids
                    if prior is not None and binding_id == "building-binding"
                    else operation.responds_to_object_ids
                ),
                responds_to_binding_ids=(
                    ("building-binding",)
                    if prior is not None and binding_id == "building-binding"
                    else ()
                ),
            )
        )
    component_by_binding = {
        "building-binding": "primary-surface",
        "portico-binding": "primary-support",
        "oculus-binding": "oculus",
        "coffer-binding": "coffers",
    }
    bindings = tuple(
        SemanticBinding(
            binding_id=binding_id,
            component_id=component_by_binding[binding_id],
            object_ids=tuple(sorted(object_ids)),
            commitment_refs=(COMMITMENT,),
            evidence_refs=(EVIDENCE,),
        )
        for binding_id, object_ids in sorted(ownership.items())
    )
    return replace(
        proposal,
        semantic_bindings=bindings,
        operations=tuple(sorted(operations, key=lambda item: item.op_id)),
    )


def _initial():
    state = _design_state(0)
    result = compile_geometry_program(
        state,
        _geometry_proposal(state, stage=0),
        active_commitment_refs=(COMMITMENT,),
    )
    assert result.program is not None
    return state, result.program


class SemanticGeometryLifecycleTests(unittest.TestCase):
    def test_initial_binding_does_not_fabricate_a_predecessor_lifecycle(self) -> None:
        state, program = _initial()

        result = bind_initial_semantic_geometry(
            transaction_id="initial-dome-geometry",
            current_state=state,
            current_proposal=state.selected_schematic.option.proposal,
            geometry_program=program,
            source_refs=(EVIDENCE,),
        )

        self.assertEqual(state.state_digest, result.receipt.design_state_digest)
        self.assertEqual(program.program_digest, result.receipt.geometry_program_digest)
        self.assertTrue(result.receipt.to_dict()["initial_binding"])
        self.assertNotIn("predecessor_program_digest", result.receipt.to_dict())

    def test_initial_binding_rejects_stale_geometry_state(self) -> None:
        state, program = _initial()
        changed = _design_state(1)

        with self.assertRaisesRegex(ValueError, "exact design state"):
            bind_initial_semantic_geometry(
                transaction_id="stale-initial-geometry",
                current_state=changed,
                current_proposal=changed.selected_schematic.option.proposal,
                geometry_program=program,
                source_refs=(EVIDENCE,),
            )

    def test_three_stage_dome_preserves_portico_and_requires_revalidation(
        self,
    ) -> None:
        coarse_state, coarse_program = _initial()
        shell_state = _design_state(1)
        shell_proposal = _geometry_proposal(
            shell_state,
            coarse_program,
            stage=1,
        )
        shell = compile_semantic_geometry_lifecycle(
            transaction_id="dome-shell-and-oculus",
            predecessor_state=coarse_state,
            current_state=shell_state,
            predecessor_proposal=(
                coarse_state.selected_schematic.option.proposal
            ),
            current_proposal=shell_state.selected_schematic.option.proposal,
            prior_program=coarse_program,
            geometry_proposal=shell_proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            shell.receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        assert shell.geometry_program is not None
        self.assertEqual(
            coarse_program.object_digest("portico-object"),
            shell.geometry_program.object_digest("portico-object"),
        )
        self.assertIn("primary-support", shell.receipt.preserved_component_ids)

        detailed_state = _design_state(2)
        detailed_proposal = _geometry_proposal(
            detailed_state,
            shell.geometry_program,
            stage=2,
        )
        unresolved = compile_semantic_geometry_lifecycle(
            transaction_id="dome-coffering-unresolved",
            predecessor_state=shell_state,
            current_state=detailed_state,
            predecessor_proposal=shell_state.selected_schematic.option.proposal,
            current_proposal=detailed_state.selected_schematic.option.proposal,
            prior_program=shell.geometry_program,
            geometry_proposal=detailed_proposal,
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIn(
            SemanticGeometryLifecycleIssueCode.MISSING_GEOMETRY_RESPONSE,
            {item.code for item in unresolved.receipt.issues},
        )
        self.assertIsNone(unresolved.geometry_program)

        detailed = compile_semantic_geometry_lifecycle(
            transaction_id="dome-coffering",
            predecessor_state=shell_state,
            current_state=detailed_state,
            predecessor_proposal=shell_state.selected_schematic.option.proposal,
            current_proposal=detailed_state.selected_schematic.option.proposal,
            prior_program=shell.geometry_program,
            geometry_proposal=detailed_proposal,
            revalidated_component_ids=("oculus",),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            detailed.receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        self.assertIn("coffers", detailed.receipt.geometry_changed_component_ids)
        self.assertEqual(detailed.receipt.revalidated_component_ids, ("oculus",))
        self.assertFalse(detailed.receipt.to_dict()["persistence_authority"])

    def test_geometry_failure_exposes_no_half_successor(self) -> None:
        before_state, before_program = _initial()
        after_state = _design_state(1)
        proposal = _geometry_proposal(after_state, before_program, stage=1)
        proposal = replace(proposal, revisions=())

        result = compile_semantic_geometry_lifecycle(
            transaction_id="missing-geometry-preconditions",
            predecessor_state=before_state,
            current_state=after_state,
            predecessor_proposal=before_state.selected_schematic.option.proposal,
            current_proposal=after_state.selected_schematic.option.proposal,
            prior_program=before_program,
            geometry_proposal=proposal,
            active_commitment_refs=(COMMITMENT,),
        )

        self.assertIs(
            result.receipt.status,
            SemanticGeometryLifecycleStatus.REJECTED,
        )
        self.assertIsNotNone(result.receipt.component_transition)
        self.assertIsNotNone(result.receipt.geometry_compilation)
        self.assertIsNone(result.component_proposal)
        self.assertIsNone(result.geometry_program)

    def test_component_retirement_requires_matching_geometry_retirement(
        self,
    ) -> None:
        coarse_state, coarse_program = _initial()
        shell_state = _design_state(1)
        shell = compile_semantic_geometry_lifecycle(
            transaction_id="build-oculus",
            predecessor_state=coarse_state,
            current_state=shell_state,
            predecessor_proposal=coarse_state.selected_schematic.option.proposal,
            current_proposal=shell_state.selected_schematic.option.proposal,
            prior_program=coarse_program,
            geometry_proposal=_geometry_proposal(
                shell_state,
                coarse_program,
                stage=1,
            ),
            active_commitment_refs=(COMMITMENT,),
        )
        assert shell.geometry_program is not None
        without_oculus = replace(
            shell_state.selected_schematic.option.proposal,
            components=tuple(
                item
                for item in shell_state.selected_schematic.option.proposal.components
                if item.component_id != "oculus"
            ),
        )
        retired_state = replace(
            shell_state,
            selected_schematic=replace(
                shell_state.selected_schematic,
                option=replace(
                    shell_state.selected_schematic.option,
                    proposal=without_oculus,
                ),
            ),
        )
        candidate = _geometry_proposal(
            retired_state,
            shell.geometry_program,
            stage=1,
        )
        candidate = replace(
            candidate,
            semantic_bindings=tuple(
                item
                for item in candidate.semantic_bindings
                if item.component_id != "oculus"
            ),
            operations=tuple(
                item for item in candidate.operations
                if item.op_id != "oculus-opening"
            ),
            revisions=(),
        )
        missing = compile_semantic_geometry_lifecycle(
            transaction_id="retire-oculus-missing-geometry",
            predecessor_state=shell_state,
            current_state=retired_state,
            predecessor_proposal=shell_state.selected_schematic.option.proposal,
            current_proposal=without_oculus,
            prior_program=shell.geometry_program,
            geometry_proposal=candidate,
            retired_component_ids=("oculus",),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            missing.receipt.status,
            SemanticGeometryLifecycleStatus.REJECTED,
        )
        self.assertIsNone(missing.geometry_program)

        retirement = ObjectRetirement(
            object_id="oculus-object",
            expected_digest=shell.geometry_program.object_digest(
                "oculus-object"
            ),
            reason_refs=("decision:retire-oculus",),
        )
        retired = compile_semantic_geometry_lifecycle(
            transaction_id="retire-oculus",
            predecessor_state=shell_state,
            current_state=retired_state,
            predecessor_proposal=shell_state.selected_schematic.option.proposal,
            current_proposal=without_oculus,
            prior_program=shell.geometry_program,
            geometry_proposal=replace(
                candidate,
                retirements=(retirement,),
            ),
            retired_component_ids=("oculus",),
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIs(
            retired.receipt.status,
            SemanticGeometryLifecycleStatus.COMPILED,
        )
        self.assertEqual(retired.receipt.retired_component_ids, ("oculus",))

    def test_component_failure_stops_before_geometry_success(self) -> None:
        before_state, before_program = _initial()
        after_state = _design_state(1)
        invalid = replace(
            after_state.selected_schematic.option.proposal,
            components=tuple(
                replace(item, revision=3)
                if item.component_id == "primary-surface"
                else item
                for item in after_state.selected_schematic.option.proposal.components
            ),
        )
        invalid_state = replace(
            after_state,
            selected_schematic=replace(
                after_state.selected_schematic,
                option=replace(
                    after_state.selected_schematic.option,
                    proposal=invalid,
                ),
            ),
        )
        geometry = _geometry_proposal(invalid_state, before_program, stage=1)

        result = compile_semantic_geometry_lifecycle(
            transaction_id="invalid-component-revision",
            predecessor_state=before_state,
            current_state=invalid_state,
            predecessor_proposal=before_state.selected_schematic.option.proposal,
            current_proposal=invalid,
            prior_program=before_program,
            geometry_proposal=geometry,
            active_commitment_refs=(COMMITMENT,),
        )

        self.assertEqual(
            result.receipt.issues[0].code,
            SemanticGeometryLifecycleIssueCode.COMPONENT_TRANSITION_REJECTED,
        )
        self.assertIsNone(result.receipt.geometry_compilation)

    def test_exact_state_and_program_bases_are_jointly_required(self) -> None:
        before_state, before_program = _initial()
        after_state = _design_state(1)
        geometry = _geometry_proposal(after_state, before_program, stage=1)
        stale_geometry = replace(
            geometry,
            predecessor_program_digest="0" * 64,
        )

        result = compile_semantic_geometry_lifecycle(
            transaction_id="stale-program",
            predecessor_state=before_state,
            current_state=after_state,
            predecessor_proposal=before_state.selected_schematic.option.proposal,
            current_proposal=after_state.selected_schematic.option.proposal,
            prior_program=before_program,
            geometry_proposal=stale_geometry,
            active_commitment_refs=(COMMITMENT,),
        )

        self.assertEqual(
            result.receipt.issues[0].code,
            SemanticGeometryLifecycleIssueCode.PREDECESSOR_PROGRAM_MISMATCH,
        )
        self.assertIsNone(result.geometry_program)
