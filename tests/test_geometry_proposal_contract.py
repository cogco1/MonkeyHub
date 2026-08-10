from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from archflow.capabilities.geometry_proposal import (
    _authoring_output_contract,
    GeometryProposalProductionError,
    load_geometry_proposal_lineage,
)
from archflow.project import ProjectRecordRef
from archflow.state.geometry_program import ASSET_URI_PATTERN
from archflow.state.operational_state import (
    PORTABLE_LOGICAL_REF_PATTERN,
    require_logical_ref,
)


ROOT = Path(__file__).resolve().parents[1]
PROBE_ROOT = ROOT / "probes" / "p026-sandbox-gold"
RUN_007_LINEAGE = ProjectRecordRef(
    project_id="p026-sandbox-gold",
    relative_path=(
        "runs/gold-agent-cli-007/records/geometry-proposal-lineage-"
        "b90208b56af5c10ab87a6a2845419cc9e53e484aab923b469240984ee5bfb11d"
        ".json"
    ),
    sha256="b90208b56af5c10ab87a6a2845419cc9e53e484aab923b469240984ee5bfb11d",
    media_type="application/json",
)


class _ReadOnlyProbeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_json(self, ref: ProjectRecordRef) -> dict[str, object]:
        return json.loads(
            (self.root / ref.relative_path).read_text(encoding="utf-8")
        )

    def put_json(self, **_kwargs) -> ProjectRecordRef:
        raise AssertionError("compatibility verification must remain read-only")


class GeometryProposalReferenceContractTests(unittest.TestCase):
    def test_provider_schema_uses_the_typed_reference_sources(self) -> None:
        contract = _authoring_output_contract(("interface:a-to-b",))
        body = contract["json_schema"]["properties"]["proposal_body"]
        assembly = body["properties"]["assemblies"]["items"]
        interface_item = assembly["properties"]["interface_refs"]["items"]
        asset = body["properties"]["assets"]["items"]

        self.assertEqual(
            interface_item["pattern"],
            PORTABLE_LOGICAL_REF_PATTERN,
        )
        self.assertEqual(interface_item["enum"], ["interface:a-to-b"])
        self.assertEqual(asset["properties"]["uri"]["pattern"], ASSET_URI_PATTERN)

    def test_published_pattern_matches_typed_validation(self) -> None:
        compiled = re.compile(PORTABLE_LOGICAL_REF_PATTERN)
        accepted = "interface:inside-to-outside"
        for rejected in (
            "inside-to-outside",
            "file:///D:/building.json",
            "FILE:///D:/building.json",
            "D:\\building.json",
            "D:/building.json",
        ):
            self.assertIsNone(compiled.fullmatch(rejected))
            with self.assertRaises(ValueError):
                require_logical_ref(rejected, "ref")
        self.assertIsNotNone(compiled.fullmatch(accepted))
        self.assertEqual(require_logical_ref(accepted, "ref"), accepted)

    @unittest.skipUnless(
        (PROBE_ROOT / RUN_007_LINEAGE.relative_path).exists(),
        "run gold-agent-cli-007 probe records are not present",
    )
    def test_pre_contract_run_007_is_historical_data_only(self) -> None:
        with self.assertRaisesRegex(
            GeometryProposalProductionError,
            "design_state_digest",
        ):
            load_geometry_proposal_lineage(
                _ReadOnlyProbeRepository(PROBE_ROOT),
                RUN_007_LINEAGE,
            )


if __name__ == "__main__":
    unittest.main()
