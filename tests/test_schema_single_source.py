"""P088: schema key sets single-sourced; reads tolerant, writes exact."""

from __future__ import annotations

import unittest

from archflow.adapters.three_dm_inspector import (
    INSPECTION_SCHEMA_KEY_SETS,
    REQUIRED_INSPECTION_KEYS,
    SUPPORTED_INSPECTION_SCHEMAS,
    ThreeDmInspection,
)
from archflow.contracts.reading import read_key_diagnostics
from archflow.contracts.references import digest_keys, require_single_digest
from tools import state_tree_viewer as viewer


def _dummy_inspection() -> ThreeDmInspection:
    return ThreeDmInspection(
        file_sha256="a" * 64,
        file_bytes=10,
        three_dm_version=8,
        archive_version=80,
        units={"model_units": "Meters"},
        layers=({"name": "walls", "index": 0},),
        object_count=1,
        top_level_object_count=1,
        instance_definition_member_count=0,
        object_counts_by_type={"Brep": 1},
        object_counts_by_layer=({"layer": "walls", "count": 1},),
        instance_definitions=(),
        instance_references=(),
        document_user_strings=(),
        object_user_strings=(),
        aggregate_bbox=None,
        bbox_contributing_geometry_count=0,
    )


def _payload(schema: str) -> dict[str, object]:
    payload = _dummy_inspection().to_dict()
    payload["schema"] = schema
    known = INSPECTION_SCHEMA_KEY_SETS[schema]
    for key in list(payload.keys()):
        if key not in known:
            del payload[key]
    for key in known - payload.keys():
        payload[key] = []
    return payload


class KeySetSingleSourceTest(unittest.TestCase):
    def test_v4_key_set_matches_emitter(self) -> None:
        emitted = set(_dummy_inspection().to_dict().keys())
        self.assertEqual(
            emitted, INSPECTION_SCHEMA_KEY_SETS["ThreeDmInspectionSummary@4"]
        )

    def test_version_deltas_are_exactly_the_declared_ones(self) -> None:
        v1 = INSPECTION_SCHEMA_KEY_SETS["ThreeDmInspectionSummary@1"]
        v3 = INSPECTION_SCHEMA_KEY_SETS["ThreeDmInspectionSummary@3"]
        v4 = INSPECTION_SCHEMA_KEY_SETS["ThreeDmInspectionSummary@4"]
        self.assertEqual(REQUIRED_INSPECTION_KEYS, v1)
        self.assertTrue(v1 < v3 < v4)
        self.assertEqual(v4 - v3, {"object_geometry_analysis"})
        self.assertEqual(
            SUPPORTED_INSPECTION_SCHEMAS, set(INSPECTION_SCHEMA_KEY_SETS)
        )


class TolerantInspectionReadTest(unittest.TestCase):
    def _assert_no_errors(self, adapted: dict[str, object]) -> None:
        errors = [
            entry
            for entry in adapted["diagnostics"]
            if entry.get("severity") == "error"
        ]
        self.assertEqual(errors, [])

    def test_v1_v3_v4_all_render(self) -> None:
        for schema in sorted(SUPPORTED_INSPECTION_SCHEMAS):
            with self.subTest(schema=schema):
                adapted = viewer.adapt_three_dm_inspection(_payload(schema))
                self.assertEqual(adapted["schema"], schema)
                self.assertTrue(adapted["headless"])
                self.assertEqual(
                    adapted["bbox"]["source"], f"{schema}.aggregate_bbox"
                )
                self._assert_no_errors(adapted)

    def test_unknown_key_warns_and_read_continues(self) -> None:
        payload = _payload("ThreeDmInspectionSummary@4")
        payload["future_key"] = {"anything": 1}
        adapted = viewer.adapt_three_dm_inspection(payload)
        warned = [
            entry
            for entry in adapted["diagnostics"]
            if entry["severity"] == "warning" and entry["path"] == "future_key"
        ]
        self.assertEqual(len(warned), 1)
        self._assert_no_errors(adapted)

    def test_absent_declared_optional_key_warns(self) -> None:
        payload = _payload("ThreeDmInspectionSummary@3")
        del payload["materials"]
        adapted = viewer.adapt_three_dm_inspection(payload)
        warned = [
            entry
            for entry in adapted["diagnostics"]
            if entry["severity"] == "warning" and entry["path"] == "materials"
        ]
        self.assertEqual(len(warned), 1)

    def test_missing_required_key_fails_closed(self) -> None:
        payload = _payload("ThreeDmInspectionSummary@3")
        del payload["layers"]
        with self.assertRaises(viewer.StagePanelError):
            viewer.adapt_three_dm_inspection(payload)

    def test_unsupported_version_fails_closed(self) -> None:
        payload = _payload("ThreeDmInspectionSummary@4")
        payload["schema"] = "ThreeDmInspectionSummary@2"
        with self.assertRaises(viewer.UnsupportedStageSchema):
            viewer.adapt_three_dm_inspection(payload)


class ReadWriteAsymmetryTest(unittest.TestCase):
    def test_reader_warns_where_required_is_hard(self) -> None:
        missing, warnings = read_key_diagnostics(
            {"a": 1, "extra": 2},
            required=frozenset({"a", "b"}),
            known=frozenset({"a", "b", "c"}),
            label="Demo@1",
        )
        self.assertEqual(missing, ("b",))
        self.assertEqual(
            sorted(entry["path"] for entry in warnings), ["c", "extra"]
        )
        self.assertTrue(
            all(entry["severity"] == "warning" for entry in warnings)
        )

    def test_required_must_be_subset_of_known(self) -> None:
        with self.assertRaises(ValueError):
            read_key_diagnostics(
                {},
                required=frozenset({"a"}),
                known=frozenset(),
                label="Demo@1",
            )


class SingleDigestReferenceTest(unittest.TestCase):
    def test_single_semantic_digest_passes(self) -> None:
        self.assertEqual(
            require_single_digest({"uri": "project://x", "sha256": "a" * 64}),
            "sha256",
        )

    def test_dual_digests_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            require_single_digest(
                {"sha256": "a" * 64, "program_digest": "b" * 64}
            )

    def test_no_digest_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            require_single_digest({"uri": "project://x"})

    def test_digest_key_discovery(self) -> None:
        self.assertEqual(
            digest_keys(
                {"file_sha256": "x", "note": "y", "content_digest": "z"}
            ),
            ("content_digest", "file_sha256"),
        )


if __name__ == "__main__":
    unittest.main()
