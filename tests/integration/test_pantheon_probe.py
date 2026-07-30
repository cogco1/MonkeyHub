from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
)


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "probes" / "test_pantheon"


class PantheonProjectProbeTests(unittest.TestCase):
    def test_probe_reloads_through_generic_project_modules(self) -> None:
        repository = FilesystemProjectRepository.open(PROBE)
        run = repository.load_run("bootstrap-001")
        requests = repository.list_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.INPUT),
        )
        records = repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )

        self.assertEqual(repository.load_manifest().project_id, "test_pantheon")
        self.assertEqual(repository.read_head().version, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(records), 1)
        request = repository.load_json(requests[0])
        receipt = repository.load_json(records[0])
        self.assertEqual(set(request), {"schema", "prompt"})
        self.assertEqual(request["schema"], "RawProjectRequest@1")
        self.assertIn("Pantheon", request["prompt"])
        self.assertFalse(any(char.isdigit() for char in request["prompt"]))
        self.assertEqual(receipt["request_ref"], requests[0].uri)
        self.assertTrue(receipt["synthetic_test"])
        self.assertFalse(receipt["generation_authority"])
        self.assertFalse(receipt["architectural_usability_proven"])
        self.assertFalse(receipt["derived_design_available"])
        self.assertEqual(repository.verify().orphan_paths, ())

    def test_case_contains_data_only_and_no_derived_answer_fields(self) -> None:
        executable_suffixes = {
            ".py",
            ".pyc",
            ".ps1",
            ".sh",
            ".bat",
            ".cmd",
            ".js",
            ".ts",
            ".mcfunction",
        }
        self.assertFalse(
            [
                path
                for path in PROBE.rglob("*")
                if path.is_file() and path.suffix.lower() in executable_suffixes
            ]
        )
        request_path = next((PROBE / "input").glob("*.json"))
        request = json.loads(request_path.read_text(encoding="utf-8"))
        forbidden_answer_keys = {
            "width",
            "depth",
            "height",
            "diameter",
            "area",
            "rooms",
            "spaces",
            "columns",
            "materials",
            "topology",
            "coordinates",
            "geometry_commands",
            "expert_order",
            "build_plan",
        }
        self.assertFalse(forbidden_answer_keys & set(request))

    def test_framework_bootstrap_has_no_instance_literal_or_type_branch(self) -> None:
        source = (
            ROOT / "archflow" / "project" / "bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Pantheon", source)
        self.assertNotIn("\u4e07\u795e\u6bbf", source)
        self.assertIsNone(
            re.search(
                r"if\s+.*(?:building[_ ]?type|use)\s*(?:==|in)",
                source,
                flags=re.IGNORECASE,
            )
        )


if __name__ == "__main__":
    unittest.main()
