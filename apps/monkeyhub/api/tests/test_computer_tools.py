"""Computer use reached through the Hub: the policy gate, the routes, the tools.

No desktop is touched here. The Hub's one ComputerService is given a runtime
factory that answers with a plain object, so what is actually under test is the
Hub's own half: when it refuses, which status each refusal becomes, what it
hands the runtime, when it builds a new one, and what the connected CLI is told
it may call.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_monkeyhub_lifecycle import LocalHubCase, ROOT
from test_chat import _tools_of

from monkeycontrol.contract import ContractError, validate_action
from monkeycontrol.host import HostError
from monkeycontrol.record import NAME as RECORDING_NAME
from monkeycontrol.runtime import RuntimeRefusal
from monkeycontrol.trace import ResolvedTarget, WindowInfo, build_receipt

from monkeyhub_api import chat
from monkeyhub_api.chat_trace import HubTurnObserver
from monkeyhub_api.computer_tools import POLICY_PATH, ComputerService, read_policy, tool_definitions
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import HubFailure
from monkeymonitor.store import UsageLog

CLICK = {
    "intent": "press save",
    "application": "notepad",
    "target": {"controlType": "Button", "name": "Save"},
    "action": {"type": "click"},
}
NOTEPAD = WindowInfo(4242, "Untitled - Notepad", 91, "notepad", (0, 0, 800, 600))
SAVE = ResolvedTarget(
    "Button", "Save", "1", "Button", (100, 200, 200, 240), "42.7.1", "windows-uia"
)


def receipt(*, status="succeeded", refusal=None, verification=None):
    """One ComputerActionReceipt@1, built by the package that owns its shape."""

    return build_receipt(
        action=validate_action(CLICK),
        step_id="s-0001",
        mode="fast",
        window=NOTEPAD,
        target=SAVE,
        fallback=False,
        point=(150, 220),
        started_at="2026-09-18T09:30:00Z",
        duration_ms=42,
        verification=verification,
        status=status,
        refusal=refusal,
        screenshots={},
    )


def hub_failure(status: int, payload: dict) -> HubFailure:
    """What chat._request_json raises for a non-2xx answer, mirrored here.

    The real one keeps the route's own code when the body carries one that
    looks like a code, and falls back to CHAT_TOOL_FAILED. A fake that simply
    handed the body back would hide the thing these two tests are about.
    """

    code = payload.get("code")
    if not (isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code)):
        code = "CHAT_TOOL_FAILED"
    return HubFailure(status, code, str(payload.get("detail", ""))[:1200])


class FakeRuntime:
    """Every ComputerUseRuntime method the Hub calls, and nothing else."""

    def __init__(self, trace_dir, policy, *, answer=None, raises=None):
        self.trace_dir, self.policy = trace_dir, policy
        self.answer = answer if answer is not None else receipt()
        self.raises = raises
        self.calls = []
        self.closed = False

    def _answer(self, *call):
        self.calls.append(call)
        if self.raises is not None:
            raise self.raises
        return self.answer

    def inspect(self, application, window=None, *, depth=6):
        self.calls.append(("inspect", application, window, depth))
        if self.raises is not None:
            raise self.raises
        return {
            "application": application,
            "window": {"title": "Untitled - Notepad"},
            "windows": [],
            "nodes": [{"controlType": "Button", "name": "Save"}],
            "truncated": False,
        }

    def execute(self, payload, *, mode=None):
        return self._answer("execute", payload, mode)

    def record_start(self, name, *, interval_ms=250, region="window-monitor"):
        self.calls.append(("record_start", name, interval_ms, region))
        if self.raises is not None:
            raise self.raises
        return {"name": name, "interval_ms": interval_ms, "region": region}

    def record_stop(self):
        self.calls.append(("record_stop",))
        if self.raises is not None:
            raise self.raises
        return {"name": "demo", "frames": 3}

    def close(self):
        self.closed = True


class ComputerHubCase(LocalHubCase):
    """The Hub fixture without its managed children: no route here needs one."""

    @contextmanager
    def hub(self):
        app = create_app(
            HubSettings(runtime_root=self.runtime, port=self.hub_port), source_root=ROOT
        )
        with patch.object(app.state.applications, "start"):
            with TestClient(app, base_url=self.base_url) as client:
                yield client

    def enable(self, **changes):
        """Write the policy file the way the machine's owner would."""

        path = self.runtime / POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "allowedProcesses": ["notepad"],
                    "mode": "fast",
                    **changes,
                }
            ),
            encoding="utf-8",
        )
        return path

    def transport(self, client):
        """chat._request_json over this TestClient, refusing the same way."""

        def request(base, path, method="GET", body=None, timeout=180, **kwargs):
            answer = client.request(method, path, json=body)
            if answer.status_code >= 400:
                raise hub_failure(answer.status_code, answer.json())
            return answer.json()

        return request

    def inject(self, client, **fake):
        """Give this Hub a ComputerService whose runtime is the fake above."""

        built = []

        def factory(trace_dir, policy):
            runtime = FakeRuntime(trace_dir, policy, **fake)
            built.append(runtime)
            return runtime

        client.app.state.computer = ComputerService(
            self.runtime, runtime_factory=factory
        )
        return built


