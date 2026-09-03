"""P079: decision-universe closure, sufficiency, and frontier tests."""

import unittest
from dataclasses import replace

from archive.archflow.capabilities import evidence_sufficiency as legacy_evidence_sufficiency
from archive.archflow.evidence import sufficiency as canonical_evidence_sufficiency
from archive.archflow.evidence.sufficiency import (
    ClosureStatus,
    ConflictDisposition,
    DecisionEdge,
    DecisionNode,
    DecisionUniverseRevision,
    DiscoverySweepReceipt,
    EpistemicRole,
    EvidenceClaimBinding,
    EvidenceModality,
    EvidenceRule,
    EvidenceSufficiencyError,
    EvidenceSufficiencyPolicy,
    ExpansionDisposition,
    ExpansionKind,
    FrontierStatus,
    GapReason,
    NumericUncertainty,
    ResolutionMode,
    ResolutionRecord,
    SufficiencyStatus,
    UniverseExpansionCandidate,
    build_research_frontier,
    compile_decision_universe_closure,
    compile_evidence_sufficiency,
)


SHA = "a" * 64


def node(ref: str, kind: str = "project:decision") -> DecisionNode:
    return DecisionNode(ref, kind)


def universe(*refs: str, edges=()) -> DecisionUniverseRevision:
    return DecisionUniverseRevision(
        universe_id="project-universe",
        revision_id="revision-001",
        scope_digest=SHA,
        ontology_ref="project:ontology/revision-001",
        nodes=tuple(node(ref) for ref in refs),
        edges=tuple(edges),
        seed_refs=tuple(refs),
    )


def rule(
    obligation: str,
    target: str,
    *,
    families: int = 1,
    qualifiers=(),
    modes=(ResolutionMode.RETRIEVE,),
    maximum_uncertainty_width=None,
    unit=None,
    datum=None,
) -> EvidenceRule:
    return EvidenceRule(
        obligation_id=obligation,
        target_ref=target,
        allowed_modes=tuple(modes),
        minimum_source_families=families,
        required_qualifiers=tuple(qualifiers),
        maximum_uncertainty_width=maximum_uncertainty_width,
        uncertainty_unit_ref=unit,
        uncertainty_datum_ref=datum,
    )


def policy(*rules, lenses=(), waves=0, conflict_authorities=()) -> EvidenceSufficiencyPolicy:
    return EvidenceSufficiencyPolicy(
        policy_id="project-policy",
        rules=tuple(rules),
        required_discovery_lenses=tuple(lenses),
        required_no_novelty_waves=waves,
        allowed_conflict_authority_refs=tuple(conflict_authorities),
    )


def claim(
    binding_id: str,
    obligation: str,
    target: str,
    *,
    family: str,
    source: str | None = None,
    claim_key: str | None = None,
    position: str = "position:accepted",
    qualifiers=(),
    uncertainty=None,
    modality=EvidenceModality.TEXT_FACT,
    epistemic_role=EpistemicRole.OBSERVATION,
    region_ref=None,
    model_ref=None,
    authority_ref=None,
    quantified_annotation=False,
) -> EvidenceClaimBinding:
    return EvidenceClaimBinding(
        binding_id=binding_id,
        obligation_id=obligation,
        target_ref=target,
        fact_ref=f"fact:{binding_id}",
        source_ref=source or f"source:{binding_id}",
        source_family_ref=family,
        claim_key=claim_key or f"claim:{target}",
        position_key=position,
        modality=modality,
        epistemic_role=epistemic_role,
        region_ref=region_ref,
        model_ref=model_ref,
        authority_ref=authority_ref,
        quantified_annotation=quantified_annotation,
        qualifiers=tuple(qualifiers),
        uncertainty=uncertainty,
    )


def resolution(target: str, mode=ResolutionMode.DERIVE) -> ResolutionRecord:
    return ResolutionRecord(
        resolution_id=f"resolution-{target.replace(':', '-')}",
        target_ref=target,
        mode=mode,
        refs=(f"project:receipt/{target.replace(':', '-')}",),
    )


