from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.production.provider_runtime import AuthorizedAsyncModelProvider, activate_codex_agent_cli_provider, activate_model_provider, bind_async_model_provider, model_responsibility_contract
from archflow.production.responsibility import ContractConflict, HandoverDecision, InvalidInvocationEnvelope, InvalidProviderReceipt, InvocationEnvelope, NoProductionAuthority, ProviderIdentity, ProviderInvocationFailed, ProviderLifecycleError, ProviderMode, ProviderUnavailable, ResponsibilityContract, ResponsibilityRouter, ResolutionStatus, StaleAuthority, StaleHandover, UnknownProvider, create_responsibility_control_plane


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _identity(label: str) -> ProviderIdentity:
    return ProviderIdentity(
        provider_id=f"provider.{label}",
        version="1.0.0",
        fingerprint=_fingerprint(label),
    )


def _contract() -> ResponsibilityContract:
    return ResponsibilityContract(
        responsibility_id="geometry.authoring",
        contract_owner_id="archflow.geometry.contract",
        request_contract="GeometryAuthoringRequest@1",
        receipt_contract="GeometryAuthoringReceipt@1",
    )


class ProductionResponsibilityTests(unittest.TestCase):
    def _registered_router(self):
        router, reconciler = create_responsibility_control_plane()
        router: ResponsibilityRouter[dict[str, object], dict[str, object]] = (
            router
        )
        identity = _identity("a")
        calls: list[tuple[dict[str, object], object]] = []

        def handler(request, authority):  # type: ignore[no-untyped-def]
            calls.append((request, authority))
            return {"provider": "a", "request": request}

        registration = router.register(_contract(), identity, handler)
        return router, reconciler, identity, calls, registration

    def _verify(self, reconciler, identity):  # type: ignore[no-untyped-def]
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:registration-reviewed",),
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.SHADOW,
            target_mode=ProviderMode.VERIFIED,
            evidence_refs=("evidence:shadow-comparison-passed",),
        )

    def _activate(self, router, reconciler, identity):  # type: ignore[no-untyped-def]
        state = router.state(_contract().responsibility_id)
        return reconciler.activate(
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest=state.binding_digest,
                target_provider=identity,
                verification_evidence_refs=("evidence:cutover-approved",),
                reason="verified replacement",
            )
        )

    def test_registration_does_not_grant_production_authority(self):
        router, _, identity, calls, registration = self._registered_router()

        self.assertEqual(ProviderMode.REGISTERED, registration.mode)
        resolution = router.resolve(_contract().responsibility_id)
        self.assertEqual(ResolutionStatus.UNAVAILABLE, resolution.status)
        self.assertIsNone(resolution.active_provider)
        self.assertEqual([], calls)
        self.assertFalse(registration.to_dict()["canonical_write_authority"])
        self.assertEqual(identity.to_dict(), registration.to_dict()["provider"])

    def test_responsibility_has_exactly_one_contract_owner(self):
        router, _, _, _, _ = self._registered_router()
        conflicting = ResponsibilityContract(
            responsibility_id=_contract().responsibility_id,
            contract_owner_id="another.contract.owner",
            request_contract=_contract().request_contract,
            receipt_contract=_contract().receipt_contract,
        )

        with self.assertRaises(ContractConflict):
            router.define(conflicting)

    def test_lifecycle_requires_shadow_then_verified_before_activation(self):
        router, reconciler, identity, _, _ = self._registered_router()

        with self.assertRaises(ProviderLifecycleError):
            self._activate(router, reconciler, identity)

        self._verify(reconciler, identity)
        receipt = self._activate(router, reconciler, identity)

        self.assertEqual(1, receipt.next_state.authority_epoch)
        self.assertEqual(identity, receipt.activated_provider)
        self.assertEqual(ProviderMode.ACTIVE, router.registration(
            _contract().responsibility_id, identity
        ).mode)
        self.assertFalse(receipt.canonical_write_authority)

    def test_shadow_invocation_has_no_production_or_write_authority(self):
        router, reconciler, identity, calls, _ = self._registered_router()
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:shadow-authorized",),
        )

        envelope = router.invoke_shadow(
            _contract().responsibility_id, identity, {"same": "request"}
        )

        self.assertEqual(1, len(calls))
        self.assertFalse(envelope.authority.production_authority)
        self.assertFalse(envelope.authority.canonical_write_authority)
        self.assertFalse(envelope.to_dict()["canonical_write_authority"])
        with self.assertRaises(NoProductionAuthority):
            router.validate_envelope(envelope)

    def test_provider_facing_router_has_no_lifecycle_or_activation_api(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("self-elevation")
        attempts = []

        def handler(request, authority):  # type: ignore[no-untyped-def]
            attempts.append(hasattr(router, "qualify_provider"))
            attempts.append(hasattr(router, "transition_provider"))
            attempts.append(hasattr(router, "activate"))
            attempts.append(hasattr(router, "_transition_provider"))
            attempts.append(hasattr(router, "_activate"))
            attempts.append(hasattr(router, "_sign"))
            attempts.append(hasattr(router, "_signing_key"))
            return {"attempts": attempts}

        router.register(_contract(), identity, handler)
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:independent-shadow-approval",),
        )

        envelope = router.invoke_shadow(
            _contract().responsibility_id, identity, {"same": "request"}
        )

        self.assertEqual([False] * 7, attempts)
        self.assertEqual(
            ProviderMode.SHADOW,
            router.registration(_contract().responsibility_id, identity).mode,
        )
        self.assertEqual([False] * 7, envelope.provider_receipt["attempts"])

    def test_router_signed_grants_and_envelopes_cannot_cross_or_be_forged(self):
        router, reconciler, identity, _, _ = self._registered_router()
        self._verify(reconciler, identity)
        self._activate(router, reconciler, identity)
        envelope = router.invoke(_contract().responsibility_id, {"x": 1})

        forged_token = replace(
            envelope.authority,
            grant_nonce="f" * 32,
            grant_signature="0" * 64,
        )
        forged_envelope = replace(
            envelope,
            authority=forged_token,
            envelope_signature="0" * 64,
        )
        with self.assertRaises(InvalidInvocationEnvelope):
            router.validate_envelope(forged_envelope)

        other_router, other_reconciler = create_responsibility_control_plane()
        other_router.register(
            _contract(), identity, lambda request, authority: {"other": True}
        )
        self._verify(other_reconciler, identity)
        self._activate(other_router, other_reconciler, identity)
        with self.assertRaises(InvalidInvocationEnvelope):
            other_router.validate_envelope(envelope)

    def test_envelope_freezes_receipt_and_rejects_digest_tampering(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("freezing")
        mutable_receipt = {"items": []}
        router.register(
            _contract(), identity, lambda request, authority: mutable_receipt
        )
        self._verify(reconciler, identity)
        self._activate(router, reconciler, identity)

        envelope = router.invoke(_contract().responsibility_id, {"x": 1})
        mutable_receipt["items"].append("mutated-after-return")

        self.assertEqual({"items": []}, envelope.provider_receipt)
        router.validate_envelope(envelope)
        with self.assertRaisesRegex(ValueError, "digest does not match"):
            InvocationEnvelope(
                authority=envelope.authority,
                provider_receipt_json='{"tampered":true}',
                provider_receipt_digest=envelope.provider_receipt_digest,
                envelope_signature=envelope.envelope_signature,
            )

    def test_provider_receipt_rejects_machine_paths_and_non_string_keys(self):
        for label, receipt in (
            ("machine-path", {"artifact": r"D:\\private\\result.json"}),
            ("non-string-key", {1: "not a JSON object contract"}),
        ):
            with self.subTest(label=label):
                router, reconciler = create_responsibility_control_plane()
                identity = _identity(label)
                router.register(
                    _contract(),
                    identity,
                    lambda request, authority, value=receipt: value,
                )
                self._verify(reconciler, identity)
                self._activate(router, reconciler, identity)

                with self.assertRaises(InvalidProviderReceipt):
                    router.invoke(_contract().responsibility_id, {"x": 1})

    def test_portable_regex_contract_is_not_misclassified_as_unc_path(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("portable-regex")
        pattern = r"^(?![Ff][Ii][Ll][Ee]:)(?![A-Za-z]:[\\/])[A-Za-z]+$"
        router.register(
            _contract(),
            identity,
            lambda request, authority: {"portable_pattern": pattern},
        )
        self._verify(reconciler, identity)
        self._activate(router, reconciler, identity)

        envelope = router.invoke(_contract().responsibility_id, {"x": 1})

        self.assertEqual(pattern, envelope.provider_receipt["portable_pattern"])
        router.validate_envelope(envelope)

    def test_file_uri_prohibition_text_is_not_itself_a_file_uri(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("file-uri-prose")
        prose = "Machine paths, file: URIs, and file:// URIs are rejected."
        router.register(
            _contract(),
            identity,
            lambda request, authority: {"instructions": prose},
        )
        self._verify(reconciler, identity)
        self._activate(router, reconciler, identity)

        envelope = router.invoke(_contract().responsibility_id, {"x": 1})

        self.assertEqual(prose, envelope.provider_receipt["instructions"])
        router.validate_envelope(envelope)

    def test_handover_receipt_rejects_contradictory_transition_evidence(self):
        router, reconciler, identity, _, _ = self._registered_router()
        self._verify(reconciler, identity)
        receipt = self._activate(router, reconciler, identity)

        with self.assertRaisesRegex(ValueError, "retired provider"):
            replace(receipt, retired_provider=identity)

        conflicting_contract = ResponsibilityContract(
            responsibility_id=_contract().responsibility_id,
            contract_owner_id="another.contract.owner",
            request_contract=_contract().request_contract,
            receipt_contract=_contract().receipt_contract,
        )
        with self.assertRaisesRegex(ValueError, "cannot change"):
            replace(
                receipt,
                next_state=replace(
                    receipt.next_state,
                    contract=conflicting_contract,
                ),
            )

    def test_exact_base_switch_and_stale_switch_rejection(self):
        router, reconciler, identity_a, _, _ = self._registered_router()
        self._verify(reconciler, identity_a)
        stale_base = router.state(_contract().responsibility_id).binding_digest
        first = self._activate(router, reconciler, identity_a)

        identity_b = _identity("b")
        router.register(_contract(), identity_b, lambda request, authority: {"b": True})
        self._verify(reconciler, identity_b)
        decision = HandoverDecision(
            responsibility_id=_contract().responsibility_id,
            expected_binding_digest=stale_base,
            target_provider=identity_b,
            verification_evidence_refs=("evidence:b-passed",),
            reason="stale cutover",
        )

        with self.assertRaises(StaleHandover):
            reconciler.activate(decision)

        self.assertEqual(first.next_state, router.state(_contract().responsibility_id))
        self.assertEqual(ProviderMode.ACTIVE, router.registration(
            _contract().responsibility_id, identity_a
        ).mode)
        self.assertEqual(ProviderMode.VERIFIED, router.registration(
            _contract().responsibility_id, identity_b
        ).mode)

    def test_concurrent_handover_has_exactly_one_winner(self):
        router, reconciler, identity_a, _, _ = self._registered_router()
        self._verify(reconciler, identity_a)
        self._activate(router, reconciler, identity_a)

        targets = [_identity("b"), _identity("c")]
        for target in targets:
            router.register(_contract(), target, lambda request, authority: {"ok": True})
            self._verify(reconciler, target)
        base = router.state(_contract().responsibility_id).binding_digest

        def switch(target):  # type: ignore[no-untyped-def]
            return reconciler.activate(
                HandoverDecision(
                    responsibility_id=_contract().responsibility_id,
                    expected_binding_digest=base,
                    target_provider=target,
                    verification_evidence_refs=("evidence:concurrent-cutover",),
                    reason="concurrent candidate",
                )
            )

        successes = []
        failures = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(switch, target) for target in targets]
            for future in futures:
                try:
                    successes.append(future.result())
                except StaleHandover as exc:
                    failures.append(exc)

        self.assertEqual(1, len(successes))
        self.assertEqual(1, len(failures))
        self.assertEqual(2, router.state(_contract().responsibility_id).authority_epoch)

    def test_receipt_construction_failure_keeps_old_binding_unchanged(self):
        router, reconciler, identity_a, _, _ = self._registered_router()
        self._verify(reconciler, identity_a)
        state_before = router.state(_contract().responsibility_id)

        with patch(
            "archflow.production.responsibility.HandoverReceipt",
            side_effect=RuntimeError("receipt construction failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "receipt construction failed"):
                self._activate(router, reconciler, identity_a)

        self.assertEqual(state_before, router.state(_contract().responsibility_id))
        self.assertEqual(ProviderMode.VERIFIED, router.registration(
            _contract().responsibility_id, identity_a
        ).mode)

    def test_unregistered_exact_identity_cannot_activate(self):
        router, reconciler, identity_a, _, _ = self._registered_router()
        self._verify(reconciler, identity_a)
        mismatched = ProviderIdentity(
            provider_id=identity_a.provider_id,
            version=identity_a.version,
            fingerprint=_fingerprint("different-build"),
        )

        with self.assertRaises(UnknownProvider):
            self._activate(router, reconciler, mismatched)

    def test_old_generation_result_is_rejected_after_switch(self):
        router, reconciler = create_responsibility_control_plane()
        router: ResponsibilityRouter[dict[str, object], dict[str, object]] = (
            router
        )
        identity_a = _identity("a")
        started = threading.Event()
        release = threading.Event()

        def slow_handler(request, authority):  # type: ignore[no-untyped-def]
            started.set()
            self.assertTrue(release.wait(5))
            return {"provider": "a"}

        router.register(_contract(), identity_a, slow_handler)
        self._verify(reconciler, identity_a)
        self._activate(router, reconciler, identity_a)

        identity_b = _identity("b")
        router.register(_contract(), identity_b, lambda request, authority: {"provider": "b"})
        self._verify(reconciler, identity_b)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(router.invoke, _contract().responsibility_id, {"x": 1})
            self.assertTrue(started.wait(5))
            base = router.state(_contract().responsibility_id).binding_digest
            reconciler.activate(
                HandoverDecision(
                    responsibility_id=_contract().responsibility_id,
                    expected_binding_digest=base,
                    target_provider=identity_b,
                    verification_evidence_refs=("evidence:b-cutover",),
                    reason="replace in-flight provider",
                )
            )
            release.set()
            with self.assertRaises(StaleAuthority):
                future.result()

    def test_provider_failure_never_calls_a_nonactive_fallback(self):
        router, reconciler = create_responsibility_control_plane()
        router: ResponsibilityRouter[dict[str, object], dict[str, object]] = (
            router
        )
        identity_a = _identity("a")
        identity_b = _identity("b")
        fallback_calls = []

        def failing(request, authority):  # type: ignore[no-untyped-def]
            raise ValueError("provider a failed")

        def fallback(request, authority):  # type: ignore[no-untyped-def]
            fallback_calls.append(request)
            return {"provider": "b"}

        router.register(_contract(), identity_a, failing)
        router.register(_contract(), identity_b, fallback)
        self._verify(reconciler, identity_a)
        self._verify(reconciler, identity_b)
        self._activate(router, reconciler, identity_a)

        with self.assertRaisesRegex(ProviderInvocationFailed, "ValueError"):
            router.invoke(_contract().responsibility_id, {"x": 1})
        self.assertEqual([], fallback_calls)
        self.assertEqual(identity_a, router.resolve(
            _contract().responsibility_id
        ).active_provider)

    def test_provider_failure_formatting_is_bounded_and_does_not_call_str(self):
        class UnprintableProviderError(Exception):
            def __str__(self):
                raise RuntimeError("must not be called")

        router, reconciler = create_responsibility_control_plane()
        identity = _identity("unprintable")

        def failing(request, authority):  # type: ignore[no-untyped-def]
            raise UnprintableProviderError()

        router.register(_contract(), identity, failing)
        self._verify(reconciler, identity)
        self._activate(router, reconciler, identity)

        with self.assertRaisesRegex(
            ProviderInvocationFailed, "UnprintableProviderError"
        ):
            router.invoke(_contract().responsibility_id, {"x": 1})

    def test_a_to_b_to_a_uses_a_new_epoch_and_prevents_aba(self):
        router, reconciler, identity_a, _, _ = self._registered_router()
        self._verify(reconciler, identity_a)
        receipt_a1 = self._activate(router, reconciler, identity_a)

        identity_b = _identity("b")
        router.register(_contract(), identity_b, lambda request, authority: {"b": True})
        self._verify(reconciler, identity_b)
        receipt_b = reconciler.activate(
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest=router.state(
                    _contract().responsibility_id
                ).binding_digest,
                target_provider=identity_b,
                verification_evidence_refs=("evidence:b-cutover",),
                reason="move to b",
            )
        )

        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity_a,
            expected_mode=ProviderMode.RETIRED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:a-requalification-started",),
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity_a,
            expected_mode=ProviderMode.SHADOW,
            target_mode=ProviderMode.VERIFIED,
            evidence_refs=("evidence:a-requalified",),
        )
        receipt_a2 = reconciler.activate(
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest=router.state(
                    _contract().responsibility_id
                ).binding_digest,
                target_provider=identity_a,
                verification_evidence_refs=("evidence:a-return-approved",),
                reason="explicit return to a",
            )
        )

        self.assertEqual((1, 2, 3), (
            receipt_a1.next_state.authority_epoch,
            receipt_b.next_state.authority_epoch,
            receipt_a2.next_state.authority_epoch,
        ))
        self.assertNotEqual(
            receipt_a1.next_state.binding_digest,
            receipt_a2.next_state.binding_digest,
        )

    def test_stable_serialization_excludes_handler_and_machine_path(self):
        class Handler:
            machine_path = r"D:\\private\\provider.exe"

            def __call__(self, request, authority):  # type: ignore[no-untyped-def]
                return {"ok": True}

        router, _ = create_responsibility_control_plane()
        router: ResponsibilityRouter[dict[str, object], dict[str, object]] = (
            router
        )
        identity = _identity("portable")
        registration = router.register(_contract(), identity, Handler())
        payload = json.dumps(registration.to_dict(), sort_keys=True)

        self.assertNotIn("private", payload)
        self.assertNotIn("provider.exe", payload)
        self.assertNotIn("handler", payload.lower())
        self.assertFalse(registration.to_dict()["canonical_write_authority"])

        with self.assertRaisesRegex(ValueError, "versioned schema ref"):
            ResponsibilityContract(
                responsibility_id="geometry.other",
                contract_owner_id="archflow.geometry.contract",
                request_contract=r"D:\\private\\request.json",
                receipt_contract="GeometryAuthoringReceipt@1",
            )
        with self.assertRaisesRegex(ValueError, "machine-local path"):
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest="0" * 64,
                target_provider=identity,
                verification_evidence_refs=("evidence:portable",),
                reason=r"approved from D:\\private\\decision.txt",
            )
        with self.assertRaisesRegex(ValueError, "machine-local path"):
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest="0" * 64,
                target_provider=identity,
                verification_evidence_refs=("evidence:file:///D:/private/x.txt",),
                reason="portable approval",
            )


