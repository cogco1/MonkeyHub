"""P068: stage declaration gates, commitment compilation, basis wiring."""

import unittest

from archflow.capabilities.declaration import (
    DeclarationError,
    DeclarationField,
    DeclarationKind,
    DeclarationQuadrant,
    GeometryCheck,
    StageDeclarationContract,
    compile_declaration_commitments,
    select_decision_basis,
    validate_stage_declarations,
)
from archflow.state.commitments import CommitmentStrength


def field(field_id, quadrant, check=GeometryCheck.NONE, minimum=0.0,
          maximum=100.0, kind=DeclarationKind.NUMBER):
    return DeclarationField(
        field_id=field_id,
        quadrant=quadrant,
        kind=kind,
        unit="m",
        minimum=minimum,
        maximum=maximum,
        geometry_check=check,
        source_refs=(f"adoption://{field_id}",),
        statement=f"declared {field_id}",
    )


def contract():
    return StageDeclarationContract(
        stage="schematic",
        fields=(
            field("footprint-width-m", DeclarationQuadrant.DIMENSIONS,
                  GeometryCheck.FOOTPRINT_WIDTH, 10.0, 60.0),
            field("orientation-axis", DeclarationQuadrant.SITE,
                  GeometryCheck.NONE, 0.0, 359.0),
            field("overall-height-m", DeclarationQuadrant.DIMENSIONS,
                  GeometryCheck.OVERALL_HEIGHT, 5.0, 60.0),
            field("ring-count", DeclarationQuadrant.DETAIL,
                  GeometryCheck.NONE, 1.0, 9.0, DeclarationKind.COUNT),
        ),
        tolerance_ratio=0.05,
    )


def proposal(width=40.0, height=30.0):
    return {
        "volumes": [
            {
                "bounds": {
                    "minimum": [0.0, 0.0, 0.0],
                    "maximum": [width, height, 50.0],
                }
            }
        ],
        "grid_basis": {"horizontal_area_per_cell": 1.0},
        "footprint_cells": [[x, 0, z] for x in range(40) for z in range(50)],
    }


DECLARED = {
    "footprint-width-m": 40.0,
    "orientation-axis": 0.0,
    "overall-height-m": 30.0,
    "ring-count": 5,
}


class ContractValidationTest(unittest.TestCase):
    def test_site_quadrant_exists(self):
        self.assertEqual("site", DeclarationQuadrant.SITE.value)

    def test_complete_in_range_holding_declarations_pass(self):
        derived = validate_stage_declarations(
            contract(), DECLARED, proposal()
        )
        self.assertAlmostEqual(40.0, derived["footprint_width"])

    def test_missing_and_extra_fields_are_typed_rejections(self):
        short = dict(DECLARED)
        del short["orientation-axis"]
        with self.assertRaises(DeclarationError):
            validate_stage_declarations(contract(), short, proposal())
        extra = dict(DECLARED, surprise=1.0)
        with self.assertRaises(DeclarationError):
            validate_stage_declarations(contract(), extra, proposal())

    def test_out_of_range_is_rejected(self):
        bad = dict(DECLARED, **{"overall-height-m": 200.0})
        with self.assertRaises(DeclarationError):
            validate_stage_declarations(contract(), bad, proposal())

    def test_declaration_must_hold_the_geometry(self):
        with self.assertRaises(DeclarationError) as ctx:
            validate_stage_declarations(
                contract(), DECLARED, proposal(width=12.0)
            )
        self.assertIn("does not hold the geometry", str(ctx.exception))

    def test_count_declarations_must_be_integers(self):
        bad = dict(DECLARED, **{"ring-count": 4.5})
        with self.assertRaises(DeclarationError):
            validate_stage_declarations(contract(), bad, proposal())


