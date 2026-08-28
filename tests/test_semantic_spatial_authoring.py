from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import replace

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.capabilities.semantic_spatial_authoring import (
    SemanticSpatialAuthoringReceipt,
    SemanticSpatialAuthoringResult,
    SemanticSpatialAuthoringStatus,
    author_semantic_spatial_option,
    semantic_spatial_authoring_contract,
    semantic_spatial_authoring_output,
    semantic_spatial_repair_feedback,
)
from archflow.state import (
    ComponentMaturity,
    ConstraintResponseStatus,
    DesignComponent,
    SpatialConnection,
    SpatialConstraintResponse,
)
from tests.test_spatial_proposals import _inputs, _proposal


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _success(
    request: ModelInvocationRequest,
    output: dict[str, object],
    *,
    bound_request: ModelInvocationRequest | None = None,
) -> ModelInvocationReceipt:
    encoded = _canonical_json(output)
    return ModelInvocationReceipt(
        receipt_id=f"scripted-{request.request_id}",
        status=ModelInvocationStatus.SUCCESS,
        request=request if bound_request is None else bound_request,
        provider_id="scripted-spatial-provider",
        model_id="scripted-spatial-model",
        provider_version="1",
        provider_fingerprint=hashlib.sha256(b"scripted").hexdigest(),
        input_bytes=len(request.payload_json.encode("utf-8")),
        output_bytes=len(encoded.encode("utf-8")),
        output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        output_json=encoded,
    )


