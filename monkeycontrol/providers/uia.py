"""Windows UI Automation and SendInput, over the MTA execution host.

This provider is the semantic backend: it asks the host for elements by their
UI Automation properties and hands back the one the caller named, with the
bounds UIA reported. A coordinate only ever appears afterwards, as the point an
input op is aimed at. Regular expressions are Python's and are evaluated here,
so the host is only ever asked for exact property matches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from ..contract import BUTTONS, ContractError, TargetSpec
from ..host import HostProcess
from ..trace import ResolvedTarget, WindowInfo
from .base import FocusError, ResolutionError, as_list, bounds_of, window_of

#: How many candidate names an ambiguity refusal spells out.
NAMED_CANDIDATES = 5
DEFAULT_DEPTH = 6
DEFAULT_MAX_NODES = 400
DEFAULT_WAIT_WINDOW_MS = 10000


def _criteria(target: TargetSpec) -> dict[str, str]:
    """The exact property matches the host can make on its own."""

    criteria: dict[str, str] = {}
    for key, value in (
        ("control_type", target.control_type),
        ("name", target.name),
        ("automation_id", target.automation_id),
        ("class_name", target.class_name),
    ):
        if value:
            criteria[key] = value
    return criteria


def _describe(target: TargetSpec) -> str:
    parts = [
        f"{key}={value!r}"
        for key, value in (
            ("controlType", target.control_type),
            ("name", target.name),
            ("nameRegex", target.name_regex),
            ("automationId", target.automation_id),
            ("className", target.class_name),
        )
        if value
    ]
    return ", ".join(parts) or "no property"


def _name(candidate: Mapping) -> str:
    return str(candidate.get("name") or "")


def _node(payload: Mapping) -> dict:
    """One element payload with its list-valued fields read back as lists."""

    node = dict(payload)
    node["patterns"] = [str(item) for item in as_list(node.get("patterns"))]
    return node


class UiaProvider:
    """Semantic resolution and input, spoken to one execution host."""

    name = "windows-uia"

    def __init__(self, host: HostProcess) -> None:
        self._host = host

    @property
    def host(self) -> HostProcess:
        return self._host

    # -- observation ----------------------------------------------------
    def windows(self, application: str, title: str | None = None) -> list[WindowInfo]:
        """Top-level windows of one process, filtered by a Python title regex.

        The regex stays here: ``Action.window`` was validated as a Python
        pattern, and .NET would read some of those patterns differently.
        """

        result = self._host.request("windows", process=application, title_regex=None)
        found = [window_of(item) for item in as_list(result.get("windows"))]
        if title:
            pattern = re.compile(title)
            found = [window for window in found if pattern.search(window.title)]
        return found

    def foreground(self) -> WindowInfo:
        """The window that would receive input right now."""

        return window_of(self._host.request("foreground"))

    def process_name(self, pid: int) -> str:
        return str(self._host.request("process", pid=int(pid)).get("name") or "")

    def inspect(
        self,
        window: WindowInfo,
        *,
        depth: int = DEFAULT_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> dict:
        """The window's element tree, bounded by ``depth`` and ``max_nodes``."""

        tree = self._host.request(
            "inspect",
            handle=window.handle,
            depth=int(depth),
            max_nodes=int(max_nodes),
        )
        return dict(
            tree, nodes=[_node(node) for node in as_list(tree.get("nodes"))]
        )

    def resolve(self, window: WindowInfo, target: TargetSpec) -> ResolvedTarget:
        """The single element ``target`` names, or a refusal that says why.

        Exact properties are matched by the host; ``name_regex`` is a
        case-insensitive :func:`re.search` applied here. Several matches
        without an ``index`` are ambiguous rather than arbitrary, because
        picking the first one silently is how automation clicks the wrong
        button.
        """

        result = self._host.request(
            "find", handle=window.handle, criteria=_criteria(target)
        )
        candidates = [_node(item) for item in as_list(result.get("candidates"))]
        if target.name_regex:
            pattern = re.compile(target.name_regex, re.IGNORECASE)
            candidates = [item for item in candidates if pattern.search(_name(item))]
        if not candidates:
            raise ResolutionError(
                "TARGET_UNRESOLVED",
                f"no element in {window.title!r} matched {_describe(target)}",
                (),
            )
        if len(candidates) > 1 and target.index is None:
            named = ", ".join(
                repr(_name(item)) for item in candidates[:NAMED_CANDIDATES]
            )
            more = (
                f" and {len(candidates) - NAMED_CANDIDATES} more"
                if len(candidates) > NAMED_CANDIDATES
                else ""
            )
            # The host stops collecting after its own cap, so say "at least"
            # rather than report a count that is quietly short.
            count = (
                f"at least {len(candidates)}"
                if result.get("truncated")
                else str(len(candidates))
            )
            raise ResolutionError(
                "TARGET_AMBIGUOUS",
                f"{count} elements in {window.title!r} matched "
                f"{_describe(target)}: {named}{more}; name one with index",
                candidates,
            )
        index = 0 if target.index is None else target.index
        if index >= len(candidates):
            raise ResolutionError(
                "TARGET_UNRESOLVED",
                f"index {index} is past the {len(candidates)} element(s) in "
                f"{window.title!r} that matched {_describe(target)}",
                candidates,
            )
        return self._element(candidates[index])

    def read(self, target: ResolvedTarget) -> dict:
        """What the element says about itself right now."""

        return self._host.request("read", runtime_id=target.runtime_id)

    # -- actuation ------------------------------------------------------
    def focus(self, window: WindowInfo, target: ResolvedTarget | None) -> None:
        """Bring the window forward, then focus the element when there is one.

        A focus that did not take is raised, not returned: every input op after
        this one goes to whatever holds the foreground, so silently carrying on
        is how a keystroke ends up in somebody else's window.
        """

        where = f"{window.title!r} (pid {window.pid})"
        reply = self._host.request(
            "focus",
            handle=window.handle,
            runtime_id=target.runtime_id if target is not None else None,
        )
        if not reply.get("foreground"):
            raise FocusError(f"{where} would not come to the foreground")
        if target is not None and not reply.get("element_focused"):
            raise FocusError(
                f"{where} came forward but {target.name!r} would not take focus"
            )

    def invoke(self, target: ResolvedTarget) -> bool:
        """Press the element through InvokePattern; ``False`` when it has none."""

        return bool(
            self._host.request("invoke", runtime_id=target.runtime_id).get("invoked")
        )

    def set_value(self, target: ResolvedTarget, text: str) -> bool:
        """Write through ValuePattern; ``False`` when the element has none."""

        return bool(
            self._host.request(
                "set_value", runtime_id=target.runtime_id, text=text
            ).get("set")
        )

    def click(
        self, point: tuple[int, int], *, button: str = "left", count: int = 1
    ) -> None:
        if button not in BUTTONS:
            raise ContractError(
                f"button must be one of {', '.join(BUTTONS)}, not {button!r}"
            )
        x, y = _point(point)
        self._host.request("click", x=x, y=y, button=button, count=int(count))

    def move(self, point: tuple[int, int]) -> None:
        x, y = _point(point)
        self._host.request("move", x=x, y=y)

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        steps: int = 20,
        ms: int = 400,
    ) -> None:
        x, y = _point(start)
        to_x, to_y = _point(end)
        self._host.request(
            "drag", x=x, y=y, to_x=to_x, to_y=to_y, steps=int(steps), ms=int(ms)
        )

    def type_text(self, text: str) -> None:
        """Send text as Unicode key events, so no keyboard layout can alter it."""

        self._host.request("type", text=str(text))

    def keypress(self, keys: str) -> None:
        """Send one chord such as ``ctrl+shift+s``, ``alt+f4`` or ``enter``."""

        self._host.request("keys", chord=str(keys))

    def scroll(self, point: tuple[int, int], delta: int) -> None:
        x, y = _point(point)
        self._host.request("scroll", x=x, y=y, delta=int(delta))

    def launch(
        self,
        command: Iterable[str],
        *,
        wait_window_ms: int = DEFAULT_WAIT_WINDOW_MS,
    ) -> WindowInfo:
        """Start a process and wait for its first top-level window.

        Which process may be launched is the runtime's decision, not this
        provider's: by the time a command arrives here it has already been
        allowed.
        """

        return window_of(
            self._host.request(
                "launch",
                command=[str(part) for part in command],
                wait_window_ms=int(wait_window_ms),
            )
        )

    def _element(self, candidate: Mapping) -> ResolvedTarget:
        return ResolvedTarget(
            control_type=str(candidate.get("controlType") or "Unknown"),
            name=_name(candidate),
            automation_id=str(candidate["automationId"])
            if candidate.get("automationId")
            else None,
            class_name=str(candidate["className"])
            if candidate.get("className")
            else None,
            bounds=bounds_of(candidate.get("bounds"), "an element's bounds"),
            runtime_id=str(candidate["runtime_id"])
            if candidate.get("runtime_id")
            else None,
            backend=self.name,
        )


def _point(point: Sequence[int]) -> tuple[int, int]:
    if len(point) != 2:
        raise ContractError("a point must be (x, y) in physical screen pixels")
    return int(point[0]), int(point[1])
