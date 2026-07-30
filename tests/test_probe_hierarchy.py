from __future__ import annotations

import json
import hashlib
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "probes" / "test_library"
RUN_ID = "framework-smoke-002"


class ProbeHierarchyTests(unittest.TestCase):
    def test_probe_root_is_data_not_a_python_package(self) -> None:
        self.assertFalse((ROOT / "probes" / "__init__.py").exists())
        for source in (ROOT / "archflow").rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            self.assertNotIn("from probes", text, source)
            self.assertNotIn("import probes", text, source)

    def test_repository_root_has_no_project_run_store(self) -> None:
        self.assertFalse((ROOT / ".runs").exists())
        source = (
            ROOT / "archflow" / "runtime" / "walking_skeleton.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('Path(".runs")', source)

    def test_case_is_data_only_and_contains_no_derived_input(self) -> None:
        request = json.loads(
            (PROBE / "input" / "request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(request["project_id"], PROBE.name)
        self.assertFalse(request["generation_authority"])
        self.assertEqual(request["supplied_constraints"], [])
        forbidden_keys = {
            "dimensions",
            "spaces",
            "materials",
            "topology",
            "coordinates",
            "geometry_commands",
            "expert_order",
        }
        self.assertFalse(forbidden_keys & set(request))
        executable_suffixes = {
            ".bat",
            ".cmd",
            ".js",
            ".ps1",
            ".py",
            ".sh",
            ".ts",
        }
        self.assertEqual(
            tuple(
                path
                for path in PROBE.rglob("*")
                if path.is_file() and path.suffix.lower() in executable_suffixes
            ),
            (),
        )

    def test_generic_runner_contains_no_case_answer(self) -> None:
        source = (ROOT / "tools" / "probe_smoke.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "test_library",
            "community library",
            "reading room",
            "service desk",
            "16x12",
        ):
            self.assertNotIn(forbidden, source)

    def test_generated_record_and_artifacts_remain_inside_probe(self) -> None:
        run_root = (PROBE / "runs" / RUN_ID).resolve()
        record = json.loads(
            (run_root / "framework-smoke.json").read_text(encoding="utf-8")
        )

        self.assertTrue(record["synthetic"])
        self.assertFalse(record["generation_authority"])
        self.assertTrue(record["framework_boundary_smoke"])
        self.assertFalse(record["architectural_usability_proven"])
        self.assertEqual(record["status"], "committed")
        self.assertEqual(record["canonical_state"], {"before": 0, "after": 1})
        self.assertEqual(
            set(record["modules_exercised"]),
            {
                "state",
                "workspace",
                "runtime",
                "adapters",
                "submission",
                "validation",
                "evaluation",
                "commit",
            },
        )
        record_paths = {item["path"] for item in record["records"]}
        self.assertEqual(
            record_paths,
            {
                "candidate.json",
                "validation.json",
                "evaluations.json",
                "canonical-state.json",
                "commit.json",
            },
        )
        for item in record["records"]:
            path = (run_root / item["path"]).resolve()
            path.relative_to(run_root)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                item["sha256"],
            )
        for artifact in record["artifacts"]:
            prefix = "file:///"
            self.assertTrue(artifact["uri"].startswith(prefix))
            path = Path(
                unquote(artifact["uri"][len(prefix) :])
            ).resolve()
            path.relative_to(run_root)
            self.assertTrue(path.is_file())

        leaked = tuple(
            path
            for source_root in (ROOT / "archflow", ROOT / "tests")
            for path in source_root.rglob("*")
            if path.is_file()
            and path.name in {"framework-smoke.json", "building.voxel.json"}
        )
        self.assertEqual(leaked, ())


if __name__ == "__main__":
    unittest.main()