class EvidenceSufficiencyOwnershipTest(unittest.TestCase):
    def test_legacy_import_is_thin_canonical_facade(self):
        for name in legacy_evidence_sufficiency.__all__:
            self.assertIs(
                getattr(legacy_evidence_sufficiency, name),
                getattr(canonical_evidence_sufficiency, name),
                name,
            )

    def test_canonical_digest_regression_is_fixed(self):
        u = universe("decision:a")
        p = policy(rule("obligation-a", "decision:a"))
        claims = (
            claim(
                "a-1",
                "obligation-a",
                "decision:a",
                family="family:one",
            ),
        )
        closure = compile_decision_universe_closure(u, p)
        receipt = compile_evidence_sufficiency(u, p, claims=claims)
        frontier = build_research_frontier(closure, receipt)

        self.assertEqual(
            "84cc60d937c3b4e4f3e10bcf67ba1c9152edb87f53a07a4abe5a17d9a9a4cfd7",
            u.universe_digest,
        )
        self.assertEqual(
            "41dae0869fbd67e8b277c65886d5163e72f0ff24cef240f885b8d0b8bd5a4642",
            p.policy_digest,
        )
        self.assertEqual(
            "bae6ac5c15bd67f3519059387823a570e4b88d56c267c0cafde16c8a58c2bb43",
            closure.closure_digest,
        )
        self.assertEqual(
            "165b86180be970bafaa1fe2c4a19e933d2c280e7d774bf8ab9c1725f6f75c6eb",
            receipt.receipt_digest,
        )
        self.assertEqual(
            "b69d74a52fc3b746b18c66012decb78e80668bdd2e3f7f50c2132e5d35305d17",
            frontier.frontier_digest,
        )


class PresenceIsNotSufficiencyTest(unittest.TestCase):
    def test_zero_presence_gaps_can_still_be_insufficient(self):
        u = universe("decision:a", "decision:b")
        p = policy(
            rule("obligation-a", "decision:a"),
            rule("obligation-b", "decision:b", families=2),
        )
        claims = (
            claim("a-1", "obligation-a", "decision:a", family="family:one"),
            claim("b-1", "obligation-b", "decision:b", family="family:shared"),
            claim(
                "b-2",
                "obligation-b",
                "decision:b",
                family="family:shared",
                source="source:mirror",
            ),
        )
        closure = compile_decision_universe_closure(u, p)
        receipt = compile_evidence_sufficiency(u, p, claims=claims)

        self.assertEqual((), receipt.missing_basis_target_refs)
        self.assertEqual(
            ("decision:a", "decision:b"), receipt.basis_present_target_refs
        )
        self.assertIs(receipt.status, SufficiencyStatus.INSUFFICIENT)
        self.assertIn(
            GapReason.INSUFFICIENT_SOURCE_FAMILIES,
            {item.reason for item in receipt.gaps},
        )
        self.assertIs(
            build_research_frontier(closure, receipt).status,
            FrontierStatus.CONTINUE,
        )


