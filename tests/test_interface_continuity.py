"""Narrow-phase interface continuity regression tests."""

from __future__ import annotations

import math
import unittest

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.interface_continuity import (
    InterfaceBoundarySegment,
    InterfaceBoundarySupportSet,
    InterfaceContinuityError,
    check_interface_boundary_continuity,
)


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="interface-fixture",
            run_id="run-001",
            base=ProjectVersionRef("interface-fixture", 0, "a" * 64),
        ),
        branch_id="candidate",
        epoch=4,
    )


def segment(ref: str, start, end) -> InterfaceBoundarySegment:
    return InterfaceBoundarySegment(ref, tuple(start), tuple(end))


class InterfaceBoundaryContinuityTest(unittest.TestCase):
    def check(self, required, supports, support_refs_by_required, *, tolerance=0.02):
        required = tuple(sorted(required, key=lambda item: item.segment_ref))
        supports = tuple(sorted(supports, key=lambda item: item.segment_ref))
        support_sets = tuple(
            InterfaceBoundarySupportSet(required_ref, tuple(sorted(support_refs)))
            for required_ref, support_refs in sorted(support_refs_by_required.items())
        )
        return check_interface_boundary_continuity(
            check_id="roof-eave-continuity",
            branch=branch(),
            scope_digest="b" * 64,
            required_segments=required,
            supporting_segments=supports,
            support_sets=support_sets,
            expected_required_refs=tuple(item.segment_ref for item in required),
            tolerance=tolerance,
            evidence_refs=("evidence:roof-detail",),
            authority_refs=("authority:stage-profile",),
        )

    def test_parallel_interlock_is_continuous_and_round_trips(self):
        required = segment("interface:roof-eave", (0, 0.0, 0), (0, 0.0, 4))
        support = segment(
            "interface:entablature-top",
            (0, 0.015, 0),
            (0, 0.015, 4),
        )
        receipt = self.check(
            [required],
            [support],
            {required.segment_ref: (support.segment_ref,)},
        )
        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertEqual(
            receipt.to_dict(),
            CheckReceiptEnvelope.from_dict(receipt.to_dict()).to_dict(),
        )

    def test_wedge_gap_fails_after_a_true_endpoint_contact(self):
        required = segment(
            "interface:roof-eave",
            (0, 0.0, 0),
            (0, 0.58, 4),
        )
        support = segment(
            "interface:entablature-top",
            (0, 0.0, 0),
            (0, 0.0, 4),
        )
        receipt = self.check(
            [required],
            [support],
            {required.segment_ref: (support.segment_ref,)},
        )
        self.assertIs(CheckStatus.FAIL, receipt.status)
        self.assertEqual((), receipt.covered_refs)
        self.assertEqual(
            "interface-boundary-discontinuous",
            receipt.findings[0].code,
        )
        uncovered = next(
            item.value
            for item in receipt.measurements
            if item.name == "largest_uncovered_length"
        )
        self.assertGreater(uncovered, 3.8)

    def test_short_supports_at_old_sample_points_cannot_false_pass(self):
        required = segment("interface:roof-eave", (0, 0, 0), (0, 0, 4))
        supports = [
            segment(
                f"interface:support-{index:02d}",
                (0, 0, index * 0.5 - 0.001),
                (0, 0, index * 0.5 + 0.001),
            )
            for index in range(9)
        ]
        receipt = self.check(
            [required],
            supports,
            {required.segment_ref: tuple(item.segment_ref for item in supports)},
        )
        self.assertIs(CheckStatus.FAIL, receipt.status)

    def test_support_segments_can_stitch_but_an_internal_gap_fails(self):
        required = segment("interface:roof-eave", (0, 0, 0), (0, 0, 4))
        first = segment("interface:support-a", (0, 0, 0), (0, 0, 2))
        second = segment("interface:support-b", (0, 0, 2), (0, 0, 4))
        joined = self.check(
            [required],
            [first, second],
            {required.segment_ref: (first.segment_ref, second.segment_ref)},
        )
        self.assertIs(CheckStatus.PASS, joined.status)

        gapped_second = segment(
            "interface:support-b-gap",
            (0, 0, 2.06),
            (0, 0, 4),
        )
        gapped = self.check(
            [required],
            [first, gapped_second],
            {
                required.segment_ref: (
                    first.segment_ref,
                    gapped_second.segment_ref,
                )
            },
        )
        self.assertIs(CheckStatus.FAIL, gapped.status)

    def test_tolerance_boundary_is_inclusive(self):
        required = segment("interface:roof-eave", (0, 0, 0), (0, 0, 4))
        exact = segment("interface:support-exact", (0, 0.02, 0), (0, 0.02, 4))
        beyond = segment(
            "interface:support-beyond",
            (0, 0.020001, 0),
            (0, 0.020001, 4),
        )
        self.assertIs(
            CheckStatus.PASS,
            self.check(
                [required],
                [exact],
                {required.segment_ref: (exact.segment_ref,)},
            ).status,
        )
        self.assertIs(
            CheckStatus.FAIL,
            self.check(
                [required],
                [beyond],
                {required.segment_ref: (beyond.segment_ref,)},
            ).status,
        )

    def test_missing_support_is_unknown(self):
        required = segment("interface:roof-eave", (0, 0, 0), (0, 0, 4))
        receipt = self.check(
            [required],
            [],
            {required.segment_ref: ()},
        )
        self.assertIs(CheckStatus.UNKNOWN, receipt.status)
        self.assertEqual("interface-support-unavailable", receipt.findings[0].code)

    def test_denominator_pairing_and_numeric_edges_are_rejected(self):
        with self.assertRaises(InterfaceContinuityError):
            segment("interface:point", (0, 0, 0), (0, 0, 0))
        with self.assertRaises(ValueError):
            segment("interface:nan", (math.nan, 0, 0), (0, 0, 1))

        required = segment("interface:roof-eave", (0, 0, 0), (0, 0, 4))
        support = segment("interface:support", (0, 0, 0), (0, 0, 4))
        with self.assertRaises(InterfaceContinuityError):
            check_interface_boundary_continuity(
                check_id="roof-eave-continuity",
                branch=branch(),
                scope_digest="b" * 64,
                required_segments=(required,),
                supporting_segments=(support,),
                support_sets=(
                    InterfaceBoundarySupportSet(required.segment_ref, ()),
                ),
                expected_required_refs=(required.segment_ref,),
                tolerance=0.02,
                evidence_refs=("evidence:roof-detail",),
                authority_refs=("authority:stage-profile",),
            )
        with self.assertRaises(InterfaceContinuityError):
            InterfaceBoundarySupportSet(
                required.segment_ref,
                (required.segment_ref,),
            )
        for tolerance in (0.0, -0.1, math.nan, math.inf):
            with self.assertRaises((ValueError, InterfaceContinuityError)):
                self.check(
                    [required],
                    [support],
                    {required.segment_ref: (support.segment_ref,)},
                    tolerance=tolerance,
                )

    def test_multiple_required_segments_keep_exact_coverage_denominator(self):
        first = segment("interface:roof-a", (0, 0, 0), (0, 0, 2))
        second = segment("interface:roof-b", (1, 0, 0), (1, 0, 2))
        support_a = segment("interface:support-a", (0, 0, 0), (0, 0, 2))
        support_b = segment("interface:support-b", (1, 0, 0), (1, 0, 2))
        receipt = self.check(
            [first, second],
            [support_a, support_b],
            {
                first.segment_ref: (support_a.segment_ref,),
                second.segment_ref: (support_b.segment_ref,),
            },
        )
        self.assertIs(CheckStatus.PASS, receipt.status)
        self.assertEqual(
            tuple(sorted((first.segment_ref, second.segment_ref))),
            receipt.coverage_denominator,
        )


if __name__ == "__main__":
    unittest.main()
