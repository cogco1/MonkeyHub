"""The chat capability binds real message provenance before Runtime persistence.

Transport is in-process here; the opt-in feedback loop separately uses a real
provider and the same product tool. No mocked response is provider acceptance.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from test_monkeyhub_lifecycle import project_fixture
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeyhub_api import chat
from monkeyhub_api.models import HubFailure


class ChatFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = project_fixture()
        self.repository, _ = fixture.make_project(Path(temporary.name))
        self.project = Path(temporary.name) / fixture.PROJECT_ID
        self.settings = StudioSettings(project_dir=self.project, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.hub, self.base = "http://127.0.0.1:8700", "http://127.0.0.1:8701"
        self.session = {"id": str(uuid4()), "projectId": fixture.PROJECT_ID,
                        "projectDir": str(self.project.resolve()), "status": "running", "messages": []}
        self.sessions = {self.session["id"]: self.session}
        self.writes = []
        self.state = self.client.get(f"/api/state?run={fixture.REFERENCE_RUN_ID}").json()
        self.body = {"projectId": fixture.PROJECT_ID, "disposition": "keep", "strength": "hard",
                     "targetRef": "entity:portico-base", "scope": {"domain": "design", "extent": "project"},
                     "source": {"kind": "design", "sourceRunId": fixture.REFERENCE_RUN_ID,
                                "stateDigest": self.state["stateDigest"]}, "applicability": "scope"}
        self.user("底座保留原样，后续不要修改它。")
        self.addCleanup(patch.stopall)
        patch.object(chat, "_bound_studio", side_effect=lambda *a, **k: (self.base, deepcopy(self.session))).start()
        patch.object(chat, "_request_json", side_effect=self.request).start()

    def user(self, content):
        message = {"id": str(uuid4()), "role": "user", "content": content, "status": "complete"}
        self.session["messages"].append(message)
        return message

    def request(self, base, path, method="GET", body=None, **kwargs):
        if base == self.hub and path.startswith("/api/chat/sessions/"):
            return deepcopy(self.sessions[path.rsplit("/", 1)[-1]])
        if base == self.hub:
            self.writes.append((path, deepcopy(body), kwargs))
            path = path.split("/studio", 1)[1]
        response = self.client.request(method, path, json=body)
        if response.is_error:
            failure = response.json()
            raise HubFailure(response.status_code, failure["code"], failure["detail"])
        return response.json()

    def tool(self, path, body=None, method="POST", name="studio_request", **options):
        args = {"method": method, "path": path, **options}
        if body is not None:
            args["body"] = body
        return chat.call_tool(self.hub, self.session["id"], name, args)

    def save(self):
        return self.tool("/api/decisions", deepcopy(self.body))

    def test_user_wording_survives_reopen_without_acceptance_or_lock_effect(self):
        head = self.repository.layout.head.read_bytes()
        saved = self.save()
        self.assertEqual(saved["rawLanguage"], self.session["messages"][-1]["content"])
        self.assertEqual(saved["messageSource"], {"sessionId": self.session["id"], "messageId": self.session["messages"][-1]["id"]})
        self.assertEqual(saved["sourceKind"], "agent")
        self.assertEqual(len(self.writes), 1)
        self.assertIn("Idempotency-Key", self.writes[0][2]["headers"])
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(cold.get("/api/decisions").json()["decisions"], [saved])
            self.assertEqual(cold.get(f"/api/state?run={self.body['source']['sourceRunId']}").json(), self.state)
            context = {"projectId": self.session["projectId"], "utterance": "调整檐口", **{key: self.body["source"][key] for key in ("sourceRunId", "stateDigest")}}
            design = cold.post("/api/intents/context", json=context)
            self.assertEqual(design.status_code, 200, design.text)
            self.assertEqual([x["decisionId"] for x in design.json()["scopedDecisions"]], [saved["decisionId"]])
            other = cold.post("/api/intents/context", json={**context, "decisionContext": {"domain": "copy"}})
            self.assertEqual(other.json()["scopedDecisions"], [])
        self.assertEqual(self.repository.layout.head.read_bytes(), head)

    def test_provider_cannot_author_user_provenance_or_save_other_decision_kinds(self):
        for change in ({"rawLanguage": "made up"}, {"raw_language": "made up"},
                       {"sourceKind": "human"}, {"messageSource": {"sessionId": "other", "messageId": "fake"}},
                       {"disposition": "lock"}, {"disposition": "require"}):
            with self.subTest(change=change), self.assertRaises(HubFailure):
                self.tool("/api/decisions", {**self.body, **change})
        self.assertEqual(self.writes, [])
        self.assertEqual(self.client.get("/api/decisions").json()["decisions"], [])

    def test_revoke_binds_new_user_message_and_preserves_original_feedback(self):
        saved = self.save()
        original = deepcopy(self.session)
        self.sessions[self.session["id"]] = original
        self.session = {**self.session, "id": str(uuid4()), "messages": []}
        self.sessions[self.session["id"]] = self.session
        current = self.user("撤销刚才保留底座的反馈。")
        path = f"/api/decisions/{saved['decisionId']}/revisions"
        body = {"projectId": self.session["projectId"], "expectedRevisionRef": saved["revisionRef"], "action": "revoke"}
        revoked = self.tool(path, body)
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked["messageSource"], saved["messageSource"])
        self.assertEqual(revoked["rawLanguage"], saved["rawLanguage"])
        self.assertEqual(revoked["reason"], current["content"])
        self.assertEqual(revoked["revisionMessageSource"], {"sessionId": self.session["id"], "messageId": current["id"]})
        with self.assertRaises(HubFailure) as stale:
            self.tool(path, body)
        self.assertEqual(stale.exception.status, 409)

    def test_revoke_cannot_supersede_or_forge_the_original_message(self):
        saved = self.save()
        path = f"/api/decisions/{saved['decisionId']}/revisions"
        body = {"expectedRevisionRef": saved["revisionRef"], "action": "revoke"}
        for change in ({"action": "supersede"}, {"replacement": self.body}, {"reason": "invented"}, {"revisionMessageSource": {}}):
            with self.subTest(change=change), self.assertRaises(HubFailure):
                self.tool(path, {**body, **change})
        self.session["messages"][0]["content"] = "different original words"
        self.user("撤销它。")
        with self.assertRaises(HubFailure) as wrong:
            self.tool(path, body)
        self.assertEqual(wrong.exception.error.code, "CHAT_FEEDBACK_SOURCE")
        self.assertEqual(len(self.writes), 1)

    def test_feedback_schema_is_the_runtime_contract_with_narrow_chat_inputs(self):
        saved = self.save()
        for path, key, hidden, field, values in (
            ("/api/decisions", "DecisionRequestDto", {"rawLanguage", "messageSource", "sourceKind"}, "disposition", ["avoid", "keep"]),
            (f"/api/decisions/{saved['decisionId']}/revisions", "DecisionRevisionRequestDto", {"reason", "revisionMessageSource", "replacement"}, "action", ["revoke"]),
        ):
            schema = self.tool(path, name="studio_schema")["components"]["schemas"][key]
            self.assertFalse(hidden.intersection(schema["properties"]))
            self.assertFalse(hidden.intersection(schema["required"]))
            self.assertEqual(schema["properties"][field]["enum"], values)
        self.assertIn("rawLanguage", self.client.get("/openapi.json").json()["components"]["schemas"]["DecisionRequestDto"]["required"])

    def test_runtime_authorization_refusal_is_not_bypassed_or_retried(self):
        def deny(base, path, *args, **kwargs):
            if base == self.hub and "/studio/" in path:
                raise HubFailure(403, "ACTION_FORBIDDEN", "actor lacks accept")
            return self.request(base, path, *args, **kwargs)
        with patch.object(chat, "_request_json", side_effect=deny) as sent, self.assertRaises(HubFailure) as refused:
            self.save()
        self.assertEqual(refused.exception.error.code, "ACTION_FORBIDDEN")
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(self.client.get("/api/decisions").json()["decisions"], [])

    def test_long_message_uses_verified_passage_without_user_repeating_feedback(self):
        phrase = "请保留底座原有形状和位置。"
        original = self.user("设计背景。" * 450 + phrase + "后续工作另行讨论。")
        with self.assertRaises(HubFailure) as long:
            self.save()
        self.assertEqual(long.exception.error.code, "CHAT_FEEDBACK_TOO_LONG")
        saved = self.tool("/api/decisions", self.body, feedbackQuote=phrase)
        self.assertEqual(saved["rawLanguage"], phrase)
        self.assertEqual(saved["messageSource"]["messageId"], original["id"])
        withdrawn = "撤销这条保留底座的反馈。"
        current = self.user("讨论补充。" * 450 + withdrawn)
        revoked = self.tool(f"/api/decisions/{saved['decisionId']}/revisions",
                            {"action": "revoke", "expectedRevisionRef": saved["revisionRef"]}, feedbackQuote=withdrawn)
        self.assertEqual(revoked["reason"], withdrawn)
        self.assertEqual(revoked["revisionMessageSource"]["messageId"], current["id"])
        self.assertEqual(revoked["messageSource"], saved["messageSource"])

    def test_quote_cannot_invent_rewrite_or_ambiguously_select_user_words(self):
        self.user("保留底座。保留底座。")
        for quote in ("保留其他对象。", "保留底座。", "", None):
            with self.subTest(quote=quote), self.assertRaises(HubFailure):
                self.tool("/api/decisions", self.body, feedbackQuote=quote)
        self.assertEqual(self.writes, [])
