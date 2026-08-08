from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from threading import Lock
from typing import Any, Generic, TypeVar


RequestT = TypeVar("RequestT")
ReceiptT = TypeVar("ReceiptT")

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_SCHEMA_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}@[1-9][0-9]*$")
_EMBEDDED_WINDOWS_PATH = re.compile(r"(?i)(?:^|[^A-Za-z0-9])[a-z]:[\\/]")
_EMBEDDED_POSIX_PATH = re.compile(r"(?:^|[\s\"'=(])/(?:[^/\s]+/?)+")
_EMBEDDED_UNC_PATH = re.compile(r"(?:^|[^\\])\\\\[^\s]+")
_CONTROL_PLANE_FACTORY_KEY = object()


class ResponsibilityError(RuntimeError):
    """Base class for responsibility-control failures."""


class ContractConflict(ResponsibilityError):
    pass


class DuplicateProvider(ResponsibilityError):
    pass


class UnknownResponsibility(ResponsibilityError):
    pass


class UnknownProvider(ResponsibilityError):
    pass


class ProviderLifecycleError(ResponsibilityError):
    pass


class StaleHandover(ResponsibilityError):
    pass


class ProviderUnavailable(ResponsibilityError):
    pass


class NoProductionAuthority(ResponsibilityError):
    pass


class StaleAuthority(ResponsibilityError):
    pass


class InvalidInvocationEnvelope(ResponsibilityError):
    pass


class InvalidProviderReceipt(ResponsibilityError):
    pass


class ProviderInvocationFailed(ResponsibilityError):
    def __init__(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
        cause: Exception,
    ) -> None:
        self.responsibility_id = responsibility_id
        self.provider = provider
        failure_type = type(cause).__name__
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", failure_type):
            failure_type = "ProviderException"
        self.failure_type = failure_type
        super().__init__(
            f"active provider {provider.provider_id}@{provider.version} failed "
            f"for {responsibility_id} with {failure_type}"
        )


class ProviderMode(str, Enum):
    REGISTERED = "registered"
    SHADOW = "shadow"
    VERIFIED = "verified"
    ACTIVE = "active"
    RETIRED = "retired"


class ResolutionStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    ACTIVE = "active"


def _require_safe_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a portable lowercase identifier")


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _looks_machine_local(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("file:")
        or " file:" in lowered
        or "file://" in lowered
        or lowered.startswith("\\\\")
        or _EMBEDDED_WINDOWS_PATH.search(value) is not None
        or _EMBEDDED_POSIX_PATH.search(value) is not None
        or _EMBEDDED_UNC_PATH.search(value) is not None
        or PureWindowsPath(value).is_absolute()
        or PurePosixPath(value).is_absolute()
    )


def _require_schema_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SCHEMA_REF.fullmatch(value):
        raise ValueError(f"{label} must be a portable versioned schema ref")


