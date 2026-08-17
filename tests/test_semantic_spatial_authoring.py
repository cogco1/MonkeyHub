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
    SemanticSpatialAuthoringStatus,
    author_semantic_spatial_option,
    semantic_spatial_authoring_output,
)
from archflow.state import ComponentMaturity, DesignComponent
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
