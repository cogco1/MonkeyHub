"""Computer use, composed by the Hub: one policy, one runtime, three tools.

MonkeyControl decides nothing about permission: it takes an allow-list and a
trace directory and does what one action says. This module is where that
permission comes from. The policy file under the Hub's runtime root is read
fresh on every request -- enabling computer use is editing a file, never
restarting the Hub -- and the Hub keeps exactly one runtime per machine,
rebuilt when the policy it was built from changed and closed when the Hub
shuts down. It writes nothing: the trace directory it hands the runtime is the
runtime's own to write in, and the policy file is only ever read.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading

from pydantic import ValidationError

from monkeycontrol.runtime import (
    ComputerUseRuntime,
    RuntimePolicy,
    RuntimeRefusal,
    build_runtime,
    process_name,
)

from . import chat
from .models import (
    ComputerActionRequest, ComputerInspectRequest, ComputerPolicy,
    ComputerRecordingRequest, HubFailure,
)

#: Where the machine's owner says yes, relative to the Hub's runtime root.
POLICY_PATH = "diagnostics/monkeycontrol/policy.json"
#: Where the runtime keeps its receipts, recordings and screenshots.
TRACE_PATH = "diagnostics/monkeycontrol"
TOOL_NAMES = ("computer_inspect", "computer_action", "computer_record")
ROUTES = {
    "computer_inspect": "/api/computer/inspect",
    "computer_action": "/api/computer/actions",
    "computer_record": "/api/computer/recordings",
}
#: One sentence every tool description ends with, because a model reading the
#: catalogue has to know where this permission comes from and where it stops.
BOUNDARY = (
    "Computer use runs only while the local policy file enables it, drives only "
    "the applications that file allows, and never changes project files."
)


def read_policy(runtime_root: Path) -> ComputerPolicy:
    """What is allowed right now; absent, unreadable and invalid all mean no."""

    try:
        saved = json.loads((Path(runtime_root) / POLICY_PATH).read_text(encoding="utf-8"))
        return ComputerPolicy.model_validate(saved)
    except (OSError, ValueError, ValidationError):
        return ComputerPolicy()


def _refused(code: str, message: str) -> HubFailure:
    """One monkeycontrol refusal as the answer this route gives it."""

    return HubFailure(409, "COMPUTER_ACTION_REFUSED", f"{code}: {message}")


class ComputerService:
    """The Hub's single desktop runtime, and the gate in front of it."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        runtime_factory: Callable[[Path, RuntimePolicy], ComputerUseRuntime] | None = None,
    ) -> None:
        self._root = Path(runtime_root)
        self._factory = runtime_factory or build_runtime
        self._lock = threading.Lock()
        self._runtime: ComputerUseRuntime | None = None
        # The allow-list and mode the live runtime was actually built from.
        self._built: tuple[tuple[str, ...], str] | None = None

    # -- permission -----------------------------------------------------
    def _policy(self) -> ComputerPolicy:
        policy = read_policy(self._root)
        if not policy.enabled:
            raise HubFailure(
                403, "COMPUTER_USE_NOT_ENABLED",
                f"Computer use is off on this machine. It is enabled only by "
                f'{POLICY_PATH} under the Hub runtime root ({self._root}), with '
                f'{{"enabled": true, "allowedProcesses": ["notepad"], "mode": "fast"}}.',
            )
        return policy

    def _allowed(self, policy: ComputerPolicy) -> tuple[str, ...]:
        """The allow-list spelled the way the runtime compares processes."""

        return tuple(process_name(name) for name in policy.allowedProcesses)

    def _for(self, policy: ComputerPolicy) -> ComputerUseRuntime:
        """The runtime this policy asks for, built once and reused until it changes."""

        wanted = (self._allowed(policy), policy.mode)
        if self._runtime is not None and self._built != wanted:
            # A widened allow-list must not leave the old one running beside it.
            self._close()
        if self._runtime is None:
            self._runtime = self._factory(
                self._root / TRACE_PATH,
                RuntimePolicy(allowed_processes=wanted[0], mode=policy.mode),
            )
            self._built = wanted
        return self._runtime

    # -- the three things it does ---------------------------------------
    def inspect(self, request: ComputerInspectRequest) -> dict:
        """The element tree of one window of an allow-listed application."""

        policy = self._policy()
        allowed = self._allowed(policy)
        if process_name(request.application) not in allowed:
            raise _refused(
                "APP_NOT_ALLOWED",
                f"{request.application!r} is not in this machine's allow-list "
                f"({', '.join(allowed) or 'empty'})",
            )
        return self._answer(
            policy, "inspect", request.application, request.window, depth=request.depth
        )

    def act(self, request: ComputerActionRequest) -> dict:
        """One action, and the receipt it earned -- including a refused one."""

        return self._answer(self._policy(), "execute", request.action, mode=request.mode)

    def record(self, request: ComputerRecordingRequest) -> dict:
        """Start or stop the recording beside the trace."""

        policy = self._policy()
        if request.command == "stop":
            return self._answer(policy, "record_stop")
        if not request.name:
            raise HubFailure(
                422, "COMPUTER_ACTION_INVALID",
                "A recording is started with a name of letters, digits, - and _.",
            )
        return self._answer(policy, "record_start", request.name)

    def _answer(self, policy: ComputerPolicy, op: str, *args, **kwargs) -> dict:
        """Compose the runtime this policy asks for, and run one of its methods.

        Composing and calling are inside the same guard because they refuse the
        same way, and because one runtime serves one request at a time.

        A receipt is the answer whatever it says: a refused or failed step is a
        200 body, because the caller has to read the refusal to do anything
        about it. Two things are not receipts. Anything the package calls a
        mistake is 422 -- an action that is not a ComputerAction@1, and also a
        recording name it will not take, since this API's own pattern is the
        wider of the two. A refusal with no receipt to carry it is 409, with
        MonkeyControl's own code in it.
        """

        with self._lock:
            try:
                return getattr(self._for(policy), op)(*args, **kwargs)
            except RuntimeRefusal as exc:
                raise _refused(exc.code, str(exc)) from exc
            except ValueError as exc:  # ContractError is one of these
                raise HubFailure(422, "COMPUTER_ACTION_INVALID", str(exc)) from exc

    def close(self) -> None:
        """Stop the runtime this Hub started; closing twice is not an error."""

        with self._lock:
            self._close()

    def _close(self) -> None:
        """The same, for a caller that already holds the lock."""

        runtime, self._runtime, self._built = self._runtime, None, None
        if runtime is not None:
            runtime.close()


