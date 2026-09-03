from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from archive.archflow.capabilities.visual_evidence import (
    VisualClaimKind,
    VisualReviewState,
)
from archflow.project.repository import FilesystemProjectRepository
from archive.tools import refine_parthenon_stage4_visual_regions as visual_regions
from archive.tools import run_parthenon_stage4_visual_rag as visual_rag


def _generated_jpeg(index: int) -> bytes:
    width = 320 + index * 8
    height = 240 + index * 6
    image = Image.new(
        "RGB",
        (width, height),
        (
            150 + index * 7,
            132 + index * 5,
            108 + index * 4,
        ),
    )
    draw = ImageDraw.Draw(image)
    for step in range(1, 8):
        x = step * width // 8
        y = step * height // 8
        draw.line((x, 0, width - x // 2, height), fill=(35, 42, 52), width=2)
        draw.rectangle(
            (x // 2, y // 2, min(width - 1, x + 18), min(height - 1, y + 14)),
            outline=(232, 224, 199),
            width=2,
        )
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=91, optimize=False, progressive=False)
    return stream.getvalue()


class ParthenonStage4VisualRegionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / visual_regions.PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id=visual_regions.PROJECT_ID,
            initial_state={"phase": "request", "commitments": []},
        )
        self.offline_sources = {
            spec.source_key: _generated_jpeg(index)
            for index, spec in enumerate(visual_rag.DEFAULT_SOURCE_SPECS)
        }
        with patch.object(
            visual_rag.urllib.request,
            "urlopen",
            side_effect=AssertionError("P085 fixture attempted network access"),
        ) as urlopen:
            self.p085 = visual_rag.run_project(
                self.root,
                captured_at="2026-08-29T18:20:00Z",
                offline_images=self.offline_sources,
            )
        urlopen.assert_not_called()
        self.predecessor_ref = self.p085["manifest_ref"]
        self.supersedes = (
            visual_regions.LEGACY_P085_MANIFEST_URI,
            self.predecessor_ref.uri,
        )

    @property
    def derived_root(self) -> Path:
        return (
            self.root
            / "runs"
            / visual_regions.RESEARCH_RUN_ID
            / "workspaces"
            / "visual-rag"
            / "derived"
        )

    def _run(self) -> dict[str, object]:
        with patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("P086 attempted network access"),
        ) as urlopen:
            result = visual_regions.refine_project(
                self.root,
                captured_at="2026-08-29T19:30:00-04:00",
                predecessor_manifest_ref=self.predecessor_ref,
                supersedes_manifest_refs=self.supersedes,
            )
        urlopen.assert_not_called()
        return result

    def test_default_region_inventory_matches_the_32_reviewed_rois(self) -> None:
        expected = {
            ("ysma_11_1", "outer_peristyle"): (
                (0.103, 0.136, 0.964, 0.866),
                "selected",
            ),
            ("ysma_11_1", "cella_wall"): (
                (0.216, 0.248, 0.859, 0.755),
                "selected",
            ),
            ("ysma_11_1", "inner_colonnade"): (
                (0.282, 0.358, 0.595, 0.645),
                "selected",
            ),
            ("ysma_11_1", "four_column"): (
                (0.682, 0.392, 0.772, 0.605),
                "selected",
            ),
            ("ysma_11_2", "pediment"): (
                (0.097, 0.143, 0.898, 0.304),
                "parked",
            ),
            ("ysma_11_2", "frieze"): (
                (0.117, 0.304, 0.868, 0.383),
                "parked",
            ),
            ("ysma_11_2", "east_colonnade"): (
                (0.111, 0.402, 0.870, 0.804),
                "parked",
            ),
            ("ysma_11_2", "stepped_base"): (
                (0.075, 0.795, 0.920, 0.879),
                "parked",
            ),
            ("ysma_11_3", "pediment"): (
                (0.093, 0.137, 0.899, 0.302),
                "parked",
            ),
            ("ysma_11_3", "frieze"): (
                (0.115, 0.295, 0.868, 0.377),
                "parked",
            ),
            ("ysma_11_3", "west_colonnade"): (
                (0.112, 0.394, 0.869, 0.796),
                "parked",
            ),
            ("ysma_11_3", "stepped_base"): (
                (0.077, 0.787, 0.918, 0.868),
                "parked",
            ),
            ("ysma_11_4", "roof"): (
                (0.527, 0.110, 0.930, 0.323),
                "parked",
            ),
            ("ysma_11_4", "two_tier_colonnade"): (
                (0.273, 0.254, 0.615, 0.668),
                "parked",
            ),
            ("ysma_11_4", "outer_long_colonnade"): (
                (0.531, 0.261, 0.925, 0.711),
                "parked",
            ),
            ("ysma_11_4", "sectioned_masonry"): (
                (0.453, 0.392, 0.615, 0.695),
                "parked",
            ),
            ("ysma_11_6", "central_doorway"): (
                (0.442, 0.333, 0.554, 0.676),
                "selected",
            ),
            ("ysma_11_6", "far_wall"): (
                (0.266, 0.250, 0.718, 0.672),
                "selected",
            ),
            ("ysma_11_6", "left_column_row"): (
                (0.048, 0.176, 0.283, 0.643),
                "selected",
            ),
            ("ysma_11_6", "right_column_row"): (
                (0.744, 0.0, 1.0, 0.671),
                "selected",
            ),
            ("ysma_12_11", "left_fluting"): (
                (0.104, 0.381, 0.352, 0.666),
                "selected",
            ),
            ("ysma_12_11", "center_fluting"): (
                (0.650, 0.363, 0.904, 0.796),
                "selected",
            ),
            ("ysma_12_11", "entablature"): (
                (0.076, 0.059, 1.0, 0.278),
                "selected",
            ),
            ("ysma_12_11", "scaffold"): (
                (0.0, 0.0, 0.145, 0.713),
                "rejected",
            ),
            ("ysma_12_7", "suspended_relief"): (
                (0.362, 0.174, 0.493, 0.319),
                "parked",
            ),
            ("ysma_12_7", "lifting_tackle"): (
                (0.381, 0.0, 0.460, 0.306),
                "rejected",
            ),
            ("ysma_12_7", "scaffold"): (
                (0.020, 0.087, 0.426, 1.0),
                "rejected",
            ),
            ("ysma_12_7", "frieze_entablature"): (
                (0.337, 0.275, 0.960, 0.512),
                "selected",
            ),
            ("ysma_12_9", "building"): (
                (0.115, 0.248, 0.834, 0.833),
                "selected",
            ),
            ("ysma_12_9", "north_colonnade"): (
                (0.120, 0.489, 0.439, 0.806),
                "selected",
            ),
            ("ysma_12_9", "scaffold"): (
                (0.432, 0.269, 0.833, 0.792),
                "rejected",
            ),
            ("ysma_12_9", "boom"): (
                (0.362, 0.250, 0.435, 0.543),
                "rejected",
            ),
        }
        actual = {
            (spec.source_id, spec.region_id): (
                spec.normalized_bbox,
                spec.review_state.value,
            )
            for spec in visual_regions.DEFAULT_REGION_SPECS
        }

        self.assertEqual(len(visual_regions.DEFAULT_REGION_SPECS), 32)
        self.assertEqual(actual, expected)
        self.assertTrue(
            all(
                VisualClaimKind.EXACT_DIMENSION in spec.cannot_support
                and VisualClaimKind.EXACT_DIMENSION not in spec.supports
                for spec in visual_regions.DEFAULT_REGION_SPECS
            )
        )
        self.assertTrue(
            all(
                spec.review_state is VisualReviewState.PARKED
                for spec in visual_regions.DEFAULT_REGION_SPECS
                if spec.source_id in {"ysma_11_2", "ysma_11_3", "ysma_11_4"}
            )
        )

    def test_offline_refinement_writes_32_crops_8_overlays_and_exact_lineage(
        self,
    ) -> None:
        head_before = self.repository.read_head()
        result = self._run()

        self.assertEqual(result["resume_mode"], "created")
        self.assertEqual(len(result["region_record_refs"]), 32)
        self.assertEqual(len(result["overlay_record_refs"]), 8)
        self.assertEqual(len(result["crop_paths"]), 32)
        self.assertEqual(len(result["overlay_paths"]), 8)
        self.assertEqual(len(set(result["crop_paths"])), 32)
        self.assertEqual(len(set(result["overlay_paths"])), 8)
        self.assertEqual(
            {path.parent for path in result["crop_paths"]},
            {self.derived_root / "regions"},
        )
        self.assertEqual(
            {path.parent for path in result["overlay_paths"]},
            {self.derived_root / "overlays"},
        )
        self.assertEqual(
            {path.name for path in result["crop_paths"]},
            {
                "ysma_11_1__r001__outer_peristyle.png",
                "ysma_11_1__r002__cella_wall.png",
                "ysma_11_1__r003__inner_colonnade.png",
                "ysma_11_1__r004__four_column.png",
                "ysma_11_2__r001__pediment.png",
                "ysma_11_2__r002__frieze.png",
                "ysma_11_2__r003__east_colonnade.png",
                "ysma_11_2__r004__stepped_base.png",
                "ysma_11_3__r001__pediment.png",
                "ysma_11_3__r002__frieze.png",
                "ysma_11_3__r003__west_colonnade.png",
                "ysma_11_3__r004__stepped_base.png",
                "ysma_11_4__r001__roof.png",
                "ysma_11_4__r002__two_tier_colonnade.png",
                "ysma_11_4__r003__outer_long_colonnade.png",
                "ysma_11_4__r004__sectioned_masonry.png",
                "ysma_11_6__r001__central_doorway.png",
                "ysma_11_6__r002__far_wall.png",
                "ysma_11_6__r003__left_column_row.png",
                "ysma_11_6__r004__right_column_row.png",
                "ysma_12_11__r001__left_fluting.png",
                "ysma_12_11__r002__center_fluting.png",
                "ysma_12_11__r003__entablature.png",
                "ysma_12_11__r004__scaffold.png",
                "ysma_12_7__r001__suspended_relief.png",
                "ysma_12_7__r002__lifting_tackle.png",
                "ysma_12_7__r003__scaffold.png",
                "ysma_12_7__r004__frieze_entablature.png",
                "ysma_12_9__r001__building.png",
                "ysma_12_9__r002__north_colonnade.png",
                "ysma_12_9__r003__scaffold.png",
                "ysma_12_9__r004__boom.png",
            },
        )
        self.assertEqual(
            {path.name for path in result["overlay_paths"]},
            {
                "ysma_11_1__labels.png",
                "ysma_11_2__labels.png",
                "ysma_11_3__labels.png",
                "ysma_11_4__labels.png",
                "ysma_11_6__labels.png",
                "ysma_12_11__labels.png",
                "ysma_12_7__labels.png",
                "ysma_12_9__labels.png",
            },
        )
        for path, expected_hash in zip(
            result["crop_paths"],
            result["crop_hashes"],
            strict=True,
        ):
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                expected_hash,
            )
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)
        for path, expected_hash in zip(
            result["overlay_paths"],
            result["overlay_hashes"],
            strict=True,
        ):
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                expected_hash,
            )
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")

        manifest = self.repository.load_json(result["refined_manifest_ref"])
        self.assertEqual(
            manifest["predecessor_manifest_ref"],
            self.predecessor_ref.uri,
        )
        self.assertEqual(manifest["supersedes_manifest_refs"], list(self.supersedes))
        self.assertEqual(
            manifest["predecessor_manifest_record"]["sha256"],
            self.predecessor_ref.sha256,
        )
        self.assertEqual(len(manifest["region_record_refs"]), 32)
        self.assertEqual(len(manifest["overlay_record_refs"]), 8)
        self.assertEqual(len(manifest["selected_candidate_ids"]), 14)
        self.assertEqual(len(manifest["parked_candidate_ids"]), 13)
        self.assertEqual(len(manifest["rejected_candidate_ids"]), 5)
        self.assertEqual(manifest["pending_candidate_ids"], [])
        self.assertFalse(manifest["reconstruction_005_created"])
        for binding in manifest["manifest"]["candidate_bindings"]:
            candidate = binding["candidate"]
            self.assertIn("exact_dimension", candidate["cannot_support"])
            self.assertNotIn("exact_dimension", candidate["supports"])
            self.assertIsNone(candidate["measurement_basis"])

        for ref in result["region_record_refs"]:
            payload = self.repository.load_json(ref)
            crop = payload["crop"]
            object_path = self.root / crop["artifact_ref"]["relative_path"]
            crop_path = self.root / crop["relative_path"]
            self.assertEqual(object_path.read_bytes(), crop_path.read_bytes())
            self.assertEqual(
                hashlib.sha256(crop_path.read_bytes()).hexdigest(),
                crop["sha256"],
            )
        for ref in result["overlay_record_refs"]:
            payload = self.repository.load_json(ref)
            overlay = payload["overlay"]
            object_path = self.root / overlay["artifact_ref"]["relative_path"]
            overlay_path = self.root / overlay["relative_path"]
            self.assertEqual(object_path.read_bytes(), overlay_path.read_bytes())

        progress = self.repository.load_json(result["progress_ref"])
        self.assertEqual(progress["network_calls"], 0)
        self.assertEqual(progress["region_count"], 32)
        self.assertEqual(progress["overlay_count"], 8)
        self.assertEqual(
            progress["canonical_head_before"],
            progress["canonical_head_after"],
        )
        self.assertEqual(self.repository.read_head(), head_before)
        self.assertFalse(
            (
                self.root
                / "runs"
                / visual_regions.FORBIDDEN_RECONSTRUCTION_RUN_ID
            ).exists()
        )

    def test_resume_reuses_identical_crop_overlay_and_branch_records(self) -> None:
        first = self._run()
        first_bytes = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in (*first["crop_paths"], *first["overlay_paths"])
        }
        second = self._run()

        self.assertEqual(second["resume_mode"], "resumed")
        self.assertEqual(second["crop_paths"], first["crop_paths"])
        self.assertEqual(second["crop_hashes"], first["crop_hashes"])
        self.assertEqual(second["overlay_paths"], first["overlay_paths"])
        self.assertEqual(second["overlay_hashes"], first["overlay_hashes"])
        self.assertEqual(second["region_record_refs"], first["region_record_refs"])
        self.assertEqual(second["overlay_record_refs"], first["overlay_record_refs"])
        self.assertEqual(
            second["refined_manifest_ref"],
            first["refined_manifest_ref"],
        )
        self.assertNotEqual(second["progress_ref"], first["progress_ref"])
        second_bytes = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in (*second["crop_paths"], *second["overlay_paths"])
        }
        self.assertEqual(second_bytes, first_bytes)
        self.assertEqual(
            len(tuple((self.derived_root / "regions").glob("*.png"))),
            32,
        )
        self.assertEqual(
            len(tuple((self.derived_root / "overlays").glob("*.png"))),
            8,
        )

    def test_parent_mismatch_and_invalid_bbox_fail_before_derived_writes(self) -> None:
        with self.assertRaisesRegex(
            visual_regions.ParthenonVisualRegionError,
            "supersedes manifests",
        ):
            visual_regions.refine_project(
                self.root,
                captured_at="2026-08-29T23:30:00Z",
                predecessor_manifest_ref=self.predecessor_ref,
                supersedes_manifest_refs=(
                    visual_regions.LEGACY_P085_MANIFEST_URI,
                    visual_regions.DEFAULT_PREDECESSOR_MANIFEST_REF.uri,
                ),
            )
        self.assertFalse(self.derived_root.exists())

        invalid = replace(visual_regions.DEFAULT_REGION_SPECS[0])
        object.__setattr__(invalid, "normalized_bbox", (-0.1, 0.1, 0.5, 0.5))
        specs = (invalid, *visual_regions.DEFAULT_REGION_SPECS[1:])
        with self.assertRaisesRegex(
            visual_regions.ParthenonVisualRegionError,
            "unit source rectangle",
        ):
            visual_regions.refine_project(
                self.root,
                captured_at="2026-08-29T23:30:00Z",
                predecessor_manifest_ref=self.predecessor_ref,
                supersedes_manifest_refs=self.supersedes,
                region_specs=specs,
            )
        self.assertFalse(self.derived_root.exists())

    def test_source_digest_mismatch_and_reconstruction_run_fail_closed(self) -> None:
        source_path = self.p085["workspace_source_paths"][0]
        source_path.write_bytes(source_path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(
            visual_regions.ParthenonVisualRegionError,
            "workspace/object digest mismatch",
        ):
            visual_regions.refine_project(
                self.root,
                captured_at="2026-08-29T23:30:00Z",
                predecessor_manifest_ref=self.predecessor_ref,
                supersedes_manifest_refs=self.supersedes,
            )
        self.assertFalse(self.derived_root.exists())

        source_key = next(
            spec.source_key
            for spec in visual_rag.DEFAULT_SOURCE_SPECS
            if source_path.name.startswith(f"{spec.source_id}__")
        )
        source_path.write_bytes(self.offline_sources[source_key])
        self.repository.create_run(
            visual_regions.FORBIDDEN_RECONSTRUCTION_RUN_ID
        )
        with self.assertRaisesRegex(FileExistsError, "must remain unopened"):
            visual_regions.refine_project(
                self.root,
                captured_at="2026-08-29T23:30:00Z",
                predecessor_manifest_ref=self.predecessor_ref,
                supersedes_manifest_refs=self.supersedes,
            )
        self.assertFalse(self.derived_root.exists())


if __name__ == "__main__":
    unittest.main()
