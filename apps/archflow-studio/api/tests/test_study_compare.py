"""Exact Study revisions compare in aspect-correct space without changing design truth."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID


class StudyComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="studio-study-compare-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=PROJECT_ID,
            initial_state={"project_id": PROJECT_ID, "version": 0},
        )
        self.head = self.repository.read_head()
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root, cad_export="off"))
        )
        self.addCleanup(self.client.close)

    @staticmethod
    def _image(width: int, height: int, color: str) -> bytes:
        output = BytesIO()
        Image.new("RGB", (width, height), color).save(output, format="PNG")
        return output.getvalue()

    def upload(self, width: int, height: int, color: str) -> dict:
        response = self.client.post("/api/documents", json={
            "projectId": PROJECT_ID,
            "fileName": f"precedent-{width}x{height}-{color}.png",
            "mimeType": "image/png",
            "contentBase64": base64.b64encode(self._image(width, height, color)).decode("ascii"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def source(document: dict) -> dict:
        return {
            "runId": document["runId"],
            "assetSha256": document["assetSha256"],
            "revisionRef": document.get("revisionRef"),
            "pageIndex": 0,
        }

    @staticmethod
    def metric_rect(
        width: int,
        height: int,
        evidence_id: str,
        kind: str,
        bounds: tuple[float, float, float, float],
        *,
        status: str = "confirmed",
    ) -> dict:
        """Author one rectangle in long-edge metric coordinates, then retain page-normalized points."""

        longest = max(width, height)
        x_scale = width / longest
        y_scale = height / longest
        x0, y0, x1, y1 = bounds
        if not (0 <= x0 < x1 <= x_scale and 0 <= y0 < y1 <= y_scale):
            raise AssertionError("fixture metric rectangle falls outside its source page")
        return {
            "evidenceId": evidence_id,
            "kind": kind,
            "points": [
                [x0 / x_scale, y0 / y_scale],
                [x1 / x_scale, y0 / y_scale],
                [x1 / x_scale, y1 / y_scale],
                [x0 / x_scale, y1 / y_scale],
            ],
            "status": status,
            "confidence": 1.0,
            "origin": "user",
        }

    def save(self, study_id: str, document: dict, evidence: list[dict], previous: str | None = None) -> dict:
        response = self.client.post("/api/studies", json={
            "projectId": PROJECT_ID,
            "studyId": study_id,
            "source": self.source(document),
            "evidence": evidence,
            "expectedPreviousRef": previous,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def compare(self, *studies: dict) -> object:
        return self.client.post("/api/studies/compare", json={
            "projectId": PROJECT_ID,
            "studies": [
                {"studyId": row["studyId"], "ledgerRef": row["ledgerRef"]}
                for row in studies
            ],
        })

    @staticmethod
    def topology_facts(comparison: dict) -> dict[str, int]:
        return {row["fact"]: row["count"] for row in comparison["sharedTopology"]}

    def test_page_aspect_does_not_change_comparison_topology(self) -> None:
        """One physical composition is one comparison even when normalized-y differs by sheet."""

        a_size = (120, 80)   # y metric scale 2/3
        b_size = (160, 80)   # y metric scale 1/2
        document_a = self.upload(*a_size, "blue")
        document_b = self.upload(*b_size, "green")

        def evidence(size: tuple[int, int]) -> list[dict]:
            width, height = size
            return [
                self.metric_rect(width, height, "envelope", "envelope", (0.04, 0.08, 0.92, 0.46)),
                self.metric_rect(width, height, "left", "mass", (0.08, 0.18, 0.18, 0.24)),
                # Centre-y differs by 0.012 long-edge units. In raw normalized
                # page coordinates that is 0.018 on A (inside the old 0.02
                # tolerance) but 0.024 on B (outside it).
                self.metric_rect(width, height, "right", "mass", (0.60, 0.192, 0.70, 0.252)),
            ]

        study_a = self.save("aspect-a", document_a, evidence(a_size))
        study_b = self.save("aspect-b", document_b, evidence(b_size))

        old_a = {row["relationId"] for row in study_a["relations"]}
        old_b = {row["relationId"] for row in study_b["relations"]}
        alignment = "aligned_y_center:left:right"
        self.assertIn(alignment, old_a)
        self.assertNotIn(alignment, old_b)

        answer = self.compare(study_a, study_b)
        self.assertEqual(answer.status_code, 200, answer.text)
        comparison = answer.json()
        self.assertEqual(comparison["schema"], "StudyComparison@1")
        self.assertEqual(comparison["method"], "aspect-correct-composition-compare@1")
        self.assertEqual(comparison["metricFrame"], "page-aspect-correct-long-edge@1")
        self.assertFalse(comparison["canonicalStateChanged"])
        self.assertEqual(comparison["pairwise"][0]["topologySimilarity"], 1.0)
        self.assertEqual(comparison["pairwise"][0]["maxProportionDelta"], 0.0)
        self.assertEqual(
            self.topology_facts(comparison)["edge:aligned_y_center:mass:mass"],
            1,
        )
        self.assertEqual(self.repository.read_head(), self.head)

    def test_no_envelope_proportions_use_long_edge_not_page_aspect(self) -> None:
        a_size = (120, 80)
        b_size = (160, 80)
        document_a = self.upload(*a_size, "cyan")
        document_b = self.upload(*b_size, "magenta")

        def evidence(size: tuple[int, int]) -> list[dict]:
            return [
                self.metric_rect(*size, "left", "mass", (0.08, 0.12, 0.22, 0.24)),
                self.metric_rect(*size, "right", "mass", (0.52, 0.18, 0.68, 0.30)),
            ]

        study_a = self.save("no-envelope-a", document_a, evidence(a_size))
        study_b = self.save("no-envelope-b", document_b, evidence(b_size))
        answer = self.compare(study_a, study_b)
        self.assertEqual(answer.status_code, 200, answer.text)
        comparison = answer.json()
        self.assertEqual(comparison["pairwise"][0]["maxProportionDelta"], 0.0)
        self.assertEqual(
            {row["proportions"]["basis"] for row in comparison["studies"]},
            {"source-page-long-edge"},
        )
        self.assertEqual(self.repository.read_head(), self.head)

    def test_same_topology_can_report_a_real_proportion_difference(self) -> None:
        size = (120, 80)
        document_a = self.upload(*size, "red")
        document_b = self.upload(*size, "yellow")

        envelope = (0.08, 0.08, 0.88, 0.56)
        compact = [
            self.metric_rect(*size, "envelope", "envelope", envelope),
            self.metric_rect(*size, "void", "void", (0.38, 0.22, 0.50, 0.38)),
        ]
        expanded = [
            self.metric_rect(*size, "envelope", "envelope", envelope),
            self.metric_rect(*size, "void", "void", (0.33, 0.20, 0.55, 0.40)),
        ]
        study_a = self.save("compact-void", document_a, compact)
        study_b = self.save("expanded-void", document_b, expanded)

        answer = self.compare(study_a, study_b)
        self.assertEqual(answer.status_code, 200, answer.text)
        pair = answer.json()["pairwise"][0]
        self.assertEqual(pair["topologySimilarity"], 1.0)
        void_delta = next(
            row for row in pair["proportionDeltas"] if row["slot"] == "void:1"
        )
        self.assertGreater(void_delta["delta"]["areaRatio"], 0.0)
        self.assertGreater(pair["maxProportionDelta"], 0.0)
        self.assertEqual(pair["leftOnlyTopology"], [])
        self.assertEqual(pair["rightOnlyTopology"], [])
        self.assertEqual(self.repository.read_head(), self.head)

    def test_comparison_reads_named_archived_revision_and_stays_out_of_openapi(self) -> None:
        size = (120, 80)
        document_a = self.upload(*size, "purple")
        document_b = self.upload(*size, "orange")
        original_evidence = [
            self.metric_rect(*size, "envelope", "envelope", (0.08, 0.08, 0.88, 0.56)),
            self.metric_rect(*size, "void", "void", (0.38, 0.22, 0.50, 0.38)),
        ]
        original = self.save("revisioned", document_a, original_evidence)
        corrected_evidence = [
            original_evidence[0],
            {**original_evidence[1], "status": "rejected"},
        ]
        current = self.save("revisioned", document_a, corrected_evidence, original["ledgerRef"])
        other = self.save("other", document_b, original_evidence)
        self.assertNotEqual(original["ledgerRef"], current["ledgerRef"])

        # Comparison is deliberately exact-revision based: it can inspect the
        # archived original after the Study has advanced, and it never has to
        # resolve or reconcile the current head to do so.
        answer = self.compare(original, other)
        self.assertEqual(answer.status_code, 200, answer.text)
        refs = {row["ledgerRef"] for row in answer.json()["studies"]}
        self.assertEqual(refs, {original["ledgerRef"], other["ledgerRef"]})
        self.assertEqual(answer.json()["pairwise"][0]["topologySimilarity"], 1.0)

        duplicate = self.client.post("/api/studies/compare", json={
            "projectId": PROJECT_ID,
            "studies": [
                {"studyId": original["studyId"], "ledgerRef": original["ledgerRef"]},
                {"studyId": original["studyId"], "ledgerRef": original["ledgerRef"]},
            ],
        })
        self.assertEqual(duplicate.status_code, 422, duplicate.text)
        self.assertEqual(duplicate.json()["code"], "STUDY_COMPARE_DUPLICATE")

        # Keep this experimental seam out of the generated web SDK while #113
        # and #116 are integrating overlapping client/UI work. The endpoint is
        # real and tested; publishing the OpenAPI contract is a later UI step.
        openapi = self.client.get("/openapi.json").json()
        self.assertNotIn("/api/studies/compare", openapi["paths"])
        self.assertEqual(self.repository.read_head(), self.head)


if __name__ == "__main__":
    unittest.main()
