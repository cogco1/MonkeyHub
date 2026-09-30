"""Checks for the manually invoked #255 ingestion benchmark."""

from pathlib import Path
import unittest

from .profile_model_ingest import distribution, measured_stage, source_profile


class ModelIngestProfileTests(unittest.TestCase):
    def test_source_profile_separates_read_decode_and_native_index(self):
        fixture = Path(__file__).parent / "fixtures" / "native-source-index.3dm"

        data, stages = source_profile(fixture)

        by_name = {stage["phase"]: stage for stage in stages}
        self.assertEqual(len(data), fixture.stat().st_size)
        self.assertEqual(
            set(by_name),
            {"source_read", "source_digest", "rhino3dm_decode", "object_index_total"},
        )
        self.assertEqual(by_name["object_index_total"]["object_count"], 5)
        self.assertEqual(by_name["object_index_total"]["layer_count"], 1)
        self.assertGreaterEqual(by_name["object_index_total"]["excluding_decode_ms"], 0)
        for stage in stages:
            self.assertGreaterEqual(stage["duration_ms"], 0)
            if stage["peak_rss_bytes"] is not None:
                self.assertGreaterEqual(stage["peak_rss_bytes"], stage["rss_before_bytes"])

    def test_stage_sampler_and_distribution_report_observed_values(self):
        with measured_stage("operation") as stage:
            sum(range(100))

        self.assertEqual(stage["phase"], "operation")
        self.assertGreaterEqual(stage["duration_ms"], 0)
        self.assertEqual(
            distribution([3.0, 1.0, 2.0]),
            {"count": 3, "min_ms": 1.0, "median_ms": 2.0, "max_ms": 3.0},
        )


if __name__ == "__main__":
    unittest.main()
