"""One-shot Agent boundary for project-specific architectural relations.

The provider receives an exact, controller-authored relation context and may
return only a proposal.  The capability performs no retries, fallback,
persistence, geometry mutation, validation, stage acceptance, or canonical
writes.  A deterministic compiler decides whether the proposal is structurally
complete or remains open for human/RAG input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier, text
from archflow.relations.authoring import (
    RelationAuthoringCompilation,
    RelationAuthoringCompilationReceipt,
    RelationAuthoringCompilationStatus,
    RelationAuthoringContext,
    RelationAuthoringError,
    RelationAuthoringProposal,
    compile_relation_authoring,
)


AUTHORING_INSTRUCTIONS = (
    "Answer every controller-authored relation question exactly once. Use only "
    "the supplied node refs, scenarios, relation kinds, and basis IDs. Propose "
    "project-specific relation shapes and mechanical policy rules; do not add "
    "nodes, shrink the denominator, emit raw evidence/authority refs, claim a "
    "check result, grant not-applicable, persist, mutate geometry, or accept a "
    "stage. If the supplied bases do not justify an answer, return UNKNOWN or "
    "NOT_APPLICABLE_REQUESTED with a precise human question."
)


class RelationAuthoringProviderStatus(StrEnum):
    PROPOSAL_COMPILED = "proposal_compiled"
    OPEN = "open"
    PROVIDER_FAILED = "provider_failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RelationAuthoringProviderIdentity:
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str

    SCHEMA: ClassVar[str] = "RelationAuthoringProviderIdentity@1"

    def __post_init__(self) -> None:
        identifier(self.provider_id, "relation authoring provider_id")
        text(self.model_id, "relation authoring model_id", maximum=1_000)
        text(
            self.provider_version,
            "relation authoring provider_version",
            maximum=1_000,
        )
        object.__setattr__(
            self,
            "provider_fingerprint",
            require_sha256(
                self.provider_fingerprint,
                "relation authoring provider_fingerprint",
            ),
        )

    def matches(self, receipt: ModelInvocationReceipt) -> bool:
        if not isinstance(receipt, ModelInvocationReceipt):
            raise TypeError("receipt must be ModelInvocationReceipt")
        return (
            receipt.provider_id == self.provider_id
            and receipt.model_id == self.model_id
            and receipt.provider_version == self.provider_version
            and receipt.provider_fingerprint == self.provider_fingerprint
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringProviderIdentity":
        payload = exact_mapping(
            value,
            {
                "schema",
                "provider_id",
                "model_id",
                "provider_version",
                "provider_fingerprint",
            },
            "relation authoring provider identity",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationAuthoringError("unsupported provider identity schema")
        result = cls(
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("provider identity roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringProviderReceipt:
    receipt_id: str
    status: RelationAuthoringProviderStatus
    request: ModelInvocationRequest
    model_receipt: ModelInvocationReceipt
    provider_identity: RelationAuthoringProviderIdentity
    relation_context_digest: str
    proposal_digest: str | None
    compilation_receipt: RelationAuthoringCompilationReceipt | None
    error_code: str | None = None
    message: str | None = None

    SCHEMA: ClassVar[str] = "RelationAuthoringProviderReceipt@1"

    def __post_init__(self) -> None:
        identifier(self.receipt_id, "relation authoring receipt_id")
        if not isinstance(self.status, RelationAuthoringProviderStatus):
            raise TypeError("status must be RelationAuthoringProviderStatus")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(self.model_receipt, ModelInvocationReceipt):
            raise TypeError("model_receipt must be ModelInvocationReceipt")
        if not isinstance(self.provider_identity, RelationAuthoringProviderIdentity):
            raise TypeError("provider_identity must be RelationAuthoringProviderIdentity")
        object.__setattr__(
            self,
            "relation_context_digest",
            require_sha256(self.relation_context_digest, "relation_context_digest"),
        )
        if self.proposal_digest is not None:
            object.__setattr__(
                self,
                "proposal_digest",
                require_sha256(self.proposal_digest, "proposal_digest"),
            )
        if self.compilation_receipt is not None and not isinstance(
            self.compilation_receipt,
            RelationAuthoringCompilationReceipt,
        ):
            raise TypeError(
                "compilation_receipt must be RelationAuthoringCompilationReceipt"
            )
        successful = self.status in {
            RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
            RelationAuthoringProviderStatus.OPEN,
        }
        if successful:
            try:
                prompt = exact_mapping(
                    self.request.payload,
                    {
                        "schema",
                        "instructions",
                        "relation_context",
                        "output_contract",
                    },
                    "retained relation authoring prompt",
                )
                retained_context = RelationAuthoringContext.from_dict(
                    prompt["relation_context"]
                )
                output = exact_mapping(
                    self.model_receipt.output,
                    {
                        "schema",
                        "request_context_digest",
                        "relation_context_digest",
                        "proposal",
                    },
                    "retained relation authoring output",
                )
                retained_proposal = RelationAuthoringProposal.from_dict(
                    output["proposal"]
                )
                replayed = compile_relation_authoring(
                    retained_context,
                    retained_proposal,
                )
            except (TypeError, ValueError) as exc:
                raise RelationAuthoringError(
                    "successful provider receipt cannot reload its exact contracts"
                ) from exc
            if (
                self.proposal_digest is None
                or self.compilation_receipt is None
                or self.error_code is not None
                or self.model_receipt.status is not ModelInvocationStatus.SUCCESS
                or self.model_receipt.request != self.request
                or not self.provider_identity.matches(self.model_receipt)
                or self.request.phase is not ModelPhase.ACTION_PROPOSAL
                or prompt["schema"] != "RelationAuthoringPrompt@1"
                or canonical_digest(prompt) != self.request.context_digest
                or self.request.checkpoint_digest != retained_context.state_digest
                or retained_context.context_digest != self.relation_context_digest
                or output["schema"] != "RelationAuthoringOutput@1"
                or output["request_context_digest"] != self.request.context_digest
                or output["relation_context_digest"]
                != self.relation_context_digest
                or retained_proposal.context_digest
                != self.relation_context_digest
                or retained_proposal.proposal_digest != self.proposal_digest
                or self.compilation_receipt != replayed.receipt
                or self.status
                is not {
                    RelationAuthoringCompilationStatus.PROPOSAL_COMPILED:
                        RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
                    RelationAuthoringCompilationStatus.OPEN:
                        RelationAuthoringProviderStatus.OPEN,
                }[replayed.receipt.status]
            ):
                raise RelationAuthoringError("successful provider receipt is incomplete")
        elif (
            self.proposal_digest is not None
            or self.compilation_receipt is not None
            or self.error_code is None
        ):
            raise RelationAuthoringError("failed provider receipt is inconsistent")
        if self.message is not None:
            text(self.message, "relation authoring receipt message", maximum=2_000)

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def compilation_receipt_digest(self) -> str | None:
        if self.compilation_receipt is None:
            return None
        return self.compilation_receipt.receipt_digest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "model_receipt": self.model_receipt.to_dict(),
            "provider_identity": self.provider_identity.to_dict(),
            "relation_context_digest": self.relation_context_digest,
            "proposal_digest": self.proposal_digest,
            "compilation_receipt": (
                None
                if self.compilation_receipt is None
                else self.compilation_receipt.to_dict()
            ),
            "compilation_receipt_digest": self.compilation_receipt_digest,
            "error_code": self.error_code,
            "message": self.message,
            "single_provider_call": True,
            "proposal_only": True,
            "validation_authority": False,
            "design_authority": False,
            "stage_acceptance_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationAuthoringProviderReceipt":
        payload = exact_mapping(
            value,
            {
                "schema",
                "receipt_id",
                "status",
                "request",
                "model_receipt",
                "provider_identity",
                "relation_context_digest",
                "proposal_digest",
                "compilation_receipt",
                "compilation_receipt_digest",
                "error_code",
                "message",
                "single_provider_call",
                "proposal_only",
                "validation_authority",
                "design_authority",
                "stage_acceptance_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "relation authoring provider receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["single_provider_call"] is not True
            or payload["proposal_only"] is not True
            or any(
                payload[field] is not False
                for field in (
                    "validation_authority",
                    "design_authority",
                    "stage_acceptance_authority",
                    "persistence_authority",
                    "canonical_write_authority",
                )
            )
        ):
            raise RelationAuthoringError("provider receipt acquired authority")
        compilation_receipt = (
            None
            if payload["compilation_receipt"] is None
            else RelationAuthoringCompilationReceipt.from_dict(
                payload["compilation_receipt"]
            )
        )
        if payload["compilation_receipt_digest"] != (
            None
            if compilation_receipt is None
            else compilation_receipt.receipt_digest
        ):
            raise RelationAuthoringError(
                "provider compilation receipt digest changed"
            )
        result = cls(
            receipt_id=payload["receipt_id"],
            status=RelationAuthoringProviderStatus(payload["status"]),
            request=ModelInvocationRequest.from_dict(payload["request"]),
            model_receipt=ModelInvocationReceipt.from_dict(payload["model_receipt"]),
            provider_identity=RelationAuthoringProviderIdentity.from_dict(
                payload["provider_identity"]
            ),
            relation_context_digest=payload["relation_context_digest"],
            proposal_digest=payload["proposal_digest"],
            compilation_receipt=compilation_receipt,
            error_code=payload["error_code"],
            message=payload["message"],
        )
        if result.to_dict() != payload:
            raise RelationAuthoringError("provider receipt roundtrip changed")
        return result


@dataclass(frozen=True, slots=True)
class RelationAuthoringProviderResult:
    receipt: RelationAuthoringProviderReceipt
    proposal: RelationAuthoringProposal | None = None
    compilation: RelationAuthoringCompilation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, RelationAuthoringProviderReceipt):
            raise TypeError("receipt must be RelationAuthoringProviderReceipt")
        successful = self.receipt.status in {
            RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
            RelationAuthoringProviderStatus.OPEN,
        }
        if successful:
            if not isinstance(self.proposal, RelationAuthoringProposal) or not isinstance(
                self.compilation, RelationAuthoringCompilation
            ):
                raise RelationAuthoringError("successful result lacks proposal compilation")
            if (
                self.receipt.proposal_digest != self.proposal.proposal_digest
                or self.receipt.compilation_receipt
                != self.compilation.receipt
                or self.receipt.relation_context_digest
                != self.proposal.context_digest
                or self.receipt.relation_context_digest
                != self.compilation.receipt.context_digest
            ):
                raise RelationAuthoringError("provider result digest changed")
            expected_status = {
                RelationAuthoringCompilationStatus.PROPOSAL_COMPILED:
                    RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
                RelationAuthoringCompilationStatus.OPEN:
                    RelationAuthoringProviderStatus.OPEN,
            }[self.compilation.receipt.status]
            if self.receipt.status is not expected_status:
                raise RelationAuthoringError("provider and compiler statuses disagree")
        elif self.proposal is not None or self.compilation is not None:
            raise RelationAuthoringError("failed provider result carries proposal artifacts")


def relation_authoring_output_contract() -> dict[str, object]:
    """Return the strict machine-facing output envelope."""

    return {
        "schema": "RelationAuthoringOutputContract@1",
        "required_output_schema": "RelationAuthoringOutput@1",
        "required_output_fields": [
            "schema",
            "request_context_digest",
            "relation_context_digest",
            "proposal",
        ],
        "proposal_schema": RelationAuthoringProposal.SCHEMA,
        "answer_statuses": [
            "proposed",
            "unknown",
            "not_applicable_requested",
        ],
        "constraints": [
            "answers exactly equal the question denominator",
            "only supplied node refs, scenarios, relation kinds, and basis IDs",
            "policy roles and min/max exactly equal controller rule envelopes",
            "no raw evidence refs or authority refs in proposal specs",
            "not_applicable_requested is a request, never a waiver",
            "no check, stage acceptance, persistence, or canonical authority",
        ],
    }


async def author_project_relations(
    provider: AsyncModelProvider,
    *,
    provider_identity: RelationAuthoringProviderIdentity,
    request_id: str,
    context: RelationAuthoringContext,
) -> RelationAuthoringProviderResult:
    """Invoke one provider exactly once and compile its complete proposal."""

    if not isinstance(provider_identity, RelationAuthoringProviderIdentity):
        raise TypeError("provider_identity must be RelationAuthoringProviderIdentity")
    if not isinstance(context, RelationAuthoringContext):
        raise TypeError("context must be RelationAuthoringContext")
    prompt = {
        "schema": "RelationAuthoringPrompt@1",
        "instructions": AUTHORING_INSTRUCTIONS,
        "relation_context": context.to_dict(),
        "output_contract": relation_authoring_output_contract(),
    }
    request = ModelInvocationRequest.create(
        request_id=request_id,
        phase=ModelPhase.ACTION_PROPOSAL,
        checkpoint_digest=context.state_digest,
        context_digest=canonical_digest(prompt),
        payload=prompt,
    )
    try:
        model_receipt = await provider.invoke(request)
    except Exception as exc:
        model_receipt = ModelInvocationReceipt(
            receipt_id=f"relation-provider-exception-{request.request_id}",
            status=ModelInvocationStatus.EXIT_ERROR,
            request=request,
            provider_id=provider_identity.provider_id,
            model_id=provider_identity.model_id,
            provider_version=provider_identity.provider_version,
            provider_fingerprint=provider_identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=0,
            output_sha256=None,
            error_code="relation_authoring.provider_exception",
            message=(
                f"{type(exc).__name__}: provider invocation raised before "
                "returning a typed receipt"
            ),
        )
        return _failure(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.PROVIDER_FAILED,
            error_code="relation_authoring.provider_exception",
            message=model_receipt.message,
        )
    if not isinstance(model_receipt, ModelInvocationReceipt):
        malformed_receipt = ModelInvocationReceipt(
            receipt_id=f"relation-provider-malformed-{request.request_id}",
            status=ModelInvocationStatus.MALFORMED,
            request=request,
            provider_id=provider_identity.provider_id,
            model_id=provider_identity.model_id,
            provider_version=provider_identity.provider_version,
            provider_fingerprint=provider_identity.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=0,
            output_sha256=None,
            error_code="relation_authoring.provider_contract_violation",
            message=(
                "provider returned a non-ModelInvocationReceipt value; "
                "the untyped value was not retained"
            ),
        )
        return _failure(
            request=request,
            model_receipt=malformed_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.REJECTED,
            error_code="relation_authoring.provider_contract_violation",
            message=malformed_receipt.message,
        )
    if model_receipt.request != request:
        return _failure(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.REJECTED,
            error_code="relation_authoring.provider_request_mismatch",
            message="provider receipt does not bind the exact relation request",
        )
    if not provider_identity.matches(model_receipt):
        return _failure(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.REJECTED,
            error_code="relation_authoring.provider_identity_mismatch",
            message="provider receipt identity differs from the frozen identity",
        )
    if model_receipt.status is not ModelInvocationStatus.SUCCESS:
        return _failure(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.PROVIDER_FAILED,
            error_code=model_receipt.error_code or "relation_authoring.provider_failed",
            message=model_receipt.message,
        )
    try:
        output = exact_mapping(
            model_receipt.output,
            {
                "schema",
                "request_context_digest",
                "relation_context_digest",
                "proposal",
            },
            "relation authoring output",
        )
        if output["schema"] != "RelationAuthoringOutput@1":
            raise RelationAuthoringError("unsupported relation authoring output schema")
        if (
            output["request_context_digest"] != request.context_digest
            or output["relation_context_digest"] != context.context_digest
        ):
            raise RelationAuthoringError("provider output crossed state or context")
        proposal = RelationAuthoringProposal.from_dict(output["proposal"])
        compilation = compile_relation_authoring(context, proposal)
    except (KeyError, TypeError, ValueError) as exc:
        return _failure(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=RelationAuthoringProviderStatus.REJECTED,
            error_code="relation_authoring.proposal_rejected",
            message=f"{type(exc).__name__}: {exc}",
        )
    status = {
        RelationAuthoringCompilationStatus.PROPOSAL_COMPILED:
            RelationAuthoringProviderStatus.PROPOSAL_COMPILED,
        RelationAuthoringCompilationStatus.OPEN:
            RelationAuthoringProviderStatus.OPEN,
    }[compilation.receipt.status]
    receipt = _receipt(
        request=request,
        model_receipt=model_receipt,
        provider_identity=provider_identity,
        context=context,
        status=status,
        proposal_digest=proposal.proposal_digest,
        compilation_receipt=compilation.receipt,
    )
    return RelationAuthoringProviderResult(
        receipt=receipt,
        proposal=proposal,
        compilation=compilation,
    )


def _failure(
    *,
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    provider_identity: RelationAuthoringProviderIdentity,
    context: RelationAuthoringContext,
    status: RelationAuthoringProviderStatus,
    error_code: str,
    message: str | None,
) -> RelationAuthoringProviderResult:
    return RelationAuthoringProviderResult(
        receipt=_receipt(
            request=request,
            model_receipt=model_receipt,
            provider_identity=provider_identity,
            context=context,
            status=status,
            error_code=error_code,
            message=message,
        )
    )


def _receipt(
    *,
    request: ModelInvocationRequest,
    model_receipt: ModelInvocationReceipt,
    provider_identity: RelationAuthoringProviderIdentity,
    context: RelationAuthoringContext,
    status: RelationAuthoringProviderStatus,
    proposal_digest: str | None = None,
    compilation_receipt: RelationAuthoringCompilationReceipt | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> RelationAuthoringProviderReceipt:
    identity = {
        "request_id": request.request_id,
        "model_receipt_id": model_receipt.receipt_id,
        "status": status.value,
        "relation_context_digest": context.context_digest,
        "proposal_digest": proposal_digest,
        "compilation_receipt_digest": (
            None
            if compilation_receipt is None
            else compilation_receipt.receipt_digest
        ),
        "error_code": error_code,
    }
    return RelationAuthoringProviderReceipt(
        receipt_id=f"relation-authoring-{canonical_digest(identity)[:24]}",
        status=status,
        request=request,
        model_receipt=model_receipt,
        provider_identity=provider_identity,
        relation_context_digest=context.context_digest,
        proposal_digest=proposal_digest,
        compilation_receipt=compilation_receipt,
        error_code=error_code,
        message=message,
    )


__all__ = [
    "AUTHORING_INSTRUCTIONS",
    "RelationAuthoringProviderIdentity",
    "RelationAuthoringProviderReceipt",
    "RelationAuthoringProviderResult",
    "RelationAuthoringProviderStatus",
    "author_project_relations",
    "relation_authoring_output_contract",
]
