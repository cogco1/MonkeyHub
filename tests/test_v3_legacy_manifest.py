from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "governance" / "v3_legacy_manifest.json"
ALLOWED_DISPOSITIONS = {
    "native_capability_candidate",
    "read_only_gate_candidate",
    "expert_heuristic_candidate",
    "building_dossier",
    "oracle_only",
    "forbidden_in_production",
}
REQUIRED_ENTRY_FIELDS = {
    "id",
    "sources",
    "responsibility",
    "current_owner",
    "disposition",
    "proposed_v4_contract",
    "permitted_side_effects",
    "target_provider",
    "evidence",
    "exit_gate",
    "audit_surface_ids",
}


def _load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _finding_id(item: dict[str, object]) -> str:
    return f"{item['code']}|{item['path']}|{item['line']}"


def _findings_digest(findings: list[dict[str, object]]) -> str:
    ordered = sorted(
        findings,
        key=lambda item: (
            item["code"],
            item["path"],
            item["line"],
            item["snippet"],
        ),
    )
    payload = json.dumps(
        ordered,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class V3LegacyManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _load_manifest()
        self.entries = self.manifest["entries"]

    def test_schema_unique_responsibilities_and_required_fields(self) -> None:
        self.assertEqual(
            self.manifest["schema"],
            "V3LegacyResponsibilityManifest@1",
        )
        self.assertEqual(
            set(self.manifest["dispositions"]),
            ALLOWED_DISPOSITIONS,
        )
        ids = [entry["id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)))
        for entry in self.entries:
            self.assertEqual(set(entry), REQUIRED_ENTRY_FIELDS)
            self.assertIn(entry["disposition"], ALLOWED_DISPOSITIONS)
            self.assertTrue(entry["sources"])
            self.assertTrue(entry["responsibility"])
            self.assertTrue(entry["current_owner"])
            self.assertTrue(entry["proposed_v4_contract"])
            self.assertTrue(entry["evidence"])
            self.assertTrue(entry["exit_gate"])

    def test_every_captured_audit_surface_has_one_owner(self) -> None:
        owned = [
            surface
            for entry in self.entries
            for surface in entry["audit_surface_ids"]
        ]
        self.assertEqual(len(owned), 18)
        self.assertEqual(len(owned), len(set(owned)))
        summary = self.manifest["source_repository"]["summary"]
        self.assertEqual(summary["composer_production_call"], 4)
        self.assertEqual(summary["keyword_route_surface"], 11)
        self.assertEqual(summary["legacy_adapter_surface"], 3)
        source = self.manifest["source_repository"]
        self.assertTrue(source["tracked_worktree_dirty"])
        self.assertEqual(source["tracked_change_count"], 22)
        self.assertRegex(source["tracked_status_sha256"], r"^[0-9a-f]{64}$")

    def test_forbidden_or_evidence_only_items_have_no_native_provider(
        self,
    ) -> None:
        non_production = {
            "forbidden_in_production",
            "oracle_only",
            "building_dossier",
        }
        for entry in self.entries:
            if entry["disposition"] in non_production:
                self.assertIsNone(entry["target_provider"])
            self.assertNotIn(
                "canonical_write",
                entry["permitted_side_effects"],
            )
            self.assertNotIn(
                "world_write",
                entry["permitted_side_effects"],
            )

    def test_selected_pilot_is_building_neutral_and_read_only(self) -> None:
        selected = next(
            entry
            for entry in self.entries
            if entry["id"] == self.manifest["selected_pilot_id"]
        )
        self.assertEqual(
            selected["disposition"],
            "read_only_gate_candidate",
        )
        self.assertEqual(
            selected["target_provider"],
            "quarantined_v3_cli",
        )
        self.assertEqual(
            set(selected["permitted_side_effects"]),
            {"read_only_source_access", "detached_receipt_output"},
        )
        contract = selected["proposed_v4_contract"].lower()
        self.assertIn("neutral component graph", contract)
        self.assertIn("without mutating v4 state", contract)

    def test_manifest_data_is_not_an_import_or_production_dependency(
        self,
    ) -> None:
        encoded = json.dumps(self.manifest)
        self.assertNotRegex(encoded, r'(?m)(?:^|")\s*(?:from|import)\s+')
        forbidden_import = re.compile(
            r"(?m)^\s*(?:from|import)\s+archflow\."
            r"(?:examples|decision\.v2_stages|"
            r"core\.adapters\.legacy_pack_adapter)\b"
        )
        offenders = []
        for path in (ROOT / "archflow").rglob("*.py"):
            if forbidden_import.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_live_v3_audit_matches_capture_when_repository_is_available(
        self,
    ) -> None:
        source = self.manifest["source_repository"]
        repository = Path(source["path_hint"])
        interpreter = repository / (
            ".venv/Scripts/python.exe"
            if sys.platform == "win32"
            else ".venv/bin/python"
        )
        if not repository.is_dir() or not interpreter.is_file():
            self.skipTest("frozen V3 checkout is not available")

        audit = subprocess.run(
            [str(interpreter), *source["audit_command"]],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(audit.stdout)
        live_ids = {_finding_id(item) for item in payload["findings"]}
        owned_ids = {
            surface
            for entry in self.entries
            for surface in entry["audit_surface_ids"]
        }
        self.assertEqual(payload["schema"], source["audit_schema"])
        self.assertEqual(payload["summary"], source["summary"])
        self.assertEqual(live_ids, owned_ids)
        self.assertEqual(
            _findings_digest(payload["findings"]),
            source["findings_sha256"],
        )

        revision = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        self.assertEqual(revision, source["git_commit"])


if __name__ == "__main__":
    unittest.main()
