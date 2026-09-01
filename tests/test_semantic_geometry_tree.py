from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from archflow.compilers.geometry import (
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state import (
    ComponentMaturity,
    DesignComponent,
    SpatialProposalError,
    compile_component_transition,
)
from archflow.state.geometry_program import SemanticBinding
from tests.test_design_portfolio import EVIDENCE, _option
from tests.test_geometry_compiler import COMMITMENT, _proposal, _state


def _component(
    component_id: str,
    parent: str,
    semantic_kind: str,
    *,
    maturity: ComponentMaturity = ComponentMaturity.MASSING,
    revision: int = 0,
    unresolved: tuple[str, ...] = (),
) -> DesignComponent:
    return DesignComponent(
        component_id=component_id,
        parent_component_id=parent,
        semantic_kind=semantic_kind,
        intent=f"Project-authored intent for {component_id}.",
        maturity=maturity,
        revision=revision,
        volume_ids=(),
        unresolved_child_roles=unresolved,
        source_refs=(EVIDENCE,),
    )


def _coarse_proposal():
    proposal = _option("branch-a").proposal
    building = next(
        item for item in proposal.components if item.component_id == "building"
    )
    return replace(
        proposal,
        components=tuple(
            sorted(
                (
                    building,
                    _component(
                        "dome",
                        "building",
                        "dome",
                        unresolved=("oculus", "shell"),
                    ),
                    _component(
                        "portico",
                        "building",
                        "portico",
                        unresolved=("column-system",),
                    ),
                ),
                key=lambda item: item.component_id,
            )
        ),
    )


class SemanticGeometryTreeTests(unittest.TestCase):
    def test_removed_projection_and_p026_bypass_have_no_production_route(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1] / "archflow"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*.py")
        )
        for forbidden in (
            "CandidateProgramProjection",
            "candidate_value_ids",
            "candidate_value_refs",
            "semantic_components",
            "runtime.sandbox_gold",
        ):
            self.assertNotIn(forbidden, source)

    def test_same_component_identity_deepens_without_invalidating_sibling(
        self,
    ) -> None:
        coarse = _coarse_proposal()
        dome = next(item for item in coarse.components if item.component_id == "dome")
        developed = replace(
            coarse,
            components=tuple(
                sorted(
                    (
                        *(
                            item
                            for item in coarse.components
                            if item.component_id != "dome"
                        ),
                        replace(
                            dome,
                            maturity=ComponentMaturity.SCHEMATIC,
                            revision=1,
                            unresolved_child_roles=(),
                        ),
                        _component("dome-shell", "dome", "shell"),
                        _component("oculus", "dome", "oculus"),
                    ),
                    key=lambda item: item.component_id,
                )
            ),
        )
        first = compile_component_transition(coarse, developed)

        self.assertEqual(
            first.changed_component_ids,
            ("dome", "dome-shell", "oculus"),
        )
        self.assertIn("portico", first.preserved_component_ids)
        self.assertNotIn("portico", first.invalidated_component_ids)

        shell = next(
            item for item in developed.components
            if item.component_id == "dome-shell"
        )
        detailed = replace(
            developed,
            components=tuple(
                sorted(
                    (
                        *(
                            item
                            for item in developed.components
                            if item.component_id != "dome-shell"
                        ),
                        replace(
                            shell,
                            maturity=ComponentMaturity.DEVELOPED,
                            revision=1,
                        ),
                        _component(
                            "dome-coffers",
                            "dome-shell",
                            "coffer-system",
                        ),
                    ),
                    key=lambda item: item.component_id,
                )
            ),
        )
        second = compile_component_transition(developed, detailed)

        self.assertEqual(
            second.invalidated_component_ids,
            ("dome-coffers", "dome-shell"),
        )
        self.assertIn("dome", second.preserved_component_ids)
        self.assertIn("oculus", second.preserved_component_ids)
        self.assertIn("portico", second.preserved_component_ids)

    def test_identity_change_revision_skip_and_silent_removal_fail_closed(
        self,
    ) -> None:
        coarse = _coarse_proposal()
        dome = next(item for item in coarse.components if item.component_id == "dome")
        with self.assertRaisesRegex(SpatialProposalError, "changed meaning"):
            compile_component_transition(
                coarse,
                replace(
                    coarse,
                    components=tuple(
                        replace(item, semantic_kind="tower")
                        if item.component_id == "dome"
                        else item
                        for item in coarse.components
                    ),
                ),
            )
        with self.assertRaisesRegex(SpatialProposalError, r"revision \+1"):
            compile_component_transition(
                coarse,
                replace(
                    coarse,
                    components=tuple(
                        replace(item, intent="Changed intent", revision=2)
                        if item.component_id == "dome"
                        else item
                        for item in coarse.components
                    ),
                ),
            )
        with self.assertRaisesRegex(SpatialProposalError, "retirement"):
            compile_component_transition(
                coarse,
                replace(
                    coarse,
                    components=tuple(
                        item
                        for item in coarse.components
                        if item.component_id != "portico"
                    ),
                ),
            )

    def test_geometry_objects_require_one_semantic_component_owner(self) -> None:
        state = _state()
        proposal = _proposal(state)
        original = proposal.semantic_bindings[0]
        ambiguous = replace(
            proposal,
            semantic_bindings=(
                original,
                SemanticBinding(
                    binding_id="support-binding",
                    component_id="primary-support",
                    object_ids=(original.object_ids[0],),
                    commitment_refs=(COMMITMENT,),
                    evidence_refs=(EVIDENCE,),
                ),
            ),
        )
        ambiguous_result = compile_geometry_program(
            state,
            ambiguous,
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIn(
            GeometryIssueCode.AMBIGUOUS_OBJECT_OWNER,
            {item.code for item in ambiguous_result.receipt.issues},
        )

        unowned_id = original.object_ids[0]
        unowned = replace(
            proposal,
            semantic_bindings=(
                replace(
                    original,
                    object_ids=tuple(
                        item for item in original.object_ids if item != unowned_id
                    ),
                ),
            ),
        )
        unowned_result = compile_geometry_program(
            state,
            unowned,
            active_commitment_refs=(COMMITMENT,),
        )
        self.assertIn(
            GeometryIssueCode.UNOWNED_OBJECT,
            {item.code for item in unowned_result.receipt.issues},
        )


if __name__ == "__main__":
    unittest.main()
