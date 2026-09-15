"""Study keeps precedent reasoning inspectable and outside canonical design truth."""

from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application.binding import bound_project, record_kind
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID
from .test_documents import image_bytes


class StudyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-study-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=PROJECT_ID,
            initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.head = self.repository.read_head()
        self.client = self.new_client()
        self.document = self.upload()

    def new_client(self) -> TestClient:
        client = TestClient(create_app(StudioSettings(project_dir=self.root, cad_export="off")))
        self.addCleanup(client.close)
        return client

    def upload(self, color: str = "blue") -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID,
            "fileName": f"precedent-{color}.png",
            "mimeType": "image/png",
            "contentBase64": base64.b64encode(image_bytes(color=color)).decode("ascii"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def source(self, document: dict | None = None) -> dict:
        document = document or self.document
        return {
            "runId": document["runId"],
            "assetSha256": document["assetSha256"],
            "revisionRef": document.get("revisionRef"),
            "pageIndex": 0,
        }

    def evidence(self, *, reject_void: bool = False) -> list[dict]:
        return [
            {
                "evidenceId": "envelope",
                "kind": "envelope",
                "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                "status": "confirmed",
                "confidence": 0.98,
                "origin": "machine",
            },
            {
                "evidenceId": "central-void",
                "kind": "void",
                "points": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
                "status": "rejected" if reject_void else "confirmed",
                "confidence": 1.0,
                "origin": "user",
            },
            {
                "evidenceId": "plate-low",
                "kind": "floor_plate",
                "points": [[0.2, 0.28], [0.8, 0.28], [0.8, 0.34], [0.2, 0.34]],
                "status": "confirmed",
                "confidence": 0.9,
                "origin": "machine",
            },
            {
                "evidenceId": "plate-high",
                "kind": "floor_plate",
                "points": [[0.2, 0.66], [0.8, 0.66], [0.8, 0.72], [0.2, 0.72]],
                "status": "confirmed",
                "confidence": 0.88,
                "origin": "machine",
            },
            {
                "evidenceId": "unconfirmed-mass",
                "kind": "mass",
                "points": [[0.15, 0.15], [0.35, 0.15], [0.35, 0.3], [0.15, 0.3]],
                "status": "proposed",
                "confidence": 0.51,
                "origin": "machine",
            },
        ]

    def save(self, evidence: list[dict] | None = None, previous: str | None = None,
             document: dict | None = None) -> object:
        return self.client.post("/api/studies", json={
            "projectId": PROJECT_ID,
            "studyId": "furniture-house",
            "source": self.source(document),
            "evidence": evidence if evidence is not None else self.evidence(),
            "expectedPreviousRef": previous,
        })

    def test_one_exact_page_builds_inspectable_graph_and_survives_restart(self) -> None:
        before_files = self.repository.read_design_branches()
        response = self.save()
        self.assertEqual(response.status_code, 201, response.text)
        study = response.json()

        self.assertEqual(study["projectId"], PROJECT_ID)
        self.assertEqual(study["studyId"], "furniture-house")
        self.assertEqual(study["runId"], "study-furniture-house")
        self.assertIsNone(study["previousRef"])
        self.assertFalse(study["canonicalStateChanged"])
        self.assertEqual(study["source"]["runId"], self.document["runId"])
        self.assertEqual(study["source"]["assetSha256"], self.document["assetSha256"])
        self.assertEqual(study["source"]["pageIndex"], 0)
        self.assertEqual(study["source"]["coordinateFrame"], "normalized-page-xy-top-left@1")
        self.assertEqual(study["source"]["pageWidth"], 120)
        self.assertEqual(study["source"]["pageHeight"], 80)

        # Proposed evidence stays inspectable but only confirmed evidence
        # participates in deterministic geometry and reasoning.
        self.assertEqual(len(study["evidence"]), 5)
        self.assertEqual(len(study["measurements"]), 4)
        self.assertEqual(len(study["compositionGraph"]["nodes"]), 4)
        self.assertEqual(len(study["compositionGraph"]["graphDigest"]), 64)
        relation_ids = {row["relationId"] for row in study["relations"]}
        self.assertIn("contains:envelope:central-void", relation_ids)
        self.assertIn("aligned_x_center:plate-high:plate-low", relation_ids)
        rules = {row["rule"] for row in study["hypotheses"]}
        self.assertIn("void_nested_in_mass", rules)
        self.assertIn("void_centrality", rules)
        self.assertIn("stacked_floor_plate_alignment", rules)
        self.assertEqual(len(study["counterfactuals"]), 3)
        self.assertTrue({row["judgement"] for row in study["counterfactuals"]} <= {"preserve-family", "transition"})

        # Study owns no canonical or branch authority.
        self.assertEqual(self.repository.read_head(), self.head)
        self.assertEqual(self.repository.read_design_branches(), before_files)
        binding = bound_project(self.client.app.state)
        run = binding.load_run("study-furniture-house")
        refs = [ref for ref in binding.repository.list_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
        ) if record_kind(ref) == RESEARCH_EVIDENCE_LEDGER]
        self.assertEqual([ref.uri for ref in refs], [study["ledgerRef"]])

        restarted = self.new_client()
        reopened = restarted.get("/api/studies/furniture-house")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json(), study)
        self.assertEqual(self.repository.read_head(), self.head)

    def test_user_correction_is_a_new_revision_and_old_evidence_remains_exactly_readable(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        original = first.json()
        corrected = self.save(self.evidence(reject_void=True), original["ledgerRef"])
        self.assertEqual(corrected.status_code, 201, corrected.text)
        current = corrected.json()
        self.assertEqual(current["previousRef"], original["ledgerRef"])
        self.assertNotEqual(current["ledgerRef"], original["ledgerRef"])
        self.assertEqual(len(current["compositionGraph"]["nodes"]), 3)
        self.assertNotIn("void_nested_in_mass", {row["rule"] for row in current["hypotheses"]})

        old = self.client.get(
            "/api/studies/furniture-house",
            params={"ledgerRef": original["ledgerRef"]},
        )
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json(), original)
        newest = self.client.get("/api/studies/furniture-house")
        self.assertEqual(newest.status_code, 200, newest.text)
        self.assertEqual(newest.json(), current)

        stale = self.save(self.evidence(), original["ledgerRef"])
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "STUDY_REVISION_STALE")
        self.assertEqual(self.repository.read_head(), self.head)

    def test_source_page_is_immutable_and_invalid_trace_does_not_create_a_study(self) -> None:
        invalid = self.save(evidence=[{
            "evidenceId": "bad",
            "kind": "mass",
            "points": [[0.1, 0.1], [1.4, 0.1], [0.3, 0.3]],
            "status": "confirmed",
        }])
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertFalse((self.repository.layout.runs / "study-furniture-house").exists())
        self.assertEqual(self.repository.read_head(), self.head)

        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        another = self.upload("red")
        switched = self.save(self.evidence(), first.json()["ledgerRef"], another)
        self.assertEqual(switched.status_code, 409, switched.text)
        self.assertEqual(switched.json()["code"], "STUDY_SOURCE_IMMUTABLE")
        self.assertEqual(self.repository.read_head(), self.head)
