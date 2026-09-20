"""Document research keeps hypotheses editable and interventions source-bound."""

from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application import study as study_application
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.routes import study as study_routes
from archflow_studio_api.transport.errors import StudioError
from archflow_studio_api.transport.study import SaveStudyRequestDto

from .study_fixture import fixture_evidence, fixture_observations, fixture_png, fixture_research
from .support import PROJECT_ID


class StudyResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-study-research-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.head = self.repository.read_head()
        self.client = self.new_client()
        self.document = self.upload()

    def new_client(self) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=self.root, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def upload(self, case="base") -> dict:
        answer = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID, "fileName": f"synthetic-{case}.png", "mimeType": "image/png",
            "contentBase64": base64.b64encode(fixture_png(case)).decode("ascii"),
        })
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()

    def request(self, *, research=None, previous=None, study_id="synthetic-passage", document=None):
        document = document or self.document
        return {
            "projectId": PROJECT_ID, "studyId": study_id,
            "source": {"runId": document["runId"], "assetSha256": document["assetSha256"],
                       "revisionRef": document.get("revisionRef"), "pageIndex": 0},
            "evidence": fixture_evidence(), "expectedPreviousRef": previous,
            "research": fixture_research() if research is None else research,
        }

    def save(self, **kwargs):
        answer = self.client.post("/api/studies", json=self.request(**kwargs))
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()

    def test_true_polygon_interventions_match_independent_cell_oracle_and_keep_prediction(self):
        study = self.save()
        research = study["research"]
        observations = research["observations"]
        original = fixture_observations()
        measurements = {row["evidenceId"]: row for row in observations["measurements"]}
        for evidence_id, area in original["polygonAreas"].items():
            self.assertAlmostEqual(measurements[evidence_id]["area"], area)
        # The stepped polygons have overlapping boxes but disjoint interiors.
        self.assertFalse(any(
            row["kind"] == "intersection_area"
            and {row["subjectEvidenceId"], row["objectEvidenceId"]} == {"left-mass", "clear-strip"}
            for row in observations["relations"]
        ))
        self.assertEqual(len(research["counterfactuals"]), 4)
        for planned, retained in zip(fixture_research()["counterfactuals"], research["counterfactuals"]):
            self.assertEqual(retained["prediction"], planned["prediction"])
            actual = retained["actual"]
            self.assertEqual(actual["status"], "computed")
            self.assertEqual(actual["interpretation"], "underdetermined")
            oracle = fixture_observations(retained["counterfactualId"])
            by_id = {row["evidenceId"]: row for row in actual["measurements"]}
            self.assertAlmostEqual(by_id["right-mass"]["area"], oracle["polygonAreas"]["right-mass"])
            self.assertAlmostEqual(by_id["clear-strip"]["clearArea"], oracle["clearVoidArea"])
            self.assertEqual(by_id["clear-strip"]["clearComponents"], oracle["clearVoidComponents"])
            self.assertFalse(by_id["clear-strip"]["passabilityEstablished"])
            for evidence in actual["evidence"]:
                if evidence["evidenceId"] != "right-mass":
                    self.assertEqual(evidence, next(row for row in study["evidence"] if row["evidenceId"] == evidence["evidenceId"]))
        self.assertFalse(research["completion"]["ready"])
        self.assertIn("changed-context-decision", research["completion"]["missing"])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_context_revision_and_editable_prior_reopen_without_reinterpreting_archives(self):
        first = self.save()
        research = fixture_research(changed_context=True)
        research["hypotheses"][0]["statement"] = "Revised explanation with the same evidence, still a hypothesis."
        second = self.save(research=research, previous=first["ledgerRef"])
        self.assertTrue(second["research"]["completion"]["ready"])
        self.assertEqual(second["research"]["designPrior"]["preferenceStatus"], "unresolved")
        self.assertNotEqual(first["ledgerRef"], second["ledgerRef"])
        with patch.object(study_application, "_polygon_observations", side_effect=AssertionError("no cold recomputation")):
            restarted = self.new_client()
            current = restarted.get("/api/studies/synthetic-passage")
            archived = restarted.get("/api/studies/synthetic-passage", params={"ledgerRef": first["ledgerRef"]})
            self.assertEqual(current.json(), second)
            self.assertEqual(archived.json(), first)
        retry = self.save(research=research, previous=second["ledgerRef"])
        self.assertEqual(retry["ledgerRef"], second["ledgerRef"])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_bad_references_or_client_supplied_actual_cannot_be_retained(self):
        for mutation in ("missing-trace", "missing-counter-evidence", "missing-hypothesis", "self-competes", "forged-actual", "false-preference"):
            research = fixture_research()
            if mutation == "missing-trace":
                research["hypotheses"][0]["evidenceIds"] = ["absent"]
            elif mutation == "missing-counter-evidence":
                research["hypotheses"][0]["counterEvidenceIds"] = ["absent"]
            elif mutation == "missing-hypothesis":
                research["counterfactuals"][0]["hypothesisIds"] = ["absent"]
            elif mutation == "self-competes":
                research["hypotheses"][0]["competesWith"] = ["connection"]
            elif mutation == "forged-actual":
                research["counterfactuals"][0]["actual"] = {"status": "computed"}
            else:
                research["designPrior"].update(preference="", preferenceStatus="stated")
            answer = self.client.post("/api/studies", json=self.request(research=research))
            self.assertEqual(answer.status_code, 422, f"{mutation}: {answer.text}")
        self.assertFalse((self.repository.layout.runs / "study-synthetic-passage").exists())

    def test_supported_changed_context_can_retain_a_conditional_prior(self):
        research = fixture_research(changed_context=True)
        research["designPrior"]["changedContext"] = {
            "changedConditions": ["The envelope grows while the measured clear strip and its required connection remain fixed."],
            "decision": "retain", "reason": "The changed condition does not alter the prior's declared geometric requirement; passability still needs separate evidence.",
            "revisedStatement": "",
        }
        saved = self.save(research=research)
        self.assertTrue(saved["research"]["completion"]["ready"])
        self.assertEqual(saved["research"]["designPrior"]["changedContext"]["decision"], "retain")
        self.assertEqual(self.new_client().get("/api/studies/synthetic-passage").json(), saved)

    def test_execute_requires_prediction_and_conditions_but_partial_research_can_be_saved(self):
        study = self.save(research={"question": "An unfinished question"})
        self.assertFalse(study["research"]["completion"]["ready"])
        self.assertIn("two-competing-explanations", study["research"]["completion"]["missing"])
        for field, value in (("prediction", ""), ("conditions", []), ("hypothesisIds", [])):
            research = fixture_research()
            research["counterfactuals"][0][field] = value
            answer = self.client.post("/api/studies", json=self.request(research=research, previous=study["ledgerRef"]))
            self.assertEqual(answer.status_code, 422, answer.text)

    def test_outside_page_and_noop_are_explicit_unsupported_results(self):
        research = fixture_research()
        research["counterfactuals"][0]["parameters"] = {"dx": 0.8}
        research["counterfactuals"][1]["parameters"] = {"dx": 0.0}
        study = self.save(research=research)
        results = study["research"]["counterfactuals"]
        self.assertEqual(results[0]["actual"]["status"], "unsupported")
        self.assertIn("not clamped", results[0]["actual"]["reason"])
        self.assertEqual(results[1]["actual"]["status"], "unsupported")
        self.assertIn("no geometric change", results[1]["actual"]["reason"])
        self.assertIn("three-to-five-computed-interventions", study["research"]["completion"]["missing"])

    def test_repeating_one_change_is_not_three_distinct_experiments(self):
        research = fixture_research(changed_context=True)
        research["counterfactuals"] = [deepcopy(research["counterfactuals"][0]) for _ in range(3)]
        for index, row in enumerate(research["counterfactuals"]):
            row["counterfactualId"] = f"same-change-{index}"
        study = self.save(research=research)
        self.assertTrue(all(row["actual"]["status"] == "computed" for row in study["research"]["counterfactuals"]))
        self.assertIn("three-to-five-computed-interventions", study["research"]["completion"]["missing"])

    def test_archived_intervention_cannot_change_an_unnamed_trace_or_source(self):
        first = self.save()
        binding = bound_project(self.client.app.state)
        template = binding.repository.load_json(record_ref_from_uri(first["ledgerRef"], PROJECT_ID))
        for field in ("source", "unnamed-trace"):
            payload = deepcopy(template)
            study_id = f"corrupt-{field}"
            run_id = f"study-{study_id}"
            payload.update(study_id=study_id, run_id=run_id, previous_ref=None)
            if field == "source":
                payload["research"]["source_binding"]["asset_sha256"] = "0" * 64
            else:
                trace = next(row for row in payload["research"]["counterfactuals"][0]["actual"]["evidence"]
                             if row["evidence_id"] == "left-mass")
                trace["geometry"]["points"] = [[round(x + 0.01, 6), y] for x, y in trace["geometry"]["points"]]
            binding.repository.put_json(
                run=binding.repository.create_run(run_id),
                destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
                record_kind=RESEARCH_EVIDENCE_LEDGER, payload=payload,
            )
            answer = self.client.get(f"/api/studies/{study_id}")
            self.assertEqual(answer.status_code, 409, answer.text)
            self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")

    def test_unconfirmed_trace_is_not_executed_and_non_simple_polygon_is_not_repaired(self):
        body = self.request()
        body["evidence"][2]["status"] = "proposed"
        answer = self.client.post("/api/studies", json=body)
        self.assertEqual(answer.status_code, 201, answer.text)
        self.assertTrue(all(row["actual"]["status"] == "unsupported" for row in answer.json()["research"]["counterfactuals"]))
        body = self.request(study_id="crossed-polygon")
        body["evidence"][2]["points"] = [[0.1, 0.1], [0.8, 0.8], [0.1, 0.8], [0.9, 0.1]]
        answer = self.client.post("/api/studies", json=body)
        self.assertEqual(answer.status_code, 201, answer.text)
        self.assertIsNone(answer.json()["research"]["observations"])
        self.assertTrue(all(row["actual"]["status"] == "unsupported" for row in answer.json()["research"]["counterfactuals"]))

    def test_list_uses_existing_runs_and_filters_exact_source_without_mutation(self):
        first = self.save()
        other_doc = self.upload("blocked")
        self.save(document=other_doc, study_id="other-source")
        listed = self.client.get("/api/studies")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 2)
        filtered = self.client.get("/api/studies", params={"sourceRunId": self.document["runId"],
            "assetSha256": self.document["assetSha256"], "pageIndex": 0})
        self.assertEqual(filtered.json(), [first])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_comparison_is_saved_with_exact_inputs_and_cold_read_replays_results(self):
        first = self.save()
        other_document = self.upload("contracted")
        other = self.save(study_id="comparison-source", document=other_document)
        references = [{"studyId": value["studyId"], "ledgerRef": value["ledgerRef"]} for value in (first, other)]
        comparison = self.client.post("/api/studies/compare", json={"projectId": PROJECT_ID, "studies": references})
        self.assertEqual(comparison.status_code, 200, comparison.text)
        research = fixture_research(changed_context=True)
        research["comparisons"] = [{"studies": references}]
        saved = self.save(research=research, previous=first["ledgerRef"])
        self.assertEqual(saved["research"]["comparisons"], [{"studies": references}])
        self.assertEqual(saved["research"]["comparisonResults"], [comparison.json()])
        self.save(study_id="comparison-source", document=other_document, previous=other["ledgerRef"],
                  research={"question": "The other Study has advanced since comparison."})
        with patch.object(study_routes, "_compare_exact_revisions", side_effect=AssertionError("do not recompare cold archives")):
            reopened = self.new_client().get("/api/studies/synthetic-passage")
            self.assertEqual(reopened.status_code, 200, reopened.text)
            self.assertEqual(reopened.json(), saved)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_comparison_results_are_not_client_inputs_and_uncalculated_changes_are_refused(self):
        first = self.save()
        other = self.save(study_id="comparison-source")
        research = fixture_research()
        references = [{"studyId": value["studyId"], "ledgerRef": value["ledgerRef"]} for value in (first, other)]
        research["comparisons"] = [{"studies": references}]
        request = self.request(research=research, previous=first["ledgerRef"])
        request["research"]["comparisonResults"] = [{"schema": "StudyComparison@1"}]
        refused = self.client.post("/api/studies", json=request)
        self.assertEqual(refused.status_code, 422, refused.text)
        request["research"].pop("comparisonResults")
        parsed = SaveStudyRequestDto.model_validate(request)
        with self.assertRaises(StudioError) as caught:
            study_application.save_study(bound_project(self.client.app.state), study_id=parsed.study_id,
                source_run_id=parsed.source.run_id, asset_sha256=parsed.source.asset_sha256,
                revision_ref=parsed.source.revision_ref, page_index=parsed.source.page_index,
                evidence_rows=[row.model_dump() for row in parsed.evidence], expected_previous_ref=parsed.expected_previous_ref,
                research=parsed.research.model_dump())
        self.assertEqual(caught.exception.code, "STUDY_COMPARISON_REQUIRED")
        self.assertEqual(self.client.get("/api/studies/synthetic-passage").json()["ledgerRef"], first["ledgerRef"])

    def test_archived_comparison_ref_mismatch_is_refused_without_recomparison(self):
        first = self.save()
        other = self.save(study_id="comparison-source")
        research = fixture_research()
        research["comparisons"] = [{"studies": [{"studyId": value["studyId"], "ledgerRef": value["ledgerRef"]} for value in (first, other)]}]
        saved = self.save(research=research, previous=first["ledgerRef"])
        binding = bound_project(self.client.app.state)
        payload = binding.repository.load_json(record_ref_from_uri(saved["ledgerRef"], PROJECT_ID))
        payload["research"]["comparison_results"][0]["studies"][0]["ledger_ref"] = other["ledgerRef"]
        corrupt = binding.repository.put_json(run=binding.load_run(payload["run_id"]),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=payload["run_id"]),
            record_kind=RESEARCH_EVIDENCE_LEDGER, payload=payload)
        answer = self.client.get("/api/studies/synthetic-passage", params={"ledgerRef": corrupt.uri})
        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")

    def test_stale_save_and_omitting_existing_research_cannot_overwrite_a_revision(self):
        first = self.save()
        body = self.request(previous=first["ledgerRef"])
        body.pop("research")
        missing = self.client.post("/api/studies", json=body)
        self.assertEqual(missing.status_code, 409, missing.text)
        self.assertEqual(missing.json()["code"], "STUDY_RESEARCH_REQUIRED")
        stale = self.client.post("/api/studies", json=self.request())
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "STUDY_REVISION_STALE")
        self.assertEqual(self.client.get("/api/studies/synthetic-passage").json(), first)


if __name__ == "__main__":
    unittest.main()
