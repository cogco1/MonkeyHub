from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archflow.adapters.v3_legacy_cli import (
    V3LegacyCapabilityRequest,
    V3LegacyStatus,
    missing_v3_provider_receipt,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectVersionRef,
)
from tools import check_v3_boundary


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "governance" / "v3_legacy_ownership.json"
CHECKER = ROOT / "tools" / "check_v3_boundary.py"


def _policy() -> dict[str, object]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


class V3BoundaryTests(unittest.TestCase):
    def test_repository_passes_v3_boundary_audit(self) -> None:
        self.assertEqual(check_v3_boundary.run_checks(ROOT, _policy()), [])

    def test_static_scan_rejects_import_symbol_bridge_and_path_backflow(
        self,
    ) -> None:
        cases = {
            "from archflow.packs import PackAdapter\n": "V3_IMPORT_BACKFLOW",
            "compose_building()\n": "V3_SYMBOL_BACKFLOW",
            (
                "from archflow.adapters.v3_legacy_cli "
                "import V3LegacyCliBridge\n"
            ): "V3_BRIDGE_BYPASS",
            'SOURCE = "D:/ARCHFLOW_V3"\n': "V3_PATH_BACKFLOW",
        }
        policy = _policy()
        for source, expected in cases.items():
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    path = root / "archflow" / "rogue.py"
                    path.parent.mkdir(parents=True)
                    path.write_text(source, encoding="utf-8")
                    findings = check_v3_boundary.scan_production(root, policy)
                    self.assertIn(expected, {item.code for item in findings})

    def test_ownership_requires_one_contract_owner_and_zero_writers(
        self,
    ) -> None:
        policy = _policy()
        self.assertEqual(
            check_v3_boundary.validate_ownership(ROOT, policy),
            [],
        )
        conflicting = copy.deepcopy(policy)
        conflicting["handover"]["production_owners"].append("legacy.v3")
        conflicting["handover"]["writer_owners"].append("legacy.v3")
        codes = {
            item.code
            for item in check_v3_boundary.validate_ownership(
                ROOT,
                conflicting,
            )
        }
        self.assertEqual(codes, {"V3_OWNER_CONFLICT", "V3_WRITER_CONFLICT"})

    def test_missing_explicit_provider_returns_versioned_no_fallback_receipt(
        self,
    ) -> None:
        base = ProjectVersionRef("boundary-project", 0, "a" * 64)
        request = V3LegacyCapabilityRequest.create(
            request_id="boundary-request",
            project_id=base.project_id,
            run_id="boundary-run",
            workspace_id="boundary-workspace",
            base=base,
            capability_id="v3.gate.load_path_analysis",
            detached_snapshot={"schema": "DetachedBoundaryInput@1"},
            obligation={"obligation_id": "check-boundary"},
        )
        receipt = missing_v3_provider_receipt(
            request,
            provider_id="provider.requested",
        ).to_dict()

        self.assertEqual(receipt["status"], V3LegacyStatus.MISSING_PROVIDER)
        self.assertEqual(receipt["provider_id"], "provider.requested")
        self.assertEqual(receipt["provider_version"], "unavailable")
        self.assertEqual(receipt["command"], [])
        self.assertFalse(receipt["fallback_attempted"])
        self.assertFalse(receipt["canonical_write_authority"])

    def test_frozen_oracles_reload_through_project_repository(self) -> None:
        policy = _policy()
        for item in policy["handover"]["oracle_evidence"]:
            with self.subTest(path=item["path"]):
                record_path = ROOT / item["path"]
                probe = next(
                    parent
                    for parent in record_path.parents
                    if parent.parent == ROOT / "probes"
                )
                repository = FilesystemProjectRepository.open(probe)
                self.assertEqual(repository.verify().orphan_paths, ())
                run = repository.load_run("pilot-001")
                records = [
                    repository.load_json(ref)
                    for ref in repository.list_json(
                        run=run,
                        destination=PersistenceDestination(
                            PersistenceArea.RUN_RECORD,
                            run_id=run.run_id,
                        ),
                    )
                ]
                evidence = next(
                    value
                    for value in records
                    if value.get("schema")
                    == "V3DiagnosticProbeEvidence@1"
                )
                self.assertFalse(evidence["generation_authority"])
                self.assertFalse(evidence["architectural_usability_proven"])
                self.assertFalse(evidence["canonical_write_authority"])

    def test_checker_cli_is_suitable_for_automation(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CHECKER), "--json"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "V3BoundaryAudit@1")
        self.assertEqual(payload["finding_count"], 0)


if __name__ == "__main__":
    unittest.main()
