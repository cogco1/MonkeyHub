"""An Agent's skill run runs what a person allowed on this machine, and asks the person in the chat only for what is missing (S-1)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from monkeyhub_api.chat import tool_calls
from monkeyhub_api.models import HubFailure
from monkeyhub_api.settings.models import UserSettingsDto

CODE = "a" * 64
CHAT = "6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d"
QUESTION = "0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01"


class FakeServices:
    """The Runtime's skill routes and the Hub's question and grant routes, as the chat tool reaches them."""

    def __init__(self, access, needs=None, broad=None, answers=("answered:allow_once",), design_loop="legacy",
                 approvals=(), approvals_unreadable=False):
        self.design_loop = design_loop
        self.approvals_unreadable = approvals_unreadable
        self.report = {"projectId": "p", "skillId": "skill:site-palette",
                       "skill": {"name": "site-palette", "source": "user"}, "codeSha256": CODE, "access": access,
                       "needs": needs or {}, "broad": broad or {}}
        self.answers = list(answers)
        self.approvals = list(approvals)
        self.calls = []

    def __call__(self, base, path, method="GET", body=None, timeout=180, **_):
        self.calls.append((base, method, path, body))
        if path == "/api/settings/apps":
            return {"designLoop": self.design_loop}
        if path == "/api/skills/approvals":
            if self.approvals_unreadable:
                raise HubFailure(404, "NOT_FOUND", "An older Runtime lists no approvals.")
            return {"projectId": "p", "approvals": self.approvals}
        if path == "/api/settings/skill-grants/carry":
            return []
        if path == "/api/skills/skill:site-palette/needs":
            return self.report
        if path.endswith("/skill-permissions"):
            return {"permissionId": QUESTION}
        if f"/skill-permissions/{QUESTION}?" in path:
            status, _, option = self.answers.pop(0).partition(":")
            allowed = option in ("allow_once", "allow_always")
            return {"permissionId": QUESTION, "status": status, "optionId": option or None,
                    "grantId": "grant-1" if allowed else None}
        raise AssertionError(path)

    def paths(self, method):
        return [path.split("?")[0] for _, verb, path, _ in self.calls if verb == method]

    def question(self):
        return next(body for _, verb, path, body in self.calls if path.endswith("/skill-permissions"))


class AgentSkillRunTest(unittest.TestCase):
    def run_as_agent(self, services, setting="ask", body=None):
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings",
                      return_value=UserSettingsDto(skillAgentRuns=setting)):
            return tool_calls._agent_skill_run("http://hub", CHAT, "http://studio", "/api/skills/skill:site-palette/run",
                                               body or {"projectId": "p"}, 5)

    def test_a_skill_that_needs_nothing_allowed_runs_without_asking(self):
        for access in (None, {"programs": [], "network": False, "read": ["C:\\Windows\\Fonts"]}):
            for setting in ("ask", "granted", "never"):
                services = FakeServices(access)
                with self.subTest(access=access, setting=setting):
                    self.assertEqual(self.run_as_agent(services, setting), {"projectId": "p", "agentRun": True})
                    self.assertNotIn(f"/api/chat/sessions/{CHAT}/skill-permissions", services.paths("POST"))

    def test_what_was_allowed_before_runs_without_asking(self):
        for setting in ("ask", "granted"):
            services = FakeServices({"programs": ["blender"]})
            self.assertEqual(self.run_as_agent(services, setting)["agentRun"], True)
            self.assertEqual(services.paths("POST"), [], "nothing asked, nothing carried")

    def test_ask_puts_only_what_is_missing_to_the_person_and_runs_on_only_this_time(self):
        services = FakeServices({"programs": ["blender"], "ssh": ["renou-win"]}, needs={"ssh": ["renou-win"]})
        body = self.run_as_agent(services, body={"projectId": "p", "grantId": "forged"})
        self.assertEqual(body, {"projectId": "p", "agentRun": True, "grantId": "grant-1"},
                         "the run names the grant the Hub kept, never one the Agent sent")
        question = services.question()
        self.assertIn("renou-win", question["title"])
        self.assertNotIn("blender", question["title"], "only what no one allowed yet")
        self.assertEqual(question["ask"], {"access": {"ssh": ["renou-win"]}, "broad": {},
                                           "skill": {"name": "site-palette", "source": "user"}, "projectId": "p",
                                           "codeSha256": CODE})

    def test_always_allow_runs_without_a_named_grant(self):
        services = FakeServices({"network": True}, needs={"network": True}, answers=("answered:allow_always",))
        self.assertNotIn("grantId", self.run_as_agent(services))

    def test_an_item_that_allows_anything_says_so(self):
        services = FakeServices({"programs": ["python"]}, needs={"programs": ["python"]}, broad={"programs": ["python"]})
        self.run_as_agent(services)
        self.assertIn("等于能做任何事", services.question()["title"])
        self.assertEqual(services.question()["ask"]["broad"], {"programs": ["python"]})

    def test_deny_waiting_and_withdrawn_refuse(self):
        for answer, code in (("answered:reject", "SKILL_PERMISSION_DENIED"),
                             ("waiting", "SKILL_PERMISSION_WAITING"),
                             ("withdrawn", "SKILL_PERMISSION_WITHDRAWN")):
            services = FakeServices({"write": ["D:\\x"]}, needs={"write": ["D:\\x"]}, answers=(answer,))
            with self.subTest(answer=answer), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services)
            self.assertEqual(caught.exception.error.code, code)

    def test_a_waiting_run_called_again_reads_the_same_question(self):
        services = FakeServices({"packages": ["numpy"]}, needs={"packages": ["numpy"]})
        body = self.run_as_agent(services, body={"projectId": "p", "permissionId": QUESTION})
        self.assertNotIn("permissionId", body)
        self.assertNotIn(f"/api/chat/sessions/{CHAT}/skill-permissions", services.paths("POST"), "not asked twice")
        self.assertEqual(body["grantId"], "grant-1")

    def test_granted_and_never_refuse_what_no_one_allowed(self):
        for setting in ("granted", "never"):
            services = FakeServices({"programs": ["blender"]}, needs={"programs": ["blender"]})
            with self.subTest(setting=setting), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services, setting)
            self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED")
            self.assertEqual(services.paths("POST"), [], "no question")
        with self.assertRaises(HubFailure) as caught:
            self.run_as_agent(FakeServices({"programs": ["blender"]}), "never")
        self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED", "never: not even what was allowed")

    def test_an_enforced_design_loop_reads_the_setting_as_never(self):
        for setting in ("ask", "granted"):
            services = FakeServices({"programs": ["blender"]}, design_loop="enforced")
            with self.subTest(setting=setting), self.assertRaises(HubFailure) as caught:
                self.run_as_agent(services, setting)
            self.assertEqual(caught.exception.error.code, "SKILL_AGENT_RUN_REFUSED")
            self.assertEqual(services.paths("POST"), [], "no question")
        services = FakeServices(None, design_loop="enforced")
        self.assertEqual(self.run_as_agent(services)["agentRun"], True, "a skill without access runs")
        for tier in ("legacy", "advisory"):
            services = FakeServices({"programs": ["blender"]}, design_loop=tier)
            self.assertEqual(self.run_as_agent(services, "granted")["agentRun"], True, tier)

    def test_a_projects_approvals_from_before_are_carried_over_first(self):
        approvals = [{"approvalId": "a1", "skillId": "skill:site-palette", "scope": "version", "status": "active",
                      "access": {"programs": ["blender"]}},
                     {"approvalId": "a2", "skillId": "skill:site-palette", "scope": "once", "status": "active"},
                     {"approvalId": "a3", "skillId": "skill:site-palette", "scope": "version", "status": "revoked"}]
        services = FakeServices({"programs": ["blender"]}, approvals=approvals)
        self.run_as_agent(services)
        carried = next(body for _, verb, path, body in services.calls if path == "/api/settings/skill-grants/carry")
        self.assertEqual((carried["projectId"], [row["approvalId"] for row in carried["approvals"]]), ("p", ["a1"]))
        order = [path for _, _, path, _ in services.calls]
        self.assertLess(order.index("/api/settings/skill-grants/carry"), order.index("/api/skills/skill:site-palette/needs"))

    def test_a_run_goes_on_when_the_approvals_from_before_cannot_be_read(self):
        services = FakeServices({"programs": ["blender"]}, needs={"programs": ["blender"]}, approvals_unreadable=True)
        self.assertEqual(self.run_as_agent(services)["grantId"], "grant-1", "it asks for what is missing instead")
        self.assertNotIn("/api/settings/skill-grants/carry", services.paths("POST"))

    def test_the_chat_cannot_reach_grants_imports_or_the_question(self):
        from monkeyhub_api.chat import studio_tool
        self.assertTrue(studio_tool._POST.fullmatch("/api/skills/skill:site-palette/run"))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/runs/skill-run-0123abcd"))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/skill:site-palette/needs"))
        for path in ("/api/skills/import", "/api/skills/skill:site-palette/approvals", "/api/skills/approvals/abc/revoke",
                     f"/api/chat/sessions/{CHAT}/skill-permissions", "/api/settings/skill-grants/carry"):
            self.assertIsNone(studio_tool._POST.fullmatch(path), path)
        self.assertIsNone(studio_tool._READ.fullmatch("/api/skills/approvals"))

    def test_the_default_is_to_ask(self):
        self.assertIsNone(UserSettingsDto().skill_agent_runs, "an unset setting is not written")
        services = FakeServices({"programs": ["blender"]}, needs={"programs": ["blender"]})
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings", return_value=UserSettingsDto()):
            tool_calls._agent_skill_run("http://hub", CHAT, "http://studio", "/api/skills/skill:site-palette/run",
                                        {"projectId": "p"}, 5)
        self.assertIn(f"/api/chat/sessions/{CHAT}/skill-permissions", services.paths("POST"))


if __name__ == "__main__":
    unittest.main()
