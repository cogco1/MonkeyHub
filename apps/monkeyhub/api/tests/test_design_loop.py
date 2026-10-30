"""The Agent's design-loop tools (#185): one change at a time, a repair limit per message, a turn that ends closed.

The tools go through studio_request, so here studio_request is a stand-in that answers as POST
/api/revisions does; the Stop hook runs as the CLI runs it, as a separate process reading the loop state.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient

from monkeycad.backends.occt.kernel import occt_available
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from test_chat import _tools_of
from test_monkeyhub_lifecycle import project_fixture
from monkeyhub_api.chat import design_loop, mcp_server, preparation, tool_calls, transport
from monkeyhub_api.models import HubFailure

HUB = "http://127.0.0.1:8700"
CHANGE = {"sourceRunId": "run-1", "stateDigest": "d1", "kind": "construction",
          "proposal": {"script": "x = 1"}, "summary": "lower the stair"}


class SubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        design_loop._loops.clear()
        self.sent: list[dict] = []
        self.answers: list[dict] = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "loop.json"
        self.addCleanup(patch.stopall)
        patch.object(design_loop, "_message_id", return_value="m-1").start()
        patch.object(tool_calls, "call_tool", side_effect=self.studio).start()
        patch.dict("os.environ", {design_loop.STATE_ENV: str(self.state)}).start()

    def studio(self, hub, chat_id, name, arguments):
        self.sent.append(arguments)
        return self.answers.pop(0)

    def submit(self, **change):
        return design_loop.call(HUB, "chat-1", "design_submit", {**CHANGE, **change})

    def test_a_change_goes_through_the_one_call_and_its_outcome_is_kept(self) -> None:
        self.answers = [{"outcome": "landed", "code": "LANDED", "candidateId": "c-1"}]
        self.assertEqual(self.submit()["outcome"], "landed")
        self.assertEqual((self.sent[0]["method"], self.sent[0]["path"]), ("POST", "/api/revisions"))
        self.assertNotIn("supersedes", self.sent[0]["body"])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual((state["messageId"], state["submits"][0]["outcome"]), ("m-1", "landed"))

    def test_a_body_said_another_way_is_sent_as_its_route_takes_it(self) -> None:
        self.answers = [{"outcome": "landed", "code": "LANDED", "candidateId": "c-1"}]
        answer = self.submit(proposal={"script": "pass", "parameters": {"door_width": 1.4, "sill": {"value": 0.9,
                                                                                                      "unit": "m"}}},
                             keep=["entity:lintel", "obligation:k3", "decision:d-1"])
        body = self.sent[0]["body"]
        self.assertEqual(body["proposal"]["parameters"], [{"key": "door_width", "value": 1.4},
                                                          {"key": "sill", "value": 0.9, "unit": "m"}])
        self.assertEqual(body["keep"], ["entity:lintel"], "the design's own conditions and decisions need no keep")
        self.assertEqual(len(answer["notes"]), 2)

    def test_a_refused_body_comes_back_with_its_kinds_fields_and_an_example(self) -> None:
        self.answers = [{"outcome": "problems", "code": "PROPOSAL_REFUSED", "problems": [
            {"source": "proposal", "kind": "PROPOSAL_INVALID", "detail": "head: Field required"}]}]
        answer = self.submit(kind="hosted-opening", proposal={"host": "w", "kind": "window", "height": 1.2})
        self.assertEqual(answer["expected"]["kind"], "hosted-opening")
        self.assertIn("head", answer["expected"]["fields"])
        self.assertEqual(set(answer["expected"]["example"]), {"host", "kind", "along", "width", "sill", "head"})
        self.assertNotIn("notes", answer)

    def test_a_corrected_change_replaces_the_attempts_on_the_same_base(self) -> None:
        self.answers = [{"outcome": "problems", "code": "CHECKS_FAILED", "candidateId": "c-1"},
                        {"outcome": "landed", "code": "LANDED", "candidateId": "c-2"}]
        self.assertEqual(self.submit()["repairsLeft"], design_loop.REPAIR_BUDGET - 1)
        self.submit()
        self.assertEqual(self.sent[1]["body"]["supersedes"], ["c-1"])

    def test_after_the_repair_budget_the_agent_asks_instead(self) -> None:
        self.answers = [{"outcome": "problems", "code": "CHECKS_FAILED", "candidateId": f"c-{n}"}
                        for n in range(design_loop.REPAIR_BUDGET)]
        for _ in range(design_loop.REPAIR_BUDGET):
            self.submit()
        with self.assertRaises(HubFailure) as refused:
            self.submit()
        self.assertEqual(refused.exception.error.code, "REPAIR_BUDGET_EXHAUSTED")
        self.assertEqual(len(self.sent), design_loop.REPAIR_BUDGET, "the refused change was not sent")
        with patch.object(design_loop, "_message_id", return_value="m-2"):
            self.answers = [{"outcome": "landed", "code": "LANDED", "candidateId": "c-9"}]
            self.assertEqual(self.submit()["outcome"], "landed", "the next message starts a new budget")

    def test_a_question_also_waits_in_the_architects_queue(self) -> None:
        from monkeyhub_api.chat import transport

        posted = []
        with (patch.object(transport, "_request_json", side_effect=lambda *a, **k: posted.append((*a, k)) or {}),
              patch.dict("os.environ", {"MONKEYHUB_PRESENTATION_TOKEN": "token-1"})):
            answer = design_loop.call(HUB, "chat-1", "design_ask", {"question": "西门廊保留柱距吗", "reason": "和楼梯冲突"})
        self.assertEqual(answer["recorded"], "asked")
        self.assertEqual(posted[0][1:3], ("/api/attention/questions", "POST"))
        self.assertEqual(posted[0][3], {"sessionId": "chat-1", "messageId": "m-1", "question": "西门廊保留柱距吗",
                                        "reason": "和楼梯冲突", "refs": []})
        self.assertEqual(posted[0][4]["headers"], {"Authorization": "Bearer token-1"}, "the conversation's own token")
        with patch.object(transport, "_request_json", side_effect=OSError("Hub is restarting")):
            self.assertEqual(design_loop.call(HUB, "chat-1", "design_ask", {"question": "q", "reason": "r"})["recorded"],
                             "asked", "the reply still carries the question")

    def test_the_budget_is_configurable(self) -> None:
        with patch.dict("os.environ", {design_loop.REPAIR_BUDGET_ENV: "1"}):
            self.answers = [{"outcome": "problems", "code": "CHECKS_FAILED", "candidateId": "c-1"}]
            self.submit()
            with self.assertRaises(HubFailure):
                self.submit()

    def test_reading_asks_studio_request_for_the_context_and_the_model(self) -> None:
        self.answers = [{"working": {}}, {"entities": []}]
        answer = design_loop.call(HUB, "chat-1", "design_read", {})
        self.assertEqual(set(answer), {"context", "model"})
        self.assertEqual([row["path"] for row in self.sent], ["/api/revisions/context", "/api/construction/model"])
        with self.assertRaises(HubFailure):
            design_loop.call(HUB, "chat-1", "design_read", {"part": "everything"})
        with self.assertRaises(HubFailure):
            design_loop.call(HUB, "chat-1", "design_submit", {**CHANGE, "rawLanguage": "用户说可以"})


class StopHookTests(unittest.TestCase):
    def test_the_turn_ends_once_closed(self) -> None:
        self.assertEqual(design_loop.stop_decision(None), (True, None), "nothing was started")
        landed = {"messageId": "m", "submits": [{"outcome": "landed", "code": "LANDED"}]}
        self.assertTrue(design_loop.stop_decision(landed)[0])
        failed = {"messageId": "m", "submits": [{"outcome": "problems", "code": "CHECKS_FAILED"}]}
        allowed, reason = design_loop.stop_decision(failed)
        self.assertFalse(allowed)
        self.assertIn("design_ask", reason)
        self.assertTrue(design_loop.stop_decision({**failed, "asked": {"question": "?"}})[0])
        self.assertTrue(design_loop.stop_decision({**failed, "limitation": {"capability": "x"}})[0])
        question = {"messageId": "m", "submits": [{"outcome": "needs_question", "code": "KEEP_CONFLICT"}]}
        self.assertIn("Call design_ask", design_loop.stop_decision(question)[1])
        self.assertTrue(design_loop.stop_decision(failed, design_loop.STOP_BLOCKS)[0], "held open only so often")

    def test_the_hook_runs_as_the_cli_runs_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "loop.json"
            state.write_text(json.dumps({"messageId": "m", "submits": [{"outcome": "problems", "code": "CHECKS_FAILED"}]}),
                             encoding="utf-8")
            hook = Path(design_loop.__file__)
            outputs = []
            for _ in range(design_loop.STOP_BLOCKS + 1):
                done = subprocess.run([sys.executable, str(hook), "stop-hook", "--state", str(state)], input="{}",
                                      capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                outputs.append(done.stdout.strip())
            self.assertEqual([json.loads(row)["decision"] for row in outputs[:-1]], ["block"] * design_loop.STOP_BLOCKS)
            self.assertEqual(outputs[-1], "", "after the last hold the turn may end")
            counted = json.loads((Path(folder) / "loop.json.stop.json").read_text(encoding="utf-8"))
            self.assertTrue(counted["unclosed"])

    def test_the_profile_names_the_hook_the_tools_and_the_environment(self) -> None:
        profile = design_loop.cli_profile(Path("D:/runs/hop-01/loop.json"))
        [stop] = profile["settings"]["hooks"]["Stop"]
        self.assertIn("stop-hook --state", stop["hooks"][0]["command"])
        self.assertIn("design_loop.py", stop["hooks"][0]["command"])
        self.assertEqual(profile["env"]["MONKEYHUB_DESIGN_LOOP"], "enforced")
        self.assertEqual(profile["mcp_args"], ["--design-loop"])
        self.assertEqual({name.rsplit("__", 1)[1] for name in profile["allowed_tools"]}, set(design_loop.TOOLS))
        self.assertLess(len(profile["system_prompt"]), 1200)
        self.assertEqual((profile["disallowed_tools"], profile["permission_mode"]), (["Bash", "PowerShell"], "dontAsk"))
        self.assertNotIn("mcp_config", profile)
        bound = design_loop.cli_profile(Path("D:/runs/hop-01/loop.json"), request_file=Path("D:/runs/hop-01/request.md"),
                                        hub_url="http://127.0.0.1:8700", project_dir=Path("D:/runs/hop-01/p"),
                                        session_id="hop-01")
        [entry] = bound["mcp_config"]["mcpServers"].values()
        self.assertEqual(entry["args"][-1], "--design-loop")
        self.assertIn("--source-session-id", entry["args"])
        self.assertEqual(entry["env"]["MONKEYHUB_DESIGN_REQUEST_FILE"], str(Path("D:/runs/hop-01/request.md")))



class ToolListTests(unittest.TestCase):
    def test_a_design_loop_connection_lists_only_the_four_tools(self) -> None:
        import io
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})]

        class _Stream(io.StringIO):
            def reconfigure(self, **kwargs):
                return None

        reader, writer = _Stream(chr(10).join(lines) + chr(10)), _Stream()
        with patch.object(mcp_server.sys, "stdin", reader), patch.object(mcp_server.sys, "stdout", writer):
            mcp_server._mcp("http://127.0.0.1:1", "00000000-0000-4000-8000-000000000000", design_loop_only=True)
        tools = json.loads(writer.getvalue().strip().splitlines()[-1])["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], list(design_loop.TOOLS))
        self.assertNotIn("design_submit", {tool["name"] for tool in _tools_of(mcp_server)},
                         "an ordinary connection keeps its own tools")


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class EndToEndTests(unittest.TestCase):
    """The tools in front of a real Runtime that builds with OCCT, behind a Hub that only relays."""

    def setUp(self) -> None:
        design_loop._loops.clear()
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub design loop ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fixture = project_fixture()
        fixture.make_project(root)
        self.project_id = fixture.PROJECT_ID
        project = root / self.project_id
        app = create_app(StudioSettings(project_dir=project, cad_export="occt", design_loop="enforced"))
        app.state.managed_instance_id = "hub-test"
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.hub, self.base = "http://127.0.0.1:8700", "http://127.0.0.1:8701"
        self.session = {"id": str(uuid4()), "projectId": self.project_id, "projectDir": str(project.resolve()),
                        "status": "running", "messages": [{"id": "m-1", "role": "user", "status": "complete",
                                                           "content": "把门廊基座加高到 2.4 米"}]}
        self.addCleanup(patch.stopall)
        patch.object(preparation, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(transport, "_request_json", side_effect=self.request).start()
        # As cli_profile starts such a turn: its loop state, and the design loop it was started with.
        patch.dict("os.environ", {design_loop.STATE_ENV: str(root / "loop.json"), "MONKEYHUB_DESIGN_LOOP": "enforced"}).start()
        self.state = root / "loop.json"

    def request(self, base, path, method="GET", body=None, **kwargs):
        if base == self.hub and path.startswith("/api/chat/sessions/"):
            return deepcopy(self.session)
        if base == self.hub and path == "/api/settings/apps":
            return {"designLoop": "enforced"}
        if base == self.hub:
            path = path.split("/studio", 1)[1]
        response = self.client.request(method, path, json=body)
        if response.is_error:
            failure = response.json()
            raise HubFailure(response.status_code, failure["code"], failure["detail"])
        return response.json()

    def tool(self, name, **arguments):
        return tool_calls.call_tool(self.hub, self.session["id"], name, arguments)

    def test_read_then_submit_lands_the_change_on_the_users_words(self) -> None:
        read = self.tool("design_read")
        working = read["context"]["working"]
        self.assertIn("entities", json.dumps(read["model"]))
        answer = self.tool("design_submit", sourceRunId=working["runId"], stateDigest=working["stateDigest"],
                           kind="proposal", summary="门廊基座加高到 2.4 米",
                           proposal={"utterance": "set height to 2.4", "targetComponentId": "portico",
                                     "elementId": "portico-base"})
        self.assertEqual(answer["outcome"], "landed", answer)
        head = self.client.get("/api/working-source").json()["head"]
        self.assertEqual(head["runId"], answer["candidateId"])
        [admission] = self.client.get("/api/admissions").json()["admissions"]
        self.assertEqual((admission["messageSource"]["messageId"], admission["rawLanguage"]),
                         ("m-1", "把门廊基座加高到 2.4 米"))
        self.assertEqual(admission["actor"]["origin"], "hub-agent")
        self.assertTrue(design_loop.stop_decision(json.loads(self.state.read_text(encoding="utf-8")))[0])
        after = self.tool("design_read", part="context")["context"]
        self.assertEqual(after["lastChange"]["words"], "把门廊基座加高到 2.4 米")

    def test_the_agents_own_continue_is_refused_while_enforced(self) -> None:
        with self.assertRaises(HubFailure) as refused:
            self.tool("studio_request", method="PUT", path="/api/working-draft",
                      body={"runId": "run-001", "baseRevisionSha256": None})
        self.assertEqual(refused.exception.error.code, "DESIGN_LOOP_REQUIRED")


if __name__ == "__main__":
    unittest.main()
