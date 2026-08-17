"""Explicit P053 activation for provider-neutral model runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelProviderSpec,
    create_codex_cli_model_provider,
)
from archflow.production.responsibility import (
    HandoverDecision,
    InvalidProviderReceipt,
    InvocationEnvelope,
    ProductionAuthorityToken,
    ProviderIdentity,
    ProviderMode,
    ResponsibilityContract,
    ResponsibilityRouter,
    create_responsibility_control_plane,
)


MODEL_REQUEST_CONTRACT = ModelInvocationRequest.SCHEMA
MODEL_RECEIPT_CONTRACT = ModelInvocationReceipt.SCHEMA


def model_responsibility_contract(
    *,
    responsibility_id: str,
    contract_owner_id: str,
) -> ResponsibilityContract:
    """Define one model responsibility without selecting its provider."""

    return ResponsibilityContract(
        responsibility_id=responsibility_id,
        contract_owner_id=contract_owner_id,
        request_contract=MODEL_REQUEST_CONTRACT,
        receipt_contract=MODEL_RECEIPT_CONTRACT,
    )


def provider_identity_from_spec(spec: ModelProviderSpec) -> ProviderIdentity:
    if not isinstance(spec, ModelProviderSpec):
        raise TypeError("spec must be ModelProviderSpec")
    return ProviderIdentity(
        provider_id=spec.provider_id,
        version=spec.version,
        fingerprint=spec.fingerprint,
    )


def bind_async_model_provider(
    provider: AsyncModelProvider,
) -> Callable[
    [ModelInvocationRequest, ProductionAuthorityToken],
    object,
]:
    """Keep the P053 token in the trusted closure, outside the model adapter."""

    invoke = getattr(provider, "invoke", None)
    if not callable(invoke):
        raise TypeError("provider must implement AsyncModelProvider")

    async def handler(
        request: ModelInvocationRequest,
        authority: ProductionAuthorityToken,
    ) -> object:
        if not isinstance(request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(authority, ProductionAuthorityToken):
            raise TypeError("authority must be ProductionAuthorityToken")
        receipt = await invoke(request)
        if not isinstance(receipt, ModelInvocationReceipt):
            raise TypeError("model provider must return ModelInvocationReceipt")
        return receipt.to_dict()

    return handler


@dataclass(frozen=True, slots=True)
class AuthorizedAsyncModelProvider:
    """Expose only ``AsyncModelProvider`` after exact P053 envelope checks."""

    router: ResponsibilityRouter[ModelInvocationRequest, object]
    responsibility_id: str
    envelope_observer: Callable[[InvocationEnvelope], None] | None = None

    async def invoke(
        self,
        request: ModelInvocationRequest,
    ) -> ModelInvocationReceipt:
        if not isinstance(request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        envelope = await self.router.invoke_async(
            self.responsibility_id,
            request,
        )
        state = self.router.validate_envelope(envelope)
        receipt = _model_receipt(envelope)
        active = state.active_provider
        if active is None:
            raise InvalidProviderReceipt(
                "validated model envelope has no active provider"
            )
        if receipt.request != request:
            raise InvalidProviderReceipt(
                "model receipt does not bind the exact invocation request"
            )
        if (
            receipt.provider_id != active.provider_id
            or receipt.provider_version != active.version
            or receipt.provider_fingerprint != active.fingerprint
        ):
            raise InvalidProviderReceipt(
                "model receipt identity disagrees with P053 authority"
            )
        if self.envelope_observer is not None:
            self.envelope_observer(envelope)
        return receipt


def _model_receipt(envelope: InvocationEnvelope) -> ModelInvocationReceipt:
    try:
        return ModelInvocationReceipt.from_dict(envelope.provider_receipt)
    except (TypeError, ValueError) as exc:
        raise InvalidProviderReceipt(
            "P053 provider returned a malformed model receipt"
        ) from exc


class InvocationEvidenceCollector:
    """Bounded in-process collector for already validated P053 envelopes."""

    def __init__(self) -> None:
        self._items: list[InvocationEnvelope] = []

    def observe(self, envelope: InvocationEnvelope) -> None:
        if not isinstance(envelope, InvocationEnvelope):
            raise TypeError("envelope must be InvocationEnvelope")
        self._items.append(envelope)

    def cursor(self) -> int:
        return len(self._items)

    def since(self, cursor: int) -> tuple[InvocationEnvelope, ...]:
        if type(cursor) is not int or not 0 <= cursor <= len(self._items):
            raise ValueError("collector cursor is invalid")
        return tuple(self._items[cursor:])


def activate_model_provider(
    provider: AsyncModelProvider,
    *,
    identity: ProviderIdentity,
    responsibility_id: str,
    contract_owner_id: str,
    verification_evidence_refs: tuple[str, ...],
    envelope_observer: Callable[[InvocationEnvelope], None] | None = None,
) -> AuthorizedAsyncModelProvider:
    """Bind any API- or CLI-backed provider behind the same P053 contract."""

    if getattr(provider, "__archflow_test_only__", False) is True:
        raise TypeError(
            "test-only model providers cannot receive active production authority"
        )
    router, reconciler = create_responsibility_control_plane()
    router.register(
        model_responsibility_contract(
            responsibility_id=responsibility_id,
            contract_owner_id=contract_owner_id,
        ),
        identity,
        bind_async_model_provider(provider),
    )
    reconciler.qualify_provider(
        responsibility_id,
        identity,
        expected_mode=ProviderMode.REGISTERED,
        target_mode=ProviderMode.SHADOW,
        evidence_refs=verification_evidence_refs,
    )
    reconciler.qualify_provider(
        responsibility_id,
        identity,
        expected_mode=ProviderMode.SHADOW,
        target_mode=ProviderMode.VERIFIED,
        evidence_refs=verification_evidence_refs,
    )
    reconciler.activate(
        HandoverDecision(
            responsibility_id=responsibility_id,
            expected_binding_digest=router.state(
                responsibility_id
            ).binding_digest,
            target_provider=identity,
            verification_evidence_refs=verification_evidence_refs,
            reason="activate explicitly configured model provider",
        )
    )
    return AuthorizedAsyncModelProvider(
        router=router,
        responsibility_id=responsibility_id,
        envelope_observer=envelope_observer,
    )


def activate_provider_from_spec(
    provider: AsyncModelProvider,
    *,
    spec: ModelProviderSpec,
    responsibility_id: str,
    contract_owner_id: str,
    verification_evidence_refs: tuple[str, ...],
    envelope_observer: Callable[[InvocationEnvelope], None] | None = None,
) -> AuthorizedAsyncModelProvider:
    """Convenience seam shared by command and future HTTP API adapters."""

    return activate_model_provider(
        provider,
        identity=provider_identity_from_spec(spec),
        responsibility_id=responsibility_id,
        contract_owner_id=contract_owner_id,
        verification_evidence_refs=verification_evidence_refs,
        envelope_observer=envelope_observer,
    )


def activate_codex_agent_cli_provider(
    *,
    executable: str,
    model_id: str,
    version: str,
    responsibility_id: str,
    contract_owner_id: str,
    verification_evidence_refs: tuple[str, ...],
    provider_id: str = "codex-agent-cli",
    timeout_seconds: float = 60.0,
    envelope_observer: Callable[[InvocationEnvelope], None] | None = None,
) -> AuthorizedAsyncModelProvider:
    """Configure today's Agent CLI implementation without granting it authority."""

    provider = create_codex_cli_model_provider(
        executable=executable,
        model_id=model_id,
        version=version,
        provider_id=provider_id,
        timeout_seconds=timeout_seconds,
    )
    return activate_provider_from_spec(
        provider,
        spec=provider.spec,
        responsibility_id=responsibility_id,
        contract_owner_id=contract_owner_id,
        verification_evidence_refs=verification_evidence_refs,
        envelope_observer=envelope_observer,
    )