def tool_definitions() -> list[dict]:
    """The three MCP tools, mirroring the request models the routes validate."""

    return [
        {
            "name": "computer_inspect",
            "description": (
                "Read the element tree of one window on this machine: control types, "
                "names and automation ids to name a target with. Nothing is clicked "
                "or typed. " + BOUNDARY
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "application": {"type": "string", "maxLength": 120,
                                    "description": "Process name, such as notepad."},
                    "window": {"type": "string", "maxLength": 300,
                               "description": "Regular expression the window title must match."},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
                },
                "required": ["application"], "additionalProperties": False,
            },
        },
        {
            "name": "computer_action",
            "description": (
                "Run one ComputerAction@1 and read the receipt it earned: which window "
                "and element were actually resolved, what was done and whether the "
                "declared verification held. A refused or failed receipt is the answer, "
                "not an error; read its refusal code rather than repeating the action. "
                + BOUNDARY
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "object", "additionalProperties": True,
                        "description": (
                            "ComputerAction@1: {intent, application, window?, target?, "
                            "action: {type, ...}, verification?, capture?}. Types are launch, "
                            "click, double_click, right_click, invoke, set_value, type, keypress, "
                            "drag, scroll, wait, screenshot, highlight. A target names UI "
                            "Automation properties (controlType, name, nameRegex, automationId, "
                            "className, index), never coordinates. Mark a password sensitive: "
                            "true so only its length is recorded."
                        ),
                    },
                    "mode": {"type": "string", "enum": ["fast", "demo"],
                             "description": "demo highlights the target and shows the verdict; "
                                            "absent uses the mode the policy file chose."},
                },
                "required": ["action"], "additionalProperties": False,
            },
        },
        {
            "name": "computer_record",
            "description": (
                "Start or stop the screen recording kept beside the action trace, so a "
                "demonstration can be replayed and re-annotated afterwards. One recording "
                "runs at a time. " + BOUNDARY
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": ["start", "stop"]},
                    "name": {"type": "string", "maxLength": 80, "pattern": "^[A-Za-z0-9_-]+$",
                             "description": "Required to start; names the recording's folder."},
                },
                "required": ["command"], "additionalProperties": False,
            },
        },
    ]


def call(hub: str, name: str, arguments: dict) -> dict:
    """Proxy one computer tool call to this Hub's own route.

    The tool adds nothing the route does not do: the gate, the allow-list and
    the refusal codes are the Hub's, so the CLI reads exactly what an HTTP
    caller would.
    """

    path = ROUTES.get(name)
    if path is None:
        raise HubFailure(422, "CHAT_TOOL_UNAVAILABLE", "This action is not exposed to the chat.")
    if not isinstance(arguments, dict):
        raise HubFailure(422, "COMPUTER_ACTION_INVALID", "Tool arguments must be an object.")
    return chat._request_json(hub, path, "POST", dict(arguments))
