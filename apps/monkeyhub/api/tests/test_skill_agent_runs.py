"""An Agent's skill run is the Agent's: it runs what a person allowed, and asks the person in the chat otherwise."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from monkeyhub_api.chat import tool_calls
from monkeyhub_api.models import HubFailure
from monkeyhub_api.settings.models import UserSettingsDto

CODE = "a" * 64


class FakeServices:
    """The Runtime's skill and approval routes and the Hub's question routes, as the chat tool reaches them."""

    def __init__(self, access, approved=False, answers=("answered:allow_once",), design_loop="legacy"):
        self.design_loop = design_loop
        self.skill = {"id": "skill:site-palette", "name": "site-palette", "version": 2, "codeSha256": CODE,
                      "access": access, "approved": approved}
        self.answers = list(answers)
        self.calls = []

    def __call__(self, base, path, method="GET", body=None, timeout=180, **_):
        self.calls.append((base, method, path, body))
        if path == "/api/settings/apps":
            return {"designLoop": self.design_loop}
        if path.startswith("/api/skills/skill:site-palette?") or path == "/api/skills/skill:site-palette":
            return self.skill
        if path == "/api/skills/skill:site-palette/approvals":
            return {"approvalId": "grant-1", "scope": body["scope"], "method": body["method"]}
        if path.endswith("/skill-permissions"):
            return {"permissionId": "0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01"}
        if "/skill-permissions/0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01?" in path:
            status, _, option = self.answers.pop(0).partition(":")
            return {"permissionId": "0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01", "status": status, "optionId": option or None}
        raise AssertionError(path)

    def paths(self, method):
        return [path.split("?")[0] for _, verb, path, _ in self.calls if verb == method]


class AgentSkillRunTest(unittest.TestCase):
    def run_as_agent(self, services, setting="ask", body=None):
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings",
                      return_value=UserSettingsDto(skillAgentRuns=setting)):
            return tool_calls._agent_skill_run("http://hub", "6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d", "http://studio",
                                               "/api/skills/skill:site-palette/run",
                                               body or {"projectId": "p", "version": 2}, 5)

    def test_a_version_without_access_still_needs_the_persons_say(self):
        services = FakeServices(None)
        self.assertEqual(self.run_as_agent(services)["grantId"], "grant-1")
        question = next(body for _, verb, path, body in services.calls if path.endswith("/skill-permissions"))
        self.assertIn("needs nothing beyond its own folders", question["title"])
        empty = {"programs": [], "network": False, "ssh": [], "read": ["*"], "write": [], "packages": []}
        for setting in ("ask", "granted", "never"):
            services = FakeServices(empty, approved=True)
            self.assertEqual(self.run_as_agent(services, setting), {"projectId": "p", "version": 2, "agentRun": True},
                             "reading is declared, not asked; an always-allowed version runs")
            self.assertEqual(services.paths("POST"), [])

    def test_an_always_allowed_version_runs_without_asking(self):
        for setting in ("ask", "granted"):
            services = FakeServices({"programs": ["blender"]}, approved=True)
            self.assertEqual(self.run_as_agent(services, setting)["agentRun"], True)
            self.assertEqual(services.paths("POST"), [])

    def test_ask_puts_the_run_to_the_person_and_runs_on_allow_once(self):
        services = FakeServices({"programs": ["blender"], "ssh": ["vast-gpu"]})
        body = self.run_as_agent(services, body={"projectId": "p"})
        self.assertEqual(body, {"projectId": "p", "version": 2, "agentRun": True, "grantId": "grant-1"})
        question = next(body for _, verb, path, body in services.calls if path.endswith("/skill-permissions"))
        self.assertIn("blender", question["title"])
        self.assertIn("vast-gpu", question["title"])
        grant = next(body for _, verb, path, body in services.calls if path.endswith("/approvals"))
        self.assertEqual((grant["method"], grant["scope"], grant["codeSha256"]), ("hub-chat", "once", CODE))

    def test_always_allow_grants_the_version_and_runs_without_a_named_grant(self):
        services = FakeServices({"network": True}, answers=("answered:allow_always",))
        self.assertNotIn("grantId", self.run_as_agent(services))
        grant = next(body for _, verb, path, body in services.calls if path.endswith("/approvals"))
        self.assertEqual(grant["scope"], "version")

    def test_deny_waiting_and_withdrawn_refuse_and_grant_nothing(self):
        for answer, code in (("answered:reject", "SKILL_PERMISSION_DENIED"),
                             ("waiting", "SKILL_PERMISSION_WAITING"),
                             ("withdrawn", "SKILL_PERMISSION_WITHDRAWN")):
            services = FakeServices({"write": ["D:\\x"]}, answers=(answer,))
            with self.subTest(answer=answer), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services)
            self.assertEqual(caught.exception.error.code, code)
            self.assertNotIn("/api/skills/skill:site-palette/approvals", services.paths("POST"))

    def test_a_waiting_run_called_again_reads_the_same_question(self):
        services = FakeServices({"packages": ["numpy"]}, answers=("answered:allow_once",))
        body = self.run_as_agent(services, body={"projectId": "p", "permissionId": "0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01"})
        self.assertNotIn("permissionId", body)
        self.assertNotIn("/api/chat/sessions/6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d/skill-permissions", services.paths("POST"), "not asked twice")
        self.assertEqual(body["grantId"], "grant-1")

    def test_granted_and_never_refuse_a_version_no_one_always_allowed(self):
        for setting in ("granted", "never"):
            services = FakeServices({"programs": ["blender"]})
            with self.subTest(setting=setting), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services, setting)
            self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED")
            self.assertEqual(services.paths("POST"), [], "no question")
        with self.assertRaises(HubFailure) as caught:
            self.run_as_agent(FakeServices({"programs": ["blender"]}, approved=True), "never")
        self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED")

    def test_an_enforced_design_loop_reads_the_setting_as_never(self):
        for setting in ("ask", "granted"):
            services = FakeServices({"programs": ["blender"]}, approved=True, design_loop="enforced")
            with self.subTest(setting=setting), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services, setting)
            self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED")
            self.assertEqual(services.paths("POST"), [], "no question")
        services = FakeServices(None, approved=True, design_loop="enforced")
        self.assertEqual(self.run_as_agent(services)["agentRun"], True, "an always-allowed version without access runs")
        for tier in ("legacy", "advisory"):
            services = FakeServices({"programs": ["blender"]}, approved=True, design_loop=tier)
            self.assertEqual(self.run_as_agent(services, "granted")["agentRun"], True, tier)

    def test_the_chat_cannot_reach_grants_revocations_or_the_question(self):
        from monkeyhub_api.chat import studio_tool
        self.assertTrue(studio_tool._POST.fullmatch("/api/skills/skill:site-palette/run"))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/runs/skill-run-0123abcd"))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/library"), "importing grants nothing")
        self.assertTrue(studio_tool._POST.fullmatch("/api/skills/import"))
        for path in ("/api/skills/skill:site-palette/approvals", "/api/skills/approvals/abc/revoke",
                     "/api/chat/sessions/6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d/skill-permissions"):
            self.assertIsNone(studio_tool._POST.fullmatch(path), path)
        self.assertIsNone(studio_tool._READ.fullmatch("/api/skills/approvals"))

    def test_the_default_is_to_ask(self):
        self.assertIsNone(UserSettingsDto().skill_agent_runs, "an unset setting is not written")
        services = FakeServices({"programs": ["blender"]})
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings", return_value=UserSettingsDto()):
            tool_calls._agent_skill_run("http://hub", "6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d", "http://studio",
                                        "/api/skills/skill:site-palette/run", {"projectId": "p"}, 5)
        self.assertIn("/api/chat/sessions/6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d/skill-permissions", services.paths("POST"))


if __name__ == "__main__":
    unittest.main()
