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
from archflow.project.repository import FilesystemProjectRepository
from archflow.state.state_record import StateRecord
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from monkeyhub_api.chat import mcp_server, preparation, store as chat, studio_tool, tool_calls, transport
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
        self.models, self.drawn = {}, []
        self.addCleanup(patch.stopall)
        patch.object(preparation, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(transport, "_request_json", side_effect=self.request).start()
        # The model-view owner's drawing is the one stand-in on the look's path:
        # it answers only the exact models this test registered.
        patch("project_runtime.application.visual_reviews.model_view", side_effect=self.model_view).start()

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
        return tool_calls.call_tool(self.hub, self.session["id"], name, arguments)

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
        self.models[run_id] = registered.json()["modelSource"]
        return run_id

    def model_view(self, binding, *, model_source, view):
        """One drawn view of an exact registered model; any other source is not retained."""
        import io
        from PIL import Image
        from project_runtime.errors import StudioError

        if model_source.to_dict() not in self.models.values():
            raise StudioError(409, "MODEL_SOURCE_MISMATCH", "Not a retained model of this project.")
        self.drawn.append((model_source.run_id, view))
        picture = io.BytesIO()
        Image.new("RGB", (64, 48), "white").save(picture, format="PNG")
        return picture.getvalue(), 64, 48

    def look(self, run_id, **changes):
        """The Agent's visual_review of one result's model, as it would call it."""
        return tool_calls.call_tool(self.hub, self.session["id"], "visual_review", {
            "taskClass": "spatial_formal", "reason": "first_bundle", "domain": "modeling",
            "sourceRefs": [{"kind": "model", **self.models[run_id]}], "viewRecipe": ["axon"],
            "task": "Check that the portico reads lighter than the base.",
            "criteria": [{"criterionId": "proportion", "text": "The portico reads lighter than the base."}], **changes})

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
                self.assertTrue({"GET": studio_tool._READ, "POST": studio_tool._POST, "PUT": studio_tool._WRITE}[method].fullmatch(path))
        # Continue and admission, never acceptance, local recovery or the full position.
        self.assertIsNone(studio_tool._WRITE.fullmatch("/api/working-draft/local"))
        self.assertIsNone(studio_tool._POST.fullmatch("/api/working-draft/save"))
        self.assertIsNone(studio_tool._POST.fullmatch("/api/candidates/run-1/accept"))
        self.assertIsNone(studio_tool._READ.fullmatch("/api/working-draft"))

        tools = {tool["name"]: tool for tool in _tools_of(mcp_server)}
        described = tools["studio_request"]["description"]
        for words in ("POST /api/admissions", "supersedes", "study: {id, label, baseRunId}", "id an ASCII slug",
                      "PUT /api/working-draft", "only when the user's words ask to continue", "taskClass",
                      "only after visual_review looked at that result or an attempt it supersedes"):
            self.assertIn(words, described)
        quote = tools["studio_request"]["inputSchema"]["properties"]["feedbackQuote"]["description"]
        self.assertIn("/api/admissions", quote)
        self.assertIn("PUT /api/working-draft", quote)
        declared = tools["studio_request"]["inputSchema"]["properties"]["taskClass"]
        self.assertEqual(declared["enum"], ["spatial_formal", "polish", "deterministic_edit"])
        self.assertIn("Only for POST /api/admissions", declared["description"])
        self.assertIn("A look admits, continues and accepts nothing", tools["visual_review"]["description"])

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
        # A loop that admits a result says what kind of loop it was; a look belongs to no other path.
        self.refused("CHAT_ADMISSION_INVALID", "/api/admissions", {"results": [{"runId": kept, "outcome": "admitted"}]})
        self.refused("CHAT_TOOL_INVALID", "/api/admissions", {"results": [{"runId": kept, "outcome": "admitted"}]},
                     taskClass="deterministic")
        self.refused("CHAT_TOOL_INVALID", "/api/proposals", {"stateDigest": "0" * 64}, taskClass="deterministic_edit")
        self.assertEqual(self.writes, [])

        record = self.tool("/api/admissions", {"results": [{"runId": kept, "outcome": "admitted", "label": "A"}]},
                           taskClass="deterministic_edit")
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
        admission = {"task": {"kind": "hub-chat"}, "study": study, "results": results}
        # Five schemes are a spatial loop: it closes only after one look at what it made.
        self.refused("ADMISSION_NOT_INSPECTED", "/api/admissions", admission, taskClass="spatial_formal")
        self.assertEqual(len(self.admissions()), 1, "a loop that has not looked admits nothing")
        self.look(redone[tries[3]])
        record = self.tool("/api/admissions", admission)

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

    # ---- a loop is admitted once it has finished (owner decision, 2026-10-01)

    def test_a_spatial_loop_is_admitted_once_after_its_look_and_its_repair_never_shows(self):
        asked = self.user("门廊太笨重了，让它的比例轻一些。")
        attempt = self.generate(height=2.4)
        # The end of a run is not the end of the loop: this one declared a look and has not looked.
        closing = {"results": [{"runId": attempt, "outcome": "admitted", "label": "轻门廊"}]}
        refusal = self.refused("ADMISSION_NOT_INSPECTED", "/api/admissions", closing, taskClass="spatial_formal")
        self.assertIn("visual_review", refusal.error.detail)
        self.assertEqual((self.writes, self.admissions()), ([], []))

        # One look at the exact result, through the Runtime's own review route. It sees and
        # decides nothing for the project: no admission, no Continue, no Candidate.
        looked = self.look(attempt)
        self.assertEqual((looked["delivery"], looked["allowance"]),
                         ("frames", {"taskClass": "spatial_formal", "allowed": 2, "used": 1}))
        self.assertEqual([frame["sourceRef"] for frame in looked["frames"]], [{"kind": "model", **self.models[attempt]}])
        self.assertEqual(self.drawn, [(attempt, "axon")])
        self.assertEqual((self.writes, self.admissions(), self.head()), ([], [], self.reference))
        self.assertEqual(self.client.get("/api/design-history").json()["candidates"], [])

        # What it saw needs a repair: the loop's result supersedes the attempt it looked at.
        repaired = self.generate(source=attempt, height=2.6)
        closing = {"results": [{"runId": repaired, "outcome": "admitted", "supersedes": [attempt], "label": "轻门廊"}]}
        record = self.tool("/api/admissions", closing, taskClass="spatial_formal")
        self.assertEqual([(row["runId"], row["supersedes"]) for row in record["results"]], [(repaired, [attempt])])
        self.assertEqual((record["task"]["kind"], record["actor"]["origin"], record["rawLanguage"]),
                         ("hub-chat", "hub-agent", None))
        self.assertEqual(record["messageSource"], {"sessionId": self.session["id"], "messageId": asked["id"]})
        # Admitted once: a retry repeats the record, and the attempt can no longer close a loop.
        self.assertEqual(self.tool("/api/admissions", closing)["admissionId"], record["admissionId"])
        self.refused("ADMISSION_CONFLICT", "/api/admissions",
                     {"results": [{"runId": attempt, "outcome": "admitted"}]}, taskClass="spatial_formal")
        self.assertEqual([row["admissionId"] for row in self.admissions()], [record["admissionId"]])

        # The repair never shows in the design tree; the attempt it replaced stays readable as one.
        for client in (self.client, self.studio()):
            history = client.get("/api/design-history", params={"include": "rejected"}).json()
            self.assertEqual([(row["candidateId"], row["supersedes"], row["admittedBy"]["origin"])
                              for row in history["candidates"]], [(repaired, [attempt], "hub-agent")])
            lines = {line["runId"]: line["admission"] for line in client.get("/api/worktrees").json()["lines"]
                     if line["kind"] == "result"}
            self.assertEqual((lines.get(attempt), lines.get(repaired)), ("superseded", "admitted"))
        self.assertEqual(self.head(), self.reference, "admission never moves the Working Head")

    def test_a_look_fixes_the_loops_class_and_has_to_see_the_loop_it_closes(self):
        self.user("比较一下两种门廊比例。")
        seen, unseen = self.generate(height=2.4), self.generate(height=2.8)
        self.look(seen)
        # The review fixed this message's class: the loop cannot now call itself deterministic.
        self.refused("VISUAL_TASK_CLASS_FIXED", "/api/admissions",
                     {"results": [{"runId": seen, "outcome": "admitted"}]}, taskClass="deterministic_edit")
        # A look at another result is not a look at this loop.
        self.refused("ADMISSION_NOT_INSPECTED", "/api/admissions", {"results": [{"runId": unseen, "outcome": "admitted"}]})
        self.assertEqual((self.writes, self.admissions()), ([], []))
        # A rejection on the user's words admits nothing, so it needs no look.
        self.user("第二个不要。")
        rejected = self.tool("/api/admissions", {"results": [{"runId": unseen, "outcome": "rejected"}]})
        self.assertEqual([row["outcome"] for row in rejected["results"]], ["rejected"])
        # The class a spent review fixed belongs to its message; a new message declares again.
        self.refused("CHAT_ADMISSION_INVALID", "/api/admissions", {"results": [{"runId": seen, "outcome": "admitted"}]})
        record = self.tool("/api/admissions", {"results": [{"runId": seen, "outcome": "admitted"}]},
                           taskClass="spatial_formal")
        self.assertEqual([row["runId"] for row in record["results"]], [seen])

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


class FirstModelRecipeTests(unittest.TestCase):
    """#404 F7: the recipe the guide states, as the Agent's actual calls, on an empty project.

    Each round trip the guide makes unnecessary is a model call saved, so the
    test counts the calls and names what must not appear: no schema read, no
    state or frame read. What gets built is checked on the retained runs.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub recipe ")
        self.addCleanup(temporary.cleanup)
        self.project_id = "first-model"
        self.project = Path(temporary.name) / self.project_id
        FilesystemProjectRepository.initialize(
            self.project, project_id=self.project_id, initial_state={"project_id": self.project_id, "version": 0},
            authored_record=StateRecord(project_id=self.project_id, run_id="authored", entities=()).to_dict())
        app = create_app(StudioSettings(project_dir=self.project, cad_export="off"))
        app.state.managed_instance_id = "hub-test"
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        fixture = project_fixture()
        self.model = (Path(fixture.__file__).parent / "fixtures/model-source-a.3dm").read_bytes()
        self.hub, self.base = "http://127.0.0.1:8700", "http://127.0.0.1:8701"
        self.session = {"id": str(uuid4()), "projectId": self.project_id, "projectDir": str(self.project.resolve()),
                        "status": "running", "messages": []}
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(preparation, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(transport, "_request_json", side_effect=self.request).start()

    def request(self, base, path, method="GET", body=None, **kwargs):
        if base == self.hub and path.startswith("/api/chat/sessions/"):
            return deepcopy(self.session)
        if base == self.hub:
            path = path.split("/studio", 1)[1]
        response = self.client.request(method, path, json=body)
        if response.is_error:
            failure = response.json()
            raise HubFailure(response.status_code, failure["code"], failure["detail"])
        return response.json()

    def user(self, content):
        self.session["messages"].append({"id": str(uuid4()), "role": "user", "content": content, "status": "complete"})
        self.calls = []

    def call(self, method, path, body=None, name="studio_request", **options):
        """One tool call exactly as the CLI would send it."""
        arguments = {"method": method, "path": path, **options}
        if body is not None:
            arguments["body"] = body
        self.calls.append((name, method, path.split("?", 1)[0]))
        return tool_calls.call_tool(self.hub, self.session["id"], name, arguments)

    def exported(self, run_id, digest):
        """The Runtime's model export between two tool calls; cad_export is off in tests."""
        registered = self.client.post("/api/model-assets", json={
            "projectId": self.project_id, "runId": run_id, "stateDigest": digest, "fileName": "complete.3dm",
            "contentBase64": base64.b64encode(self.model).decode("ascii")})
        self.assertEqual(registered.status_code, 201, registered.text)

    def extents(self, run_id):
        state = self.client.get("/api/state", params={"run": run_id}).json()
        return {row["elementId"]: (row["producer"], row["verticalExtent"]) for row in state["elements"]}

    def assert_no_extra_round_trips(self, most):
        self.assertLessEqual(len(self.calls), most, self.calls)
        self.assertFalse([call for call in self.calls if call[0] == "studio_schema"], "the guide's bodies are complete")
        self.assertFalse([call for call in self.calls if call[1] == "GET"], "every value came back with a call")

    def test_a_first_model_and_its_follow_up_need_only_the_calls_the_guide_states(self):
        guide = next(tool for tool in _tools_of(mcp_server) if tool["name"] == "studio_request")["description"]
        for stated in ("POST /api/project/modeling with body {}", "POST /api/proposals/construction", "at=top(",
                       "awaitSeconds: 60", "candidate.stateDigest", "supersedes may be []", "POST /api/proposals/facets",
                       "taskClass: deterministic_edit when readback checks it"):
            self.assertIn(stated, guide)

        # The evidence turn (#404 F7): two stacked masses on an empty project, as one script (#419).
        self.user("做一个 12×8 米、3.5 米高的体块，上面再叠一个 8×6×3 米的体块。")
        base = self.call("POST", "/api/project/modeling")
        self.assertTrue(base["initialized"])
        self.assertEqual((base["levels"], base["components"], base["elementCount"], base["sourceStageRef"]),
                         ([{"levelId": "ground", "elevation": 0.0}], [{"componentId": "model", "parentComponentId": None}],
                          0, None))
        self.assertEqual(base["stateDigest"], self.client.get("/api/state").json()["stateDigest"],
                         "the answer is the base a state read would have given")
        proposal = self.call("POST", "/api/proposals/construction", {"stateDigest": base["stateDigest"], "script": (
            "mass = extrude(rect(0, 0, 12, 8), 3.5)\n"
            "upper = extrude(rect(2, 1, 8, 6), 3, at=top(mass))")})
        self.assertEqual(proposal["status"], "proposed")
        made = self.call("POST", f"/api/proposals/{proposal['proposalId']}/candidate", awaitSeconds=60)
        self.assertEqual(made["status"], "succeeded", made)
        first = made["candidateId"]
        self.assertEqual(made["candidate"]["stateDigest"],
                         self.client.get("/api/state", params={"run": first}).json()["stateDigest"])
        self.exported(first, made["candidate"]["stateDigest"])
        admitted = self.call("POST", "/api/admissions", {"results": [
            {"runId": first, "outcome": "admitted", "supersedes": [], "label": "叠加体块"}]}, taskClass="deterministic_edit")
        self.assertEqual([row["outcome"] for row in admitted["results"]], ["admitted"])
        self.assertEqual(len(self.calls), 4)
        self.assert_no_extra_round_trips(5)
        self.assertEqual(self.extents(first), {"mass-body": ("prism", {"base": 0.0, "top": 3.5}),
                                               "upper-body": ("prism", {"base": 3.5, "top": 6.5})})

        # A follow-up edit writes against the candidate it just made.
        self.user("把上面的体块改成 4 米高。")
        revised = self.call("POST", "/api/proposals/construction", {
            "stateDigest": made["candidate"]["stateDigest"], "sourceRunId": first,
            "script": "set_height(get('upper'), 4)"})
        self.assertEqual(revised["status"], "proposed")
        remade = self.call("POST", f"/api/proposals/{revised['proposalId']}/candidate", awaitSeconds=60)
        self.assertEqual(remade["status"], "succeeded", remade)
        second = remade["candidateId"]
        self.exported(second, remade["candidate"]["stateDigest"])
        readmitted = self.call("POST", "/api/admissions", {"results": [
            {"runId": second, "outcome": "admitted", "supersedes": [], "label": "上部 4 米"}]}, taskClass="deterministic_edit")
        self.assertEqual([row["outcome"] for row in readmitted["results"]], ["admitted"])
        self.assert_no_extra_round_trips(3)
        self.assertEqual(self.extents(second), {"mass-body": ("prism", {"base": 0.0, "top": 3.5}),
                                                "upper-body": ("prism", {"base": 3.5, "top": 7.5})})

        # "A wall": geometry first, then its meaning on the same id, as one chain and one candidate.
        self.user("沿南边加一道墙。")
        south = self.call("POST", "/api/proposals/construction", {
            "stateDigest": remade["candidate"]["stateDigest"], "sourceRunId": second,
            "script": "south = extrude(rect(0, -0.2, 12, 0.2), 3)"})
        self.assertEqual(south["status"], "proposed")
        meaning = self.call("POST", "/api/proposals/facets", {
            "stateDigest": remade["candidate"]["stateDigest"], "sourceProposalId": south["proposalId"],
            "targets": [{"id": "south", "set": {"architectural.role": "wall"}}]})
        self.assertEqual(meaning["status"], "proposed")
        walled = self.call("POST", f"/api/proposals/{meaning['proposalId']}/candidate", awaitSeconds=60)
        self.assertEqual(walled["status"], "succeeded", walled)
        self.assert_no_extra_round_trips(3)
        self.assertEqual(self.extents(walled["candidateId"])["south-body"], ("prism", {"base": 0.0, "top": 3.0}))
        model = self.client.get("/api/construction/model", params={"run": walled["candidateId"]}).json()
        [row] = [entity for entity in model["entities"] if entity["id"] == "south"]
        self.assertEqual(row["facets"], {"architectural.role": "wall"})

    def test_preparing_an_existing_design_keeps_it_and_answers_its_base(self):
        self.user("继续")
        first = self.call("POST", "/api/project/modeling")
        proposal = self.call("POST", "/api/proposals/construction", {
            "stateDigest": first["stateDigest"], "script": "mass = extrude(rect(0, 0, 4, 3), 3)"})
        made = self.call("POST", f"/api/proposals/{proposal['proposalId']}/candidate", awaitSeconds=60)
        self.assertEqual(made["status"], "succeeded", made)
        before = self.client.get("/api/state").json()
        again = self.call("POST", "/api/project/modeling")
        self.assertFalse(again["initialized"])
        after = self.client.get("/api/state").json()
        self.assertEqual(after["recordDigest"], before["recordDigest"], "an existing project keeps its model inputs")
        self.assertEqual((again["stateDigest"], again["elementCount"]), (after["stateDigest"], len(after["elements"])))


if __name__ == "__main__":
    unittest.main()
