"""The designLoop setting (#185): what the Hub offers the Agent, and what it hands the Runtime.

While it is enforced the chat offers POST /api/revisions, binds the user's message and
words to it as it does for a Continue, and gives the guide that names that one call.
While it is off nothing the Agent sees changes: the call is not offered and the guide
is the step-by-step one, word for word.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
import json
from types import SimpleNamespace

from monkeyhub_api.chat import preparation
from monkeyhub_api.runtime.operations import OperationManager
from monkeyhub_api.runtime.worker_http import HttpResult

from monkeyhub_api.chat import guides, judgments, studio_tool, tool_calls, transport
from monkeyhub_api.models import HubFailure
from monkeyhub_api.settings.models import DESIGN_LOOP_DEFAULT, ApplicationSettingsDto

HUB = "http://127.0.0.1:8700"


class DesignLoopSettingTests(unittest.TestCase):
    def test_the_setting_is_enforced_or_off(self) -> None:
        self.assertEqual(ApplicationSettingsDto().design_loop, DESIGN_LOOP_DEFAULT)
        self.assertEqual(ApplicationSettingsDto.model_validate({"designLoop": "off"}).design_loop, "off")
        with self.assertRaises(ValidationError):
            ApplicationSettingsDto.model_validate({"designLoop": "sometimes"})

    def test_the_runtime_is_launched_with_it(self) -> None:
        source = (Path(studio_tool.__file__).parents[1] / "runtime" / "applications.py").read_text(encoding="utf-8")
        self.assertIn('"ARCHFLOW_STUDIO_DESIGN_LOOP": settings.design_loop', source)

    def test_the_conversation_is_offered_what_it_was_started_with(self) -> None:
        for value, offered in (("enforced", True), ("off", False), ("", False)):
            with patch.dict("os.environ", {studio_tool.DESIGN_LOOP_ENV: value}):
                self.assertIs(studio_tool.design_loop_offered(), offered)
        source = (Path(studio_tool.__file__).parent / "store.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("environment[studio_tool.DESIGN_LOOP_ENV] = read_application_settings("), 2,
                         "both kinds of chat turn are started with the Hub's setting")

    def test_an_unreadable_setting_reads_as_the_default(self) -> None:
        with patch.object(transport, "_request_json", side_effect=OSError("no Hub")):
            self.assertEqual(studio_tool.design_loop_enforced(HUB), DESIGN_LOOP_DEFAULT == "enforced")
        for value, enforced in (("enforced", True), ("off", False)):
            with patch.object(transport, "_request_json", return_value={"designLoop": value}):
                self.assertIs(studio_tool.design_loop_enforced(HUB), enforced)


class OfferedCallTests(unittest.TestCase):
    DOCUMENT = {"paths": {"/api/revisions": {"post": {"summary": "One change carried onto the working design"}},
                          "/api/admissions": {"post": {"summary": "Admit"}}}}

    def test_the_call_is_offered_only_while_enforced(self) -> None:
        self.assertFalse(studio_tool._schema_allowed("POST", "/api/revisions"))
        self.assertTrue(studio_tool._schema_allowed("POST", "/api/revisions", enforced=True))
        offered = {row["path"] for row in studio_tool._available_actions(self.DOCUMENT)}
        self.assertEqual(offered, {"/api/admissions"})
        offered = {row["path"] for row in studio_tool._available_actions(self.DOCUMENT, enforced=True)}
        self.assertEqual(offered, {"/api/admissions", "/api/revisions"})

    def test_while_off_the_chat_refuses_the_call_before_any_runtime_is_asked(self) -> None:
        asked = []
        with patch.object(transport, "_request_json", side_effect=lambda *a, **k: asked.append(a) or {"designLoop": "off"}):
            with self.assertRaises(HubFailure) as refused:
                tool_calls.call_tool(HUB, str(uuid4()), "studio_request", {
                    "method": "POST", "path": "/api/revisions", "body": {"kind": "construction", "proposal": {}}})
        self.assertEqual(refused.exception.error.code, "CHAT_TOOL_UNAVAILABLE")
        self.assertIn("design loop is enforced", refused.exception.error.detail)
        self.assertEqual([call[1] for call in asked], ["/api/settings/apps"])

    def test_the_change_inside_a_revision_is_held_to_its_own_routes_narrowing(self) -> None:
        geometry = {"kind": "proposal", "proposal": {"semanticEdit": {"entities": [{"schema": "Element@1", "fields": {}}]}}}
        self.assertEqual(studio_tool._revision_refusal(geometry).error.code, "CHAT_TOOL_INVALID")
        level = {"kind": "elevation", "proposal": {"action": "set-base", "elementId": "wall"}}
        self.assertEqual(studio_tool._revision_refusal(level).error.code, "CHAT_TOOL_INVALID")
        self.assertIsNone(studio_tool._revision_refusal({"kind": "elevation", "proposal": {"action": "set-datum"}}))
        self.assertIsNone(studio_tool._revision_refusal({"kind": "construction", "proposal": {"script": "x = 1"}}))


class BoundWordsTests(unittest.TestCase):
    def session(self, content: str = "楼梯缓一点，别动楼板") -> tuple[str, dict]:
        chat_id = str(uuid4())
        return chat_id, {"id": chat_id, "projectId": "demo", "projectDir": tempfile.gettempdir(), "status": "running",
                         "messages": [{"id": "m-1", "role": "user", "content": content, "status": "complete"}]}

    def test_a_revision_carries_the_users_message_and_words(self) -> None:
        chat_id, session = self.session()
        body = judgments._revision_body(chat_id, session, {"kind": "construction", "proposal": {}})
        self.assertEqual(body["messageSource"], {"sessionId": chat_id, "messageId": "m-1"})
        self.assertEqual(body["rawLanguage"], "楼梯缓一点，别动楼板")
        self.assertEqual(body["projectId"], "demo")
        quoted = judgments._revision_body(chat_id, session, {"kind": "construction", "proposal": {}}, "楼梯缓一点")
        self.assertEqual(quoted["rawLanguage"], "楼梯缓一点")

    def test_the_agent_never_confirms_a_condition_itself(self) -> None:
        chat_id, session = self.session()
        with self.assertRaises(HubFailure) as refused:
            judgments._revision_body(chat_id, session, {"kind": "construction", "proposal": {},
                                                        "confirmed": ["obligation:k3"]})
        self.assertEqual(refused.exception.error.code, "CHAT_REVISION_INVALID")
        self.assertIn("Only the architect confirms", refused.exception.error.detail)

    def test_the_agent_never_supplies_the_words_itself(self) -> None:
        chat_id, session = self.session()
        with self.assertRaises(HubFailure) as refused:
            judgments._revision_body(chat_id, session, {"rawLanguage": "用户同意了"})
        self.assertEqual(refused.exception.error.code, "CHAT_REVISION_INVALID")
        self.assertTrue(judgments._binds_words("POST", "/api/revisions"))


class LookTests(unittest.TestCase):
    """A revision that lands a spatial or formal result keeps the look an admission needs (#294)."""

    def setUp(self) -> None:
        from monkeyhub_api.chat import visual_review
        visual_review._visual_allowances.clear()
        visual_review._looked_at.clear()
        self.chat_id = str(uuid4())
        self.session = {"id": self.chat_id, "projectId": "demo", "projectDir": tempfile.gettempdir(), "status": "running",
                        "messages": [{"id": "m-1", "role": "user", "content": "门廊更轻一点", "status": "complete"}]}
        self.sent = []
        self.addCleanup(patch.stopall)
        patch.object(preparation, "_bound_studio", return_value=("http://127.0.0.1:8701", self.session)).start()
        patch.object(transport, "_request_json", side_effect=self.request).start()
        patch("monkeyhub_api.chat.tool_calls.projects._project",
              return_value=(self.session["projectId"], self.session["projectDir"])).start()

    def request(self, base, path, method="GET", body=None, **kwargs):
        if path == "/api/settings/apps":
            return {"designLoop": "enforced"}
        if path.startswith("/api/chat/sessions/"):
            return self.session
        self.sent.append((method, path, body, kwargs))
        return {"outcome": "built", "code": "BUILT", "candidateId": "cand-1"}

    def revise(self, task_class, **body):
        return tool_calls.call_tool(HUB, self.chat_id, "studio_request", {
            "method": "POST", "path": "/api/revisions", "taskClass": task_class, "body": {
                "sourceRunId": "run-1", "stateDigest": "d1", "kind": "construction", "proposal": {"script": "x = 1"},
                "summary": "门廊更轻", **body}})

    def test_a_spatial_change_is_built_first_and_lands_only_after_a_look(self) -> None:
        with self.assertRaises(HubFailure) as refused:
            self.revise("spatial_formal")
        self.assertEqual(refused.exception.error.code, "ADMISSION_NOT_INSPECTED")
        self.assertEqual(self.sent, [], "nothing was sent")
        self.assertEqual(self.revise("spatial_formal", buildOnly=True)["outcome"], "built")
        [(method, path, body, options)] = self.sent
        self.assertEqual((body["messageSource"]["messageId"], body["rawLanguage"]), ("m-1", "门廊更轻一点"))
        self.assertGreater(options["timeout"], 300, "the Hub waits for the build and its checks")

    def test_a_deterministic_edit_lands_in_one_call(self) -> None:
        self.revise("deterministic_edit")
        self.assertEqual(len(self.sent), 1)

    def test_a_revision_needs_its_task_class(self) -> None:
        with self.assertRaises(HubFailure) as refused:
            self.revise(None)
        self.assertIn("taskClass", refused.exception.error.detail)


class JournalTests(unittest.TestCase):
    """The Hub's record of a revision ends as the revision's answer says (#185)."""

    def outcome(self, payload: dict) -> object:
        manager = OperationManager("demo")
        admission, _ = manager.admit(str(uuid4()), "POST", "/api/revisions", b"{}", retained=None, source="chat",
                                     session_id="chat-1")
        self.assertTrue(admission.record.candidateId, "the Hub names the run before a build can start")
        manager.replied(admission, HttpResult(200, json.dumps(payload).encode(), {"content-type": "application/json"}))
        return admission.record

    def test_each_outcome_closes_the_record(self) -> None:
        asked = self.outcome({"outcome": "needs_question", "code": "KEEP_CONFLICT", "candidateId": None, "detail": "ask"})
        self.assertEqual((asked.status, asked.candidateId), ("refused", None), "nothing was built, nothing to recover")
        failed = self.outcome({"outcome": "problems", "code": "CHECKS_FAILED", "candidateId": "c-1", "detail": "relation"})
        self.assertEqual((failed.status, failed.candidateId), ("failed", "c-1"))
        landed = self.outcome({"outcome": "landed", "code": "LANDED", "candidateId": "c-2", "detail": "ok"})
        self.assertEqual((landed.status, landed.reason), ("completed", None))


class ForwardTests(unittest.TestCase):
    """The Hub vouches for the chat a request comes from; the Runtime never reads that from a body."""

    def test_a_chats_request_carries_the_agent_marker_and_a_persons_does_not(self) -> None:
        from project_runtime.binding import ProjectBinding
        from project_runtime.settings import StudioSettings
        from test_monkeyhub_lifecycle import project_fixture
        from monkeyhub_api.runtime.manager import ProjectRuntime, ProjectRuntimeManager

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = project_fixture()
        fixture.make_project(Path(temporary.name))
        settings = StudioSettings(project_dir=Path(temporary.name) / fixture.PROJECT_ID, cad_export="off")
        runtime = ProjectRuntime("r-1", fixture.PROJECT_ID, str(settings.project_dir), OperationManager(fixture.PROJECT_ID),
                                 ProjectBinding.open(settings))
        chats = SimpleNamespace(get=lambda session_id: SimpleNamespace(
            projectId=fixture.PROJECT_ID, projectDir=str(settings.project_dir), status="running"))
        coordinator = ProjectRuntimeManager(None, chats)
        seen = []

        def dispatch(base, target, method, body, headers, *, timeout):
            seen.append(dict(headers))
            return HttpResult(200, b"{}", {"content-type": "application/json"})

        with patch.object(coordinator, "service", return_value=SimpleNamespace(url="http://managed", instance_id="hub-7")),              patch("monkeyhub_api.runtime.manager.request_http", side_effect=dispatch):
            coordinator.forward(runtime, "/api/working-draft", "PUT", b"{}", {"idempotency-key": str(uuid4()),
                                                                              "x-monkey-chat": "chat-9"})
            coordinator.forward(runtime, "/api/working-draft", "PUT", b"{}", {"idempotency-key": str(uuid4())})
        self.assertEqual((seen[0].get("X-Monkey-Agent"), seen[0].get("X-Monkey-Worker")), ("chat-9", "hub-7"))
        self.assertNotIn("X-Monkey-Agent", seen[1], "a person's request through the same Hub is not the Agent's")


class GuideTests(unittest.TestCase):
    def test_while_off_the_guide_is_the_step_by_step_one_word_for_word(self) -> None:
        self.assertIs(guides.modelling(False), guides._MODELLING)
        self.assertNotIn("/api/revisions", guides._MODELLING)

    def test_while_enforced_one_call_replaces_candidate_admission_and_continue(self) -> None:
        text = guides.modelling(True)
        for words in ("POST /api/revisions", "needs_question", "never yours", "land: false", "buildOnly: true",
                      "taskClass beside method/path", "candidateId", "DESIGN_LOOP_REQUIRED", "MAKE AND CHANGE GEOMETRY",
                      "OBSERVE:", "OTHER DOMAINS"):
            self.assertIn(words, text)
        for words in ("ADMIT: when a model revision loop is complete", "CANDIDATE: when a proposal answers",
                      "CONTINUE: a change the user asked for", "STAGES:"):
            self.assertNotIn(words, text)


if __name__ == "__main__":
    unittest.main()