class UniverseClosureTest(unittest.TestCase):
    def test_pending_and_unapplied_expansions_block_closure(self):
        u = universe("decision:seed")
        p = policy(rule("seed-basis", "decision:seed"))
        pending = UniverseExpansionCandidate(
            candidate_id="candidate-new-node",
            kind=ExpansionKind.NODE,
            proposed_ref="decision:new-node",
            predecessor_universe_digest=u.universe_digest,
            scope_digest=u.scope_digest,
            evidence_refs=("project:evidence/new-node",),
        )
        closure = compile_decision_universe_closure(u, p, expansions=(pending,))
        self.assertIs(closure.status, ClosureStatus.OPEN)
        self.assertIn(
            GapReason.PENDING_UNIVERSE_EXPANSION,
            {item.reason for item in closure.gaps},
        )
        receipt = compile_evidence_sufficiency(
            u,
            p,
            claims=(claim("seed", "seed-basis", "decision:seed", family="family:seed"),),
        )
        self.assertIs(
            build_research_frontier(closure, receipt).status,
            FrontierStatus.UNIVERSE_EXPANSION_REQUIRED,
        )

        adopted = replace(
            pending,
            disposition=ExpansionDisposition.ADOPTED,
            disposition_ref="project:decision/adopt-new-node",
        )
        closure = compile_decision_universe_closure(u, p, expansions=(adopted,))
        self.assertIn(
            GapReason.ADOPTED_EXPANSION_NOT_APPLIED,
            {item.reason for item in closure.gaps},
        )

    def test_foreign_or_stale_expansion_is_rejected(self):
        u = universe("decision:seed")
        p = policy(rule("seed", "decision:seed"))
        foreign = UniverseExpansionCandidate(
            candidate_id="foreign-candidate",
            kind=ExpansionKind.NODE,
            proposed_ref="decision:foreign",
            predecessor_universe_digest="b" * 64,
            scope_digest=u.scope_digest,
            evidence_refs=("project:evidence/foreign",),
        )
        with self.assertRaisesRegex(EvidenceSufficiencyError, "predecessor"):
            compile_decision_universe_closure(u, p, expansions=(foreign,))
        foreign_scope = replace(
            foreign,
            predecessor_universe_digest=u.universe_digest,
            scope_digest="c" * 64,
        )
        with self.assertRaisesRegex(EvidenceSufficiencyError, "scope"):
            compile_decision_universe_closure(u, p, expansions=(foreign_scope,))

    def test_required_edge_gap_is_typed_and_resolution_closes_it(self):
        edge = DecisionEdge(
            "edge:a-to-b", "decision:a", "decision:b", "project:constrains"
        )
        u = universe("decision:a", "decision:b", edges=(edge,))
        p = policy(
            rule("a", "decision:a"),
            rule("b", "decision:b"),
            rule("a-to-b", "edge:a-to-b", modes=(ResolutionMode.DERIVE,)),
        )
        open_receipt = compile_decision_universe_closure(u, p)
        self.assertEqual(
            [GapReason.REQUIRED_EDGE_UNRESOLVED],
            [item.reason for item in open_receipt.gaps],
        )
        closed = compile_decision_universe_closure(
            u, p, resolutions=(resolution("edge:a-to-b"),)
        )
        self.assertIs(closed.status, ClosureStatus.CLOSED)

    def test_saturation_requires_consecutive_complete_no_novelty_waves(self):
        u = universe("decision:seed")
        p = policy(
            rule("seed", "decision:seed"),
            lenses=("lens:one", "lens:two"),
            waves=2,
        )
        rejected = UniverseExpansionCandidate(
            candidate_id="candidate-reviewed",
            kind=ExpansionKind.NODE,
            proposed_ref="decision:not-adopted",
            predecessor_universe_digest=u.universe_digest,
            scope_digest=u.scope_digest,
            evidence_refs=("project:evidence/candidate",),
            disposition=ExpansionDisposition.REJECTED,
            disposition_ref="project:decision/reject-candidate",
        )
        wave_1 = tuple(
            DiscoverySweepReceipt(1, lens, True, ("candidate-reviewed",))
            for lens in p.required_discovery_lenses
        )
        wave_2 = tuple(
            DiscoverySweepReceipt(2, lens, True)
            for lens in p.required_discovery_lenses
        )
        first = compile_decision_universe_closure(
            u, p, expansions=(rejected,), sweeps=wave_1 + wave_2
        )
        self.assertEqual(1, first.no_novelty_streak)
        self.assertIs(first.status, ClosureStatus.OPEN)

        wave_3 = tuple(
            DiscoverySweepReceipt(3, lens, True)
            for lens in p.required_discovery_lenses
        )
        second = compile_decision_universe_closure(
            u, p, expansions=(rejected,), sweeps=wave_1 + wave_2 + wave_3
        )
        self.assertEqual(2, second.no_novelty_streak)
        self.assertIs(second.status, ClosureStatus.CLOSED)


