"""Verify event labels against real source-bound geometry and PNG evidence."""
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from archflow.adapters.occt_backend import occt_available
from labs.event_gating.fixtures import MINIMUM_TWIN_DISTANCE, prepare


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    if not occt_available():
        pytest.skip("real cadquery-ocp runtime required")
    return prepare(tmp_path_factory.mktemp("event-fixtures") / "run")


def test_fixed_splits_cover_all_events_without_provider_calls(prepared):
    for split in ("dev", "holdout"):
        tasks = [row for row in prepared["tasks"] if row["split"] == split]
        assert {row["kind"] for row in tasks} == {"harmless", "relation", "hard", "stale", "visual", "semantic"}
        assert sum(len(row["checkpoints"]) for row in tasks) == 14
    smoke = prepared["provenance"]["portico_readback"]
    assert smoke["retained_fields_preserved"]
    assert smoke["head_unchanged_by_readback"]
    assert smoke["provider_calls"] == 0
    assert smoke["courtyard_loop_replayed"] is False


def test_relation_and_hard_labels_follow_measured_solids(prepared):
    for task in prepared["tasks"]:
        scale = task["scale"]
        if task["kind"] == "relation":
            point, = task["checkpoints"]
            assert point["before"]["relation"]["distance_m"] == pytest.approx(.5 * scale)
            assert point["after"]["relation"]["distance_m"] == pytest.approx(.25 * scale)
            assert point["after"]["relation"]["intersection_volume_m3"] == pytest.approx(0)
            assert point["expected_action"] == "review"
        if task["kind"] == "hard":
            failed, recovered = task["checkpoints"]
            assert task["constraint"]["minimum_distance_m"] == MINIMUM_TWIN_DISTANCE * scale
            assert failed["before"]["relation"]["distance_m"] == pytest.approx(4 * scale)
            assert failed["after"]["relation"]["distance_m"] == pytest.approx(3 * scale)
            assert failed["before"]["hard_valid"] is True
            assert failed["after"]["hard_valid"] is False
            assert failed["expected_action"] == "revise"
            assert recovered["after"]["hard_valid"] is True
            assert recovered["expected_action"] == "review"


def test_source_staleness_is_a_real_rejection_and_images_match_source(prepared):
    for task in prepared["tasks"]:
        for point in task["checkpoints"]:
            if task["kind"] == "stale":
                assert point["current_source"] != point["observed_source"]
                assert point["stale_rejection"]["type"] == "SourceMismatch"
                assert point["query_count"] == 1
                assert point["observation_verified"] is False
                assert point["expected_action"] == "refresh"
                continue
            assert point["current_source"] == point["observed_source"]
            assert point["query_count"] == 3
            assert point["query_bytes"] > 0
            assert point["observation_seconds"] >= 0
            assert all(row["source"] == point["current_source"] for row in point["raw_queries"])
            assert point["after"]["dependency_closure"]
            if task["kind"] == "visual":
                assert point["requires_visual"] and point["image_sources_verified"]
                assert point["image_sources"] == [point["current_source"]]
                assert point["expected_action"] == "visual-review"
                with Image.open(BytesIO(point["images"][0])) as image:
                    assert image.format == "PNG"
                    assert image.width > 1 and image.height > 1
                    assert image.getextrema()[0] < image.getextrema()[1]


def test_same_source_harmless_and_semantic_inputs_have_no_geometry_oracle(prepared):
    wording = {}
    for task in prepared["tasks"]:
        if task["kind"] not in {"harmless", "semantic"}:
            continue
        for point in task["checkpoints"]:
            assert point["before"] == point["after"]
            assert point["current_source"] == point["observed_source"]
            if task["kind"] == "harmless":
                assert point["semantic_text"] is None
                assert point["expected_action"] == "continue"
        if task["kind"] == "semantic":
            wording[task["split"]] = {point["semantic_text"] for point in task["checkpoints"]}
            assert [point["expected_action"] for point in task["checkpoints"]] == ["continue", "continue", "review", "review"]
    assert wording["dev"].isdisjoint(wording["holdout"])


def test_preparation_requires_new_absolute_external_root(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        prepare("relative")
    with pytest.raises(FileExistsError, match="new diagnostic"):
        prepare(tmp_path)
    with pytest.raises(ValueError, match="outside the source"):
        prepare(Path(__file__).resolve().parent / "diagnostic-output")
