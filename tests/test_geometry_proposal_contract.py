from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile
import unittest

from archflow.ports.model import ModelInvocationStatus
from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProductionError,
    GeometryProposalStatus,
    load_geometry_proposal_lineage,
    produce_geometry_program_proposal,
    proposal_authoring_output,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
)
from archflow.state.operational_state import (
    PORTABLE_LOGICAL_REF_PATTERN,
    require_logical_ref,
)
from tests.test_geometry_compiler import COMMITMENT, EVIDENCE
from tests.test_geometry_proposal_producer import (
    IDENTITY,
    _ScriptedProvider,
    _spatial_option,
)
from archflow.contracts.canonical import canonical_json as _canonical_json
from tests.test_sandbox_realization import compiled_room


ROOT = Path(__file__).resolve().parents[1]


def _probe_root() -> Path:
    """Resolve the relocated evidence probe; an absent root triggers skips."""

    try:
        from tools._probe_paths import resolve_probe_root

        return resolve_probe_root("p026-sandbox-gold")
    except Exception:
        return ROOT / "probes" / "p026-sandbox-gold"


PROBE_ROOT = _probe_root()
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


class _ProducerHarness(unittest.IsolatedAsyncioTestCase):
    """Current design-state wiring for contract-level producer checks."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "demo"
        self.repository = FilesystemProjectRepository.initialize(
            self.root,
            project_id="demo",
            initial_state={"schema": "TestState@1"},
        )
        self.run = self.repository.create_run("run")
        self.destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=self.run.run_id,
        )
        self.option = _spatial_option()
        self.option_ref = self.repository.put_json(
            run=self.run,
            destination=self.destination,
            record_kind="spatial-option",
            payload=self.option.to_dict(),
        )
        original_state, original_program, _ = compiled_room()
        self.design_state = replace(
            original_state,
            selected_schematic=replace(
                original_state.selected_schematic,
                project_id=self.run.project_id,
                run_id=self.run.run_id,
                base=self.run.base,
                option=replace(
                    original_state.selected_schematic.option,
                    proposal=self.option,
                ),
            ),
        )
        original_binding = original_program.proposal.semantic_bindings[0]
        binding = replace(
            original_binding,
            commitment_refs=(COMMITMENT,),
            evidence_refs=(EVIDENCE, self.option_ref.uri),
        )
        self.proposal = replace(
            original_program.proposal,
            project_id=self.run.project_id,
            run_id=self.run.run_id,
            base=self.run.base,
            design_state_digest=self.design_state.state_digest,
            semantic_bindings=(binding,),
        )

    async def _produce(self, provider, *, rounds: int = 2):
        return await produce_geometry_program_proposal(
            self.repository,
            provider,
            run=self.run,
            destination=self.destination,
            spatial_option_ref=self.option_ref,
            design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,),
            provider_identity=IDENTITY,
            policy=GeometryProposalPolicy(rounds),
        )

    def _valid_output(self) -> dict[str, object]:
        return json.loads(
            _canonical_json(proposal_authoring_output(self.proposal))
        )


class IssueAggregationTests(_ProducerHarness):
    async def test_independent_defects_share_one_round_receipt(self) -> None:
        broken = self._valid_output()
        body = broken["proposal_body"]
        body["length_unit"] = "parsec"
        body["operations"][0]["kind"] = "undeclared_building_primitive"
        body["assemblies"][0]["interface_refs"] = [
            "not-a-portable-reference"
        ]
        provider = _ScriptedProvider((broken, self._valid_output()))

        result = await self._produce(provider)

        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        first_round = loaded.rounds[0]
        self.assertEqual(first_round.status.value, "rejected")
        self.assertEqual(len(first_round.issues), 3)
        details = tuple(item.detail for item in first_round.issues)
        self.assertIn("parsec", details[0])
        self.assertIn("operations[0]", details[1])
        self.assertIn("undeclared_building_primitive", details[1])
        self.assertIn("assemblies[0]", details[2])
        self.assertIn("portable logical reference", details[2])
        self.assertTrue(
            all(
                item.code == "malformed_model_output"
                for item in first_round.issues
            )
        )
        repair_issues = provider.requests[1].payload["repair_issues"]
        self.assertEqual(len(repair_issues), 3)
        self.assertEqual(
            [item["detail"] for item in repair_issues],
            list(details),
        )

    async def test_semantic_coverage_defects_aggregate(self) -> None:
        broken = self._valid_output()
        binding = broken["proposal_body"]["semantic_bindings"][0]
        binding["commitment_refs"] = []
        binding["evidence_refs"] = [EVIDENCE]
        provider = _ScriptedProvider((broken,))

        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        first_round = loaded.rounds[0]
        self.assertEqual(len(first_round.issues), 2)
        self.assertIn(
            "omit required active commitments",
            first_round.issues[0].detail,
        )
        self.assertIn(
            "cite the current source spatial option record",
            first_round.issues[1].detail,
        )
        self.assertIsNotNone(first_round.proposal_digest)

    async def test_dependent_body_drift_remains_a_single_issue(self) -> None:
        broken = self._valid_output()
        broken["proposal_body"].pop("tolerance")
        provider = _ScriptedProvider((broken,))

        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        first_round = loaded.rounds[0]
        self.assertEqual(len(first_round.issues), 1)
        self.assertIn("missing=['tolerance']", first_round.issues[0].detail)

    async def test_element_defects_in_one_collection_each_get_an_issue(
        self,
    ) -> None:
        broken = self._valid_output()
        operations = broken["proposal_body"]["operations"]
        self.assertGreaterEqual(len(operations), 2)
        operations[0]["kind"] = "undeclared_building_primitive"
        operations[1]["frame_id"] = ""
        provider = _ScriptedProvider((broken,))

        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        loaded = load_geometry_proposal_lineage(
            self.repository,
            result.lineage_ref,
        )
        details = tuple(item.detail for item in loaded.rounds[0].issues)
        self.assertEqual(len(details), 2)
        self.assertIn("operations[0]", details[0])
        self.assertIn("operations[1]", details[1])


class AvailableInterfaceRefsTests(_ProducerHarness):
    async def test_request_enumerates_existing_interface_refs(self) -> None:
        provider = _ScriptedProvider(
            (ModelInvocationStatus.BUDGET_EXHAUSTED,)
        )

        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.REFUSED)
        published = provider.requests[0].payload["available_interface_refs"]
        self.assertEqual(published["refs"], ["interface:outside-to-room"])
        self.assertEqual(published["pattern"], PORTABLE_LOGICAL_REF_PATTERN)
        self.assertIn("connection records", published["description"])

    async def test_enumeration_only_restates_supplied_records(self) -> None:
        provider = _ScriptedProvider(
            (ModelInvocationStatus.BUDGET_EXHAUSTED,)
        )

        await self._produce(provider, rounds=1)

        payload = provider.requests[0].payload
        serialized_connections = payload["developed_design_state"][
            "selected_schematic"
        ]["option"]["proposal"]["connections"]
        expected = sorted(
            {
                ref
                for connection in serialized_connections
                for ref in connection["relationship_refs"]
            }
        )
        self.assertEqual(payload["available_interface_refs"]["refs"], expected)


class GeometryProposalReferenceContractTests(unittest.TestCase):
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
