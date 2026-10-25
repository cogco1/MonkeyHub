"""An Agent's skill or script run runs what a person allowed on this machine, and asks the person in the chat only for
what is missing (S-1); a script that declares nothing runs without asking, and the network, ssh and writing outside the
project are asked about before every run (S23)."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from project_runtime.application import skill_access

from monkeyhub_api.chat import tool_calls
from monkeyhub_api.models import HubFailure
from monkeyhub_api.settings.models import UserSettingsDto

CODE = "a" * 64
CHAT = "6c1e2f3a-4b5c-4d6e-8f70-81920a1b2c3d"
QUESTION = "0f5d6a8e-1c2b-4d3e-9f40-5a6b7c8d9e01"
SKILL_PATH = "/api/skills/skill:site-palette/run"
SCRATCH_PATH = "/api/skills/scratch/run"
SCRIPT = {"projectId": "p", "description": "数一下 PDF 里的颜色", "name": "pdf-colours", "code": "print(1)",
          "inputs": {"pdf": {"runId": "r", "assetSha256": "b" * 64}}, "parameters": {}}


class FakeServices:
    """The Runtime's skill routes and the Hub's question and grant routes, as the chat tool reaches them."""

    def __init__(self, access, needs=None, broad=None, answers=("answered:allow_once",), design_loop="legacy",
                 approvals=(), approvals_unreadable=False, source="user"):
        self.design_loop = design_loop
        self.approvals_unreadable = approvals_unreadable
        needs = needs or {}
        self.report = {"projectId": "p", "skillId": "skill:site-palette" if source == "user" else "scratch:aaaaaaaaaaaa",
                       "skill": {"name": "site-palette" if source == "user" else "scratch-aaaaaaaaaaaa", "source": source},
                       "codeSha256": CODE, "access": access, "needs": needs, "broad": broad or {},
                       "tier": skill_access.tier(access), "eachTime": skill_access.each_time(needs)}
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
        if path in ("/api/skills/skill:site-palette/needs", "/api/skills/scratch/needs"):
            return self.report
        if path.endswith("/skill-permissions"):
            return {"permissionId": QUESTION}
        if f"/skill-permissions/{QUESTION}?" in path:
            status, _, option = self.answers.pop(0).partition(":")
            allowed = option in ("allow_once", "allow_always")
            return {"permissionId": QUESTION, "status": status, "optionId": option or None,
                    "grantId": "grant-1" if allowed and body is None and self.report["needs"] else None}
        raise AssertionError(path)

    def paths(self, method):
        return [path.split("?")[0] for _, verb, path, _ in self.calls if verb == method]

    def question(self):
        return next(body for _, verb, path, body in self.calls if path.endswith("/skill-permissions"))


class AgentSkillRunTest(unittest.TestCase):
    def run_as_agent(self, services, setting="ask", body=None, *, path=SKILL_PATH, low_risk=None):
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings",
                      return_value=UserSettingsDto(skillAgentRuns=setting, lowRiskScriptsRun=low_risk)):
            return tool_calls._agent_skill_run("http://hub", CHAT, "http://studio", path, body or {"projectId": "p"}, 5)

    def test_a_skill_that_declares_nothing_runs_without_asking(self):
        for access in (None, {"programs": [], "network": False}):
            for setting in ("ask", "granted", "never"):
                services = FakeServices(access)
                with self.subTest(access=access, setting=setting):
                    self.assertEqual(self.run_as_agent(services, setting), ({"projectId": "p", "agentRun": True}, "no"))
                    self.assertNotIn(f"/api/chat/sessions/{CHAT}/skill-permissions", services.paths("POST"))

    def test_with_low_risk_scripts_off_one_that_declares_nothing_is_asked_this_time_or_not(self):
        services = FakeServices(None)
        body, asked = self.run_as_agent(services, low_risk=False)
        self.assertEqual((body, asked), ({"projectId": "p", "agentRun": True}, "once"), "no grant is named")
        question = services.question()
        self.assertEqual((question["ask"], question["choices"]), (None, ["allow_once", "reject"]))
        self.assertIn("低风险脚本直接跑", question["title"])
        with self.assertRaises(HubFailure) as caught:
            self.run_as_agent(FakeServices(None, answers=("answered:reject",)), low_risk=False)
        self.assertEqual(caught.exception.error.code, "SKILL_PERMISSION_DENIED")

    def test_what_was_allowed_before_runs_without_asking(self):
        for setting in ("ask", "granted"):
            services = FakeServices({"programs": ["blender"]})
            self.assertEqual(self.run_as_agent(services, setting)[1], "earlier")
            self.assertEqual(services.paths("POST"), [], "nothing asked, nothing carried")

    def test_what_is_asked_once_offers_from_now_on(self):
        services = FakeServices({"programs": ["blender"], "read": ["D:\\assets"]}, needs={"read": ["D:\\assets"]},
                                answers=("answered:allow_always",))
        body, asked = self.run_as_agent(services, body={"projectId": "p", "grantId": "forged"})
        self.assertEqual((body, asked), ({"projectId": "p", "agentRun": True}, "always"),
                         "the Agent never names a grant")
        question = services.question()
        self.assertEqual(question["choices"], ["allow_always", "allow_once", "reject"])
        self.assertIn("D:\\assets", question["title"])
        self.assertNotIn("blender", question["title"], "only what no one allowed yet")
        self.assertIn("（代码 aaaaaaaaaaaa）", question["title"], "the card names the skill and its code")
        self.assertEqual(question["ask"], {"access": {"read": ["D:\\assets"]}, "broad": {},
                                           "skill": {"name": "site-palette", "source": "user"}, "projectId": "p",
                                           "codeSha256": CODE})

    def test_the_network_ssh_and_writing_outside_are_asked_this_time_only(self):
        for needs in ({"network": True}, {"ssh": ["renou-win"]}, {"write": ["D:\\x"], "programs": ["blender"]}):
            services = FakeServices(needs, needs=needs)
            with self.subTest(needs=needs):
                body, asked = self.run_as_agent(services)
                self.assertEqual((body["grantId"], asked), ("grant-1", "once"),
                                 "the run names the grant the Hub kept for this one run")
                self.assertEqual(services.question()["choices"], ["allow_once", "reject"], "no 'from now on'")
                self.assertIn("每次运行前都问", services.question()["title"])

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
        body, _ = self.run_as_agent(services, body={"projectId": "p", "permissionId": QUESTION})
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
        self.assertEqual(self.run_as_agent(services)[0]["agentRun"], True, "a skill without access runs")
        for tier in ("legacy", "advisory"):
            services = FakeServices({"programs": ["blender"]}, design_loop=tier)
            self.assertEqual(self.run_as_agent(services, "granted")[0]["agentRun"], True, tier)

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
        self.assertEqual(self.run_as_agent(services)[0]["grantId"], "grant-1", "it asks for what is missing instead")
        self.assertNotIn("/api/settings/skill-grants/carry", services.paths("POST"))

    def test_a_script_is_asked_about_by_its_code_and_runs_like_a_skill(self):
        services = FakeServices(None, source="scratch")
        body, asked = self.run_as_agent(services, body=dict(SCRIPT), path=SCRATCH_PATH)
        self.assertEqual((asked, body), ("no", {**SCRIPT, "agentRun": True}))
        needs_body = next(body for _, verb, path, body in services.calls if path == "/api/skills/scratch/needs")
        self.assertEqual(needs_body, {key: SCRIPT[key] for key in ("projectId", "description", "name", "code")},
                         "the script, without what the run is given")
        services = FakeServices({"network": True}, needs={"network": True}, source="scratch")
        body, asked = self.run_as_agent(services, body={**SCRIPT, "access": {"network": True}}, path=SCRATCH_PATH)
        self.assertEqual((asked, body["grantId"]), ("once", "grant-1"))
        self.assertIn("现写的脚本「数一下 PDF 里的颜色」", services.question()["title"])
        self.assertEqual(services.question()["ask"]["skill"], {"name": "scratch-aaaaaaaaaaaa", "source": "scratch"})

    def test_the_chat_cannot_reach_grants_imports_or_the_question(self):
        from monkeyhub_api.chat import studio_tool
        self.assertTrue(studio_tool._POST.fullmatch(SKILL_PATH))
        self.assertTrue(studio_tool._POST.fullmatch(SCRATCH_PATH))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/runs/skill-run-0123abcd"))
        self.assertTrue(studio_tool._READ.fullmatch("/api/skills/skill:site-palette/needs"))
        for path in ("/api/skills/import", "/api/skills/skill:site-palette/approvals", "/api/skills/approvals/abc/revoke",
                     f"/api/chat/sessions/{CHAT}/skill-permissions", "/api/settings/skill-grants/carry",
                     f"/api/chat/sessions/{CHAT}/skill-runs", "/api/skills/scratch/needs"):
            self.assertIsNone(studio_tool._POST.fullmatch(path), path)
        self.assertIsNone(studio_tool._READ.fullmatch("/api/skills/approvals"))

    def test_the_default_is_to_ask(self):
        self.assertIsNone(UserSettingsDto().skill_agent_runs, "an unset setting is not written")
        self.assertIsNone(UserSettingsDto().low_risk_scripts, "nor is low-risk scripts, which is on")
        services = FakeServices({"programs": ["blender"]}, needs={"programs": ["blender"]})
        with patch.object(tool_calls.transport, "_request_json", side_effect=services), \
                patch("monkeyhub_api.settings.store.read_user_settings", return_value=UserSettingsDto()):
            tool_calls._agent_skill_run("http://hub", CHAT, "http://studio", SKILL_PATH, {"projectId": "p"}, 5)
        self.assertIn(f"/api/chat/sessions/{CHAT}/skill-permissions", services.paths("POST"))


class StudioRunBodyTest(unittest.TestCase):
    """A chat is bound to one project, so the agent need not name it to run a skill (S01: the run failed on a missing projectId)."""

    def call(self, arguments: dict, answer: dict | None = None) -> dict:
        seen: dict = {"hub": []}

        def agent_run(hub, chat, base, path, body, wait):
            seen["agent_body"] = dict(body)
            return {**body, "agentRun": True}, "no"

        def request(base, path, method="GET", body=None, **_):
            if "/skill-runs" in path:
                seen["hub"].append((path, method, body))
                return {}
            seen["posted"] = (path.rsplit("/api/", 1)[-1], method, body)
            return answer or {"runId": "skill-run-0123abcd", "status": "queued", "codeSha256": CODE, "tier": "low"}

        with tempfile.TemporaryDirectory() as folder:
            session = {"projectId": "p", "projectDir": folder}
            with patch.object(tool_calls.preparation, "_bound_studio", return_value=("http://studio", session)), \
                    patch.object(tool_calls, "_agent_skill_run", side_effect=agent_run), \
                    patch.object(tool_calls.transport, "_request_json", side_effect=request):
                tool_calls.call_tool("http://hub", CHAT, "studio_request", arguments)
        return seen

    def test_the_hub_adds_the_project_to_a_skill_run(self):
        for body in (None, {}, {"inputs": {}}, {"projectId": "p", "inputs": {}}):
            with self.subTest(body=body):
                seen = self.call({"method": "POST", "path": SKILL_PATH, **({} if body is None else {"body": body})})
                self.assertEqual(seen["agent_body"]["projectId"], "p")
                self.assertEqual(seen["posted"][2], {**(body or {}), "projectId": "p", "agentRun": True})

    def test_a_skill_run_cannot_name_another_project(self):
        with self.assertRaises(HubFailure) as refused:
            self.call({"method": "POST", "path": SKILL_PATH, "body": {"projectId": "other"}})
        self.assertEqual(refused.exception.error.code, "CHAT_PROJECT_MISMATCH")

    def test_a_started_script_gets_its_card_and_reading_it_updates_the_card(self):
        seen = self.call({"method": "POST", "path": SCRATCH_PATH, "body": {k: v for k, v in SCRIPT.items() if k != "projectId"}})
        (path, method, card), = seen["hub"]
        self.assertEqual((path, method), (f"/api/chat/sessions/{CHAT}/skill-runs", "POST"))
        self.assertEqual({key: card[key] for key in ("runId", "kind", "asked", "code", "tier", "name", "description")},
                         {"runId": "skill-run-0123abcd", "kind": "scratch", "asked": "no", "code": CODE[:12], "tier": "low",
                          "name": "pdf-colours", "description": "数一下 PDF 里的颜色"})
        self.assertEqual(card["script"], {"files": {"main.py": "print(1)"}, "entry": "main.py", "manifest": None},
                         "the card keeps the code, so the person can keep it as a skill")
        report = {"runId": "skill-run-0123abcd", "status": "failed", "failureWords": "已拦下：这个脚本想写项目以外的地方（D:\\x）",
                  "outputs": []}
        seen = self.call({"method": "GET", "path": "/api/skills/runs/skill-run-0123abcd"}, answer=report)
        self.assertEqual(seen["hub"], [(f"/api/chat/sessions/{CHAT}/skill-runs/skill-run-0123abcd", "PUT",
                                        {"status": "failed", "failureWords": report["failureWords"], "outputs": []})])


if __name__ == "__main__":
    unittest.main()
