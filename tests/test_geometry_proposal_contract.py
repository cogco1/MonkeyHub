"""Protocol-ergonomics tests for the geometry proposal producer contract.

These tests cover round-receipt issue aggregation and the machine-readable
reference contract.  They intentionally live outside
``tests/test_geometry_proposal_producer.py`` so parallel parameter-JSON work
can evolve that file independently.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import (
    _authoring_output_contract,
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
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
from archflow.state.geometry_program import ASSET_URI_PATTERN
from archflow.state.operational_state import (
    _PORTABLE_REF,
    PORTABLE_LOGICAL_REF_PATTERN,
    require_logical_ref,
)
from archflow.state import (
    MassingVolume,
    SiteBounds,
    SpatialConnection,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)
from tests.test_geometry_compiler import COMMITMENT, EVIDENCE
from tests.test_sandbox_realization import compiled_room


ROOT = Path(__file__).resolve().parents[1]

IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-provider",
    model_id="scripted-model",
    provider_version="1",
    provider_fingerprint="f" * 64,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class _ScriptedProvider:
    def __init__(
        self,
        outputs: tuple[dict[str, object] | ModelInvocationStatus, ...],
    ) -> None:
        self.outputs = list(outputs)
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, ModelInvocationStatus):
            return ModelInvocationReceipt(
                receipt_id=f"scripted-receipt-{len(self.requests):02d}",
                status=output,
                request=request,
                provider_id=IDENTITY.provider_id,
                model_id=IDENTITY.model_id,
                provider_version=IDENTITY.provider_version,
                provider_fingerprint=IDENTITY.provider_fingerprint,
                input_bytes=len(request.payload_json.encode("utf-8")),
                output_bytes=0,
                output_sha256=None,
                output_json=None,
                error_code=output.value,
                message="scripted refusal",
            )
        encoded = _canonical_json(output)
        return ModelInvocationReceipt(
            receipt_id=f"scripted-receipt-{len(self.requests):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=IDENTITY.provider_id,
            model_id=IDENTITY.model_id,
            provider_version=IDENTITY.provider_version,
            provider_fingerprint=IDENTITY.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            output_json=encoded,
        )


def _spatial_option(
    *,
    connections: tuple[SpatialConnection, ...] = (),
) -> SpatialOptionProposal:
    evidence = ("evidence:spatial-option",)
    return SpatialOptionProposal(
        option_id="selected-option",
        label="Project supplied selected option",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(1.0, "square-meter", evidence),
        footprint_cells=((0, 0), (0, 1), (1, 0), (1, 1)),
        levels=(SpatialLevel("ground", 0, 3, evidence),),
        volumes=(
            MassingVolume(
                "main-volume",
                SiteBounds((0, 0, 0), (1, 2, 1)),
                ("ground",),
                evidence,
            ),
        ),
        zones=(
            SpatialZone(
                "main-zone",
                ("program-node:main",),
                ("ground",),
                ("main-volume",),
                evidence,
            ),
            SpatialZone(
                "entry-zone",
                ("program-node:entry",),
                ("ground",),
                ("main-volume",),
                evidence,
            ),
        ),
        connections=connections,
        constraint_responses=(),
        typology_hypothesis="Project supplied typology hypothesis",
        palette_refs=(),
        rationale="Selected from project-authored spatial alternatives.",
        responds_to_refs=("program:main",),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )


class _ProducerHarness(unittest.IsolatedAsyncioTestCase):
    """Shared repository wiring mirroring the producer test harness."""

    connections: tuple[SpatialConnection, ...] = ()

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
        self.option = _spatial_option(connections=self.connections)
        self.option_ref = self.repository.put_json(
            run=self.run,
            destination=self.destination,
            record_kind="spatial-option",
            payload=self.option.to_dict(),
        )
        original_projection, original_program, _ = compiled_room()
        values = tuple(
            replace(item, derivation_refs=(self.option.ref,))
            for item in original_projection.values
        )
        self.projection = replace(
            original_projection,
            base=self.run.base,
            selected_option_ref=self.option.ref,
            values=values,
        )
        original_binding = original_program.proposal.semantic_bindings[0]
        binding = replace(
            original_binding,
            candidate_value_ids=tuple(
                item.value_id for item in self.projection.values
            ),
            commitment_refs=(COMMITMENT,),
            evidence_refs=(EVIDENCE, self.option_ref.uri),
        )
        self.proposal = replace(
            original_program.proposal,
            base=self.run.base,
            candidate_program_digest=self.projection.projection_digest,
            semantic_bindings=(binding,),
        )

    async def _produce(self, provider, *, rounds: int = 2):
        return await produce_geometry_program_proposal(
            self.repository,
            provider,
            run=self.run,
            destination=self.destination,
            spatial_option_ref=self.option_ref,
            projection=self.projection,
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
        body["assemblies"][0]["interface_refs"] = ["not-a-portable-reference"]
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
            all(item.code == "malformed_model_output" for item in first_round.issues)
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
            "cite the source spatial option record",
            first_round.issues[1].detail,
        )
        # The typed proposal decoded, so the rejected round still records its
        # digest, exactly as before aggregation.
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


class ReferenceContractTests(unittest.TestCase):
    def test_logical_ref_pattern_is_published_from_the_typed_source(self) -> None:
        contract = _authoring_output_contract()
        body = contract["json_schema"]["properties"]["proposal_body"]
        assembly = body["properties"]["assemblies"]["items"]
        interface_ref = assembly["properties"]["interface_refs"]["items"]
        self.assertEqual(
            interface_ref["pattern"],
            PORTABLE_LOGICAL_REF_PATTERN,
        )
        self.assertEqual(_PORTABLE_REF.pattern, PORTABLE_LOGICAL_REF_PATTERN)
        frame = body["properties"]["frames"]["items"]
        self.assertEqual(
            frame["properties"]["source_refs"]["items"]["pattern"],
            PORTABLE_LOGICAL_REF_PATTERN,
        )
        asset = body["properties"]["assets"]["items"]
        self.assertEqual(
            asset["properties"]["uri"]["pattern"],
            ASSET_URI_PATTERN,
        )
        self.assertEqual(
            asset["properties"]["sha256"]["pattern"],
            "^[0-9a-f]{64}$",
        )

    def test_pattern_agrees_with_typed_validation_on_run_007_shape(self) -> None:
        compiled = re.compile(PORTABLE_LOGICAL_REF_PATTERN)
        accepted = "interface:inside-to-outside"
        rejected = "semantic-component-entry"
        self.assertIsNotNone(compiled.fullmatch(accepted))
        self.assertIsNone(compiled.fullmatch(rejected))
        self.assertEqual(require_logical_ref(accepted, "ref"), accepted)
        with self.assertRaises(ValueError):
            require_logical_ref(rejected, "ref")

    def test_pattern_survives_canonical_json_round_trip(self) -> None:
        contract = _authoring_output_contract()
        decoded = json.loads(_canonical_json(contract))
        body = decoded["json_schema"]["properties"]["proposal_body"]
        assembly = body["properties"]["assemblies"]["items"]
        self.assertEqual(
            assembly["properties"]["interface_refs"]["items"]["pattern"],
            PORTABLE_LOGICAL_REF_PATTERN,
        )


class AvailableInterfaceRefsTests(_ProducerHarness):
    connections = (
        SpatialConnection(
            connection_id="entry-to-main",
            source_zone_id="entry-zone",
            target_zone_id="main-zone",
            relationship_refs=(
                "interface:entry-to-main",
                "program-relation:000:adjacent",
            ),
            directed=False,
            source_refs=("evidence:spatial-option",),
        ),
    )

    async def test_request_enumerates_existing_interface_refs(self) -> None:
        provider = _ScriptedProvider(
            (ModelInvocationStatus.BUDGET_EXHAUSTED,)
        )
        result = await self._produce(provider, rounds=1)

        self.assertIs(result.status, GeometryProposalStatus.REFUSED)
        published = provider.requests[0].payload["available_interface_refs"]
        self.assertEqual(
            published["refs"],
            ["interface:entry-to-main", "program-relation:000:adjacent"],
        )
        self.assertEqual(published["pattern"], PORTABLE_LOGICAL_REF_PATTERN)
        self.assertIn("relationship references", published["description"])

    async def test_enumeration_only_restates_supplied_records(self) -> None:
        provider = _ScriptedProvider(
            (ModelInvocationStatus.BUDGET_EXHAUSTED,)
        )
        await self._produce(provider, rounds=1)

        payload = provider.requests[0].payload
        serialized_connections = payload["spatial_option_record"]["proposal"][
            "connections"
        ]
        expected = sorted(
            {
                ref
                for connection in serialized_connections
                for ref in connection["relationship_refs"]
            }
        )
        self.assertEqual(
            payload["available_interface_refs"]["refs"],
            expected,
        )


_PROBE_ROOT = ROOT / "probes" / "p026-sandbox-gold"
_RUN_007_LINEAGE = ProjectRecordRef(
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
    """Load persisted probe records without any write authority."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load_json(self, ref: ProjectRecordRef) -> dict[str, object]:
        return json.loads(
            (self.root / ref.relative_path).read_text(encoding="utf-8")
        )

    def put_json(self, **_kwargs) -> ProjectRecordRef:
        raise AssertionError("read-only verification must not write records")


@unittest.skipUnless(
    (_PROBE_ROOT / _RUN_007_LINEAGE.relative_path).exists(),
    "run gold-agent-cli-007 probe records are not present",
)
class PersistedLineageCompatibilityTests(unittest.TestCase):
    def test_pre_pattern_run_records_still_load(self) -> None:
        loaded = load_geometry_proposal_lineage(
            _ReadOnlyProbeRepository(_PROBE_ROOT),
            _RUN_007_LINEAGE,
        )
        self.assertIs(loaded.lineage.status, GeometryProposalStatus.EXHAUSTED)
        self.assertEqual(len(loaded.rounds), 2)
        self.assertIsNone(loaded.proposal)
        self.assertEqual(
            loaded.rounds[1].issues[0].code,
            "malformed_model_output",
        )
        # The persisted request payloads predate the published pattern and
        # interface enumeration; loading must not require the new keys.
        for round_receipt in loaded.rounds:
            self.assertNotIn(
                "available_interface_refs",
                round_receipt.request.payload,
            )


if __name__ == "__main__":
    unittest.main()