class ConflictAndUncertaintyTest(unittest.TestCase):
    def test_conflict_requires_explicit_authority_disposition(self):
        u = universe("decision:value")
        p = policy(
            rule("value", "decision:value"),
            conflict_authorities=("project:authority/owner",),
        )
        claims = (
            claim(
                "value-a", "value", "decision:value",
                family="family:a", claim_key="claim:value", position="position:a",
            ),
            claim(
                "value-b", "value", "decision:value",
                family="family:b", claim_key="claim:value", position="position:b",
            ),
        )
        unresolved = compile_evidence_sufficiency(u, p, claims=claims)
        self.assertIn(
            GapReason.UNRESOLVED_CONFLICT,
            {item.reason for item in unresolved.gaps},
        )
        closure = compile_decision_universe_closure(u, p)
        self.assertIs(
            build_research_frontier(closure, unresolved).status,
            FrontierStatus.HUMAN_REVIEW_REQUIRED,
        )
        pending = UniverseExpansionCandidate(
            candidate_id="candidate-during-conflict",
            kind=ExpansionKind.NODE,
            proposed_ref="decision:new-target",
            predecessor_universe_digest=u.universe_digest,
            scope_digest=u.scope_digest,
            evidence_refs=("project:evidence/new-target",),
        )
        expansion_closure = compile_decision_universe_closure(
            u, p, expansions=(pending,)
        )
        self.assertIs(
            build_research_frontier(expansion_closure, unresolved).status,
            FrontierStatus.HUMAN_REVIEW_REQUIRED,
        )
        disposition = ConflictDisposition(
            claim_key="claim:value",
            accepted_position_keys=("position:a",),
            authority_ref="project:authority/owner",
            evidence_refs=("project:decision/value-conflict",),
        )
        resolved = compile_evidence_sufficiency(
            u, p, claims=claims, conflict_dispositions=(disposition,)
        )
        self.assertIs(resolved.status, SufficiencyStatus.SUFFICIENT)

        foreign = replace(disposition, authority_ref="project:authority/foreign")
        with self.assertRaisesRegex(EvidenceSufficiencyError, "not allowed"):
            compile_evidence_sufficiency(
                u, p, claims=claims, conflict_dispositions=(foreign,)
            )

    def test_typed_uncertainty_is_compared_only_with_same_unit_and_datum(self):
        u = universe("decision:quantity")
        p = policy(
            rule(
                "quantity",
                "decision:quantity",
                modes=(ResolutionMode.RETRIEVE, ResolutionMode.MEASURE),
                maximum_uncertainty_width=0.2,
                unit="unit:m",
                datum="datum:clear",
            )
        )
        inside = claim(
            "quantity-inside",
            "quantity",
            "decision:quantity",
            family="family:survey",
            uncertainty=NumericUncertainty(3.0, 3.1, "unit:m", "datum:clear"),
        )
        self.assertIs(
            compile_evidence_sufficiency(u, p, claims=(inside,)).status,
            SufficiencyStatus.SUFFICIENT,
        )
        outside = replace(
            inside,
            binding_id="quantity-outside",
            fact_ref="fact:quantity-outside",
            uncertainty=NumericUncertainty(3.0, 3.4, "unit:m", "datum:clear"),
        )
        receipt = compile_evidence_sufficiency(u, p, claims=(outside,))
        self.assertIn(
            GapReason.UNCERTAINTY_EXCEEDS_TOLERANCE,
            {item.reason for item in receipt.gaps},
        )
        self.assertIs(
            next(item for item in receipt.gaps if item.reason is GapReason.UNCERTAINTY_EXCEEDS_TOLERANCE).route,
            ResolutionMode.MEASURE,
        )


class ModalityNeutralTest(unittest.TestCase):
    def test_one_visual_region_cannot_close_a_two_family_topology_obligation(self):
        u = universe("decision:row-topology")
        p = policy(rule("row-topology", "decision:row-topology", families=2))
        primary = claim(
            "primary-observation",
            "row-topology",
            "decision:row-topology",
            family="image-family:primary",
            claim_key="claim:row-topology",
            position="position:one-row",
            modality=EvidenceModality.VISUAL_REGION,
            epistemic_role=EpistemicRole.OBSERVATION,
            region_ref="region:primary/repeats",
            model_ref="model:vision-001",
        )
        first = compile_evidence_sufficiency(u, p, claims=(primary,))
        self.assertIn(
            GapReason.INSUFFICIENT_SOURCE_FAMILIES,
            {item.reason for item in first.gaps},
        )

        independent = replace(
            primary,
            binding_id="independent-observation",
            fact_ref="fact:independent-observation",
            source_ref="source:independent-view",
            source_family_ref="image-family:independent",
            region_ref="region:independent/rows",
        )
        second = compile_evidence_sufficiency(
            u, p, claims=(primary, independent)
        )
        self.assertIs(second.status, SufficiencyStatus.SUFFICIENT)

    def test_designer_drawing_role_is_explicit_not_inferred(self):
        base = dict(
            binding_id="drawing-mark",
            obligation_id="layout",
            target_ref="decision:layout",
            fact_ref="fact:drawing-mark",
            source_ref="source:designer-sketch",
            source_family_ref="family:designer-sketch",
            claim_key="claim:layout",
            position_key="position:relative-layout",
            modality=EvidenceModality.DRAWING_OBSERVATION,
            region_ref="region:sketch/mark-1",
            model_ref="model:annotation-reader",
        )
        with self.assertRaises(EvidenceSufficiencyError):
            EvidenceClaimBinding(
                **base,
                epistemic_role=EpistemicRole.OBSERVATION,
            )
        hypothesis = EvidenceClaimBinding(
            **base,
            epistemic_role=EpistemicRole.HYPOTHESIS,
        )
        self.assertIs(hypothesis.epistemic_role, EpistemicRole.HYPOTHESIS)
        declaration = EvidenceClaimBinding(
            **{**base, "binding_id": "dimensioned-annotation", "fact_ref": "fact:dimensioned"},
            epistemic_role=EpistemicRole.AUTHOR_DECLARATION,
            authority_ref="project:authority/designer",
            quantified_annotation=True,
        )
        self.assertTrue(declaration.quantified_annotation)


