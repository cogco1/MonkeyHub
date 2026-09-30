"""Study keeps precedent reasoning inspectable and outside canonical design truth."""

from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import RESEARCH_EVIDENCE_LEDGER, STUDIO_SOURCE_DOCUMENT
from archflow.project.refs import record_ref_from_uri
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application import study as study_application
from archflow_studio_api.application.binding import bound_project, record_kind
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, advance_head
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
             document: dict | None = None, study_id: str = "furniture-house") -> object:
        return self.client.post("/api/studies", json={
            "projectId": PROJECT_ID,
            "studyId": study_id,
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
        by_rule = {row["rule"]: row for row in study["hypotheses"]}
        self.assertEqual(
            sorted(by_rule),
            ["stacked_floor_plate_alignment", "void_centrality", "void_nested_in_mass"],
        )
        # An envelope encloses every mass, so it may not win a mass comparison;
        # this page traces no second mass, so nothing is dominant.
        self.assertNotIn("dominant_mass", by_rule)
        self.assertEqual(
            by_rule["void_nested_in_mass"]["supportEvidenceIds"],
            ["envelope", "central-void"],
        )
        self.assertEqual(
            by_rule["stacked_floor_plate_alignment"]["supportEvidenceIds"],
            ["plate-high", "plate-low"],
        )
        self.assertEqual(
            by_rule["stacked_floor_plate_alignment"]["counterEvidenceIds"], []
        )

        # Falsification names the trace the composition rests on, and every
        # retained variant is one this page can actually carry out.
        self.assertEqual(
            [row["counterfactualId"] for row in study["counterfactuals"]],
            ["shift_x:envelope", "contract:envelope", "remove:envelope"],
        )
        removal = study["counterfactuals"][2]
        self.assertEqual(removal["judgement"], "transition")
        self.assertIn("edge:contains:envelope:central-void", removal["removedFacts"])

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

    def test_null_revision_pins_the_unversioned_document_record_not_a_later_match(self) -> None:
        binding = bound_project(self.client.app.state)
        source_refs = [
            ref for ref in binding.record_refs(self.document["runId"])
            if record_kind(ref) == STUDIO_SOURCE_DOCUMENT
            and binding.repository.load_json(ref).get("asset_sha256") == self.document["assetSha256"]
            and binding.repository.load_json(ref).get("revisionRef") is None
        ]
        self.assertEqual(len(source_refs), 1)
        original_ref = source_refs[0]
        run = binding.load_run(self.document["runId"])
        binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STUDIO_SOURCE_DOCUMENT,
            payload={
                "schema": "StudioSourceDocument@1",
                "project_id": PROJECT_ID,
                "run_id": run.run_id,
                "asset_sha256": self.document["assetSha256"],
                "file_name": "later-generated.png",
                "mime_type": "image/png",
                "size_bytes": 1,
                "pages": [{"page_index": 0, "width": 120.0, "height": 80.0, "rotation": 0}],
                "drawingId": "later-drawing",
                "revisionRef": "project://demo/runs/later/records/drawing-projection-receipt-" + "a" * 64 + ".json",
                "sourceStageRef": None,
                "viewRecipe": None,
                "generatedAt": "2099-01-01T00:00:00+00:00",
            },
        )

        saved = self.save()
        self.assertEqual(saved.status_code, 201, saved.text)
        body = saved.json()
        self.assertIsNone(body["source"]["revisionRef"])
        self.assertEqual(body["source"]["documentRef"], original_ref.uri)
        restarted = self.new_client()
        reopened = restarted.get("/api/studies/furniture-house")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json(), body)

    def test_legacy_unversioned_ledger_reopens_and_can_be_corrected_forward(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        binding = bound_project(self.client.app.state)
        first_ref = record_ref_from_uri(first.json()["ledgerRef"], PROJECT_ID)
        template = binding.repository.load_json(first_ref)

        legacy = dict(template)
        legacy["study_id"] = "legacy-house"
        legacy["run_id"] = "study-legacy-house"
        legacy["previous_ref"] = None
        legacy["source"] = dict(legacy["source"])
        legacy["source"].pop("document_ref", None)
        legacy.pop("derivation_method", None)
        # Stand in for a previous rule implementation: the archived output is
        # intentionally different from what today's _hypotheses would derive.
        legacy["hypotheses"] = []
        run = binding.repository.create_run("study-legacy-house")
        legacy_ref = binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=RESEARCH_EVIDENCE_LEDGER,
            payload=legacy,
        )

        exact_hypotheses = study_application._hypotheses
        study_application._hypotheses = lambda *_: (_ for _ in ()).throw(
            AssertionError("an archived ledger must not run the current hypothesis method")
        )
        try:
            reopened = self.client.get("/api/studies/legacy-house")
        finally:
            study_application._hypotheses = exact_hypotheses
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["hypotheses"], [])
        self.assertEqual(reopened.json()["ledgerRef"], legacy_ref.uri)

        corrected = self.save(
            self.evidence(), legacy_ref.uri, study_id="legacy-house"
        )
        self.assertEqual(corrected.status_code, 201, corrected.text)
        self.assertNotEqual(corrected.json()["ledgerRef"], legacy_ref.uri)
        current_ref = record_ref_from_uri(corrected.json()["ledgerRef"], PROJECT_ID)
        current_payload = binding.repository.load_json(current_ref)
        self.assertEqual(
            current_payload["derivation_method"],
            study_application.CURRENT_DERIVATION_METHOD,
        )
        self.assertIn("document_ref", current_payload["source"])
        old = self.client.get(
            "/api/studies/legacy-house", params={"ledgerRef": legacy_ref.uri}
        )
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["hypotheses"], [])

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

    def test_someone_else_issuing_a_version_does_not_refuse_a_correction(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        original = first.json()

        # A Study has no canonical writer: it creates its own run and retains
        # run records. So an unrelated issue landing mid-save is not a Study
        # authority violation, and refusing a correction that was in fact
        # retained would leave the caller unable to tell what happened.
        exact_source = study_application._source
        issued: list[str] = []

        def issue_then_resolve(binding, **named):
            if not issued:
                issued.append("run-promotion")
                advance_head(self.repository, run_id="run-promotion")
            return exact_source(binding, **named)

        study_application._source = issue_then_resolve
        try:
            corrected = self.save(self.evidence(reject_void=True), original["ledgerRef"])
        finally:
            study_application._source = exact_source

        self.assertEqual(corrected.status_code, 201, corrected.text)
        self.assertEqual(corrected.json()["previousRef"], original["ledgerRef"])
        self.assertFalse(corrected.json()["canonicalStateChanged"])
        self.assertEqual(self.client.get("/api/studies/furniture-house").json(), corrected.json())
        # The issue was the other operator's; the Study neither made nor undid it.
        self.assertEqual(self.repository.read_head().version, self.head.version + 1)

    def test_retained_revisions_are_not_reinterpreted_when_current_rules_change(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        ancestor = first.json()
        second = self.save(self.evidence(reject_void=True), ancestor["ledgerRef"])
        self.assertEqual(second.status_code, 201, second.text)
        current = second.json()

        exact_hypotheses = study_application._hypotheses

        def without_void_centrality(evidence, graph):
            return [
                row for row in exact_hypotheses(evidence, graph)
                if row["rule"] != "void_centrality"
            ]

        study_application._hypotheses = without_void_centrality
        try:
            reopened = self.client.get("/api/studies/furniture-house")
            self.assertEqual(reopened.status_code, 200, reopened.text)
            self.assertEqual(reopened.json(), current)
            archived = self.client.get(
                "/api/studies/furniture-house",
                params={"ledgerRef": ancestor["ledgerRef"]},
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertEqual(archived.json(), ancestor)
            # A new correction uses the method that exists now; old revisions
            # remain readable as the snapshots their own ledger retained.
            repaired = self.save(self.evidence(), current["ledgerRef"])
            self.assertEqual(repaired.status_code, 201, repaired.text)
            self.assertNotIn(
                "void_centrality",
                {row["rule"] for row in repaired.json()["hypotheses"]},
            )
        finally:
            study_application._hypotheses = exact_hypotheses
        self.assertEqual(self.repository.read_head(), self.head)

    def retain_variant(self, template: dict, study_id: str, **fields) -> str:
        """Retain a ledger this API would never write, to read it back."""

        binding = bound_project(self.client.app.state)
        run_id = f"study-{study_id}"
        payload = {**template, "study_id": study_id, "run_id": run_id, "previous_ref": None}
        payload.update(fields)
        run = binding.repository.create_run(run_id)
        return binding.repository.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
            record_kind=RESEARCH_EVIDENCE_LEDGER,
            payload=payload,
        ).uri

    def test_a_corrupt_retained_finding_names_its_ledger_instead_of_failing_as_a_server_error(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        binding = bound_project(self.client.app.state)
        template = binding.repository.load_json(
            record_ref_from_uri(first.json()["ledgerRef"], PROJECT_ID)
        )

        # Replaying a snapshot means nothing re-judges what an older method
        # concluded, but a retained finding is still a row of fields. A corrupt
        # row has to name the ledger that cannot be read here, where the record
        # is known, rather than reaching the wire contract and failing there as
        # an unexplained server error.
        for field in ("measurements", "relations", "hypotheses", "counterfactuals", "evidence"):
            with self.subTest(field=field):
                study_id = f"corrupt-{field}"
                self.retain_variant(
                    template, study_id, **{field: ["not a retained row"]}
                )
                answer = self.client.get(f"/api/studies/{study_id}")
                self.assertEqual(answer.status_code, 409, answer.text)
                self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")
        self.assertEqual(self.repository.read_head(), self.head)

    def test_a_retained_revision_that_disagrees_with_itself_is_refused_not_served(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        binding = bound_project(self.client.app.state)
        template = binding.repository.load_json(
            record_ref_from_uri(first.json()["ledgerRef"], PROJECT_ID)
        )

        def relation(object_evidence_id: str) -> dict:
            return {
                "relation_id": f"contains:envelope:{object_evidence_id}",
                "kind": "contains",
                "subject_evidence_id": "envelope",
                "object_evidence_id": object_evidence_id,
                "method": "normalized-bounds-relation@1",
            }

        # A cold read replays what an older method concluded, but the graph it
        # reconstructs must be one this archive actually holds. An edge whose
        # endpoint was never traced, or was kept unconfirmed by the same
        # revision, has no node to stand on; and a receipt naming another
        # composition cannot be served beside this revision's graph digest.
        zeroed = [
            {**row, "reasoning_receipt": {**row["reasoning_receipt"], "graph_digest": "0" * 64}}
            for row in template["hypotheses"]
        ]
        self.assertTrue(zeroed)
        for study_id, fields in (
            (
                "relation-to-absent-trace",
                {"relations": [*template["relations"], relation("never-traced")]},
            ),
            (
                "relation-to-unconfirmed-trace",
                {"relations": [*template["relations"], relation("unconfirmed-mass")]},
            ),
            ("receipt-names-another-graph", {"hypotheses": zeroed}),
        ):
            with self.subTest(study_id=study_id):
                ledger_ref = self.retain_variant(template, study_id, **fields)
                answer = self.client.get(f"/api/studies/{study_id}")
                self.assertEqual(answer.status_code, 409, answer.text)
                self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")
                named = self.client.get(
                    f"/api/studies/{study_id}", params={"ledgerRef": ledger_ref}
                )
                self.assertEqual(named.status_code, 409, named.text)
                self.assertEqual(named.json()["code"], "STUDY_LEDGER_INVALID")

        # The same retained rows, left as they were written, still read back.
        intact = self.retain_variant(template, "intact-house")
        answer = self.client.get("/api/studies/intact-house")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["ledgerRef"], intact)
        self.assertEqual(
            answer.json()["hypotheses"], first.json()["hypotheses"]
        )
        self.assertEqual(self.repository.read_head(), self.head)

    def test_every_answer_says_which_method_produced_the_findings_it_carries(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(
            first.json()["derivationMethod"],
            study_application.CURRENT_DERIVATION_METHOD,
        )

        # A cold read replays the retained findings instead of deriving them
        # again, so the answer has to say which method produced them. A ledger
        # retained before the method was stamped is still readable and must not
        # borrow today's name for its archived conclusions.
        binding = bound_project(self.client.app.state)
        template = dict(binding.repository.load_json(
            record_ref_from_uri(first.json()["ledgerRef"], PROJECT_ID)
        ))
        source = dict(template["source"])
        source.pop("document_ref")
        template.pop("derivation_method")
        self.retain_variant(template, "unstamped-house", source=source)

        archived = self.client.get("/api/studies/unstamped-house")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertIsNone(archived.json()["derivationMethod"])
        self.assertEqual(
            archived.json()["hypotheses"], first.json()["hypotheses"]
        )

    def test_a_malformed_retained_method_is_answered_not_crashed(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        binding = bound_project(self.client.app.state)
        template = binding.repository.load_json(
            record_ref_from_uri(first.json()["ledgerRef"], PROJECT_ID)
        )
        for index, method in enumerate((123, False, {}, [], None, "")):
            with self.subTest(method=method):
                study_id = f"corrupt-method-{index}"
                self.retain_variant(template, study_id, derivation_method=method)
                answer = self.client.get(f"/api/studies/{study_id}")
                self.assertEqual(answer.status_code, 409, answer.text)
                self.assertEqual(answer.json()["code"], "STUDY_LEDGER_INVALID")

        self.retain_variant(template, "old-method", derivation_method="ArchivedStudyMethod@1")
        archived = self.client.get("/api/studies/old-method")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["derivationMethod"], "ArchivedStudyMethod@1")
        self.assertEqual(self.repository.read_head(), self.head)

    def test_a_ledger_reference_naming_no_retained_record_is_answered_not_crashed(self) -> None:
        first = self.save()
        self.assertEqual(first.status_code, 201, first.text)
        retained = first.json()["ledgerRef"]

        absent = retained.replace(retained.rsplit("-", 1)[1], "b" * 64 + ".json")
        self.assertNotEqual(absent, retained)
        missing = self.client.get(
            "/api/studies/furniture-house", params={"ledgerRef": absent}
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["code"], "STUDY_LEDGER_NOT_FOUND")

        for refused in (
            retained.replace("/records/", "/records/nested/"),
            retained.replace("study-furniture-house", "study-other-house"),
            "project://other-project/runs/study-furniture-house/records/x.json",
            "not a reference at all",
        ):
            answer = self.client.get(
                "/api/studies/furniture-house", params={"ledgerRef": refused}
            )
            self.assertEqual(answer.status_code, 422, f"{refused}: {answer.text}")
            self.assertEqual(answer.json()["code"], "STUDY_LEDGER_REF_INVALID")

    def test_reasoning_states_only_the_alignment_and_dominance_it_can_show(self) -> None:
        def plate(evidence_id: str, x0: float, y0: float) -> dict:
            return {
                "evidenceId": evidence_id,
                "kind": "floor_plate",
                "points": [
                    [x0, y0], [x0 + 0.2, y0], [x0 + 0.2, y0 + 0.04], [x0, y0 + 0.04],
                ],
                "status": "confirmed",
                "confidence": 1.0,
                "origin": "user",
            }

        # Two plate columns 0.6 apart share no alignment, so they are two
        # stacking claims, not one merged family.
        columns = self.save([
            plate("l1", 0.10, 0.10), plate("l2", 0.10, 0.30),
            plate("r1", 0.70, 0.10), plate("r2", 0.70, 0.30),
        ])
        self.assertEqual(columns.status_code, 201, columns.text)
        stacked = [
            row for row in columns.json()["hypotheses"]
            if row["rule"] == "stacked_floor_plate_alignment"
        ]
        self.assertEqual(
            [row["supportEvidenceIds"] for row in stacked],
            [["l1", "l2"], ["r1", "r2"]],
        )

        masses = self.save([
            {
                "evidenceId": name,
                "kind": "mass",
                "points": [[x, 0.5], [x + 0.1, 0.5], [x + 0.1, 0.6], [x, 0.6]],
                "status": "confirmed",
                "confidence": 1.0,
                "origin": "user",
            }
            for name, x in (("m0", 0.1), ("m1", 0.3), ("m2", 0.5))
        ], columns.json()["ledgerRef"])
        self.assertEqual(masses.status_code, 201, masses.text)
        # Three equal masses: nothing dominates, and no rule may say otherwise.
        self.assertEqual(
            [row for row in masses.json()["hypotheses"] if row["rule"] == "dominant_mass"],
            [],
        )

    def test_a_counterfactual_is_refused_rather_than_clamped_onto_the_page_edge(self) -> None:
        response = self.save([
            {
                "evidenceId": "envelope",
                "kind": "envelope",
                "points": [[0.01, 0.01], [0.99, 0.01], [0.99, 0.99], [0.01, 0.99]],
                "status": "confirmed",
                "confidence": 1.0,
                "origin": "user",
            },
            {
                "evidenceId": "edge-mass",
                "kind": "mass",
                "points": [[0.95, 0.40], [0.99, 0.40], [0.99, 0.60], [0.95, 0.60]],
                "status": "confirmed",
                "confidence": 1.0,
                "origin": "user",
            },
        ])
        self.assertEqual(response.status_code, 201, response.text)
        counterfactuals = response.json()["counterfactuals"]
        # The envelope carries the relations, and this page has no room to
        # slide it either way, so no shifted variant is claimed at all.
        self.assertEqual(
            [row["operation"] for row in counterfactuals], ["contract", "remove"]
        )
        for row in counterfactuals:
            self.assertEqual(row["targetEvidenceId"], "envelope")

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
