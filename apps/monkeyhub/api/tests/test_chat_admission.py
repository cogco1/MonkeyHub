"""The Agent closes each loop with one admission and continues only on the user's words.

#294 slices S3 and S4 at the chat boundary. A real Studio, managed by a Hub,
runs real candidate runs over one P036 project; the chat tool adapter binds each
admission and Continue to the user's actual message before the Runtime sees it.
Transport is in-process, and the provider is a fake: the Hub turn is a local
fake CLI that only records the prompt it was given, and the Agent's calls are a
script that follows that prompt's contract. No live model runs here.
"""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import time
import base64
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from test_chat import FAKE_CLI, _tools_of, wait_for
from test_monkeyhub_lifecycle import project_fixture
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeyhub_api import chat
from monkeyhub_api.models import ChatCreateRequest, ChatPostRequest, HubFailure

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class AgentAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub admission ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = project_fixture()
        self.repository, _ = self.fixture.make_project(self.root)
        self.project_id = self.fixture.PROJECT_ID
        self.project = self.root / self.project_id
        self.settings = StudioSettings(project_dir=self.project, cad_export="off")
        self.client = self.studio()
        self.model = (Path(self.fixture.__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        self.reference = self.fixture.REFERENCE_RUN_ID
        self.hub, self.base = "http://127.0.0.1:8700", "http://127.0.0.1:8701"
        self.session = {"id": str(uuid4()), "projectId": self.project_id, "projectDir": str(self.project.resolve()),
                        "status": "running", "messages": []}
        self.writes = []
        self.addCleanup(patch.stopall)
        patch.object(chat, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(chat, "_request_json", side_effect=self.request).start()

    def studio(self) -> TestClient:
        """The project's Runtime as the Hub starts it: with the Hub's instance id."""
        app = create_app(self.settings)
        app.state.managed_instance_id = "hub-test"
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    # ---- the chat, its tool and the Studio behind it

    def user(self, content, **fields):
        message = {"id": str(uuid4()), "role": "user", "content": content, "status": "complete", **fields}
        self.session["messages"].append(message)
        return message

    def request(self, base, path, method="GET", body=None, **kwargs):
        if base == self.hub and path.startswith("/api/chat/sessions/"):
            return deepcopy(self.session)
        if base == self.hub:
            self.writes.append((method, path, deepcopy(body), kwargs))
            path = path.split("/studio", 1)[1]
        response = self.client.request(method, path, json=body)
        if response.is_error:
            failure = response.json()
            raise HubFailure(response.status_code, failure["code"], failure["detail"])
        return response.json()

    def tool(self, path, body=None, method="POST", name="studio_request", **options):
        arguments = {"method": method, "path": path, **options}
        if body is not None:
            arguments["body"] = body
        return chat.call_tool(self.hub, self.session["id"], name, arguments)

    def refused(self, code, *args, **kwargs):
        with self.assertRaises(HubFailure) as refusal:
            self.tool(*args, **kwargs)
        self.assertEqual(refusal.exception.error.code, code, refusal.exception.error.detail)
        return refusal.exception

    # ---- what the Agent does between its tool calls: real runs

    def generate(self, source=None, height=2.2):
        """One finished run from the reference run or from another run, with its complete model."""
        state = self.client.get("/api/state", params={"run": source} if source else {}).json()
        body = {"projectId": self.project_id, "utterance": f"set height to {height}", "targetComponentId": "portico",
                "elementId": "portico-base", "stateDigest": state["stateDigest"]}
        if source:
            body["sourceRunId"] = source
        proposal = self.client.post("/api/proposals", json=body)
        self.assertEqual(proposal.status_code, 201, proposal.text)
        started = self.client.post(f"/api/proposals/{proposal.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 120
        while (job := self.client.get(f"/api/jobs/{started.json()['jobId']}").json())["status"] not in TERMINAL:
            self.assertLess(time.monotonic(), deadline, "the candidate job never finished")
            time.sleep(0.02)
        self.assertEqual(job["status"], "succeeded", job)
        run_id = started.json()["candidateId"]
        digest = self.client.get("/api/state", params={"run": run_id}).json()["stateDigest"]
        registered = self.client.post("/api/model-assets", json={
            "projectId": self.project_id, "runId": run_id, "stateDigest": digest, "fileName": "complete.3dm",
            "contentBase64": base64.b64encode(self.model).decode("ascii")})
        self.assertEqual(registered.status_code, 201, registered.text)
        return run_id

    def head(self, client=None):
        return (client or self.client).get("/api/working-source").json()["head"]["runId"]

    def revision(self):
        return self.client.get("/api/working-draft/revision").json()["revisionSha256"]

    def continued(self, run_id):
        """The retained design.continued events beside one run."""
        run = self.repository.load_run(run_id)
        refs = self.repository.list_json(run=run, record_kind=AUDIT_EVENT,
                                          destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id))
        rows = [self.repository.load_json(ref) for ref in refs]
        return [row for row in rows if row.get("action") == "design.continued"]

    def admissions(self, client=None):
        return (client or self.client).get("/api/admissions", params={"include": "rejected"}).json()["admissions"]

    # ---- the tool surface

    def test_the_tool_admits_loops_and_continues_only_with_a_bound_message(self):
        for method, path in (("POST", "/api/admissions"), ("PUT", "/api/working-draft"), ("GET", "/api/admissions"),
                             ("GET", "/api/working-source"), ("GET", "/api/working-draft/revision")):
            with self.subTest(method=method, path=path):
                self.assertTrue({"GET": chat._READ, "POST": chat._POST, "PUT": chat._WRITE}[method].fullmatch(path))
        # Continue and admission, never acceptance, local recovery or the full position.
        self.assertIsNone(chat._WRITE.fullmatch("/api/working-draft/local"))
        self.assertIsNone(chat._POST.fullmatch("/api/working-draft/save"))
        self.assertIsNone(chat._POST.fullmatch("/api/candidates/run-1/accept"))
        self.assertIsNone(chat._READ.fullmatch("/api/working-draft"))

        tools = {tool["name"]: tool for tool in _tools_of(chat)}
        described = tools["studio_request"]["description"]
        for words in ("POST /api/admissions", "supersedes", "study: {id, label, baseRunId}", "id an ASCII slug",
                      "PUT /api/working-draft", "only when the user's words ask to continue"):
            self.assertIn(words, described)
        quote = tools["studio_request"]["inputSchema"]["properties"]["feedbackQuote"]["description"]
        self.assertIn("/api/admissions", quote)
        self.assertIn("PUT /api/working-draft", quote)

        # The Agent reads the contracts without the fields Hub binds for it.
        self.user("看看这个方案")
        admission = self.tool("/api/admissions", name="studio_schema")["components"]["schemas"]
        self.assertFalse({"messageSource", "rawLanguage"} & set(admission["AdmissionRequestDto"]["properties"]))
        self.assertEqual(admission["AdmissionTaskDto"]["properties"]["kind"]["enum"], ["hub-chat"])
        selection = self.tool("/api/working-draft", method="PUT", name="studio_schema")["components"]["schemas"]
        self.assertFalse({"messageSource", "rawLanguage"} & set(selection["WorkingDraftSelectionDto"]["properties"]))
        self.assertIn("messageSource", self.client.get("/openapi.json").json()["components"]["schemas"][
            "WorkingDraftSelectionDto"]["properties"])
        for method, path in (("GET", "/api/admissions"), ("POST", "/api/proposals")):
            self.refused("CHAT_FEEDBACK_QUOTE", path, method=method, feedbackQuote="看看这个方案")

    def test_a_continue_needs_the_users_own_message_and_words(self):
        first, second = self.generate(height=2.4), self.generate(height=2.8)
        body = lambda run_id: {"runId": run_id, "baseRevisionSha256": self.revision()}  # noqa: E731
        # No user message the Agent was given: nothing to continue on.
        self.refused("CHAT_FEEDBACK_SOURCE", "/api/working-draft", body(first), method="PUT")
        # A message with no words (an attachment alone) asks for nothing either.
        self.user("")
        self.refused("CHAT_FEEDBACK_SOURCE", "/api/working-draft", body(first), method="PUT")
        opener = self.user("先把这两个方案都做出来，我看看。")
        self.refused("CHAT_FEEDBACK_QUOTE", "/api/working-draft", body(first), method="PUT", feedbackQuote="就从第二个继续")
        # The provider cannot author provenance or words, and cannot return to the default.
        for change in ({"messageSource": {"sessionId": self.session["id"], "messageId": opener["id"]}},
                       {"rawLanguage": "就从第二个继续"}, {"runId": None}):
            with self.subTest(change=change):
                self.refused("CHAT_CONTINUE_INVALID", "/api/working-draft", {**body(first), **change}, method="PUT")
        self.assertEqual(self.writes, [])
        self.assertEqual(self.head(), self.reference)
        self.assertEqual(self.continued(first) + self.continued(second), [])

        # An interjection the Agent has not been given yet binds nothing (#301).
        steer = self.user("好，就从第二个继续。", interjection="pending")
        self.refused("CHAT_FEEDBACK_QUOTE", "/api/working-draft", body(second), method="PUT", feedbackQuote="就从第二个继续")
        steer["interjection"] = "delivered"
        answer = self.tool("/api/working-draft", body(second), method="PUT", feedbackQuote="就从第二个继续")
        self.assertEqual(set(answer), {"projectId", "revisionSha256", "current"})
        self.assertEqual(answer["current"]["runId"], second)
        self.assertEqual(self.head(), second)
        [(method, path, sent, options)] = self.writes
        self.assertEqual((method, path.rsplit("/studio", 1)[1]), ("PUT", "/api/working-draft"))
        self.assertEqual((sent["messageSource"], sent["rawLanguage"]),
                         ({"sessionId": self.session["id"], "messageId": steer["id"]}, "就从第二个继续"))
        self.assertEqual(options["headers"]["X-Monkey-Chat"], self.session["id"])
        self.assertIn("Idempotency-Key", options["headers"])
        [event] = self.continued(second)
        self.assertEqual((event["origin"], event["previousHeadRunId"], event["targetRunId"], event["messageSource"]),
                         ("hub-agent", self.reference, second, {"sessionId": self.session["id"], "messageId": steer["id"]}))
        # Continue admits nothing.
        self.assertEqual(self.admissions(), [])

    def test_the_binding_fills_message_source_and_words_only_where_they_decide(self):
        kept, tried = self.generate(height=2.4), self.generate(height=2.8)
        current = self.user("做两个方案比较一下")
        for change in ({"messageSource": {"sessionId": "made-up", "messageId": "made-up"}}, {"rawLanguage": "made up"},
                       {"task": {"kind": "ui"}}, {"task": {"kind": "retroactive"}}):
            with self.subTest(change=change):
                self.refused("CHAT_ADMISSION_INVALID", "/api/admissions",
                             {"results": [{"runId": kept, "outcome": "admitted"}], **change})
        self.assertEqual(self.writes, [])

        record = self.tool("/api/admissions", {"results": [{"runId": kept, "outcome": "admitted", "label": "A"}]})
        self.assertEqual(record["messageSource"], {"sessionId": self.session["id"], "messageId": current["id"]})
        self.assertIsNone(record["rawLanguage"], "a policy admission claims no words of the user's")
        self.assertEqual((record["task"]["kind"], record["actor"]["origin"]), ("hub-chat", "hub-agent"))

        rejecting = self.user("第二个太高了，不要。其他的以后再说。")
        rejection = self.tool("/api/admissions", {"results": [{"runId": tried, "outcome": "rejected"}]},
                              feedbackQuote="第二个太高了，不要。")
        self.assertEqual((rejection["messageSource"]["messageId"], rejection["rawLanguage"]),
                         (rejecting["id"], "第二个太高了，不要。"))
        self.assertEqual(self.head(), self.reference, "admission never moves the Working Head")
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(self.admissions(restarted), [record, rejection])

    def test_a_testmodel_shaped_replay_admits_five_schemes_in_one_call(self):
        # What the project held before the Agent had this contract: a first scheme and
        # the C3 result, retained runs with no admission (the TESTMODEL shape).
        first = self.generate(height=2.2)
        c3 = self.generate(height=2.3)
        self.assertEqual(self.admissions(), [])

        request = "第一个方案不行，不要了。从 C3 出发做五个家具之家方案。"
        prompt, message = self.hub_turn(request)
        for contract in ("Close each completed loop with one admission that lists the attempts each result superseded",
                         "declare its Study with an id and label from the request", "Never admit intermediate runs",
                         "Reject a result, or continue from one, only when the user's own words say so"):
            self.assertIn(contract, prompt)

        # The Agent's script, following that contract. The user's words reject the first scheme.
        rejection = self.tool("/api/admissions", {"results": [{"runId": first, "outcome": "rejected",
                                                               "reason": "the user dropped it"}]},
                              feedbackQuote="第一个方案不行，不要了。")
        # Five alternatives from C3; three of them are redone, and the redo replaces its first try.
        tries = [self.generate(source=c3, height=height) for height in (2.4, 2.5, 2.6, 2.7, 2.8)]
        redone = {attempt: self.generate(source=attempt, height=height)
                  for attempt, height in zip(tries[2:], (2.65, 2.75, 2.85))}
        self.assertEqual(len(set(tries) | set(redone.values())), 8, "eight runs for five schemes")
        results = [{"runId": run_id, "outcome": "admitted", "label": label,
                    "supersedes": [] if run_id in tries else [attempt]}
                   for label, run_id, attempt in zip("ABCDE", tries[:2] + list(redone.values()),
                                                     [None, None, *redone])]
        study = {"id": "furniture-house", "label": "五个家具之家方案", "baseRunId": c3}
        record = self.tool("/api/admissions", {"task": {"kind": "hub-chat"}, "study": study, "results": results})

        admitted = [row["runId"] for row in record["results"]]
        self.assertEqual(len(admitted), 5)
        self.assertEqual(sorted(attempt for row in record["results"] for attempt in row["supersedes"]), sorted(tries[2:]))
        self.assertEqual({row["outcome"] for row in record["results"]}, {"admitted"})
        self.assertEqual(record["study"], {**study, "baseStageRef": None})
        bound = {"sessionId": self.session["id"], "messageId": message["id"]}
        self.assertEqual((record["messageSource"], record["rawLanguage"]), (bound, None))
        self.assertEqual((rejection["messageSource"], rejection["rawLanguage"]), (bound, "第一个方案不行，不要了。"))
        self.assertEqual([row["outcome"] for row in rejection["results"]], ["rejected"])
        self.assertEqual(len(self.admissions()), 2, "one admission closes the loop; the rejection is the user's")

        history = self.client.get("/api/design-history").json()
        self.assertEqual([row["candidateId"] for row in history["candidates"]], admitted)
        self.assertEqual({row["studyId"] for row in history["candidates"]}, {"furniture-house"})
        self.assertEqual(history["studies"], [{**study, "baseStageRef": None, "candidateIds": admitted,
                                               "source": "declared"}])
        lines = {line["runId"]: line["admission"] for line in self.client.get("/api/worktrees").json()["lines"]
                 if line["kind"] == "result"}
        self.assertEqual({run_id: lines.get(run_id) for run_id in (first, *tries[2:])},
                         {first: "rejected", **{attempt: "superseded" for attempt in tries[2:]}})
        self.assertEqual(self.head(), self.reference, "admission never moves the Working Head")
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(restarted.get("/api/design-history").json()["candidates"], history["candidates"])
            rejected = restarted.get("/api/design-history", params={"include": "rejected"}).json()["candidates"]
            self.assertEqual({row["candidateId"]: row["outcome"] for row in rejected if row["outcome"] == "rejected"},
                             {first: "rejected"})

        # The user picks D in their own words, and only then does the head move.
        scheme_d = redone[tries[3]]
        prompt, choice = self.hub_turn("D 不错，就从 D 继续深化。")
        position = self.tool("/api/working-draft", {"runId": scheme_d, "baseRevisionSha256": self.revision()},
                             method="PUT", feedbackQuote="就从 D 继续深化")
        self.assertEqual((position["current"]["runId"], self.head()), (scheme_d, scheme_d))
        [event] = self.continued(scheme_d)
        self.assertEqual((event["origin"], event["previousHeadRunId"], event["messageSource"]),
                         ("hub-agent", self.reference, {"sessionId": self.session["id"], "messageId": choice["id"]}))
        self.assertEqual(len(self.admissions()), 2, "Continue admits nothing")

    # ---- the Hub turn that gives the provider its instructions

    def hub_turn(self, content):
        """One real Hub turn with a fake CLI: the prompt it received and the user message it answered."""
        if not hasattr(self, "store"):
            environment = {"CODEX_HOME": str(self.root / "codex"), "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
                           "APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local"),
                           "CHAT_TEST_SECRET": "fake-private-token-12345"}
            patch.dict(os.environ, environment).start()
            fake, self.log = self.root / "fake cli.py", self.root / "calls.jsonl"
            fake.write_text(FAKE_CLI, encoding="utf-8")
            commands = {name: (sys.executable, str(fake), str(self.log)) for name in ("codex", "claude")}
            self.store = chat.ChatStore(self.root / "runtime", self.hub, commands=commands)
            self.addCleanup(self.store.shutdown)
            self.chat_id = self.store.create(ChatCreateRequest(projectDir=str(self.project), provider="codex")).id
        self.store.post(self.chat_id, ChatPostRequest(projectId=self.project_id, content=content))
        detail = wait_for(lambda: self.store.get(self.chat_id), lambda row: row.status != "running", timeout=30)
        self.assertEqual(detail.status, "idle", detail.error)
        # The Agent's calls happen inside this turn: the tool reads the same conversation.
        self.session = {**detail.model_dump(), "status": "running"}
        prompt = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()][-1]["prompt"]
        message = next(row for row in reversed(self.session["messages"]) if row["role"] == "user")
        self.assertEqual(message["content"], content)
        return prompt, message


if __name__ == "__main__":
    unittest.main()
