"""Targeted tests for generic deterministic assembly relationship checks."""

from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.validation.assembly import (
    AssemblyCoverageManifest,
    AssemblyObligationDisposition,
    AssemblyProfile,
    AssemblyRelationCandidate,
    AssemblySubject,
    AssemblySubjectObligation,
    AssemblyValidationError,
    GeometryBoundsBasis,
    RelationCandidateDisposition,
    RelationshipKind,
    RelationshipRequirement,
    check_assembly,
)
from archive.archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archive.archflow.validation.spatial import AABB


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def branch() -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="assembly-fixture",
            run_id="run-001",
            base=ProjectVersionRef("assembly-fixture", 3, SHA_A),
        ),
        branch_id="selected",
        epoch=2,
    )


def box(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> AABB:
    return AABB(minimum=minimum, maximum=maximum)


def requirement(
    requirement_id: str,
    kind: RelationshipKind,
    subject_refs: tuple[str, ...],
    *,
    maximum_overlap_volume: float | None = None,
) -> RelationshipRequirement:
    return RelationshipRequirement(
        requirement_id=requirement_id,
        kind=kind,
        subject_refs=subject_refs,
        evidence_refs=(f"evidence:{requirement_id}",),
        authority_refs=("authority:assembly-review",),
        maximum_overlap_volume=maximum_overlap_volume,
    )


def obligation(
    obligation_id: str,
    role_id: str,
    subject_ref: str,
    *,
    kind: RelationshipKind | None,
    endpoint_index: int | None,
    requirement_id: str | None,
    disposition: AssemblyObligationDisposition = (
        AssemblyObligationDisposition.REQUIRED
    ),
    authority_refs: tuple[str, ...] = ("authority:assembly-coverage",),
) -> AssemblySubjectObligation:
    return AssemblySubjectObligation(
        obligation_id=obligation_id,
        role_id=role_id,
        subject_ref=subject_ref,
        disposition=disposition,
        relationship_kind=kind,
        endpoint_index=endpoint_index,
        requirement_id=requirement_id,
        evidence_refs=(f"evidence:{obligation_id}",),
        authority_refs=authority_refs,
    )


def passing_profile(
    *,
    reverse: bool = False,
    stage_subject_source_digest: str = SHA_C,
) -> AssemblyProfile:
    subjects = (
        AssemblySubject(
            "component:foundation",
            box((0.0, 0.0, 0.0), (4.0, 4.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:foundation",
        ),
        AssemblySubject(
            "component:column",
            box((1.0, 1.0, 1.0), (2.0, 2.0, 4.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:column",
        ),
        AssemblySubject(
            "component:beam",
            box((0.5, 0.5, 4.0), (2.5, 2.5, 5.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:beam",
        ),
        AssemblySubject(
            "component:panel-a",
            box((5.0, 0.0, 0.0), (6.0, 1.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:panel-a",
        ),
        AssemblySubject(
            "component:panel-b",
            box((6.0, 0.0, 0.0), (7.0, 1.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:panel-b",
        ),
        AssemblySubject(
            "component:embedded",
            box((10.2, 0.2, 1.0), (10.4, 0.8, 2.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:embedded",
        ),
        AssemblySubject(
            "component:host",
            box((10.0, 0.0, 0.0), (11.0, 1.0, 3.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:host",
        ),
        AssemblySubject(
            "clear-region:door",
            box((12.0, 0.0, 0.0), (13.0, 1.0, 2.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:door-clear-region",
        ),
        AssemblySubject(
            "component:door-obstruction",
            box((13.1, 0.0, 0.0), (14.0, 1.0, 2.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:door-obstruction",
        ),
    )
    requirements = (
        requirement(
            "beam-on-column",
            RelationshipKind.SUPPORT,
            ("component:beam", "component:column"),
        ),
        requirement(
            "column-on-foundation",
            RelationshipKind.SUPPORT,
            ("component:column", "component:foundation"),
        ),
        requirement(
            "panel-joint",
            RelationshipKind.TOUCH,
            ("component:panel-b", "component:panel-a"),
        ),
        requirement(
            "beam-host-separation",
            RelationshipKind.FORBIDDEN_OVERLAP,
            ("component:beam", "component:host"),
        ),
        requirement(
            "embedded-host-overlap",
            RelationshipKind.BOUNDED_EMBEDDED_OVERLAP,
            ("component:embedded", "component:host"),
            maximum_overlap_volume=0.13,
        ),
        requirement(
            "embedded-in-host",
            RelationshipKind.HOST_CONTAINMENT,
            ("component:embedded", "component:host"),
        ),
        requirement(
            "door-opening-clear",
            RelationshipKind.OPENING_CLEAR,
            ("clear-region:door", "component:door-obstruction"),
        ),
        requirement(
            "beam-support-chain",
            RelationshipKind.VERTICAL_SUPPORT_CHAIN,
            (
                "component:beam",
                "component:column",
                "component:foundation",
            ),
        ),
        requirement(
            "beam-load-path",
            RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            ("component:beam", "component:foundation"),
        ),
    )
    obligations = (
        obligation(
            "foundation-support-target",
            "support-target",
            "component:foundation",
            kind=RelationshipKind.SUPPORT,
            endpoint_index=1,
            requirement_id="column-on-foundation",
        ),
        obligation(
            "column-support-source",
            "support-source",
            "component:column",
            kind=RelationshipKind.SUPPORT,
            endpoint_index=0,
            requirement_id="column-on-foundation",
        ),
        obligation(
            "beam-transfer-source",
            "transfer-source",
            "component:beam",
            kind=RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            endpoint_index=0,
            requirement_id="beam-load-path",
        ),
        obligation(
            "panel-a-contact",
            "contact-endpoint-a",
            "component:panel-a",
            kind=RelationshipKind.TOUCH,
            endpoint_index=0,
            requirement_id="panel-joint",
        ),
        obligation(
            "panel-b-contact",
            "contact-endpoint-b",
            "component:panel-b",
            kind=RelationshipKind.TOUCH,
            endpoint_index=1,
            requirement_id="panel-joint",
        ),
        obligation(
            "embedded-host-source",
            "hosted-source",
            "component:embedded",
            kind=RelationshipKind.HOST_CONTAINMENT,
            endpoint_index=0,
            requirement_id="embedded-in-host",
        ),
        obligation(
            "host-target",
            "host-target",
            "component:host",
            kind=RelationshipKind.HOST_CONTAINMENT,
            endpoint_index=1,
            requirement_id="embedded-in-host",
        ),
        obligation(
            "clear-region-source",
            "clearance-source",
            "clear-region:door",
            kind=RelationshipKind.OPENING_CLEAR,
            endpoint_index=0,
            requirement_id="door-opening-clear",
        ),
        obligation(
            "clearance-target",
            "clearance-target",
            "component:door-obstruction",
            kind=RelationshipKind.OPENING_CLEAR,
            endpoint_index=1,
            requirement_id="door-opening-clear",
        ),
    )
    manifest = AssemblyCoverageManifest(
        manifest_id="generic-frame-coverage",
        stage_subject_refs=tuple(
            sorted(item.subject_ref for item in subjects)
        ),
        stage_subject_source_digest=stage_subject_source_digest,
        obligations=obligations,
    )
    if reverse:
        subjects = tuple(reversed(subjects))
        requirements = tuple(reversed(requirements))
    return AssemblyProfile(
        profile_id="generic-frame",
        subjects=subjects,
        requirements=requirements,
        coverage_manifest=manifest,
        length_unit="m",
        vertical_axis="z",
        linear_tolerance=1.0e-6,
        volume_tolerance=1.0e-9,
    )


def replace_subject(
    profile: AssemblyProfile,
    subject_ref: str,
    bounds: AABB | None,
) -> AssemblyProfile:
    return replace(
        profile,
        subjects=tuple(
            replace(
                item,
                bounds=bounds,
                bounds_basis=(
                    None
                    if bounds is None
                    else item.bounds_basis
                ),
            )
            if item.subject_ref == subject_ref
            else item
            for item in profile.subjects
        ),
    )


def add_subject_obligation(
    profile: AssemblyProfile,
    subject: AssemblySubject,
    subject_obligation: AssemblySubjectObligation,
) -> AssemblyProfile:
    manifest = replace(
        profile.coverage_manifest,
        stage_subject_refs=tuple(
            sorted((*profile.coverage_manifest.stage_subject_refs, subject.subject_ref))
        ),
        obligations=(
            *profile.coverage_manifest.obligations,
            subject_obligation,
        ),
    )
    return replace(
        profile,
        subjects=(*profile.subjects, subject),
        coverage_manifest=manifest,
    )


def check(profile: AssemblyProfile) -> CheckReceiptEnvelope:
    return check_assembly(
        profile,
        branch=branch(),
        scope_digest=SHA_B,
        stage_subject_digest=SHA_C,
    )


class AssemblyValidationTests(unittest.TestCase):
    def test_complete_assembly_passes_exact_endpoint_denominator(self) -> None:
        profile = passing_profile()
        receipt = check(profile)

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertEqual(profile.check_denominator, receipt.coverage_denominator)
        self.assertTrue(
            set(profile.endpoint_denominator) < set(profile.check_denominator)
        )
        self.assertTrue(set(profile.subject_refs) < set(profile.check_denominator))
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)
        self.assertEqual(SHA_C, receipt.subject_digest)
        self.assertIn(profile.ref, receipt.coverage_denominator)
        profile_measurement = next(
            item
            for item in receipt.measurements
            if item.measurement_id == "assembly-profile-digest"
        )
        self.assertEqual(profile.profile_digest, profile_measurement.value)
        self.assertEqual(
            {item.kind for item in profile.requirements},
            set(RelationshipKind),
        )
        payload = receipt.to_dict()
        self.assertEqual("CheckReceiptEnvelope@1", payload["schema"])
        self.assertFalse(payload["design_authority"])
        self.assertFalse(payload["geometry_mutation_authority"])
        self.assertFalse(payload["promotion_authority"])
        self.assertFalse(payload["canonical_write_authority"])
        self.assertEqual(receipt, CheckReceiptEnvelope.from_dict(payload))

    def test_profile_contract_roundtrip_and_digest_are_canonical(self) -> None:
        profile = passing_profile()
        payload = profile.to_dict()
        restored = AssemblyProfile.from_dict(payload)

        self.assertEqual("AssemblyProfile@2", payload["schema"])
        self.assertEqual(profile, restored)
        self.assertEqual(profile.profile_digest, restored.profile_digest)
        self.assertEqual(profile.profile_digest, canonical_digest(payload))
        self.assertEqual(
            "RelationshipRequirement@1",
            payload["requirements"][0]["schema"],
        )
        self.assertEqual("AssemblySubject@1", payload["subjects"][0]["schema"])
        self.assertEqual(
            "AssemblyCoverageManifest@1",
            payload["coverage_manifest"]["schema"],
        )

    def test_second_clearance_subject_cannot_be_omitted(self) -> None:
        profile = passing_profile()
        subject = AssemblySubject(
            "clear-region:secondary",
            box((20.0, 0.0, 0.0), (21.0, 1.0, 2.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:secondary-clear-region",
        )
        incomplete = add_subject_obligation(
            profile,
            subject,
            obligation(
                "secondary-clearance-source",
                "clearance-source",
                subject.subject_ref,
                kind=RelationshipKind.OPENING_CLEAR,
                endpoint_index=0,
                requirement_id="secondary-opening-clear",
            ),
        )

        receipt = check(incomplete)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "assembly-obligation-uncovered",
            {item.code for item in receipt.findings},
        )
        self.assertNotEqual(receipt.coverage_denominator, receipt.covered_refs)

    def test_manifest_must_bind_the_exact_stage_subject_source(self) -> None:
        profile = passing_profile()
        stale = replace(
            profile,
            coverage_manifest=replace(
                profile.coverage_manifest,
                stage_subject_source_digest="d" * 64,
            ),
        )

        receipt = check(stale)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "assembly-subject-source-mismatch",
            {item.code for item in receipt.findings},
        )

    def test_second_transfer_subject_cannot_omit_its_load_path(self) -> None:
        profile = passing_profile()
        subject = AssemblySubject(
            "component:secondary-transfer",
            box((3.0, 1.0, 1.0), (4.0, 2.0, 4.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:secondary-transfer",
        )
        incomplete = add_subject_obligation(
            profile,
            subject,
            obligation(
                "secondary-transfer-source",
                "transfer-source",
                subject.subject_ref,
                kind=RelationshipKind.LOAD_PATH_TO_FOUNDATION,
                endpoint_index=0,
                requirement_id="secondary-load-path",
            ),
        )

        receipt = check(incomplete)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "assembly-obligation-uncovered",
            {item.code for item in receipt.findings},
        )

    def test_unresolved_relation_candidate_fails_closed(self) -> None:
        profile = passing_profile()
        candidate = AssemblyRelationCandidate(
            candidate_id="panel-contact-candidate",
            subject_refs=("component:panel-a", "component:panel-b"),
            disposition=RelationCandidateDisposition.UNRESOLVED,
            requirement_id=None,
            evidence_refs=("evidence:relation-discovery",),
        )
        incomplete = replace(
            profile,
            coverage_manifest=replace(
                profile.coverage_manifest,
                relation_candidates=(candidate,),
            ),
        )

        receipt = check(incomplete)

        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn(
            "assembly-relation-candidate-unresolved",
            {item.code for item in receipt.findings},
        )

        mapped = replace(
            candidate,
            disposition=RelationCandidateDisposition.REQUIREMENT,
            requirement_id="panel-joint",
        )
        complete = replace(
            profile,
            coverage_manifest=replace(
                profile.coverage_manifest,
                relation_candidates=(mapped,),
            ),
        )
        mapped_receipt = check(complete)
        self.assertIs(mapped_receipt.status, CheckStatus.PASS)
        self.assertIn(mapped.ref, mapped_receipt.covered_refs)

    def test_not_applicable_requires_authority_and_is_a_legal_path(self) -> None:
        subject = AssemblySubject(
            "component:independent",
            box((30.0, 0.0, 0.0), (31.0, 1.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:independent",
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            obligation(
                "independent-applicability",
                "relationship-applicability",
                subject.subject_ref,
                kind=None,
                endpoint_index=None,
                requirement_id=None,
                disposition=AssemblyObligationDisposition.NOT_APPLICABLE,
                authority_refs=(),
            )

        explicit = obligation(
            "independent-applicability",
            "relationship-applicability",
            subject.subject_ref,
            kind=None,
            endpoint_index=None,
            requirement_id=None,
            disposition=AssemblyObligationDisposition.NOT_APPLICABLE,
        )
        complete = add_subject_obligation(
            passing_profile(),
            subject,
            explicit,
        )

        receipt = check(complete)

        self.assertIs(receipt.status, CheckStatus.PASS)
        self.assertIn(explicit.ref, receipt.covered_refs)
        self.assertEqual(receipt.coverage_denominator, receipt.covered_refs)

    def test_requirement_and_subject_input_order_do_not_change_receipt(self) -> None:
        ordinary = passing_profile()
        reversed_input = passing_profile(reverse=True)

        self.assertEqual(ordinary.to_dict(), reversed_input.to_dict())
        self.assertEqual(check(ordinary), check(reversed_input))
        self.assertEqual(check(ordinary).receipt_digest, check(reversed_input).receipt_digest)

    def test_conservative_envelopes_never_fake_narrow_phase_contact(self) -> None:
        profile = passing_profile()
        conservative_touch = replace(
            profile,
            subjects=tuple(
                replace(
                    item,
                    bounds_basis=GeometryBoundsBasis.CONSERVATIVE_ENVELOPE,
                )
                if item.subject_ref == "component:panel-b"
                else item
                for item in profile.subjects
            ),
        )
        touch_receipt = check(conservative_touch)
        self.assertIs(touch_receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "touch-narrow-phase-required",
            {item.code for item in touch_receipt.findings},
        )

        overlapping_host = replace_subject(
            profile,
            "component:host",
            box((1.5, 1.5, 4.2), (3.0, 3.0, 5.2)),
        )
        conservative_overlap = replace(
            overlapping_host,
            subjects=tuple(
                replace(
                    item,
                    bounds_basis=GeometryBoundsBasis.CONSERVATIVE_ENVELOPE,
                )
                if item.subject_ref == "component:host"
                else item
                for item in overlapping_host.subjects
            ),
        )
        overlap_receipt = check(conservative_overlap)
        self.assertIs(overlap_receipt.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "overlap-narrow-phase-required",
            {item.code for item in overlap_receipt.findings},
        )

        with self.assertRaisesRegex(
            AssemblyValidationError,
            "explicit GeometryBoundsBasis",
        ):
            AssemblySubject(
                "component:unsafe-default",
                box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
                None,
            )

    def test_touch_overlap_embedding_host_and_opening_fail_from_geometry(self) -> None:
        cases = (
            (
                "component:panel-b",
                box((6.2, 0.0, 0.0), (7.2, 1.0, 1.0)),
                "touch-requirement-unsatisfied",
            ),
            (
                "component:host",
                box((1.5, 1.5, 4.2), (3.0, 3.0, 5.2)),
                "forbidden-overlap-detected",
            ),
            (
                "component:embedded",
                box((10.0, 0.0, 0.0), (11.0, 1.0, 3.0)),
                "embedded-overlap-out-of-bounds",
            ),
            (
                "component:host",
                box((10.3, 0.3, 1.1), (10.5, 0.7, 1.9)),
                "host-containment-unsatisfied",
            ),
            (
                "component:door-obstruction",
                box((12.5, 0.0, 0.0), (13.5, 1.0, 2.0)),
                "opening-obstructed",
            ),
        )
        for subject_ref, bounds, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                receipt = check(replace_subject(passing_profile(), subject_ref, bounds))
                self.assertIs(receipt.status, CheckStatus.FAIL)
                self.assertIn(expected_code, {item.code for item in receipt.findings})

    def test_broken_chain_wrong_foundation_and_floating_member_fail_closed(self) -> None:
        profile = passing_profile()
        without_edge = replace(
            profile,
            requirements=tuple(
                item
                for item in profile.requirements
                if item.requirement_id != "beam-on-column"
            ),
        )
        broken = check(without_edge)
        self.assertIs(broken.status, CheckStatus.FAIL)
        self.assertTrue(
            {
                "vertical-support-chain-broken",
                "load-path-disconnected",
            }
            <= {item.code for item in broken.findings}
        )

        floating_profile = replace_subject(
            profile,
            "component:beam",
            box((0.5, 0.5, 4.25), (2.5, 2.5, 5.25)),
        )
        floating = check(floating_profile)
        self.assertIs(floating.status, CheckStatus.FAIL)
        self.assertTrue(
            {
                "support-requirement-unsatisfied",
                "vertical-support-chain-invalid",
                "load-path-invalid",
            }
            <= {item.code for item in floating.findings}
        )

        decoy = AssemblySubject(
            "component:foundation-decoy",
            box((30.0, 30.0, 0.0), (34.0, 34.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
            "geometry:foundation-decoy",
        )
        wrong_foundation = replace(
            profile,
            subjects=(*profile.subjects, decoy),
            requirements=tuple(
                replace(
                    item,
                    subject_refs=("component:beam", decoy.subject_ref),
                )
                if item.requirement_id == "beam-load-path"
                else item
                for item in profile.requirements
            ),
        )
        receipt = check(wrong_foundation)
        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn("load-path-disconnected", {item.code for item in receipt.findings})

    def test_missing_geometry_unknown_unit_and_unknown_axis_never_pass(self) -> None:
        missing = check(
            replace_subject(passing_profile(), "component:beam", None)
        )
        self.assertIs(missing.status, CheckStatus.UNKNOWN)
        self.assertIn("assembly-geometry-missing", {item.code for item in missing.findings})
        self.assertNotEqual(missing.coverage_denominator, missing.covered_refs)

        unknown_unit = check(replace(passing_profile(), length_unit="cubit"))
        self.assertIs(unknown_unit.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "assembly-length-unit-unknown",
            {item.code for item in unknown_unit.findings},
        )
        self.assertNotEqual(
            unknown_unit.coverage_denominator,
            unknown_unit.covered_refs,
        )

        unknown_axis = check(replace(passing_profile(), vertical_axis="up"))
        self.assertIs(unknown_axis.status, CheckStatus.UNKNOWN)
        self.assertIn(
            "assembly-vertical-axis-unknown",
            {item.code for item in unknown_axis.findings},
        )
        self.assertNotEqual(
            unknown_axis.coverage_denominator,
            unknown_axis.covered_refs,
        )

    def test_caller_boolean_and_name_like_endpoints_cannot_authorize_pass(self) -> None:
        profile = passing_profile()
        with self.assertRaises(TypeError):
            check_assembly(
                profile,
                branch=branch(),
                scope_digest=SHA_B,
                stage_subject_digest=SHA_C,
                caller_passed=True,
            )

        payload = check(profile).to_dict()
        payload["caller_passed"] = True
        with self.assertRaisesRegex(ValueError, "schema drifted"):
            CheckReceiptEnvelope.from_dict(payload)

        named_like_foundation = AssemblySubject(
            "component:foundation-by-name-only",
            box((40.0, 40.0, 0.0), (41.0, 41.0, 1.0)),
            GeometryBoundsBasis.EXACT_AXIS_ALIGNED_SOLID,
        )
        name_probe = replace(
            profile,
            subjects=(*profile.subjects, named_like_foundation),
            requirements=tuple(
                replace(
                    item,
                    subject_refs=("component:beam", named_like_foundation.subject_ref),
                )
                if item.requirement_id == "beam-load-path"
                else item
                for item in profile.requirements
            ),
        )
        receipt = check(name_probe)
        self.assertIs(receipt.status, CheckStatus.FAIL)
        self.assertIn("load-path-disconnected", {item.code for item in receipt.findings})

    def test_every_requirement_requires_exact_evidence_and_authority_refs(self) -> None:
        exact = requirement(
            "exact-endpoints",
            RelationshipKind.TOUCH,
            ("component:a", "component:b"),
        )
        changed_endpoint = replace(
            exact,
            subject_refs=("component:a", "component:c"),
        )
        self.assertNotEqual(exact.ref, changed_endpoint.ref)

        with self.assertRaises(ValueError):
            RelationshipRequirement(
                requirement_id="missing-evidence",
                kind=RelationshipKind.TOUCH,
                subject_refs=("component:a", "component:b"),
                evidence_refs=(),
                authority_refs=("authority:review",),
            )
        with self.assertRaises(ValueError):
            RelationshipRequirement(
                requirement_id="missing-authority",
                kind=RelationshipKind.TOUCH,
                subject_refs=("component:a", "component:b"),
                evidence_refs=("evidence:joint",),
                authority_refs=(),
            )
        with self.assertRaisesRegex(
            AssemblyValidationError,
            "exact endpoint",
        ):
            requirement(
                "wildcard-endpoint",
                RelationshipKind.TOUCH,
                ("component:a-*", "component:b"),
            )


if __name__ == "__main__":
    unittest.main()
