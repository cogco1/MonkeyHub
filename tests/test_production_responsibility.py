from __future__ import annotations

import hashlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from archflow.production import (
    ContractConflict,
    HandoverDecision,
    InvalidInvocationEnvelope,
    InvalidProviderReceipt,
    InvocationEnvelope,
    NoProductionAuthority,
    ProviderIdentity,
    ProviderInvocationFailed,
    ProviderLifecycleError,
    ProviderMode,
    ResponsibilityContract,
    ResponsibilityRouter,
    ResolutionStatus,
    StaleAuthority,
    StaleHandover,
    UnknownProvider,
    create_responsibility_control_plane,
)


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


if __name__ == "__main__":
    unittest.main()
