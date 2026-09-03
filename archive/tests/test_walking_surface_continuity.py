"""Tests for project-neutral walking-surface continuity validation."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.walking_surface_continuity import (
    WalkingSurfaceContinuityProfile,
    WalkingSurfaceCriteria,
    WalkingSurfaceEdge,
    WalkingSurfaceEdgeKind,
    WalkingSurfaceNode,
    WalkingSurfaceNodeRole,
    WalkingSurfacePathRequirement,
    check_walking_surface_continuity,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="walking-surface-fixture",
            run_id="run-001",
            base=ProjectVersionRef("walking-surface-fixture", 2, SHA_A),
        ),
        branch_id="candidate-a",
        epoch=4,
    )


def node(
    name: str,
    role: WalkingSurfaceNodeRole,
    datum: float | None,
) -> WalkingSurfaceNode:
    return WalkingSurfaceNode(
        node_ref=f"surface:{name}",
        role=role,
        datum=datum,
        evidence_refs=(f"evidence:datum-{name}",),
    )


def edge(
    name: str,
    from_name: str,
    to_name: str,
    kind: WalkingSurfaceEdgeKind,
    *,
    horizontal_run: float | None = None,
) -> WalkingSurfaceEdge:
    return WalkingSurfaceEdge(
        edge_ref=f"edge:{name}",
        from_node_ref=f"surface:{from_name}",
        to_node_ref=f"surface:{to_name}",
        kind=kind,
        horizontal_run=horizontal_run,
        evidence_refs=(f"evidence:edge-{name}",),
    )


def passing_profile() -> WalkingSurfaceContinuityProfile:
    return WalkingSurfaceContinuityProfile(
        profile_id="generic-entry-route",
        nodes=(
            node("outside", WalkingSurfaceNodeRole.EXTERIOR, 10.0),
            node("walk", WalkingSurfaceNodeRole.TRANSITION, 10.01),
            node("step", WalkingSurfaceNodeRole.TRANSITION, 10.11),
            node("ramp", WalkingSurfaceNodeRole.TRANSITION, 10.31),
            node("door", WalkingSurfaceNodeRole.TRANSITION, 10.32),
            node("inside", WalkingSurfaceNodeRole.INTERIOR, 10.32),
        ),
        edges=(
            edge("walk", "outside", "walk", WalkingSurfaceEdgeKind.CONTINUOUS),
            edge("step", "walk", "step", WalkingSurfaceEdgeKind.STEP),
            edge(
                "ramp",
                "step",
                "ramp",
                WalkingSurfaceEdgeKind.RAMP,
                horizontal_run=2.0,
            ),
            edge("threshold", "ramp", "door", WalkingSurfaceEdgeKind.THRESHOLD),
            edge("inside", "door", "inside", WalkingSurfaceEdgeKind.CONTINUOUS),
        ),
        paths=(
            WalkingSurfacePathRequirement(
                path_id="outside-to-inside",
                node_refs=(
                    "surface:outside",
                    "surface:walk",
                    "surface:step",
                    "surface:ramp",
                    "surface:door",
                    "surface:inside",
                ),
                evidence_refs=("evidence:route-declaration",),
            ),
        ),
        criteria=WalkingSurfaceCriteria(
            max_continuous_delta=0.02,
            max_step_rise=0.15,
            max_ramp_slope=0.12,
            max_threshold_rise=0.025,
        ),
        length_unit_ref="unit:metre",
    )


def check(profile: WalkingSurfaceContinuityProfile) -> CheckReceiptEnvelope:
    return check_walking_surface_continuity(
        profile,
        branch=branch(),
        scope_digest=SHA_A,
        stage_subject_digest=SHA_B,
    )


class WalkingSurfaceContinuityTests(unittest.TestCase):
    def test_complete_exterior_to_interior_path_passes(self) -> None:
        profile = passing_profile()

        receipt = check(profile)

        self.assertEqual(receipt.status, CheckStatus.PASS)
        self.assertEqual(receipt.coverage_denominator, profile.path_refs)
        self.assertEqual(
            receipt.coverage_denominator,
            profile.checker_requirement_refs,
        )
        self.assertEqual(receipt.covered_refs, profile.path_refs)
        self.assertEqual(receipt.findings, ())
        self.assertEqual(receipt.checker_id, "walking-surface-continuity-checker")
        self.assertEqual(CheckReceiptEnvelope.from_dict(receipt.to_dict()), receipt)
        self.assertEqual(
            WalkingSurfaceContinuityProfile.from_dict(profile.to_dict()),
            profile,
        )
        self.assertIn("ramp_slope", {item.name for item in receipt.measurements})

    def test_project_supplied_limit_exceedance_fails(self) -> None:
        profile = passing_profile()
        profile = replace(
            profile,
            criteria=replace(profile.criteria, max_threshold_rise=0.005),
        )

        receipt = check(profile)

        self.assertEqual(receipt.status, CheckStatus.FAIL)
        self.assertEqual(receipt.covered_refs, profile.path_refs)
        self.assertIn(
            "walking-surface-limit-exceeded",
            {item.code for item in receipt.findings},
        )

    def test_missing_segment_is_unknown_and_not_covered(self) -> None:
        profile = passing_profile()
        profile = replace(
            profile,
            edges=tuple(item for item in profile.edges if item.edge_ref != "edge:ramp"),
        )

        receipt = check(profile)

        self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
        self.assertEqual(receipt.covered_refs, ())
        self.assertIn(
            "walking-surface-segment-missing",
            {item.code for item in receipt.findings},
        )

    def test_unknown_datum_criterion_and_ramp_run_fail_closed(self) -> None:
        base = passing_profile()
        cases = {
            "datum": replace(
                base,
                nodes=tuple(
                    replace(item, datum=None)
                    if item.node_ref == "surface:step"
                    else item
                    for item in base.nodes
                ),
            ),
            "criterion": replace(
                base,
                criteria=replace(base.criteria, max_step_rise=None),
            ),
            "ramp-run": replace(
                base,
                edges=tuple(
                    replace(item, horizontal_run=None)
                    if item.edge_ref == "edge:ramp"
                    else item
                    for item in base.edges
                ),
            ),
        }

        for label, profile in cases.items():
            with self.subTest(label=label):
                receipt = check(profile)
                self.assertEqual(receipt.status, CheckStatus.UNKNOWN)
                self.assertEqual(receipt.covered_refs, ())

    def test_path_endpoints_must_be_exterior_then_interior(self) -> None:
        profile = passing_profile()
        profile = replace(
            profile,
            nodes=tuple(
                replace(item, role=WalkingSurfaceNodeRole.TRANSITION)
                if item.node_ref == "surface:inside"
                else item
                for item in profile.nodes
            ),
        )

        receipt = check(profile)

        self.assertEqual(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "walking-surface-endpoint-role-mismatch",
            {item.code for item in receipt.findings},
        )


if __name__ == "__main__":
    unittest.main()
