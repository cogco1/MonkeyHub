"""ComputerActionReceipt@1: what one executed desktop action left behind.

MonkeyControl owns the receipt's shape and its content digest: which window
and element were actually resolved, which backend produced them, what was
done, and whether the declared post-condition held. A receipt proves only what
this package observed at the screen boundary; it is never project state.
"""

from __future__ import annotations

from dataclasses import dataclass

from archflow.contracts.canonical import canonical_digest

from .contract import RECEIPT_SCHEMA, REFUSALS, Action, action_payload, target_payload

STATUSES = ("succeeded", "failed", "refused")


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


def _window_payload(window: WindowInfo | None) -> dict | None:
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
    match. The digest covers every other key, so a trace line cannot be edited
    without saying so.
    """

    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, not {status!r}")
    if refusal is not None:
        if not isinstance(refusal, dict) or refusal.get("code") not in REFUSALS:
            raise ValueError("refusal must carry a code from monkeycontrol.REFUSALS")
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "step_id": step_id,
        "intent": action.intent,
        "application": action.application,
        "mode": mode,
        "window": _window_payload(window),
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
        "verification": dict(verification) if verification is not None else None,
        "status": status,
        "refusal": dict(refusal) if refusal is not None else None,
        "screenshots": {
            "before": (screenshots or {}).get("before"),
            "after": (screenshots or {}).get("after"),
        },
    }
    receipt["digest"] = canonical_digest(receipt)
    return receipt