class AsyncProductionResponsibilityTests(unittest.IsolatedAsyncioTestCase):
    def _activate(self, router, reconciler, identity):  # type: ignore[no-untyped-def]
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:async-shadow",),
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.SHADOW,
            target_mode=ProviderMode.VERIFIED,
            evidence_refs=("evidence:async-verified",),
        )
        return reconciler.activate(
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest=router.state(
                    _contract().responsibility_id
                ).binding_digest,
                target_provider=identity,
                verification_evidence_refs=("evidence:async-cutover",),
                reason="activate async provider",
            )
        )

    async def test_async_provider_receives_bounded_active_authority(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("async")
        observed = []

        async def handler(request, authority):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            observed.append(authority)
            return {"request": request, "epoch": authority.authority_epoch}

        router.register(_contract(), identity, handler)
        self._activate(router, reconciler, identity)

        envelope = await router.invoke_async(
            _contract().responsibility_id,
            {"step": "spatial"},
        )

        self.assertEqual(1, len(observed))
        self.assertTrue(observed[0].production_authority)
        self.assertEqual(1, envelope.provider_receipt["epoch"])
        self.assertEqual(
            router.state(_contract().responsibility_id),
            router.validate_envelope(envelope),
        )

    async def test_async_result_is_rejected_after_inflight_cutover(self):
        router, reconciler = create_responsibility_control_plane()
        identity_a = _identity("async-a")
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_handler(request, authority):  # type: ignore[no-untyped-def]
            started.set()
            await release.wait()
            return {"provider": "a"}

        router.register(_contract(), identity_a, slow_handler)
        self._activate(router, reconciler, identity_a)
        identity_b = _identity("async-b")
        router.register(
            _contract(),
            identity_b,
            lambda request, authority: {"provider": "b"},
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity_b,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:b-shadow",),
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity_b,
            expected_mode=ProviderMode.SHADOW,
            target_mode=ProviderMode.VERIFIED,
            evidence_refs=("evidence:b-verified",),
        )

        task = asyncio.create_task(
            router.invoke_async(
                _contract().responsibility_id,
                {"step": "geometry"},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        reconciler.activate(
            HandoverDecision(
                responsibility_id=_contract().responsibility_id,
                expected_binding_digest=router.state(
                    _contract().responsibility_id
                ).binding_digest,
                target_provider=identity_b,
                verification_evidence_refs=("evidence:b-cutover",),
                reason="replace in-flight async provider",
            )
        )
        release.set()

        with self.assertRaises(StaleAuthority):
            await task

    async def test_async_shadow_has_no_production_authority(self):
        router, reconciler = create_responsibility_control_plane()
        identity = _identity("async-shadow")

        async def handler(request, authority):  # type: ignore[no-untyped-def]
            return {"production": authority.production_authority}

        router.register(_contract(), identity, handler)
        reconciler.qualify_provider(
            _contract().responsibility_id,
            identity,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:async-shadow",),
        )

        envelope = await router.invoke_shadow_async(
            _contract().responsibility_id,
            identity,
            {"step": "qualification"},
        )

        self.assertFalse(envelope.provider_receipt["production"])
        with self.assertRaises(NoProductionAuthority):
            router.validate_envelope(envelope)

    async def test_async_failure_does_not_call_verified_fallback(self):
        router, reconciler = create_responsibility_control_plane()
        active = _identity("async-fail")
        shadow = _identity("async-unused")
        fallback_calls = []

        async def failing(request, authority):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            raise ValueError("async failure")

        router.register(_contract(), active, failing)
        self._activate(router, reconciler, active)
        router.register(
            _contract(),
            shadow,
            lambda request, authority: fallback_calls.append(request) or {"ok": True},
        )
        reconciler.qualify_provider(
            _contract().responsibility_id,
            shadow,
            expected_mode=ProviderMode.REGISTERED,
            target_mode=ProviderMode.SHADOW,
            evidence_refs=("evidence:unused-shadow",),
        )

        with self.assertRaisesRegex(ProviderInvocationFailed, "ValueError"):
            await router.invoke_async(
                _contract().responsibility_id,
                {"step": "spatial"},
            )
        self.assertEqual([], fallback_calls)


RESPONSIBILITY_ID = "model.semantic-spatial"
MODEL_CONTRACT = model_responsibility_contract(
    responsibility_id=RESPONSIBILITY_ID,
    contract_owner_id="archflow.semantic-spatial",
)
MODEL_IDENTITY = ProviderIdentity(
    provider_id="provider.scripted",
    version="1.0.0",
    fingerprint=hashlib.sha256(b"scripted-provider").hexdigest(),
)


def _request() -> ModelInvocationRequest:
    return ModelInvocationRequest.create(
        request_id="semantic-spatial-request",
        phase=ModelPhase.SPATIAL_PROPOSAL,
        checkpoint_digest=hashlib.sha256(b"checkpoint").hexdigest(),
        context_digest=hashlib.sha256(b"context").hexdigest(),
        payload={"schema": "TestSemanticSpatialPrompt@1"},
    )


def _model_receipt(
    request: ModelInvocationRequest,
    *,
    identity: ProviderIdentity = MODEL_IDENTITY,
) -> ModelInvocationReceipt:
    output = {"schema": "TestSemanticSpatialOutput@1"}
    encoded = json.dumps(output, sort_keys=True, separators=(",", ":"))
    return ModelInvocationReceipt(
        receipt_id="scripted-model-receipt",
        status=ModelInvocationStatus.SUCCESS,
        request=request,
        provider_id=identity.provider_id,
        model_id="scripted-model",
        provider_version=identity.version,
        provider_fingerprint=identity.fingerprint,
        input_bytes=len(request.payload_json.encode("utf-8")),
        output_bytes=len(encoded.encode("utf-8")),
        output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        output_json=encoded,
    )


class _ScriptedModelProvider:
    def __init__(self, receipt_factory=_model_receipt) -> None:  # type: ignore[no-untyped-def]
        self.receipt_factory = receipt_factory
        self.requests = []

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.receipt_factory(request)


def _active(provider):  # type: ignore[no-untyped-def]
    router, reconciler = create_responsibility_control_plane()
    router.register(
        MODEL_CONTRACT,
        MODEL_IDENTITY,
        bind_async_model_provider(provider),
    )
    reconciler.qualify_provider(
        RESPONSIBILITY_ID,
        MODEL_IDENTITY,
        expected_mode=ProviderMode.REGISTERED,
        target_mode=ProviderMode.SHADOW,
        evidence_refs=("evidence:model-shadow",),
    )
    reconciler.qualify_provider(
        RESPONSIBILITY_ID,
        MODEL_IDENTITY,
        expected_mode=ProviderMode.SHADOW,
        target_mode=ProviderMode.VERIFIED,
        evidence_refs=("evidence:model-verified",),
    )
    reconciler.activate(
        HandoverDecision(
            responsibility_id=RESPONSIBILITY_ID,
            expected_binding_digest=router.state(
                RESPONSIBILITY_ID
            ).binding_digest,
            target_provider=MODEL_IDENTITY,
            verification_evidence_refs=("evidence:model-cutover",),
            reason="activate typed model provider",
        )
    )
    return router


class AuthorizedModelProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_provider_is_api_neutral_and_exactly_bound(self):
        provider = _ScriptedModelProvider()
        envelopes = []
        authorized = AuthorizedAsyncModelProvider(
            _active(provider),
            RESPONSIBILITY_ID,
            envelopes.append,
        )
        request = _request()

        receipt = await authorized.invoke(request)

        self.assertEqual(request, receipt.request)
        self.assertEqual(MODEL_IDENTITY.provider_id, receipt.provider_id)
        self.assertEqual([request], provider.requests)
        self.assertEqual(1, len(envelopes))
        self.assertEqual(
            request.to_dict(),
            envelopes[0].provider_receipt["request"],
        )

    async def test_provider_identity_drift_is_rejected_after_envelope(self):
        other = replace(
            MODEL_IDENTITY,
            fingerprint=hashlib.sha256(b"other-build").hexdigest(),
        )
        provider = _ScriptedModelProvider(
            lambda request: _model_receipt(request, identity=other)
        )
        envelopes = []
        authorized = AuthorizedAsyncModelProvider(
            _active(provider),
            RESPONSIBILITY_ID,
            envelopes.append,
        )

        with self.assertRaisesRegex(InvalidProviderReceipt, "identity disagrees"):
            await authorized.invoke(_request())
        self.assertEqual([], envelopes)

    async def test_request_drift_is_rejected_after_envelope(self):
        stale = replace(_request(), request_id="stale-spatial-request")
        provider = _ScriptedModelProvider(
            lambda request: _model_receipt(stale)
        )
        authorized = AuthorizedAsyncModelProvider(
            _active(provider),
            RESPONSIBILITY_ID,
        )

        with self.assertRaisesRegex(
            InvalidProviderReceipt,
            "exact invocation request",
        ):
            await authorized.invoke(_request())

    async def test_unavailable_responsibility_never_calls_provider(self):
        provider = _ScriptedModelProvider()
        router, _ = create_responsibility_control_plane()
        router.register(
            MODEL_CONTRACT,
            MODEL_IDENTITY,
            bind_async_model_provider(provider),
        )
        authorized = AuthorizedAsyncModelProvider(router, RESPONSIBILITY_ID)

        with self.assertRaises(ProviderUnavailable):
            await authorized.invoke(_request())
        self.assertEqual([], provider.requests)

    async def test_provider_neutral_activation_accepts_api_shaped_adapter(self):
        provider = _ScriptedModelProvider()
        authorized = activate_model_provider(
            provider,
            identity=MODEL_IDENTITY,
            responsibility_id=RESPONSIBILITY_ID,
            contract_owner_id="archflow.semantic-spatial",
            verification_evidence_refs=("evidence:configured-api-provider",),
        )

        receipt = await authorized.invoke(_request())

        self.assertEqual(MODEL_IDENTITY.provider_id, receipt.provider_id)
        self.assertEqual([_request()], provider.requests)
        self.assertEqual(
            MODEL_CONTRACT.to_dict(),
            authorized.router.state(RESPONSIBILITY_ID).contract.to_dict(),
        )
        self.assertEqual(
            "ModelInvocationReceipt@2",
            authorized.router.state(
                RESPONSIBILITY_ID
            ).contract.receipt_contract,
        )

    def test_agent_cli_is_only_one_configured_provider_implementation(self):
        authorized = activate_codex_agent_cli_provider(
            executable="codex",
            model_id="gpt-test",
            version="test-cli",
            responsibility_id=RESPONSIBILITY_ID,
            contract_owner_id="archflow.semantic-spatial",
            verification_evidence_refs=("evidence:configured-agent-cli",),
        )

        active = authorized.router.state(RESPONSIBILITY_ID).active_provider
        assert active is not None
        self.assertEqual("codex-agent-cli", active.provider_id)
        self.assertEqual(
            MODEL_CONTRACT.to_dict(),
            authorized.router.state(RESPONSIBILITY_ID).contract.to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
