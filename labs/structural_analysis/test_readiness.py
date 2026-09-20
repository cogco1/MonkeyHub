"""Observable boundaries of the progressive structural-analysis experiment."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from archflow.contracts.canonical import CanonicalValueError
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import (
    StateRecordError, apply_state_record_operator, compile_component_edit,
)
from labs.structural_analysis.analysis import AnalysisUnavailable, compile_analysis, readiness
from labs.structural_analysis.fixture import (
    MEMBERS, SOURCE, commitment_review, enrich, geometry_projection, initial_record,
    revise_facet,
)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = FilesystemProjectRepository.initialize(
            Path(temporary.name) / "project", project_id="structural-demo", initial_state={},
        )
        self.run = self.repository.create_run("enrichment")
        self.records = [initial_record().bound_to(self.run)]
        for milestone in (2, 3, 4):
            previous = self.records[-1]
            self.records.append(apply_state_record_operator(previous, enrich(previous, milestone)))
        self.complete = self.records[-1]

    def check(self, record, members=MEMBERS):
        return readiness(record, members, expected_run=self.run, expected_digest=record.digest)

    def project(self, record, members=MEMBERS):
        return compile_analysis(record, members, expected_run=self.run, expected_digest=record.digest)

    def revised_structural(self, edit, member_id="beam-0"):
        structural = deepcopy(self.complete.entity(member_id).fields["structural"])
        edit(structural)
        return apply_state_record_operator(self.complete, revise_facet(
            self.complete, member_id, "structural", structural, "explicit-test-revision",
        ))

    def assertUnavailable(self, record, field, status="unavailable", entity_id="beam-0"):
        check = self.check(record)
        self.assertFalse(check.ready)
        self.assertTrue(any(
            finding.entity_id == entity_id and finding.field == field and finding.status == status
            for finding in check.findings
        ), check.findings)
        with self.assertRaises(AnalysisUnavailable):
            self.project(record)

    def test_progressive_enrichment_preserves_entities_and_producer_geometry(self):
        original = self.records[0]
        geometry = geometry_projection(original)
        self.assertEqual(set(geometry), {*MEMBERS, "roof"})
        for milestone, record in enumerate(self.records, start=1):
            with self.subTest(milestone=milestone):
                self.assertEqual(geometry_projection(record), geometry)
                for entity_id in (*MEMBERS, "roof"):
                    before, after = original.entity(entity_id), record.entity(entity_id)
                    self.assertEqual((after.entity_id, after.schema, after.parent_id),
                                     (before.entity_id, before.schema, before.parent_id))
                    self.assertEqual(after.fields["producer"], before.fields["producer"])
                self.assertEqual(self.check(record).ready, milestone == 4)
        spec = self.project(self.complete)
        self.assertEqual(set(spec.source["entity_ids"]), set(MEMBERS))
        self.assertEqual({member["source_entity"] for member in spec.members}, set(MEMBERS))
        self.assertEqual(spec.source["record_digest"], self.complete.digest)
        self.assertEqual(spec.source["run"], self.run.to_dict())
        self.assertEqual(self.repository.read_head(), self.run.base)

    def test_unknown_hypothesis_and_not_required_are_distinct(self):
        early = self.records[0]
        self.assertUnavailable(early, "semantic")
        self.assertEqual(early.entity("beam-0").fields["semantic"]["status"], "unknown")
        self.assertEqual(commitment_review(early, 1)["physical_facts"], "not_required")
        self.assertEqual(self.complete.entity("beam-0").fields["semantic"]["status"], "hypothesis")
        self.assertTrue(self.check(self.complete).ready)
        self.assertEqual(commitment_review(self.complete, 4)["roof_physical_facts"], "not_required")
        # An unselected, still-unknown roof does not prevent analysis of the declared frame.
        self.assertEqual(self.complete.entity("roof").fields["semantic"]["status"], "unknown")
        self.assertFalse(self.check(self.complete, (*MEMBERS, "roof")).ready)

    def test_overlap_policy_changes_without_repairing_or_erasing_the_fact(self):
        observations = []
        statuses = []
        for milestone, record in enumerate(self.records, start=1):
            digest = record.digest
            review = commitment_review(record, milestone)
            observations.append(review["overlap"]["observation"])
            statuses.append(review["overlap"]["status"])
            self.assertEqual(record.digest, digest)
        self.assertTrue(all(observation == observations[0] for observation in observations))
        self.assertEqual(statuses, ["informational", "unresolved", "warning", "blocker"])

    def test_semantic_concrete_never_supplies_a_physical_profile(self):
        material_only = self.records[2]
        self.assertEqual(material_only.entity("beam-0").fields["material"]["family"], "concrete")
        self.assertNotIn("structural", material_only.entity("beam-0").fields)
        self.assertUnavailable(material_only, "structural")
        missing = self.revised_structural(lambda structural: structural.pop("physical_profile"))
        self.assertUnavailable(missing, "physical_profile")
        self.assertUnavailable(missing, "physical_profile.E")

    def test_each_required_analysis_input_is_checked(self):
        cases = (
            ("section", "section"), ("endpoints", "endpoints.i"),
            ("supports", "supports"), ("loads", "loads"), ("releases", "releases"),
        )
        for key, field in cases:
            with self.subTest(missing=key):
                record = self.revised_structural(lambda structural: structural.pop(key))
                self.assertUnavailable(record, field)
        missing_e = self.revised_structural(
            lambda structural: structural["physical_profile"]["properties"].pop("E"),
        )
        self.assertUnavailable(missing_e, "physical_profile.E")

    def test_sourced_quantities_convert_units_without_losing_provenance(self):
        def edit(structural):
            e = structural["physical_profile"]["properties"]["E"]
            e.update(value=30000, unit="MPa", uncertainty={"low": 27000, "high": 33000, "unit": "MPa"})
            structural["section"]["A"].update(value=80000, unit="mm2")
            structural["loads"][0]["at"].update(value=1500, unit="mm")
        record = self.revised_structural(edit)
        spec = self.project(record)
        beam = next(member for member in spec.members if member["source_entity"] == "beam-0")
        self.assertAlmostEqual(beam["E_Pa"], 30e9)
        self.assertAlmostEqual(beam["section_si"]["A"], .08)
        self.assertEqual(beam["source_facts"]["structural"]["physical_profile"]["properties"]["E"]["source"], SOURCE)
        load = next(load for load in spec.loads if load["member"] == "beam-0")
        self.assertEqual(load["at_m"], 1.5)
        self.assertEqual(load["force_N"], -10000)
        self.assertEqual(load["provenance"]["at"]["unit"], "mm")

    def test_bad_units_missing_source_and_invalid_values_refuse_projection(self):
        cases = (
            ({"unit": "psi"}, "unsupported"), ({"source": ""}, "invalid"),
            ({"status": "unknown"}, "unavailable"), ({"value": 0}, "invalid"),
            ({"value": True}, "invalid"), ({"value": -1}, "invalid"),
            ({"value": 1e308, "uncertainty": None}, "invalid"),
            ({"value": 10**400, "uncertainty": None}, "invalid"),
            ({"unit": ["GPa"]}, "invalid"),
        )
        for updates, status in cases:
            with self.subTest(updates=updates):
                record = self.revised_structural(
                    lambda structural: structural["physical_profile"]["properties"]["E"].update(updates),
                )
                self.assertUnavailable(record, "physical_profile.E", status)

    def test_nonfinite_values_cannot_cross_the_canonical_content_boundary(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                # The canonical finite-JSON boundary may reject before readiness runs.
                with self.assertRaisesRegex(CanonicalValueError, "finite"):
                    record = self.revised_structural(
                        lambda structural: structural["physical_profile"]["properties"]["E"].update(value=value),
                    )
                    self.project(record)
        self.assertTrue(self.check(self.complete).ready)

    def test_wrong_material_profile_and_conflicting_joint_are_real_failures(self):
        record = self.revised_structural(
            lambda structural: structural["physical_profile"].update(material_ref="another-material"),
        )
        self.assertUnavailable(record, "physical_profile.material_ref", "invalid")
        record = self.revised_structural(
            lambda structural: structural["endpoints"]["i"]["position"].update(value=[0, 2.9, 0]),
        )
        self.assertUnavailable(record, "endpoints.i", "invalid")

    def test_exact_run_base_content_and_entity_selection_are_required(self):
        other_run = self.repository.create_run("another-analysis")
        wrong_base = replace(self.run, base=replace(self.run.base, version=self.run.base.version + 1))
        cases = (
            (other_run, self.complete.digest), (wrong_base, self.complete.digest),
            (self.run, self.records[2].digest),
        )
        for expected_run, expected_digest in cases:
            with self.subTest(run=expected_run, digest=expected_digest):
                with self.assertRaises(AnalysisUnavailable) as rejected:
                    compile_analysis(self.complete, MEMBERS, expected_run=expected_run, expected_digest=expected_digest)
                self.assertTrue(any(finding.field == "revision" and finding.status == "invalid"
                                    for finding in rejected.exception.readiness.findings))
        for selected in ((), (*MEMBERS, MEMBERS[0]), (*MEMBERS, "absent-member")):
            with self.subTest(selected=selected), self.assertRaises(AnalysisUnavailable):
                self.project(self.complete, selected)
        with self.assertRaises(AnalysisUnavailable):
            compile_analysis(initial_record(), MEMBERS, expected_run=self.run, expected_digest=initial_record().digest)

    def test_revocation_preserves_previous_claim_identity_and_geometry(self):
        previous_digest = self.complete.digest
        geometry = geometry_projection(self.complete)
        revoked = apply_state_record_operator(self.complete, revise_facet(
            self.complete, "beam-0", "semantic", {"status": "unknown", "source": SOURCE},
            "architect-withdrew-load-bearing-claim",
        ))
        self.assertUnavailable(revoked, "semantic")
        self.assertEqual(geometry_projection(revoked), geometry)
        self.assertEqual(revoked.entity("beam-0").fields["producer"], "prism")
        self.assertEqual(self.complete.digest, previous_digest)
        self.assertTrue(self.check(self.complete).ready)
        self.assertNotEqual(revoked.digest, previous_digest)
        self.assertEqual(revoked.entity("beam-0").entity_id, self.complete.entity("beam-0").entity_id)

    def test_enrichment_cannot_bypass_locked_or_protected_geometry(self):
        record = self.complete
        with self.assertRaisesRegex(StateRecordError, "locked parameters"):
            apply_state_record_operator(record, compile_component_edit(
                record, parameters=(replace(record.parameter("height"), value=4),),
            ))
        column = record.entity("column-0")
        fields = deepcopy(column.fields)
        fields["params"]["height"] = 4
        with self.assertRaisesRegex(StateRecordError, "detaches locked"):
            apply_state_record_operator(record, compile_component_edit(
                record, entities=(replace(column, fields=fields),),
            ))
        beam = record.entity("beam-0")
        fields = deepcopy(beam.fields)
        fields["params"]["height"] = .8
        with self.assertRaisesRegex(StateRecordError, "reaches protected"):
            apply_state_record_operator(record, compile_component_edit(
                record, entities=(replace(beam, fields=fields),), protected=("entity:beam-0",),
            ))
        self.assertTrue(self.check(record).ready)


if __name__ == "__main__":
    unittest.main()
