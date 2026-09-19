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
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_monkeyhub_lifecycle import LocalHubCase, ROOT
from test_chat import _tools_of

from monkeycontrol.contract import ContractError, validate_action
from monkeycontrol.runtime import RuntimeRefusal
from monkeycontrol.trace import ResolvedTarget, WindowInfo, build_receipt

from monkeyhub_api import chat
from monkeyhub_api.chat_trace import HubTurnObserver
from monkeyhub_api.computer_tools import POLICY_PATH, ComputerService, read_policy, tool_definitions
from monkeyhub_api.main import HubSettings, create_app
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

    def test_a_computer_tool_call_reaches_the_hub_route(self):
        self.enable()
        expected = receipt()
        with self.hub() as client:
            self.inject(client, answer=expected)
            with patch.object(chat, "_request_json") as request:
                request.side_effect = lambda base, path, method="GET", body=None, **kw: (
                    client.post(path, json=body).json()
                )
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