class CommitmentCompilationTest(unittest.TestCase):
    def test_irreversible_quadrants_compile_to_hard_commitments(self):
        commitments = compile_declaration_commitments(
            contract(),
            DECLARED,
            quadrants=(DeclarationQuadrant.SITE,
                       DeclarationQuadrant.DIMENSIONS),
            gate_receipt_ref="project://p/runs/r/records/gate-1",
            authority_id="authority.user",
            authorized_by="authority.user",
            source_event_ref="event://gate-pass",
            criterion_provider_id="validator.declaration-gate",
        )
        ids = [item.commitment_id for item in commitments]
        self.assertEqual(
            [
                "declared-footprint-width-m",
                "declared-orientation-axis",
                "declared-overall-height-m",
            ],
            ids,
        )
        for item in commitments:
            self.assertIs(CommitmentStrength.HARD, item.strength)
            self.assertIn(
                "project://p/runs/r/records/gate-1", item.evidence_refs
            )
        width = commitments[0]
        self.assertIn("adoption://footprint-width-m", width.evidence_refs)
        self.assertEqual(
            "footprint-width-m", width.satisfaction_criterion.criterion_id
        )

    def test_reversible_quadrants_stay_out(self):
        commitments = compile_declaration_commitments(
            contract(),
            DECLARED,
            quadrants=(DeclarationQuadrant.SITE,
                       DeclarationQuadrant.DIMENSIONS),
            gate_receipt_ref="ref://gate",
            authority_id="authority.user",
            authorized_by="authority.user",
            source_event_ref="event://gate",
            criterion_provider_id="validator.declaration-gate",
        )
        self.assertNotIn(
            "declared-ring-count",
            [item.commitment_id for item in commitments],
        )

    def test_cannot_commit_undeclared_or_out_of_range(self):
        with self.assertRaises(DeclarationError):
            compile_declaration_commitments(
                contract(),
                {},
                quadrants=(DeclarationQuadrant.SITE,),
                gate_receipt_ref="ref://gate",
                authority_id="authority.user",
                authorized_by="authority.user",
                source_event_ref="event://gate",
                criterion_provider_id="validator.declaration-gate",
            )
        with self.assertRaises(DeclarationError):
            compile_declaration_commitments(
                contract(),
                dict(DECLARED, **{"orientation-axis": 999.0}),
                quadrants=(DeclarationQuadrant.SITE,),
                gate_receipt_ref="ref://gate",
                authority_id="authority.user",
                authorized_by="authority.user",
                source_event_ref="event://gate",
                criterion_provider_id="validator.declaration-gate",
            )


class DecisionBasisSelectionTest(unittest.TestCase):
    SHARDS = {
        "declaration:orientation-axis": {
            "facts": [
                {"fact_id": "axial-symmetry", "statement": "s",
                 "strength": "hard", "quote": "q",
                 "snapshot_ref": "ref://snap", "extra": "dropped"},
            ]
        },
        "declaration:colonnade-bay-spacing": {
            "facts": [
                {"fact_id": "bay-canon", "statement": "s2",
                 "strength": "soft", "quote": "q2",
                 "snapshot_ref": "ref://snap"},
            ]
        },
        "declaration:footprint-width-m": {"facts": []},
    }

    def test_selects_exactly_the_contract_fields(self):
        payload, metrics = select_decision_basis(contract(), self.SHARDS)
        self.assertEqual(["declaration:orientation-axis"], list(payload))
        row = payload["declaration:orientation-axis"][0]
        self.assertEqual("axial-symmetry", row["fact_id"])
        self.assertNotIn("extra", row)

    def test_metrics_report_the_bounding(self):
        payload, metrics = select_decision_basis(contract(), self.SHARDS)
        self.assertEqual(1, metrics["facts_selected"])
        self.assertEqual(2, metrics["facts_total"])
        self.assertLess(metrics["chars_selected"], metrics["chars_total"])

    def test_prompt_guard_bounds_and_shapes(self):
        from archive.archflow.capabilities.semantic_spatial_authoring import (
            _validated_decision_basis,
        )

        payload, _ = select_decision_basis(contract(), self.SHARDS)
        validated = _validated_decision_basis(payload)
        self.assertIn("declaration:orientation-axis", validated)
        with self.assertRaises(ValueError):
            _validated_decision_basis({"declaration:x": []})
        with self.assertRaises(ValueError):
            _validated_decision_basis(
                {"declaration:x": [{"statement": "y" * 30_000}]}
            )


if __name__ == "__main__":
    unittest.main()