class HistoricalAndModernSyntheticTest(unittest.TestCase):
    def test_reconstruction_like_policy_uses_observations_without_framework_terms(self):
        u = universe("decision:repeat-topology", "decision:measured-proportion")
        p = policy(
            rule("topology", "decision:repeat-topology", families=2),
            rule(
                "proportion",
                "decision:measured-proportion",
                qualifiers=("qualifier:datum", "qualifier:unit"),
            ),
        )
        claims = (
            claim("topology-a", "topology", "decision:repeat-topology", family="family:a"),
            claim("topology-b", "topology", "decision:repeat-topology", family="family:b"),
            claim(
                "proportion-a",
                "proportion",
                "decision:measured-proportion",
                family="family:survey",
                qualifiers=("qualifier:datum", "qualifier:unit"),
                modality=EvidenceModality.MEASUREMENT,
            ),
        )
        receipt = compile_evidence_sufficiency(u, p, claims=claims)
        self.assertIs(receipt.status, SufficiencyStatus.SUFFICIENT)

    def test_contemporary_like_policy_accepts_derivation_but_not_stale_regulation(self):
        edge = DecisionEdge(
            "edge:layout-to-path",
            "decision:derived-layout",
            "decision:regulated-path",
            "project:interfaces-with",
        )
        u = universe(
            "decision:derived-layout", "decision:regulated-path", edges=(edge,)
        )
        p = policy(
            rule("layout", "decision:derived-layout", modes=(ResolutionMode.DERIVE,)),
            rule(
                "path",
                "decision:regulated-path",
                qualifiers=("qualifier:jurisdiction", "qualifier:effective-date"),
            ),
            rule("interface", "edge:layout-to-path", modes=(ResolutionMode.DERIVE,)),
        )
        resolutions = (
            resolution("decision:derived-layout"),
            resolution("edge:layout-to-path"),
        )
        stale = claim(
            "regulated-path",
            "path",
            "decision:regulated-path",
            family="family:authority",
            qualifiers=("qualifier:jurisdiction",),
        )
        failed = compile_evidence_sufficiency(
            u, p, claims=(stale,), resolutions=resolutions
        )
        self.assertIn(
            GapReason.MISSING_QUALIFIER,
            {item.reason for item in failed.gaps},
        )
        current = replace(
            stale,
            qualifiers=("qualifier:jurisdiction", "qualifier:effective-date"),
        )
        passed = compile_evidence_sufficiency(
            u, p, claims=(current,), resolutions=resolutions
        )
        closure = compile_decision_universe_closure(
            u, p, resolutions=(resolution("edge:layout-to-path"),)
        )
        self.assertIs(passed.status, SufficiencyStatus.SUFFICIENT)
        self.assertIs(
            build_research_frontier(closure, passed).status,
            FrontierStatus.COMPLETE,
        )

    def test_compilation_is_order_independent(self):
        u = universe("decision:a")
        p = policy(rule("a", "decision:a", families=2))
        claims = (
            claim("a-1", "a", "decision:a", family="family:one"),
            claim("a-2", "a", "decision:a", family="family:two"),
        )
        first = compile_evidence_sufficiency(u, p, claims=claims)
        second = compile_evidence_sufficiency(u, p, claims=tuple(reversed(claims)))
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
