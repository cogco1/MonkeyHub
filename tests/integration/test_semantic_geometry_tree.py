from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

from monkeyarch.compilers.geometry import (
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state.spatial import NEUTRAL_SEMANTIC_KIND, ComponentMaturity, DesignComponent, SpatialProposalError, compile_component_transition
from archflow.state.spatial import ConstraintResponseStatus, MassingVolume, SchematicOption, SiteBounds, SpatialConstraintResponse, SpatialGridBasis, SpatialLevel, SpatialOptionProposal, SpatialZone
from archflow.state.geometry_program import SemanticBinding
from tests.integration.test_geometry_compiler import COMMITMENT, _proposal, _state


# The option these tests deepen, as ArchFlow's own suite builds it
# (packages/archflow/tests/test_design_portfolio.py). A package's suite is not
# importable from the repository suite, so this copy is the tree tests' own.
EVIDENCE = "project://portfolio-project/input/research.json"
REQUIREMENT_A = "commitment:public-access"
REQUIREMENT_B = "obligation:protected-site"


def _option(
    option_id: str,
    *,
    requirement_refs: tuple[str, ...] = (
        REQUIREMENT_A,
        REQUIREMENT_B,
    ),
    evidence_ref: str = EVIDENCE,
    shape: int = 0,
) -> SchematicOption:
    level = SpatialLevel(
        level_id="ground",
        base_y=0,
        height=4,
        source_refs=(evidence_ref,),
    )
    volume = MassingVolume(
        volume_id="primary",
        bounds=SiteBounds(
            minimum=(shape, 0, 0),
            maximum=(shape + 1, 3, 1),
        ),
        level_ids=("ground",),
        source_refs=(evidence_ref,),
    )
    zone = SpatialZone(
        zone_id="main",
        program_node_refs=("program-node:main",),
        level_ids=("ground",),
        volume_ids=("primary",),
        source_refs=(evidence_ref,),
    )
    responses = tuple(
        SpatialConstraintResponse(
            response_id=f"response-{index}",
            constraint_ref=ref,
            status=ConstraintResponseStatus.SATISFIED,
            rationale=f"Option {option_id} addresses {ref}.",
            source_refs=(evidence_ref,),
        )
        for index, ref in enumerate(requirement_refs, start=1)
    )
    proposal = SpatialOptionProposal(
        option_id=option_id,
        label=f"Project-authored option {option_id}",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(
            horizontal_area_per_cell=1.0,
            area_unit="project_grid_unit",
            source_refs=(evidence_ref,),
        ),
        footprint_cells=((shape, 0), (shape + 1, 0)),
        levels=(level,),
        volumes=(volume,),
        zones=(zone,),
        components=(
            DesignComponent(
                component_id="building",
                parent_component_id=None,
                semantic_kind="building",
                intent="Own the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=("primary",),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
            DesignComponent(
                component_id="primary-support",
                parent_component_id="building",
                semantic_kind="structural-support",
                intent="Carry the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
            DesignComponent(
                component_id="primary-surface",
                parent_component_id="building",
                semantic_kind="enclosure-surface",
                intent="Resolve the selected schematic envelope.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
        ),
        connections=(),
        constraint_responses=responses,
        typology_hypothesis=f"Project hypothesis {option_id}",
        palette_refs=(),
        rationale=f"Architect rationale for {option_id}.",
        responds_to_refs=requirement_refs,
        expert_advice_refs=(),
        evidence_refs=(evidence_ref,),
    )
    signature = hashlib.sha256(
        f"{option_id}:{shape}".encode("utf-8")
    ).hexdigest()
    return SchematicOption(
        proposal=proposal,
        footprint_area=2.0,
        topology_signature=signature,
    )


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
        root = Path(__file__).resolve().parents[2] / "packages" / "archflow" / "src" / "archflow"
        paths = tuple(root.rglob("*.py"))
        # A moved kernel would leave nothing to read and the scan would pass.
        self.assertIn(root / "__init__.py", paths)
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
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

    def test_an_unclassified_component_gains_meaning_but_a_stated_one_keeps_it(
        self,
    ) -> None:
        """Neutral -> enclosure is enrichment; enclosure -> column is a change of meaning (#408)."""

        coarse = _coarse_proposal()

        def with_dome(proposal, **changes):
            return replace(proposal, components=tuple(
                replace(item, **changes) if item.component_id == "dome" else item
                for item in proposal.components
            ))

        neutral = with_dome(coarse, semantic_kind=NEUTRAL_SEMANTIC_KIND)
        dome = next(item for item in neutral.components if item.component_id == "dome")
        enclosure = with_dome(neutral, semantic_kind="enclosure", revision=dome.revision + 1)
        receipt = compile_component_transition(neutral, enclosure)
        self.assertIn("dome", receipt.changed_component_ids)
        with self.assertRaisesRegex(SpatialProposalError, "changed meaning"):
            compile_component_transition(
                enclosure,
                with_dome(enclosure, semantic_kind="column", revision=dome.revision + 2),
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
