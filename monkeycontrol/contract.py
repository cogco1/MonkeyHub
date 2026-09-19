"""ComputerAction@1: one declarative desktop action, validated into a value.

MonkeyControl owns what a caller may ask a desktop to do, the closed sets of
action types, expectations and refusal codes, and the redacted dict form a
receipt records. It owns nothing about how an action reaches the screen, where
a trace is written, or any project state on the design spine.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Callable

ACTION_SCHEMA = "ComputerAction@1"
RECEIPT_SCHEMA = "ComputerActionReceipt@1"
ACTION_TYPES = (
    "launch",
    "click",
    "double_click",
    "right_click",
    "invoke",
    "set_value",
    "type",
    "keypress",
    "drag",
    "scroll",
    "wait",
    "screenshot",
    "highlight",
)
EXPECTATIONS = ("window", "element", "absent", "file")
REFUSALS = (
    "ACTION_INVALID",
    "APP_NOT_ALLOWED",
    "WINDOW_NOT_FOUND",
    "TARGET_UNRESOLVED",
    "TARGET_AMBIGUOUS",
    "FOCUS_LOST",
    "BACKEND_UNAVAILABLE",
    "HOST_ERROR",
    "VERIFY_FAILED",
    "RECORDING_ACTIVE",
    "RECORDING_NOT_ACTIVE",
)
BUTTONS = ("left", "right", "middle")
#: ``None`` means the automatic backend, which is ``windows-uia``.
BACKENDS = ("windows-uia", "visual-fallback")
VISUAL_FALLBACK = "visual-fallback"
#: A coordinate is an execution projection, so explicit bounds need this backend.
TARGET_REQUIRED = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "invoke",
        "set_value",
        "type",
        "drag",
        "highlight",
    }
)
ELEMENT_STATE = {"value": str, "enabled": bool, "toggled": bool}
DEFAULT_TIMEOUT_MS = 5000
MAX_TIMEOUT_MS = 60000
MAX_WAIT_MS = 600000
MAX_INTENT = 200

# Target fields name UI Automation properties and keep that spelling; every
# other key of the payload is snake_case, like the receipt it ends up in.
_TARGET_KEYS = (
    "controlType",
    "name",
    "nameRegex",
    "automationId",
    "className",
    "index",
    "bounds",
    "backend",
)
_VERIFICATION_KEYS = ("expect", "title", "target", "state", "path", "timeout_ms")
_ACTION_KEYS = (
    "schema",
    "step_id",
    "intent",
    "application",
    "window",
    "target",
    "action",
    "verification",
    "capture",
)
_VERB_KEYS = (
    "type",
    "button",
    "text",
    "sensitive",
    "keys",
    "to",
    "delta",
    "ms",
    "command",
)


class ContractError(ValueError):
    """A payload is not a ComputerAction@1; the message names the bad key."""

    code: str = "ACTION_INVALID"


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """What the caller says the UI element is, never where it is on screen."""

    control_type: str | None = None
    name: str | None = None
    name_regex: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    index: int | None = None
    bounds: tuple[int, int, int, int] | None = None
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class Verification:
    """One declarative post-condition, polled until ``timeout_ms``."""

    expect: str
    title: str | None = None
    target: TargetSpec | None = None
    state: dict[str, object] = field(default_factory=dict)
    path: str | None = None
    timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclass(frozen=True, slots=True, kw_only=True)
class Action:
    """A validated desktop action: what to do, to which element, and the proof."""

    step_id: str | None = None
    intent: str
    application: str
    window: str | None = None
    target: TargetSpec | None = None
    type: str
    button: str = "left"
    text: str | None = None
    sensitive: bool = False
    keys: str | None = None
    to: TargetSpec | None = None
    delta: int | None = None
    ms: int | None = None
    command: tuple[str, ...] | None = None
    verification: Verification | None = None
    capture: bool = False


def _known(payload: Mapping, allowed: Sequence[str], prefix: str) -> None:
    for key in payload:
        if not isinstance(key, str) or key not in allowed:
            name = f"{prefix}{key}" if isinstance(key, str) else repr(key)
            raise ContractError(f"{name!r} is not a {ACTION_SCHEMA} key")


def _mapping(value: object, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    return value


def _string(value: object, name: str) -> str:
    """Any text, including the empty string a ``set_value`` clears a field with."""

    if not isinstance(value, str):
        raise ContractError(f"{name} must be text")
    return value


def _text(value: object, name: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be non-empty text")
    if maximum is not None and len(value) > maximum:
        raise ContractError(f"{name} must be at most {maximum} characters")
    return value


def _regex(value: object, name: str) -> str:
    pattern = _text(value, name)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ContractError(f"{name} must be a regular expression: {exc}") from exc
    return pattern


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ContractError(f"{name} must be at most {maximum}")
    return value


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{name} must be true or false")
    return value


def _choice(value: object, name: str, allowed: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(
            f"{name} must be one of {', '.join(allowed)}, not {value!r}"
        )
    return value


def _bounds(value: object, name: str) -> tuple[int, int, int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise ContractError(f"{name} must be [left, top, right, bottom] screen pixels")
    left, top, right, bottom = (_integer(item, name) for item in value)
    if right <= left or bottom <= top:
        raise ContractError(f"{name} must have a positive width and height")
    return (left, top, right, bottom)


def _ticks(value: object, name: str) -> int:
    ticks = _integer(value, name)
    if ticks == 0:
        raise ContractError(f"{name} must be a non-zero number of wheel ticks")
    return ticks


def _command(value: object, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise ContractError(f"{name} must be a non-empty [executable, argument...] list")
    return tuple(_text(item, name) for item in value)


def _application(value: object) -> str:
    text = _text(value, "application")
    if any(separator in text for separator in ("/", "\\", ":")):
        # A launch may name an absolute executable path; it keeps its spelling.
        return text
    name = text[:-4] if text.casefold().endswith(".exe") else text
    if not name:
        raise ContractError("application must name a process")
    return name.lower()


def _optional(
    payload: Mapping,
    key: str,
    prefix: str,
    read: Callable[[object, str], object],
    default: object = None,
) -> object | None:
    """Read one optional key; absent and explicitly null both take the default."""

    value = payload.get(key)
    return default if value is None else read(value, f"{prefix}{key}")


def _element_state(value: object, name: str) -> dict[str, object]:
    payload = _mapping(value, name)
    state: dict[str, object] = {}
    for key, item in payload.items():
        expected = ELEMENT_STATE.get(key) if isinstance(key, str) else None
        if expected is None:
            raise ContractError(f"{name}.{key} is not a verifiable element state")
        if expected is bool:
            state[key] = _flag(item, f"{name}.{key}")
        else:
            state[key] = _text(item, f"{name}.{key}")
    return state


def _target(value: object, name: str) -> TargetSpec:
    payload = _mapping(value, name)
    _known(payload, _TARGET_KEYS, f"{name}.")
    prefix = f"{name}."
    bounds = _optional(payload, "bounds", prefix, _bounds)
    backend = _optional(
        payload, "backend", prefix, lambda item, key: _choice(item, key, BACKENDS)
    )
    if bounds is not None and backend != VISUAL_FALLBACK:
        raise ContractError(
            f"{name}.bounds is honoured only when {name}.backend is "
            f"{VISUAL_FALLBACK!r}: a coordinate is an execution projection"
        )
    target = TargetSpec(
        control_type=_optional(payload, "controlType", prefix, _text),
        name=_optional(payload, "name", prefix, _text),
        name_regex=_optional(payload, "nameRegex", prefix, _regex),
        automation_id=_optional(payload, "automationId", prefix, _text),
        class_name=_optional(payload, "className", prefix, _text),
        index=_optional(
            payload, "index", prefix, lambda item, key: _integer(item, key, minimum=0)
        ),
        bounds=bounds,
        backend=backend,
    )
    if not any(
        (
            target.control_type,
            target.name,
            target.name_regex,
            target.automation_id,
            target.class_name,
            target.bounds,
        )
    ):
        raise ContractError(
            f"{name} must name at least one UI Automation property or explicit bounds"
        )
    return target


def _verification(value: object, name: str) -> Verification:
    payload = _mapping(value, name)
    _known(payload, _VERIFICATION_KEYS, f"{name}.")
    prefix = f"{name}."
    return Verification(
        expect=_choice(payload.get("expect"), f"{name}.expect", EXPECTATIONS),
        title=_optional(payload, "title", prefix, _regex),
        target=_optional(payload, "target", prefix, _target),
        state=_optional(payload, "state", prefix, _element_state, {}),
        path=_optional(payload, "path", prefix, _text),
        timeout_ms=_optional(
            payload,
            "timeout_ms",
            prefix,
            lambda item, key: _integer(
                item, key, minimum=0, maximum=MAX_TIMEOUT_MS
            ),
            DEFAULT_TIMEOUT_MS,
        ),
    )


def validate_action(payload: object) -> Action:
    """Read one ComputerAction@1 payload into an :class:`Action`.

    Unknown keys are refused rather than ignored, defaults are applied here so
    every caller sees the same action, and each action type must carry what it
    needs: a target for the element actions, a destination for a drag, wheel
    ticks for a scroll, ``keys`` for a keypress, ``command`` for a launch,
    ``ms`` for a wait, ``text`` for typing. ``set_value`` may carry an empty
    string, which is how a field is cleared.
    """

    body = _mapping(payload, "the action payload")
    _known(body, _ACTION_KEYS, "")
    if body.get("schema") is not None:
        _choice(body["schema"], "schema", (ACTION_SCHEMA,))
    verb = _mapping(body.get("action"), "action")
    _known(verb, _VERB_KEYS, "action.")
    kind = _choice(verb.get("type"), "action.type", ACTION_TYPES)
    target = _optional(body, "target", "", _target)
    destination = _optional(verb, "to", "action.", _target)
    text = _optional(verb, "text", "action.", _string)
    keys = _optional(verb, "keys", "action.", _text)
    command = _optional(verb, "command", "action.", _command)
    delta = _optional(verb, "delta", "action.", _ticks)
    milliseconds = _optional(
        verb,
        "ms",
        "action.",
        lambda item, key: _integer(item, key, minimum=0, maximum=MAX_WAIT_MS),
    )
    if kind in TARGET_REQUIRED and target is None:
        raise ContractError(f"target is required for a {kind} action")
    if kind == "drag" and destination is None:
        raise ContractError("action.to is required for a drag action")
    if kind == "type" and not (text and text.strip()):
        raise ContractError("action.text is required for a type action")
    if kind == "set_value" and text is None:
        raise ContractError("action.text is required for a set_value action")
    if kind == "keypress" and keys is None:
        raise ContractError("action.keys is required for a keypress action")
    if kind == "launch" and command is None:
        raise ContractError("action.command is required for a launch action")
    if kind == "scroll" and delta is None:
        raise ContractError("action.delta is required for a scroll action")
    if kind == "wait" and milliseconds is None:
        raise ContractError("action.ms is required for a wait action")
    verification = _optional(body, "verification", "", _verification)
    # An element expectation falls back to the action's own target; absence
    # has nothing to fall back to unless a title or a target says what is gone.
    if (
        verification is not None
        and verification.expect == "absent"
        and verification.title is None
        and verification.target is None
        and target is None
    ):
        raise ContractError(
            "verification.title or a target is required for an absent expectation"
        )
    return Action(
        step_id=_optional(body, "step_id", "", _text),
        intent=_text(body.get("intent"), "intent", maximum=MAX_INTENT),
        application=_application(body.get("application")),
        window=_optional(body, "window", "", _regex),
        target=target,
        type=kind,
        button=_optional(
            verb,
            "button",
            "action.",
            lambda item, key: _choice(item, key, BUTTONS),
            "left",
        ),
        text=text,
        sensitive=_optional(verb, "sensitive", "action.", _flag, False),
        keys=keys,
        to=destination,
        delta=delta,
        ms=milliseconds,
        command=command,
        verification=verification,
        capture=_optional(body, "capture", "", _flag, False),
    )


def redacted_text(text: str) -> str:
    """How sensitive text appears outside this package: its length, never itself."""

    return f"<redacted {len(text)} chars>"


def target_payload(target: TargetSpec | None) -> dict | None:
    """The requested target as canonical JSON values, or ``None``."""

    if target is None:
        return None
    payload: dict[str, object] = {}
    for key, value in (
        ("controlType", target.control_type),
        ("name", target.name),
        ("nameRegex", target.name_regex),
        ("automationId", target.automation_id),
        ("className", target.class_name),
        ("index", target.index),
        ("backend", target.backend),
    ):
        if value is not None:
            payload[key] = value
    if target.bounds is not None:
        payload["bounds"] = list(target.bounds)
    return payload


def action_payload(action: Action) -> dict:
    """The action as a receipt records it: only the keys it uses, text redacted.

    ``sensitive`` text is replaced by its length, so a receipt and the demo
    overlay built from it can be read by anyone who may see the screen.
    """

    payload: dict[str, object] = {
        "schema": ACTION_SCHEMA,
        "type": action.type,
        "button": action.button,
        "sensitive": action.sensitive,
        "capture": action.capture,
    }
    if action.text is not None:
        payload["text"] = (
            redacted_text(action.text) if action.sensitive else action.text
        )
    if action.keys is not None:
        payload["keys"] = action.keys
    if action.to is not None:
        payload["to"] = target_payload(action.to)
    if action.delta is not None:
        payload["delta"] = action.delta
    if action.ms is not None:
        payload["ms"] = action.ms
    if action.command is not None:
        payload["command"] = list(action.command)
    return payload