class _ScriptedProvider:
    def __init__(self, output_factory) -> None:
        self.output_factory = output_factory
        self.requests: list[ModelInvocationRequest] = []

    async def invoke(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationReceipt:
        self.requests.append(request)
        output = self.output_factory(request)
        if isinstance(output, ModelInvocationReceipt):
            return output
        return _success(request, output)


def _semantic_proposal(brief, program):
    proposal = _proposal(
        option_id="dome-and-portico",
        program=program,
        brief=brief,
        two_levels=True,
    )
    evidence = proposal.evidence_refs
    return replace(
        proposal,
        components=(
            DesignComponent(
                component_id="building",
                parent_component_id=None,
                semantic_kind="public-building",
                intent="Coordinate the current schematic composition.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=("enclosure", "circulation"),
                source_refs=evidence,
            ),
            DesignComponent(
                component_id="dome-mass",
                parent_component_id="building",
                semantic_kind="dome-roof",
                intent="Establish the coarse domed hall mass.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=("west",),
                unresolved_child_roles=("oculus", "shell"),
                source_refs=evidence,
            ),
            DesignComponent(
                component_id="portico-mass",
                parent_component_id="building",
                semantic_kind="entrance-portico",
                intent="Establish the coarse entrance portico mass.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=("east",),
                unresolved_child_roles=("columns", "pediment"),
                source_refs=evidence,
            ),
        ),
    )


class SemanticSpatialAuthoringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        (
            self.brief,
            self.program,
            self.site,
            self.policy,
            self.state,
            self.maturity,
            self.gate,
        ) = _inputs()
        self.proposal = _semantic_proposal(self.brief, self.program)

    async def _author(self, provider):
        return await author_semantic_spatial_option(
            provider,
            request_id="semantic-spatial-option",
            state=self.state,
            maturity=self.maturity,
            phase_gate=self.gate,
            program=self.program,
            site_context=self.site,
            build_policy=self.policy,
        )

    def test_contract_publishes_exact_proposal_authority_literals(self) -> None:
        contract = semantic_spatial_authoring_contract()

        self.assertEqual("SemanticSpatialAuthoringContract@2", contract["schema"])
        self.assertEqual(
            {
                "proposal_only": True,
                "selected": False,
                "hard_usability_verdict": None,
                "design_development_complete": False,
                "execution_ready": False,
            },
            contract["proposal_fixed_values"],
        )

    def test_recursive_contract_matches_real_proposal_serializer_fields(self) -> None:
        contract = semantic_spatial_authoring_contract()
        nested = contract["proposal_nested_contracts"]
        payload = self.proposal.to_dict()

        self.assertEqual(
            set(payload["grid_basis"]),
            set(nested["grid_basis"]["exact_fields"]),
        )
        for field in ("levels", "zones", "components"):
            self.assertTrue(payload[field])
            self.assertEqual(
                set(payload[field][0]),
                set(nested[field]["item_exact_fields"]),
            )
        self.assertTrue(payload["volumes"])
        self.assertEqual(
            set(payload["volumes"][0]),
            set(nested["volumes"]["item_exact_fields"]),
        )
        self.assertEqual(
            set(payload["volumes"][0]["bounds"]),
            set(
                nested["volumes"]["item_exact_fields"]["bounds"][
                    "exact_fields"
                ]
            ),
        )
        evidence_ref = self.proposal.evidence_refs[0]
        connection = SpatialConnection(
            connection_id="contract-shape",
            source_zone_id="source-zone",
            target_zone_id="target-zone",
            relationship_refs=(evidence_ref,),
            directed=True,
            source_refs=(evidence_ref,),
        )
        response = SpatialConstraintResponse(
            response_id="contract-shape",
            constraint_ref=evidence_ref,
            status=ConstraintResponseStatus.SATISFIED,
            rationale="Exercise the generic serializer shape.",
            source_refs=(evidence_ref,),
        )
        self.assertEqual(
            set(connection.to_dict()),
            set(nested["connections"]["item_exact_fields"]),
        )
        self.assertEqual(
            set(response.to_dict()),
            set(nested["constraint_responses"]["item_exact_fields"]),
        )
        self.assertEqual(
            {"massing", "schematic", "developed", "detailed"},
            set(
                nested["components"]["item_exact_fields"]["maturity"][
                    "values"
                ]
            ),
        )
        self.assertEqual(
            {"satisfied", "risk", "not_applicable"},
            set(
                nested["constraint_responses"]["item_exact_fields"][
                    "status"
                ]["values"]
            ),
        )

    async def test_components_and_coarse_geometry_are_authored_together(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(
                request,
                self.proposal,
            )
        )

        result = await self._author(provider)

        self.assertIs(
            result.receipt.status,
            SemanticSpatialAuthoringStatus.ACCEPTED,
        )
        self.assertIsNotNone(result.option)
        self.assertEqual(
            {
                component.semantic_kind: component.volume_ids
                for component in result.proposal.components
            },
            {
                "public-building": (),
                "dome-roof": ("west",),
                "entrance-portico": ("east",),
            },
        )
        request = provider.requests[0]
        self.assertIs(request.phase, ModelPhase.SPATIAL_PROPOSAL)
        self.assertEqual(
            request.payload["exact_base_state_digest"],
            self.state.state_digest,
        )
        reference_contract = request.payload["reference_contract"]
        self.assertEqual(
            "SpatialAuthoringReferenceContract@1",
            reference_contract["schema"],
        )
        self.assertTrue(
            set(self.proposal.evidence_refs)
            <= set(reference_contract["allowed_evidence_refs"])
        )
        self.assertEqual([], reference_contract["allowed_expert_advice_refs"])
        self.assertFalse(reference_contract["reference_normalization_authority"])
        validation_contract = request.payload["validation_contract"]
        self.assertEqual(
            "SpatialAuthoringValidationContract@1",
            validation_contract["schema"],
        )
        self.assertEqual(
            self.site.observed_envelope.to_dict(),
            validation_contract["current_facts"]["observed_site_envelope"],
        )
        self.assertFalse(validation_contract["output_repair_authority"])
        encoded = _canonical_json(request.to_dict())
        for forbidden in (
            "workspace_path",
            "canonical_writer",
            "world_handle",
        ):
            self.assertNotIn(forbidden, encoded)
        receipt = result.receipt.to_dict()
        self.assertTrue(receipt["proposal_only"])
        self.assertFalse(receipt["selection_authority"])
        self.assertFalse(receipt["canonical_write_authority"])

    async def test_same_request_and_output_are_deterministic(self) -> None:
        def provider():
            return _ScriptedProvider(
                lambda request: semantic_spatial_authoring_output(
                    request,
                    self.proposal,
                )
            )

        first = await self._author(provider())
        second = await self._author(provider())

        self.assertEqual(first.proposal, second.proposal)
        self.assertEqual(first.option, second.option)
        self.assertEqual(first.receipt.receipt_id, second.receipt.receipt_id)

    async def test_stale_base_and_provider_request_mismatch_fail_closed(
        self,
    ) -> None:
        stale = _ScriptedProvider(
            lambda request: {
                **semantic_spatial_authoring_output(request, self.proposal),
                "exact_base_state_digest": "0" * 64,
            }
        )
        stale_result = await self._author(stale)
        self.assertEqual(
            stale_result.receipt.error_code,
            "spatial_authoring.stale_base",
        )

        def mismatched(request):
            old_request = ModelInvocationRequest.create(
                request_id="old-semantic-spatial-option",
                phase=ModelPhase.SPATIAL_PROPOSAL,
                checkpoint_digest=request.checkpoint_digest,
                context_digest=request.context_digest,
                payload=request.payload,
            )
            return _success(
                request,
                semantic_spatial_authoring_output(request, self.proposal),
                bound_request=old_request,
            )

        mismatch_result = await self._author(_ScriptedProvider(mismatched))
        self.assertEqual(
            mismatch_result.receipt.error_code,
            "spatial_authoring.provider_request_mismatch",
        )

    async def test_malformed_topology_ownership_and_sources_are_named(
        self,
    ) -> None:
        base = self.proposal.to_dict()

        def missing_parent(payload):
            payload["components"][1]["parent_component_id"] = "absent"

        def cycle(payload):
            payload["components"][0]["parent_component_id"] = "dome-mass"

        def duplicate_owner(payload):
            payload["components"][0]["volume_ids"] = ["west"]

        def unowned(payload):
            payload["components"][1]["volume_ids"] = []

        def unknown_source(payload):
            payload["components"][1]["source_refs"] = [
                "evidence:unknown"
            ]

        cases = (
            (
                "missing-parent",
                missing_parent,
                "spatial_authoring.topology_rejected",
            ),
            ("cycle", cycle, "spatial_authoring.topology_rejected"),
            (
                "duplicate-owner",
                duplicate_owner,
                "spatial_authoring.ownership_rejected",
            ),
            (
                "unowned",
                unowned,
                "spatial_authoring.ownership_rejected",
            ),
            (
                "unknown-source",
                unknown_source,
                "spatial_authoring.source_rejected",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                payload = copy.deepcopy(base)
                mutate(payload)
                provider = _ScriptedProvider(
                    lambda request, value=payload: {
                        **semantic_spatial_authoring_output(
                            request,
                            self.proposal,
                        ),
                        "proposal": value,
                    }
                )
                result = await self._author(provider)
                self.assertIs(
                    result.receipt.status,
                    SemanticSpatialAuthoringStatus.REJECTED,
                )
                self.assertEqual(result.receipt.error_code, expected)
                self.assertIsNone(result.proposal)

    async def test_repair_feedback_binds_exact_rejection_without_authority(self) -> None:
        unknown = self.proposal.to_dict()
        unknown["evidence_refs"] = [*unknown["evidence_refs"], "evidence:unknown"]
        rejected = await self._author(
            _ScriptedProvider(
                lambda request: {
                    **semantic_spatial_authoring_output(request, self.proposal),
                    "proposal": unknown,
                }
            )
        )
        feedback = semantic_spatial_repair_feedback(rejected)
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(
                request,
                self.proposal,
            )
        )

        repaired = await author_semantic_spatial_option(
            provider,
            request_id="semantic-spatial-option-repair",
            state=self.state,
            maturity=self.maturity,
            phase_gate=self.gate,
            program=self.program,
            site_context=self.site,
            build_policy=self.policy,
            repair_feedback=feedback,
        )

        self.assertIs(
            repaired.receipt.status,
            SemanticSpatialAuthoringStatus.ACCEPTED,
        )
        retained = provider.requests[0].payload["repair_feedback"]
        self.assertEqual(feedback, retained)
        self.assertTrue(retained["complete_replacement_required"])
        self.assertFalse(retained["field_patch_authority"])
        self.assertFalse(retained["validation_authority"])
        self.assertNotEqual(
            rejected.receipt.request.context_digest,
            repaired.receipt.request.context_digest,
        )

    async def test_provider_failure_cannot_become_repair_feedback(self) -> None:
        failed_request = ModelInvocationRequest.create(
            request_id="provider-timeout",
            phase=ModelPhase.SPATIAL_PROPOSAL,
            checkpoint_digest=self.state.state_digest,
            context_digest="0" * 64,
            payload={"schema": "TimeoutFixture@1"},
        )
        failed_receipt = ModelInvocationReceipt(
            receipt_id="provider-timeout",
            status=ModelInvocationStatus.TIMEOUT,
            request=failed_request,
            provider_id="scripted-spatial-provider",
            model_id="scripted-spatial-model",
            provider_version="1",
            provider_fingerprint=hashlib.sha256(b"scripted").hexdigest(),
            input_bytes=len(failed_request.payload_json.encode("utf-8")),
            output_bytes=0,
            output_sha256=None,
            error_code="model.timeout",
            message="provider exceeded deadline",
        )
        result = SemanticSpatialAuthoringResult(
            receipt=SemanticSpatialAuthoringReceipt(
                receipt_id="provider-timeout",
                status=SemanticSpatialAuthoringStatus.PROVIDER_FAILED,
                request=failed_receipt.request,
                model_receipt=failed_receipt,
                proposal_digest=None,
                option_digest=None,
                error_code="model.timeout",
                message="provider exceeded deadline",
            )
        )
        with self.assertRaisesRegex(ValueError, "deterministic"):
            semantic_spatial_repair_feedback(result)

    async def test_alternative_context_is_identity_bound_and_non_authoritative(self) -> None:
        option = (await self._author(
            _ScriptedProvider(
                lambda request: semantic_spatial_authoring_output(
                    request,
                    self.proposal,
                )
            )
        )).option
        assert option is not None
        projection = {
            "schema": "SchematicOptionDecisionProjection@1",
            "option_id": option.option_id,
            "option_digest": option.option_digest,
            "topology_signature": option.topology_signature,
            "decision_projection_only": True,
        }
        context = {
            "schema": "SpatialAlternativeAuthoringContext@1",
            "excluded_option_ids": [option.option_id],
            "excluded_option_digests": [option.option_digest],
            "excluded_topology_signatures": [option.topology_signature],
            "existing_option_projection": projection,
            "instructions": "Author one complete distinct alternative.",
            "complete_alternative_required": True,
            "option_mutation_authority": False,
            "selection_authority": False,
            "validation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(
                request,
                self.proposal,
            )
        )

        await author_semantic_spatial_option(
            provider,
            request_id="semantic-spatial-alternative",
            state=self.state,
            maturity=self.maturity,
            phase_gate=self.gate,
            program=self.program,
            site_context=self.site,
            build_policy=self.policy,
            alternative_context=context,
        )

        retained = provider.requests[0].payload["alternative_context"]
        self.assertEqual(context, retained)
        self.assertFalse(retained["option_mutation_authority"])
        self.assertFalse(retained["selection_authority"])
        stale = copy.deepcopy(context)
        stale["excluded_option_ids"] = ["another-option"]
        with self.assertRaisesRegex(ValueError, "exactly excluded"):
            await author_semantic_spatial_option(
                provider,
                request_id="semantic-spatial-stale-alternative",
                state=self.state,
                maturity=self.maturity,
                phase_gate=self.gate,
                program=self.program,
                site_context=self.site,
                build_policy=self.policy,
                alternative_context=stale,
            )

    async def test_architectural_revision_context_binds_exact_failed_predecessor(self) -> None:
        context = {
            "schema": "SpatialArchitecturalRevisionContext@1",
            "predecessor_proposal": self.proposal.to_dict(),
            "predecessor_proposal_digest": self.proposal.proposal_digest,
            "architectural_contract_digest": "1" * 64,
            "architectural_receipt_digest": "2" * 64,
            "failed_mandatory_findings": [
                {
                    "schema": "ArchitecturalRevisionFailure@1",
                    "criterion": {
                        "schema": "ProjectArchitecturalCriterion@1",
                        "criterion_id": "usable-main-entry",
                        "measurement_key": "usable-main-entry-count",
                        "operator": "minimum",
                        "expected_json": "1",
                        "unit": "count",
                        "mandatory": True,
                        "source_refs": ["brief:main-entry"],
                        "component_ids": [],
                        "geometry_object_ids": [],
                        "obligation_refs": [],
                    },
                    "finding": {
                        "schema": "ArchitecturalUsabilityFinding@1",
                        "criterion_id": "usable-main-entry",
                        "status": "fail",
                        "code": "criterion-failed",
                        "expected_json": "1",
                        "observed_json": "0",
                        "unit": "count",
                        "source_refs": ["brief:main-entry"],
                        "component_ids": [],
                        "geometry_object_ids": [],
                        "obligation_refs": [],
                        "evidence_refs": ["observation:main-entry"],
                        "detail": "No usable main entry was observed.",
                        "mandatory": True,
                    },
                }
            ],
            "instructions": (
                "Return the complete successor option with the same option id; "
                "revise semantic components and coarse geometry together."
            ),
            "complete_successor_required": True,
            "preserve_option_identity": True,
            "component_patch_authority": False,
            "geometry_patch_authority": False,
            "selection_authority": False,
            "validation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(
                request,
                self.proposal,
            )
        )

        result = await author_semantic_spatial_option(
            provider,
            request_id="semantic-spatial-architectural-revision",
            state=self.state,
            maturity=self.maturity,
            phase_gate=self.gate,
            program=self.program,
            site_context=self.site,
            build_policy=self.policy,
            revision_context=context,
        )

        self.assertIs(
            result.receipt.status,
            SemanticSpatialAuthoringStatus.ACCEPTED,
        )
        self.assertEqual(
            provider.requests[0].payload["revision_context"],
            context,
        )
        stale = copy.deepcopy(context)
        stale["predecessor_proposal_digest"] = "3" * 64
        with self.assertRaisesRegex(ValueError, "proposal digest changed"):
            await author_semantic_spatial_option(
                provider,
                request_id="semantic-spatial-stale-architectural-revision",
                state=self.state,
                maturity=self.maturity,
                phase_gate=self.gate,
                program=self.program,
                site_context=self.site,
                build_policy=self.policy,
                revision_context=stale,
            )

    async def test_provider_failure_and_malformed_envelope_are_typed(
        self,
    ) -> None:
        def failed(request):
            return ModelInvocationReceipt(
                receipt_id="scripted-timeout",
                status=ModelInvocationStatus.TIMEOUT,
                request=request,
                provider_id="scripted-spatial-provider",
                model_id="scripted-spatial-model",
                provider_version="1",
                provider_fingerprint=hashlib.sha256(b"scripted").hexdigest(),
                input_bytes=len(request.payload_json.encode("utf-8")),
                output_bytes=0,
                output_sha256=None,
                error_code="model.timeout",
                message="scripted timeout",
            )

        failed_result = await self._author(_ScriptedProvider(failed))
        self.assertIs(
            failed_result.receipt.status,
            SemanticSpatialAuthoringStatus.PROVIDER_FAILED,
        )
        self.assertEqual(failed_result.receipt.error_code, "model.timeout")

        malformed_result = await self._author(
            _ScriptedProvider(lambda request: {"schema": "wrong"})
        )
        self.assertEqual(
            malformed_result.receipt.error_code,
            "spatial_authoring.malformed_output",
        )

    async def test_false_hard_verdict_is_rejected_with_exact_diagnostic(self) -> None:
        invalid = self.proposal.to_dict()
        invalid["hard_usability_verdict"] = False
        provider = _ScriptedProvider(
            lambda request: {
                **semantic_spatial_authoring_output(request, self.proposal),
                "proposal": invalid,
            }
        )

        result = await self._author(provider)

        self.assertIs(
            SemanticSpatialAuthoringStatus.REJECTED,
            result.receipt.status,
        )
        self.assertEqual(
            "spatial_authoring.proposal_rejected",
            result.receipt.error_code,
        )
        self.assertEqual(
            "SpatialProposalError: spatial proposal acquired forbidden authority",
            result.receipt.message,
        )

    async def test_stale_input_context_stops_before_provider(self) -> None:
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(
                request,
                self.proposal,
            )
        )
        stale_state = replace(
            self.state,
            branch=replace(self.state.branch, epoch=1),
        )

        with self.assertRaisesRegex(ValueError, "stale or cross-branch"):
            await author_semantic_spatial_option(
                provider,
                request_id="semantic-spatial-option",
                state=stale_state,
                maturity=self.maturity,
                phase_gate=self.gate,
                program=self.program,
                site_context=self.site,
                build_policy=self.policy,
            )
        self.assertEqual(provider.requests, [])
