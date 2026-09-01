from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)
from tools import run_parthenon_stage4_visual_rag as visual_rag


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int],
    color: tuple[int, int, int],
) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=image_format)
    return stream.getvalue()


class ParthenonStage4VisualRagTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "parthenon-reconstruction"
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=visual_rag.PROJECT_ID,
            initial_state={"phase": "request", "commitments": []},
        )
        self.source_specs = (
            visual_rag.VisualImageSourceSpec(
                source_id="fixture_plan",
                view_id="overall",
                url="https://fixtures.invalid/plan.png",
                source_family="offline_fixture",
                modality="measured_drawing",
                usage_note="Generated plan fixture; topology only.",
                expected_media_type="image/png",
            ),
            visual_rag.VisualImageSourceSpec(
                source_id="fixture_photo",
                view_id="northwest",
                url="https://fixtures.invalid/photo.jpg",
                source_family="offline_fixture",
                modality="current_photo",
                usage_note="Generated photograph fixture; visible condition only.",
                expected_media_type="image/jpeg",
            ),
        )
        self.candidate_specs = (
            visual_rag.VisualCandidateSpec(
                candidate_id="fixture_plan_selected",
                source_id="fixture_plan",
                view_id="overall",
                normalized_bbox=(0.0, 0.0, 1.0, 1.0),
                element_tag="temple_plan",
                host_component="parthenon",
                spatial_location="overall",
                confidence=0.95,
                supports=(
                    "element_existence",
                    "topology",
                    "relative_position",
                ),
                cannot_support=(
                    "visible_morphology",
                    "material_condition",
                    "exact_dimension",
                ),
                review_state="selected",
            ),
            visual_rag.VisualCandidateSpec(
                candidate_id="fixture_photo_parked",
                source_id="fixture_photo",
                view_id="northwest",
                normalized_bbox=(0.25, 0.2, 0.75, 0.8),
                element_tag="northwest_exterior",
                host_component="parthenon",
                spatial_location="northwest",
                confidence=0.7,
                supports=("visible_morphology", "material_condition"),
                cannot_support=("topology", "exact_dimension"),
                review_state="parked",
            ),
        )
        self.offline_images = {
            "fixture_plan__overall": _image_bytes(
                "PNG",
                size=(64, 40),
                color=(245, 241, 230),
            ),
            "fixture_photo__northwest": _image_bytes(
                "JPEG",
                size=(80, 50),
                color=(176, 164, 142),
            ),
        }

    def _run(self) -> dict[str, object]:
        with patch.object(
            visual_rag.urllib.request,
            "urlopen",
            side_effect=AssertionError("offline fixture attempted network access"),
        ) as urlopen:
            result = visual_rag.run_project(
                self.root,
                captured_at="2026-08-29T12:00:00-04:00",
                source_specs=self.source_specs,
                candidate_specs=self.candidate_specs,
                offline_images=self.offline_images,
            )
        urlopen.assert_not_called()
        return result

    def test_offline_run_uses_exact_workspace_names_and_repository_records(
        self,
    ) -> None:
        result = self._run()

        self.assertEqual(result["run"].run_id, visual_rag.RESEARCH_RUN_ID)
        self.assertEqual(result["resume_mode"], "created")
        expected_root = (
            self.root
            / "runs"
            / visual_rag.RESEARCH_RUN_ID
            / "workspaces"
            / "visual-rag"
            / "source-images"
        )
        expected_paths = {
            expected_root / "fixture_plan__overall.png",
            expected_root / "fixture_photo__northwest.jpg",
        }
        self.assertEqual(set(result["workspace_source_paths"]), expected_paths)
        self.assertEqual(
            (expected_root / "fixture_plan__overall.png").read_bytes(),
            self.offline_images["fixture_plan__overall"],
        )
        self.assertEqual(
            (expected_root / "fixture_photo__northwest.jpg").read_bytes(),
            self.offline_images["fixture_photo__northwest"],
        )

        reopened = FilesystemProjectRepository.open(self.root)
        run = reopened.load_run(visual_rag.RESEARCH_RUN_ID)
        branch_refs = reopened.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_BRANCH,
                run_id=run.run_id,
                branch_id=visual_rag.BRANCH_ID,
            ),
        )
        branch_payloads = tuple(reopened.load_json(ref) for ref in branch_refs)
        schemas = [payload["schema"] for payload in branch_payloads]
        self.assertEqual(schemas.count("ParthenonVisualSourceRecord@1"), 2)
        self.assertEqual(schemas.count("ParthenonVisualCandidateRecord@1"), 2)
        self.assertEqual(
            schemas.count("ParthenonVisualCandidateSelection@1"),
            1,
        )

        source_payloads = {
            payload["source"]["source_id"]: payload
            for payload in branch_payloads
            if payload["schema"] == "ParthenonVisualSourceRecord@1"
        }
        self.assertEqual(
            source_payloads["fixture_plan"]["filename"],
            "fixture_plan__overall.png",
        )
        self.assertEqual(
            source_payloads["fixture_photo"]["filename"],
            "fixture_photo__northwest.jpg",
        )
        for source_id, source_payload in source_payloads.items():
            artifact = source_payload["artifact_ref"]
            self.assertTrue(artifact["relative_path"].startswith("objects/sha256/"))
            object_path = self.root / artifact["relative_path"]
            self.assertTrue(object_path.is_file())
            source_key = (
                "fixture_plan__overall"
                if source_id == "fixture_plan"
                else "fixture_photo__northwest"
            )
            self.assertEqual(object_path.read_bytes(), self.offline_images[source_key])

        manifest = reopened.load_json(result["manifest_ref"])
        self.assertEqual(
            manifest["selected_candidate_ids"],
            ["fixture_plan_selected"],
        )
        self.assertEqual(
            manifest["parked_candidate_ids"],
            ["fixture_photo_parked"],
        )
        self.assertFalse(manifest["reconstruction_005_created"])
        progress = reopened.load_json(result["progress_ref"])
        self.assertEqual(progress["network_calls"], 0)
        self.assertEqual(progress["source_count"], 2)
        self.assertEqual(progress["candidate_count"], 2)
        self.assertEqual(progress["selected_candidate_count"], 1)
        self.assertEqual(progress["parked_candidate_count"], 1)
        self.assertEqual(
            progress["workspace_relative_path"],
            "runs/research-005/workspaces/visual-rag",
        )
        self.assertFalse(progress["reconstruction_005_created"])
        self.assertFalse(
            (self.root / "runs" / visual_rag.FORBIDDEN_RECONSTRUCTION_RUN_ID).exists()
        )

    def test_resume_is_immutable_and_adds_only_a_resumed_progress_receipt(
        self,
    ) -> None:
        first = self._run()
        second = self._run()

        self.assertEqual(second["resume_mode"], "resumed")
        self.assertEqual(second["source_refs"], first["source_refs"])
        self.assertEqual(second["candidate_refs"], first["candidate_refs"])
        self.assertEqual(second["manifest_ref"], first["manifest_ref"])
        self.assertNotEqual(second["progress_ref"], first["progress_ref"])
        run = self.repository.load_run(visual_rag.RESEARCH_RUN_ID)
        progress_refs = self.repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        progress_payloads = tuple(
            self.repository.load_json(ref) for ref in progress_refs
        )
        self.assertEqual(
            {payload["resume_mode"] for payload in progress_payloads},
            {"created", "resumed"},
        )
        self.assertEqual(
            set((self.root / "runs").iterdir()),
            {self.root / "runs" / visual_rag.RESEARCH_RUN_ID},
        )

    def test_existing_reconstruction_005_fails_before_research_run_creation(
        self,
    ) -> None:
        self.repository.create_run(visual_rag.FORBIDDEN_RECONSTRUCTION_RUN_ID)

        with self.assertRaisesRegex(FileExistsError, "must remain unopened"):
            self._run()

        self.assertFalse(
            (self.root / "runs" / visual_rag.RESEARCH_RUN_ID).exists()
        )

    def test_live_inventory_is_bounded_explicit_and_https_only(self) -> None:
        self.assertEqual(len(visual_rag.DEFAULT_SOURCE_SPECS), 8)
        self.assertEqual(len(visual_rag.DEFAULT_CANDIDATE_SPECS), 8)
        self.assertEqual(
            len({item.source_id for item in visual_rag.DEFAULT_SOURCE_SPECS}),
            8,
        )
        self.assertTrue(
            all(
                item.url.startswith("https://www.ysma.gr/wp-content/uploads/")
                for item in visual_rag.DEFAULT_SOURCE_SPECS
            )
        )
        self.assertEqual(
            {item.source_key for item in visual_rag.DEFAULT_SOURCE_SPECS},
            {item.source_key for item in visual_rag.DEFAULT_CANDIDATE_SPECS},
        )
        self.assertTrue(
            all(
                "exact_dimension" in item.cannot_support
                for item in visual_rag.DEFAULT_CANDIDATE_SPECS
            )
        )
        by_source = {
            item.source_id: item
            for item in visual_rag.DEFAULT_CANDIDATE_SPECS
        }
        self.assertEqual(by_source["ysma_11_1"].review_state, "selected")
        self.assertEqual(
            {
                by_source[source_id].review_state
                for source_id in ("ysma_11_2", "ysma_11_3", "ysma_11_4")
            },
            {"parked"},
        )
        self.assertEqual(
            {
                item.review_state
                for item in visual_rag.DEFAULT_CANDIDATE_SPECS
            },
            {"selected", "parked"},
        )


if __name__ == "__main__":
    unittest.main()