class ComputerRouteTests(ComputerHubCase):
    def test_disabled_policy_refuses_with_location(self):
        with self.hub() as client:
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 403, response.text)
        body = response.json()
        self.assertEqual(body["code"], "COMPUTER_USE_NOT_ENABLED")
        self.assertIn("diagnostics/monkeycontrol/policy.json", body["detail"])

    def test_an_unreadable_policy_reads_as_disabled(self):
        path = self.runtime / POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertFalse(read_policy(self.runtime).enabled)
        with self.hub() as client:
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_USE_NOT_ENABLED")

    def test_enabled_policy_runs_action_through_injected_runtime(self):
        self.enable()
        expected = receipt()
        with self.hub() as client:
            built = self.inject(client, answer=expected)
            response = client.post(
                "/api/computer/actions", json={"action": CLICK, "mode": "demo"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        [runtime] = built
        self.assertEqual(runtime.policy.allowed_processes, ("notepad",))
        self.assertEqual(runtime.policy.mode, "fast")
        self.assertEqual(runtime.trace_dir, self.runtime / "diagnostics/monkeycontrol")
        self.assertEqual(runtime.calls, [("execute", CLICK, "demo")])

    def test_a_refused_receipt_is_the_answer_rather_than_an_error(self):
        # The agent has to read the refusal to do anything about it, so a
        # receipt that says "refused" is a 200 body like any other receipt.
        self.enable()
        refused = receipt(
            status="refused",
            refusal={"code": "TARGET_UNRESOLVED", "message": "no Button named Save"},
        )
        with self.hub() as client:
            self.inject(client, answer=refused)
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["refusal"]["code"], "TARGET_UNRESOLVED")

    def test_invalid_action_is_422(self):
        self.enable()
        with self.hub() as client:
            self.inject(client, raises=ContractError("action.type must be one of"))
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["code"], "COMPUTER_ACTION_INVALID")
        self.assertIn("action.type", body["detail"])

    def test_recording_state_refusal_is_409(self):
        self.enable()
        with self.hub() as client:
            self.inject(
                client, raises=RuntimeRefusal("RECORDING_ACTIVE", "'demo' is recording")
            )
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["code"], "COMPUTER_ACTION_REFUSED")
        self.assertIn("RECORDING_ACTIVE", body["detail"])
        self.assertIn("'demo' is recording", body["detail"])

    def test_a_recording_starts_and_stops_through_the_same_runtime(self):
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            started = client.post(
                "/api/computer/recordings", json={"command": "start", "name": "demo-01"}
            )
            stopped = client.post("/api/computer/recordings", json={"command": "stop"})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["name"], "demo-01")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["frames"], 3)
        [runtime] = built
        self.assertEqual(
            runtime.calls, [("record_start", "demo-01", 250, "window-monitor"), ("record_stop",)]
        )

    def test_a_recording_without_a_name_is_refused_before_the_runtime(self):
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            response = client.post("/api/computer/recordings", json={"command": "start"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_ACTION_INVALID")
        self.assertEqual(built, [], "a nameless recording never reaches a runtime")

    def test_a_recording_name_the_package_refuses_is_422_not_a_crash(self):
        # This API's pattern is the wider of the two: monkeycontrol's recorder
        # wants a letter or a digit first. The difference has to be an answer.
        self.assertIsNone(RECORDING_NAME.match("-demo"))
        self.enable()
        with self.hub() as client:
            self.inject(client, raises=ValueError(
                "'-demo' must be a plain recording name: letters, digits, - and _"
            ))
            response = client.post(
                "/api/computer/recordings", json={"command": "start", "name": "-demo"}
            )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_ACTION_INVALID")
        self.assertIn("plain recording name", response.json()["detail"])

    def test_inspect_reads_an_allowed_application_and_refuses_another(self):
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            allowed = client.post(
                "/api/computer/inspect", json={"application": "notepad", "depth": 3}
            )
            other = client.post("/api/computer/inspect", json={"application": "chrome"})
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["nodes"][0]["name"], "Save")
        self.assertEqual(built[0].calls, [("inspect", "notepad", None, 3)])
        self.assertEqual(other.status_code, 409, other.text)
        self.assertEqual(other.json()["code"], "COMPUTER_ACTION_REFUSED")
        self.assertIn("APP_NOT_ALLOWED", other.json()["detail"])

    def test_an_invalid_window_regex_is_422_rather_than_a_500(self):
        # window is a Python regex compiled deep in the provider, where re.error
        # is not a ValueError: without the model's own check this route answered
        # an internal error to one unbalanced bracket.
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            response = client.post(
                "/api/computer/inspect", json={"application": "notepad", "window": "("}
            )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_ACTION_INVALID")
        self.assertEqual(built, [], "a request that is not one never reaches a runtime")

    def test_a_regex_error_from_below_is_422_and_not_an_internal_error(self):
        # The model guards the one field this API declares; anything further
        # down that still raises re.error is the caller's mistake too.
        self.enable()
        with self.hub() as client:
            self.inject(client, raises=re.error("missing ), unterminated subpattern"))
            response = client.post("/api/computer/inspect", json={"application": "notepad"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_ACTION_INVALID")
        self.assertIn("unterminated subpattern", response.json()["detail"])

    def test_a_backend_that_will_not_start_is_409_not_a_crash(self):
        # A missing host script, no powershell.exe, a host that died mid-protocol:
        # inspect answers none of those with a receipt, and both documents
        # promise the refusal code rather than an internal error.
        self.enable()
        with self.hub() as client:
            self.inject(client, raises=HostError(
                "BACKEND_UNAVAILABLE", "powershell.exe was not found on this machine"
            ))
            response = client.post("/api/computer/inspect", json={"application": "notepad"})
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["code"], "COMPUTER_ACTION_REFUSED")
        self.assertIn("BACKEND_UNAVAILABLE", body["detail"])
        self.assertIn("powershell.exe", body["detail"])

    def test_a_host_that_died_mid_action_is_409_too(self):
        self.enable()
        with self.hub() as client:
            self.inject(client, raises=HostError("HOST_ERROR", "the execution host stopped"))
            response = client.post("/api/computer/recordings", json={"command": "stop"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("HOST_ERROR", response.json()["detail"])

    def test_a_policy_file_that_is_there_and_invalid_says_so(self):
        # Fail closed either way, but "there is no policy file" and "your policy
        # file has a typo in it" are two different things to be told.
        path = self.runtime / POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"enabled": True, "allowedProcesses": ["notepad"], "allow_all": True}),
            encoding="utf-8",
        )
        with self.hub() as client:
            response = client.post("/api/computer/actions", json={"action": CLICK})
        self.assertEqual(response.status_code, 403, response.text)
        detail = response.json()["detail"]
        self.assertEqual(response.json()["code"], "COMPUTER_USE_NOT_ENABLED")
        self.assertIn(POLICY_PATH, detail)
        self.assertIn("invalid", detail)
        self.assertIn("allow_all", detail)
        self.assertFalse(read_policy(self.runtime).enabled)

    def test_a_missing_policy_file_is_not_reported_as_a_broken_one(self):
        with self.hub() as client:
            detail = client.post(
                "/api/computer/actions", json={"action": CLICK}
            ).json()["detail"]
        self.assertNotIn("invalid", detail)

    def test_a_changed_policy_replaces_the_runtime_it_built(self):
        # Enabling computer use, or widening it, must not need a Hub restart:
        # the policy is read again on every request and the runtime that was
        # built from the old one is closed rather than left holding two hosts.
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            client.post("/api/computer/actions", json={"action": CLICK})
            self.enable(allowedProcesses=["notepad", "explorer"], mode="demo")
            client.post("/api/computer/actions", json={"action": CLICK})
            self.assertEqual(len(built), 2)
            self.assertTrue(built[0].closed)
            self.assertEqual(built[1].policy.allowed_processes, ("notepad", "explorer"))
            self.assertEqual(built[1].policy.mode, "demo")
            client.post("/api/computer/actions", json={"action": CLICK})
            self.assertEqual(len(built), 2, "an unchanged policy reuses its runtime")

    def test_the_hub_closes_the_runtime_it_owns_when_it_shuts_down(self):
        self.enable()
        with self.hub() as client:
            built = self.inject(client)
            client.post("/api/computer/actions", json={"action": CLICK})
        self.assertTrue(built[0].closed)

    def test_a_malformed_request_is_one_refusal_the_agent_can_read(self):
        self.enable()
        with self.hub() as client:
            self.inject(client)
            response = client.post("/api/computer/actions", json={"nope": 1})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPUTER_ACTION_INVALID")


class ComputerToolTests(ComputerHubCase):
    def test_mcp_tools_list_includes_computer_tools(self):
        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        for name in ("computer_inspect", "computer_action", "computer_record"):
            self.assertIn(name, tools, name)
            self.assertIs(tools[name]["inputSchema"]["additionalProperties"], False)
            self.assertTrue(tools[name]["description"].strip())
        self.assertEqual(
            {tool["name"] for tool in tool_definitions()},
            {"computer_inspect", "computer_action", "computer_record"},
        )

    def test_every_tool_says_where_its_permission_comes_from(self):
        for tool in tool_definitions():
            said = tool["description"]
            self.assertIn("policy", said, tool["name"])
            self.assertIn("project files", said, tool["name"])

    def test_claude_allowlist_follows_policy(self):
        names = ("computer_inspect", "computer_action", "computer_record")
        without = chat._claude_approved(self.runtime)
        for name in names:
            self.assertNotIn(f"mcp__monkeyhub__{name}", without, name)
        self.assertEqual(without, chat._CLAUDE_APPROVED)
        self.enable()
        approved = chat._claude_approved(self.runtime)
        for name in names:
            self.assertIn(f"mcp__monkeyhub__{name}", approved, name)
        self.assertEqual(approved[: len(chat._CLAUDE_APPROVED)], chat._CLAUDE_APPROVED)

    def test_a_disabled_machine_refuses_the_tool_call_by_its_own_code(self):
        # No policy file: the tool adds nothing, so what the conversation sees
        # has to be the route's refusal, carried across as a HubFailure rather
        # than flattened into a body that reads like an answer.
        with self.hub() as client:
            with patch.object(chat, "_request_json") as request:
                request.side_effect = self.transport(client)
                with self.assertRaises(HubFailure) as refused:
                    chat.call_tool(
                        self.base_url,
                        "00000000-0000-4000-8000-000000000000",
                        "computer_inspect",
                        {"application": "notepad"},
                    )
        self.assertEqual(refused.exception.status, 403)
        self.assertEqual(refused.exception.error.code, "COMPUTER_USE_NOT_ENABLED")
        self.assertIn(POLICY_PATH, refused.exception.error.detail)
        self.assertEqual(request.call_args[0][1], "/api/computer/inspect")

    def test_a_computer_tool_call_reaches_the_hub_route(self):
        self.enable()
        expected = receipt()
        with self.hub() as client:
            self.inject(client, answer=expected)
            with patch.object(chat, "_request_json") as request:
                request.side_effect = self.transport(client)
                answer = chat.call_tool(
                    self.base_url,
                    "00000000-0000-4000-8000-000000000000",
                    "computer_action",
                    {"action": CLICK},
                )
        self.assertEqual(answer, expected)
        self.assertEqual(request.call_args[0][1], "/api/computer/actions")
        self.assertEqual(request.call_args[0][2], "POST")

    def test_activity_line_names_target(self):
        line, candidate, failed = chat._tool_activity(
            {
                "server": "monkeyhub",
                "tool": "computer_action",
                "arguments": {"action": CLICK},
                "status": "completed",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                receipt(
                                    verification={
                                        "expect": "window",
                                        "status": "passed",
                                        "detail": "'Save As' matched",
                                        "duration_ms": 12,
                                    }
                                )
                            ),
                        }
                    ]
                },
            }
        )
        head = line.splitlines()[0]
        self.assertIn("CLICK", head)
        self.assertIn("Save", head)
        self.assertIn("✓", head)
        self.assertIsNone(candidate)
        self.assertFalse(failed)

    def test_a_refused_action_says_so_on_one_line(self):
        line, _, _ = chat._tool_activity(
            {
                "server": "monkeyhub",
                "tool": "computer_action",
                "arguments": {},
                "status": "completed",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                receipt(
                                    status="refused",
                                    refusal={
                                        "code": "APP_NOT_ALLOWED",
                                        "message": "'chrome' is not allowed",
                                    },
                                )
                            ),
                        }
                    ]
                },
            }
        )
        head = line.splitlines()[0]
        self.assertTrue(head.startswith("REFUSED APP_NOT_ALLOWED"), head)
        self.assertIn("press save", head)

    def test_the_diagnostic_journal_names_the_computer_tools_it_may_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = HubTurnObserver(
                UsageLog(Path(temporary)), "turn", "project", "claude", "model"
            )
            trace.tool("a", "computer_action", {"action": CLICK}, running=True)
            trace.tool("b", "rm -rf private", {}, running=True)
            details = trace.spans["hub:tool:turn:a"]["details"]
            self.assertEqual(details["tool_name"], "computer_action")
            self.assertEqual(details["request_kind"], "computer")
            self.assertEqual(trace.spans["hub:tool:turn:b"]["details"]["tool_name"], "agent_tool")


if __name__ == "__main__":
    unittest.main()
