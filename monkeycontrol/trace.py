"""ComputerActionReceipt@1: what one executed desktop action left behind.

MonkeyControl owns the receipt's shape and its content digest: which window
and element were actually resolved, which backend produced them, what was
done, and whether the declared post-condition held. A receipt proves only what
this package observed at the screen boundary; it is never project state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from archflow.contracts.canonical import canonical_digest

from .contract import (
    RECEIPT_SCHEMA,
    REFUSALS,
    Action,
    ContractError,
    action_payload,
    redacted_text,
    target_payload,
)

STATUSES = ("succeeded", "failed", "refused")
VERIFICATION_STATUSES = ("passed", "failed", "skipped")


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """The element a provider actually found, with the bounds it reported."""

    control_type: str
    name: str
    automation_id: str | None
    class_name: str | None
    bounds: tuple[int, int, int, int]
    runtime_id: str | None
    backend: str

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """One top-level window and the process it belongs to."""

    handle: int
    title: str
    pid: int
    process: str
    bounds: tuple[int, int, int, int]


def window_payload(window: WindowInfo | None) -> dict | None:
    """One window as JSON values, for a receipt or a caller's own answer."""

    if window is None:
        return None
    return {
        "handle": window.handle,
        "title": window.title,
        "pid": window.pid,
        "process": window.process,
        "bounds": list(window.bounds),
    }


def _resolved_payload(target: ResolvedTarget | None) -> dict | None:
    if target is None:
        return None
    return {
        "controlType": target.control_type,
        "name": target.name,
        "automationId": target.automation_id,
        "className": target.class_name,
        "bounds": list(target.bounds),
        "runtimeId": target.runtime_id,
        "backend": target.backend,
    }


def _redact(value: object, secret: str, mask: str) -> object:
    """Replace every string that carries the secret, however deeply it is nested."""

    if isinstance(value, str):
        return mask if secret in value else value
    if isinstance(value, Mapping):
        return {key: _redact(item, secret, mask) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_redact(item, secret, mask) for item in value]
    return value


def _verification_payload(action: Action, verification: object) -> dict | None:
    """The verification outcome as the receipt keeps it, with the secret removed.

    An element post-condition reads back what was typed, so the value a
    ``sensitive`` action wrote would otherwise reach the trace through
    ``detail`` or ``state.value``, and through the digest computed over them.
    """

    if verification is None:
        return None
    if not isinstance(verification, Mapping):
        raise ContractError("verification must be an object with expect and status")
    if not isinstance(verification.get("expect"), str) or not verification["expect"]:
        raise ContractError("verification.expect must name the declared expectation")
    if verification.get("status") not in VERIFICATION_STATUSES:
        raise ContractError(
            "verification.status must be one of " + ", ".join(VERIFICATION_STATUSES)
        )
    payload = dict(verification)
    if action.sensitive and action.text:
        payload = _redact(payload, action.text, redacted_text(action.text))
    return payload


def build_receipt(
    *,
    action: Action,
    step_id: str,
    mode: str,
    window: WindowInfo | None,
    target: ResolvedTarget | None,
    fallback: bool,
    point: tuple[int, int] | None,
    started_at: str,
    duration_ms: int,
    verification: dict | None,
    status: str,
    refusal: dict | None,
    screenshots: dict,
) -> dict:
    """Assemble one ComputerActionReceipt@1 and seal it with its own digest.

    Without a resolution the receipt says so: ``backend`` is ``"none"`` and the
    resolved target is absent, so a coordinate can never be read as a semantic
    match. Sensitive text is redacted wherever it appears, including inside the
    verification outcome. The digest covers every other key, so a trace line
    cannot be edited without saying so.
    """

    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, not {status!r}")
    if refusal is not None:
        if not isinstance(refusal, dict) or refusal.get("code") not in REFUSALS:
            raise ValueError(
                "refusal must carry a code from monkeycontrol.contract.REFUSALS"
            )
    verified = _verification_payload(action, verification)
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "step_id": step_id,
        "intent": action.intent,
        "application": action.application,
        "mode": mode,
        "window": window_payload(window),
        "target": {
            "requested": target_payload(action.target),
            "resolved": _resolved_payload(target),
        },
        "backend": target.backend if target is not None else "none",
        "fallback": bool(fallback),
        "action": action_payload(action),
        "execution": {
            "point": list(point) if point is not None else None,
            "started_at": started_at,
            "duration_ms": duration_ms,
        },
        "verification": verified,
        "status": status,
        "refusal": dict(refusal) if refusal is not None else None,
        "screenshots": {
            "before": (screenshots or {}).get("before"),
            "after": (screenshots or {}).get("after"),
        },
    }
    receipt["digest"] = canonical_digest(receipt)
    return receipt