def _require_portable_text(label: str, value: str, *, max_length: int = 512) -> None:
    _require_text(label, value)
    if len(value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters")
    if _looks_machine_local(value):
        raise ValueError(f"{label} cannot contain a machine-local path")


def _normalize_evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    refs = tuple(values)
    if not refs:
        raise ValueError("at least one evidence ref is required")
    for ref in refs:
        _require_portable_text("evidence ref", ref)
    if len(set(refs)) != len(refs):
        raise ValueError("evidence refs must be unique")
    return tuple(sorted(refs))


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers are not canonical JSON")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    raise TypeError(f"value of type {type(value).__name__} is not canonical JSON")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_portable_payload(value: Any) -> None:
    if isinstance(value, str):
        if _looks_machine_local(value):
            raise ValueError("provider receipt cannot contain a machine-local path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _looks_machine_local(key):
                raise ValueError(
                    "provider receipt keys cannot contain a machine-local path"
                )
            _require_portable_payload(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _require_portable_payload(item)
        return
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        _require_portable_payload(to_dict())


@dataclass(frozen=True, slots=True)
class ResponsibilityContract:
    responsibility_id: str
    contract_owner_id: str
    request_contract: str
    receipt_contract: str

    def __post_init__(self) -> None:
        _require_safe_id("responsibility_id", self.responsibility_id)
        _require_safe_id("contract_owner_id", self.contract_owner_id)
        _require_schema_ref("request_contract", self.request_contract)
        _require_schema_ref("receipt_contract", self.receipt_contract)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionResponsibilityContract@1",
            "responsibility_id": self.responsibility_id,
            "contract_owner_id": self.contract_owner_id,
            "request_contract": self.request_contract,
            "receipt_contract": self.receipt_contract,
        }


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider_id: str
    version: str
    fingerprint: str

    def __post_init__(self) -> None:
        _require_safe_id("provider_id", self.provider_id)
        _require_text("version", self.version)
        if _looks_machine_local(self.version):
            raise ValueError("provider version cannot be a machine-local path")
        if not _FINGERPRINT.fullmatch(self.fingerprint):
            raise ValueError("provider fingerprint must be a lowercase sha256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "version": self.version,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    responsibility_id: str
    provider: ProviderIdentity
    mode: ProviderMode = ProviderMode.REGISTERED
    qualification_evidence_refs: tuple[str, ...] = ()
    canonical_write_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_safe_id("responsibility_id", self.responsibility_id)
        if self.qualification_evidence_refs:
            object.__setattr__(
                self,
                "qualification_evidence_refs",
                _normalize_evidence_refs(self.qualification_evidence_refs),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionProviderRegistration@1",
            "responsibility_id": self.responsibility_id,
            "provider": self.provider.to_dict(),
            "mode": self.mode.value,
            "qualification_evidence_refs": list(
                self.qualification_evidence_refs
            ),
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ProviderLifecycleReceipt:
    responsibility_id: str
    provider: ProviderIdentity
    previous_mode: ProviderMode
    next_mode: ProviderMode
    evidence_refs: tuple[str, ...]
    canonical_write_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_safe_id("responsibility_id", self.responsibility_id)
        object.__setattr__(
            self, "evidence_refs", _normalize_evidence_refs(self.evidence_refs)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionProviderLifecycleReceipt@1",
            "responsibility_id": self.responsibility_id,
            "provider": self.provider.to_dict(),
            "previous_mode": self.previous_mode.value,
            "next_mode": self.next_mode.value,
            "evidence_refs": list(self.evidence_refs),
            "canonical_write_authority": False,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResponsibilityState:
    contract: ResponsibilityContract
    authority_epoch: int
    active_provider: ProviderIdentity | None

    def __post_init__(self) -> None:
        if self.authority_epoch < 0:
            raise ValueError("authority_epoch must be non-negative")

    @property
    def responsibility_id(self) -> str:
        return self.contract.responsibility_id

    @property
    def contract_owner_id(self) -> str:
        return self.contract.contract_owner_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionResponsibilityState@1",
            "contract": self.contract.to_dict(),
            "authority_epoch": self.authority_epoch,
            "status": "active" if self.active_provider is not None else "unavailable",
            "active_provider": (
                None if self.active_provider is None else self.active_provider.to_dict()
            ),
        }

    @property
    def binding_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class HandoverDecision:
    responsibility_id: str
    expected_binding_digest: str
    target_provider: ProviderIdentity
    verification_evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _require_safe_id("responsibility_id", self.responsibility_id)
        if not _FINGERPRINT.fullmatch(self.expected_binding_digest):
            raise ValueError("expected_binding_digest must be a lowercase sha256 digest")
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _normalize_evidence_refs(self.verification_evidence_refs),
        )
        _require_portable_text("reason", self.reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionHandoverDecision@1",
            "responsibility_id": self.responsibility_id,
            "expected_binding_digest": self.expected_binding_digest,
            "target_provider": self.target_provider.to_dict(),
            "verification_evidence_refs": list(self.verification_evidence_refs),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class HandoverReceipt:
    previous_state: ResponsibilityState
    next_state: ResponsibilityState
    retired_provider: ProviderIdentity | None
    activated_provider: ProviderIdentity
    verification_evidence_refs: tuple[str, ...]
    reason: str
    canonical_write_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.previous_state.responsibility_id != self.next_state.responsibility_id:
            raise ValueError("handover states must share one responsibility")
        if self.previous_state.contract != self.next_state.contract:
            raise ValueError("handover cannot change the responsibility contract")
        if self.next_state.authority_epoch != self.previous_state.authority_epoch + 1:
            raise ValueError("handover must increment authority_epoch exactly once")
        if self.retired_provider != self.previous_state.active_provider:
            raise ValueError("retired provider must equal the previous active provider")
        if self.next_state.active_provider != self.activated_provider:
            raise ValueError("activated provider must equal the next active provider")
        if self.activated_provider == self.retired_provider:
            raise ValueError("handover must change the active provider binding")
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _normalize_evidence_refs(self.verification_evidence_refs),
        )
        _require_portable_text("reason", self.reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionHandoverReceipt@1",
            "previous_state": self.previous_state.to_dict(),
            "previous_binding_digest": self.previous_state.binding_digest,
            "next_state": self.next_state.to_dict(),
            "next_binding_digest": self.next_state.binding_digest,
            "retired_provider": (
                None if self.retired_provider is None else self.retired_provider.to_dict()
            ),
            "activated_provider": self.activated_provider.to_dict(),
            "verification_evidence_refs": list(self.verification_evidence_refs),
            "reason": self.reason,
            "canonical_write_authority": False,
        }

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ProductionAuthorityToken:
    responsibility_id: str
    contract_owner_id: str
    provider: ProviderIdentity
    authority_epoch: int
    binding_digest: str
    production_authority: bool
    grant_nonce: str
    grant_signature: str
    canonical_write_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_safe_id("responsibility_id", self.responsibility_id)
        _require_safe_id("contract_owner_id", self.contract_owner_id)
        if self.authority_epoch < 0:
            raise ValueError("authority_epoch must be non-negative")
        if not _FINGERPRINT.fullmatch(self.binding_digest):
            raise ValueError("binding_digest must be a lowercase sha256 digest")
        if not _NONCE.fullmatch(self.grant_nonce):
            raise ValueError("grant_nonce must be a 128-bit lowercase hex value")
        if not _FINGERPRINT.fullmatch(self.grant_signature):
            raise ValueError("grant_signature must be a lowercase sha256 hmac")

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionAuthorityToken@2",
            "responsibility_id": self.responsibility_id,
            "contract_owner_id": self.contract_owner_id,
            "provider": self.provider.to_dict(),
            "authority_epoch": self.authority_epoch,
            "binding_digest": self.binding_digest,
            "production_authority": self.production_authority,
            "grant_nonce": self.grant_nonce,
            "canonical_write_authority": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "grant_signature": self.grant_signature}


@dataclass(frozen=True, slots=True)
class ResponsibilityResolution:
    status: ResolutionStatus
    state: ResponsibilityState
    active_provider: ProviderIdentity | None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is ResolutionStatus.ACTIVE:
            if self.active_provider is None:
                raise ValueError("active resolution requires an active provider")
            if self.state.active_provider != self.active_provider:
                raise ValueError("resolution provider must match responsibility state")
            if self.unavailable_reason is not None:
                raise ValueError("active resolution cannot have an unavailable reason")
        else:
            if self.active_provider is not None:
                raise ValueError("unavailable resolution cannot carry a provider")
            _require_text("unavailable_reason", self.unavailable_reason or "")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionResponsibilityResolution@1",
            "status": self.status.value,
            "state": self.state.to_dict(),
            "binding_digest": self.state.binding_digest,
            "active_provider": (
                None if self.active_provider is None else self.active_provider.to_dict()
            ),
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class InvocationEnvelope:
    authority: ProductionAuthorityToken
    provider_receipt_json: str
    provider_receipt_digest: str
    envelope_signature: str
    canonical_write_authority: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        try:
            decoded = json.loads(self.provider_receipt_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("provider_receipt_json must be canonical JSON") from exc
        if _canonical_json(decoded) != self.provider_receipt_json:
            raise ValueError("provider_receipt_json must use canonical encoding")
        if not _FINGERPRINT.fullmatch(self.provider_receipt_digest):
            raise ValueError("provider_receipt_digest must be a lowercase sha256 digest")
        if _digest(decoded) != self.provider_receipt_digest:
            raise ValueError("provider receipt digest does not match its payload")
        if not _FINGERPRINT.fullmatch(self.envelope_signature):
            raise ValueError("envelope_signature must be a lowercase sha256 hmac")

    @property
    def provider_receipt(self) -> object:
        return json.loads(self.provider_receipt_json)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "schema": "ProductionInvocationEnvelope@2",
            "authority": self.authority.to_dict(),
            "provider_receipt_json": self.provider_receipt_json,
            "provider_receipt_digest": self.provider_receipt_digest,
            "canonical_write_authority": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "envelope_signature": self.envelope_signature}


@dataclass(frozen=True, slots=True)
class _RuntimeProvider(Generic[RequestT, ReceiptT]):
    registration: ProviderRegistration
    handler: Callable[[RequestT, ProductionAuthorityToken], ReceiptT]


class _ResponsibilityControlPlane(Generic[RequestT, ReceiptT]):
    """Internal state, mutation, and signing authority.

    This object is deliberately never passed to provider handlers. Public
    callers receive separate router and reconciler facades.
    """

    _ALLOWED_TRANSITIONS = {
        (ProviderMode.REGISTERED, ProviderMode.SHADOW),
        (ProviderMode.SHADOW, ProviderMode.VERIFIED),
        (ProviderMode.VERIFIED, ProviderMode.SHADOW),
        (ProviderMode.RETIRED, ProviderMode.SHADOW),
    }

    def __init__(self) -> None:
        self._lock = Lock()
        self._signing_key = secrets.token_bytes(32)
        self._contracts: dict[str, ResponsibilityContract] = {}
        self._states: dict[str, ResponsibilityState] = {}
        self._providers: dict[
            tuple[str, ProviderIdentity], _RuntimeProvider[RequestT, ReceiptT]
        ] = {}

    def define(self, contract: ResponsibilityContract) -> ResponsibilityState:
        with self._lock:
            return self._define_locked(contract)

    def _define_locked(self, contract: ResponsibilityContract) -> ResponsibilityState:
        existing = self._contracts.get(contract.responsibility_id)
        if existing is not None:
            if existing != contract:
                raise ContractConflict(
                    f"responsibility {contract.responsibility_id} already has "
                    f"contract owner {existing.contract_owner_id}"
                )
            return self._states[contract.responsibility_id]
        state = ResponsibilityState(
            contract=contract,
            authority_epoch=0,
            active_provider=None,
        )
        self._contracts[contract.responsibility_id] = contract
        self._states[contract.responsibility_id] = state
        return state

    def register(
        self,
        contract: ResponsibilityContract,
        provider: ProviderIdentity,
        handler: Callable[[RequestT, ProductionAuthorityToken], ReceiptT],
    ) -> ProviderRegistration:
        if not callable(handler):
            raise TypeError("provider handler must be callable")
        with self._lock:
            self._define_locked(contract)
            key = (contract.responsibility_id, provider)
            if key in self._providers:
                raise DuplicateProvider(
                    f"provider {provider.provider_id}@{provider.version} is already "
                    f"registered for {contract.responsibility_id}"
                )
            registration = ProviderRegistration(
                responsibility_id=contract.responsibility_id,
                provider=provider,
            )
            self._providers[key] = _RuntimeProvider(
                registration=registration,
                handler=handler,
            )
            return registration

    def state(self, responsibility_id: str) -> ResponsibilityState:
        with self._lock:
            return self._require_state_locked(responsibility_id)

    def registration(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
    ) -> ProviderRegistration:
        with self._lock:
            return self._require_provider_locked(
                responsibility_id, provider
            ).registration

    def _transition_provider(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
        *,
        expected_mode: ProviderMode,
        target_mode: ProviderMode,
        evidence_refs: Sequence[str],
    ) -> ProviderLifecycleReceipt:
        normalized_refs = _normalize_evidence_refs(evidence_refs)
        with self._lock:
            runtime = self._require_provider_locked(responsibility_id, provider)
            current = runtime.registration
            if current.mode is not expected_mode:
                raise ProviderLifecycleError(
                    f"provider mode is {current.mode.value}, expected "
                    f"{expected_mode.value}"
                )
            if (expected_mode, target_mode) not in self._ALLOWED_TRANSITIONS:
                raise ProviderLifecycleError(
                    f"transition {expected_mode.value}->{target_mode.value} is not allowed"
                )
            receipt = ProviderLifecycleReceipt(
                responsibility_id=responsibility_id,
                provider=provider,
                previous_mode=expected_mode,
                next_mode=target_mode,
                evidence_refs=normalized_refs,
            )
            next_registration = replace(
                current,
                mode=target_mode,
                qualification_evidence_refs=normalized_refs,
            )
            self._providers[(responsibility_id, provider)] = replace(
                runtime, registration=next_registration
            )
            return receipt

    def _activate(self, decision: HandoverDecision) -> HandoverReceipt:
        with self._lock:
            previous_state = self._require_state_locked(decision.responsibility_id)
            if previous_state.binding_digest != decision.expected_binding_digest:
                raise StaleHandover(
                    f"responsibility {decision.responsibility_id} binding changed"
                )
            target = self._require_provider_locked(
                decision.responsibility_id, decision.target_provider
            )
            if target.registration.mode is not ProviderMode.VERIFIED:
                raise ProviderLifecycleError(
                    "only a verified provider can become active"
                )

            retired_provider = previous_state.active_provider
            previous_runtime: _RuntimeProvider[RequestT, ReceiptT] | None = None
            if retired_provider is not None:
                previous_runtime = self._require_provider_locked(
                    decision.responsibility_id, retired_provider
                )
                if previous_runtime.registration.mode is not ProviderMode.ACTIVE:
                    raise ProviderLifecycleError(
                        "active binding and provider lifecycle mode disagree"
                    )

            next_state = ResponsibilityState(
                contract=previous_state.contract,
                authority_epoch=previous_state.authority_epoch + 1,
                active_provider=decision.target_provider,
            )
            receipt = self._build_handover_receipt(
                previous_state=previous_state,
                next_state=next_state,
                retired_provider=retired_provider,
                activated_provider=decision.target_provider,
                verification_evidence_refs=decision.verification_evidence_refs,
                reason=decision.reason,
            )

            if previous_runtime is not None:
                self._providers[
                    (decision.responsibility_id, retired_provider)
                ] = replace(
                    previous_runtime,
                    registration=replace(
                        previous_runtime.registration,
                        mode=ProviderMode.RETIRED,
                    ),
                )
            self._providers[
                (decision.responsibility_id, decision.target_provider)
            ] = replace(
                target,
                registration=replace(
                    target.registration,
                    mode=ProviderMode.ACTIVE,
                    qualification_evidence_refs=(
                        decision.verification_evidence_refs
                    ),
                ),
            )
            self._states[decision.responsibility_id] = next_state
            return receipt

    def _build_handover_receipt(
        self,
        *,
        previous_state: ResponsibilityState,
        next_state: ResponsibilityState,
        retired_provider: ProviderIdentity | None,
        activated_provider: ProviderIdentity,
        verification_evidence_refs: tuple[str, ...],
        reason: str,
    ) -> HandoverReceipt:
        return HandoverReceipt(
            previous_state=previous_state,
            next_state=next_state,
            retired_provider=retired_provider,
            activated_provider=activated_provider,
            verification_evidence_refs=verification_evidence_refs,
            reason=reason,
        )

    def resolve(self, responsibility_id: str) -> ResponsibilityResolution:
        with self._lock:
            resolution, _ = self._resolve_locked(responsibility_id)
            return resolution

    def _resolve_locked(
        self, responsibility_id: str
    ) -> tuple[
        ResponsibilityResolution,
        _RuntimeProvider[RequestT, ReceiptT] | None,
    ]:
        state = self._require_state_locked(responsibility_id)
        if state.active_provider is None:
            return (
                ResponsibilityResolution(
                    status=ResolutionStatus.UNAVAILABLE,
                    state=state,
                    active_provider=None,
                    unavailable_reason="no active provider is bound",
                ),
                None,
            )
        runtime = self._require_provider_locked(
            responsibility_id, state.active_provider
        )
        if runtime.registration.mode is not ProviderMode.ACTIVE:
            raise ProviderLifecycleError(
                "active binding and provider lifecycle mode disagree"
            )
        return (
            ResponsibilityResolution(
                status=ResolutionStatus.ACTIVE,
                state=state,
                active_provider=state.active_provider,
            ),
            runtime,
        )

    def invoke(
        self,
        responsibility_id: str,
        request: RequestT,
    ) -> InvocationEnvelope:
        with self._lock:
            resolution, runtime = self._resolve_locked(responsibility_id)
            authority = (
                None
                if resolution.active_provider is None
                else self._issue_authority_locked(
                    resolution.state,
                    resolution.active_provider,
                    production_authority=True,
                )
            )
        if resolution.status is ResolutionStatus.UNAVAILABLE:
            raise ProviderUnavailable(
                f"responsibility {responsibility_id} is unavailable: "
                f"{resolution.unavailable_reason}"
            )
        assert authority is not None
        assert runtime is not None
        try:
            provider_receipt = runtime.handler(request, authority)
        except Exception as exc:
            raise ProviderInvocationFailed(
                responsibility_id, authority.provider, exc
            ) from exc
        provider_receipt_json, provider_receipt_digest = (
            self._freeze_provider_receipt(provider_receipt)
        )
        with self._lock:
            self._validate_authority_locked(authority)
            return self._make_envelope(
                authority,
                provider_receipt_json,
                provider_receipt_digest,
            )

    def invoke_shadow(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
        request: RequestT,
    ) -> InvocationEnvelope:
        with self._lock:
            state = self._require_state_locked(responsibility_id)
            runtime = self._require_provider_locked(responsibility_id, provider)
            if runtime.registration.mode not in {
                ProviderMode.SHADOW,
                ProviderMode.VERIFIED,
            }:
                raise ProviderLifecycleError(
                    "shadow invocation requires a shadow or verified provider"
                )
            authority = self._issue_authority_locked(
                state,
                provider,
                production_authority=False,
            )
        try:
            provider_receipt = runtime.handler(request, authority)
        except Exception as exc:
            raise ProviderInvocationFailed(responsibility_id, provider, exc) from exc
        provider_receipt_json, provider_receipt_digest = (
            self._freeze_provider_receipt(provider_receipt)
        )
        with self._lock:
            return self._make_envelope(
                authority,
                provider_receipt_json,
                provider_receipt_digest,
            )

    def validate_envelope(
        self, envelope: InvocationEnvelope
    ) -> ResponsibilityState:
        expected_envelope_signature = self._sign(envelope.unsigned_dict())
        if not hmac.compare_digest(
            expected_envelope_signature, envelope.envelope_signature
        ):
            raise InvalidInvocationEnvelope(
                "invocation envelope was not issued by this responsibility router"
            )
        authority = envelope.authority
        if not authority.production_authority:
            raise NoProductionAuthority(
                f"provider {authority.provider.provider_id} has no production authority"
            )
        with self._lock:
            return self._validate_authority_locked(authority)

    def _issue_authority_locked(
        self,
        state: ResponsibilityState,
        provider: ProviderIdentity,
        *,
        production_authority: bool,
    ) -> ProductionAuthorityToken:
        unsigned = ProductionAuthorityToken(
            responsibility_id=state.responsibility_id,
            contract_owner_id=state.contract_owner_id,
            provider=provider,
            authority_epoch=state.authority_epoch,
            binding_digest=state.binding_digest,
            production_authority=production_authority,
            grant_nonce=secrets.token_hex(16),
            grant_signature="0" * 64,
        )
        return replace(
            unsigned,
            grant_signature=self._sign(unsigned.unsigned_dict()),
        )

    def _validate_authority_locked(
        self, authority: ProductionAuthorityToken
    ) -> ResponsibilityState:
        expected_grant_signature = self._sign(authority.unsigned_dict())
        if not hmac.compare_digest(
            expected_grant_signature, authority.grant_signature
        ):
            raise StaleAuthority(
                "production authority token was not issued by this router"
            )
        try:
            state = self._states[authority.responsibility_id]
        except KeyError as exc:
            raise StaleAuthority(
                f"responsibility {authority.responsibility_id} no longer exists"
            ) from exc
        if (
            not authority.production_authority
            or state.contract_owner_id != authority.contract_owner_id
            or state.active_provider != authority.provider
            or state.authority_epoch != authority.authority_epoch
            or state.binding_digest != authority.binding_digest
        ):
            raise StaleAuthority(
                f"authority for {authority.responsibility_id} is stale"
            )
        runtime = self._providers.get(
            (authority.responsibility_id, authority.provider)
        )
        if runtime is None or runtime.registration.mode is not ProviderMode.ACTIVE:
            raise StaleAuthority(
                f"provider {authority.provider.provider_id} is no longer active"
            )
        return state

    def _make_envelope(
        self,
        authority: ProductionAuthorityToken,
        provider_receipt_json: str,
        provider_receipt_digest: str,
    ) -> InvocationEnvelope:
        unsigned = InvocationEnvelope(
            authority=authority,
            provider_receipt_json=provider_receipt_json,
            provider_receipt_digest=provider_receipt_digest,
            envelope_signature="0" * 64,
        )
        return replace(
            unsigned,
            envelope_signature=self._sign(unsigned.unsigned_dict()),
        )

    def _freeze_provider_receipt(self, provider_receipt: ReceiptT) -> tuple[str, str]:
        try:
            jsonable_receipt = _jsonable(provider_receipt)
            _require_portable_payload(jsonable_receipt)
            provider_receipt_json = _canonical_json(jsonable_receipt)
        except (TypeError, ValueError) as exc:
            raise InvalidProviderReceipt(
                "provider receipt violates the portable JSON contract "
                f"({type(exc).__name__})"
            ) from exc
        return provider_receipt_json, _digest(jsonable_receipt)

    def _sign(self, value: object) -> str:
        return hmac.new(
            self._signing_key,
            _canonical_json(value).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _require_state_locked(self, responsibility_id: str) -> ResponsibilityState:
        try:
            return self._states[responsibility_id]
        except KeyError as exc:
            raise UnknownResponsibility(
                f"unknown responsibility {responsibility_id}"
            ) from exc

    def _require_provider_locked(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
    ) -> _RuntimeProvider[RequestT, ReceiptT]:
        try:
            return self._providers[(responsibility_id, provider)]
        except KeyError as exc:
            raise UnknownProvider(
                f"provider {provider.provider_id}@{provider.version} with fingerprint "
                f"{provider.fingerprint} is not registered for {responsibility_id}"
            ) from exc


class ResponsibilityRouter(Generic[RequestT, ReceiptT]):
    """Provider-facing registration, resolution, and invocation surface.

    It intentionally has no lifecycle mutation or signing API. The trusted
    in-process handler receives only a request and a bounded authority token;
    an untrusted provider must remain behind a trusted out-of-process adapter.
    """

    __slots__ = ("__control",)

    def __init__(
        self,
        control: _ResponsibilityControlPlane[RequestT, ReceiptT],
        factory_key: object,
    ) -> None:
        if factory_key is not _CONTROL_PLANE_FACTORY_KEY:
            raise PermissionError(
                "ResponsibilityRouter must be created by the control-plane factory"
            )
        self.__control = control

    def define(self, contract: ResponsibilityContract) -> ResponsibilityState:
        return self.__control.define(contract)

    def register(
        self,
        contract: ResponsibilityContract,
        provider: ProviderIdentity,
        handler: Callable[[RequestT, ProductionAuthorityToken], ReceiptT],
    ) -> ProviderRegistration:
        return self.__control.register(contract, provider, handler)

    def state(self, responsibility_id: str) -> ResponsibilityState:
        return self.__control.state(responsibility_id)

    def registration(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
    ) -> ProviderRegistration:
        return self.__control.registration(responsibility_id, provider)

    def resolve(self, responsibility_id: str) -> ResponsibilityResolution:
        return self.__control.resolve(responsibility_id)

    def invoke(
        self,
        responsibility_id: str,
        request: RequestT,
    ) -> InvocationEnvelope:
        return self.__control.invoke(responsibility_id, request)

    def invoke_shadow(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
        request: RequestT,
    ) -> InvocationEnvelope:
        return self.__control.invoke_shadow(responsibility_id, provider, request)

    def validate_envelope(
        self,
        envelope: InvocationEnvelope,
    ) -> ResponsibilityState:
        return self.__control.validate_envelope(envelope)


class ResponsibilityReconciler(Generic[RequestT, ReceiptT]):
    """Exclusive object capability for lifecycle and active-owner mutation.

    Runtime providers receive only a request and authority token. Keeping this
    reconciler out of trusted adapter closures is the in-process capability
    boundary; adversarial code requires process isolation.
    """

    def __init__(
        self,
        control: _ResponsibilityControlPlane[RequestT, ReceiptT],
        factory_key: object,
    ) -> None:
        if factory_key is not _CONTROL_PLANE_FACTORY_KEY:
            raise PermissionError(
                "ResponsibilityReconciler must be created by the control-plane factory"
            )
        self.__control = control

    def qualify_provider(
        self,
        responsibility_id: str,
        provider: ProviderIdentity,
        *,
        expected_mode: ProviderMode,
        target_mode: ProviderMode,
        evidence_refs: Sequence[str],
    ) -> ProviderLifecycleReceipt:
        return self.__control._transition_provider(
            responsibility_id,
            provider,
            expected_mode=expected_mode,
            target_mode=target_mode,
            evidence_refs=evidence_refs,
        )

    def activate(self, decision: HandoverDecision) -> HandoverReceipt:
        return self.__control._activate(decision)


def create_responsibility_control_plane(
) -> tuple[
    ResponsibilityRouter[RequestT, ReceiptT],
    ResponsibilityReconciler[RequestT, ReceiptT],
]:
    control: _ResponsibilityControlPlane[RequestT, ReceiptT] = (
        _ResponsibilityControlPlane()
    )
    router = ResponsibilityRouter(control, _CONTROL_PLANE_FACTORY_KEY)
    reconciler = ResponsibilityReconciler(control, _CONTROL_PLANE_FACTORY_KEY)
    return router, reconciler
