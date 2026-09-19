"""Polling one declared post-condition until it holds, or until it will not.

A verification is the only thing that turns an action that ran into an action
that worked, so it is checked here rather than inferred from the fact that no
call raised. This module reaches the desktop only through the two callables
the runtime hands it, keeps no state and writes nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .contract import Action, ContractError, TargetSpec
from .host import HostError
from .providers import ResolutionError
from .trace import ResolvedTarget

#: How often a post-condition is re-checked while its timeout has not expired.
POLL_S = 0.15
#: What an element's state answers with; every other key is ignored.
READABLE = ("value", "enabled", "toggled", "offscreen")

#: ``element(spec)`` resolves one target and reads it, or raises.
Element = Callable[[TargetSpec], "tuple[ResolvedTarget, dict]"]
#: ``windows(title)`` lists the application's windows whose title matches.
Windows = Callable[[str | None], list]


def target_name(spec: TargetSpec | None) -> str:
    """The shortest honest name for a declared target, for a label or a badge."""

    if spec is None:
        return "the target"
    for value in (
        spec.name,
        spec.name_regex,
        spec.automation_id,
        spec.class_name,
        spec.control_type,
    ):
        if value:
            return str(value)
    return "explicit bounds"


def summary(action: Action) -> str:
    """One short phrase naming what was expected, safe to show on screen."""

    expectation = action.verification
    if expectation is None:
        return "nothing"
    if expectation.title:
        return f"{expectation.expect} {expectation.title}"
    if expectation.expect == "file" and expectation.path:
        return f"file {Path(expectation.path).name}"
    return f"{expectation.expect} {target_name(expectation.target or action.target)}"


def _missing(exc: Exception) -> bool:
    """Whether an exception means "not there", rather than "cannot tell"."""

    if isinstance(exc, (ResolutionError, ContractError)):
        return True
    return isinstance(exc, HostError) and exc.code != "HOST_ERROR"


def _state_holds(state: dict, wanted: dict, *, secret: bool) -> tuple[bool, str]:
    """Whether a read element satisfies the declared state.

    ``value`` is a containment, because a caller verifies that what was typed
    arrived, not that it is the whole of a document; the flags are equality.
    A sensitive action's expected value is not named in the answer: the
    receipt redacts what it can recognise, and the surest way not to leak a
    secret through a sentence about it is not to put it there.
    """

    for key, expected in wanted.items():
        actual = state.get(key)
        if key == "value":
            if not isinstance(actual, str) or expected not in actual:
                said = (
                    "the expected text" if secret else repr(expected)
                )
                return False, f"value does not contain {said}"
        elif bool(actual) is not bool(expected):
            return False, f"{key} is {actual!r}, not {expected!r}"
    return True, ""


def _check(
    action: Action, windows: Windows, element: Element
) -> tuple[bool, str, dict | None]:
    """One pass over the declared expectation: held, why, and what was read."""

    expectation = action.verification
    if expectation.expect == "file":
        if not expectation.path:
            return False, "no path was declared", None
        there = Path(expectation.path).exists()
        said = "exists" if there else "is not there"
        return there, f"{expectation.path} {said}", None
    if expectation.expect == "window":
        found = windows(expectation.title)
        titles = ", ".join(repr(window.title) for window in found[:3])
        return bool(found), titles or f"no window matched {expectation.title!r}", None
    if expectation.expect == "absent" and expectation.title:
        found = windows(expectation.title)
        titles = ", ".join(repr(window.title) for window in found[:3])
        said = f"still open: {titles}" if found else "no window matched"
        return not found, said, None
    spec = expectation.target or action.target
    if spec is None:
        return False, "the expectation names no element", None
    try:
        resolved, state = element(spec)
    except Exception as exc:  # narrowed by _missing: anything else is a real fault
        if not _missing(exc):
            raise
        gone = expectation.expect == "absent"
        return gone, str(exc), None
    if expectation.expect == "absent":
        return False, f"{resolved.name!r} is still there", None
    held, why = _state_holds(state, expectation.state, secret=action.sensitive)
    kept = {key: state.get(key) for key in READABLE if key in state}
    return held, why or f"{resolved.name!r} matched", kept


def verify_action(
    action: Action,
    *,
    windows: Windows,
    element: Element,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Poll the action's post-condition and answer what the receipt records.

    The first check happens before any waiting, so a zero timeout is one
    honest look rather than no look at all.
    """

    expectation = action.verification
    if expectation is None:
        raise ContractError("the action declared no verification to check")
    started = clock()
    deadline = started + expectation.timeout_ms / 1000
    while True:
        held, detail, state = _check(action, windows, element)
        remaining = deadline - clock()
        if held or remaining <= 0:
            break
        time.sleep(min(POLL_S, remaining))
    outcome: dict[str, object] = {
        "expect": expectation.expect,
        "status": "passed" if held else "failed",
        "detail": detail,
        "duration_ms": int((clock() - started) * 1000),
    }
    if state is not None:
        outcome["state"] = state
    return outcome
