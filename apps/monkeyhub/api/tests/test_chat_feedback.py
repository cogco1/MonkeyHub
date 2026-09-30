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
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from monkeyhub_api.chat import store as chat
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
        if base == self.hub and path == "/api/settings/apps":
            return {"libraryDir": getattr(self, "library_dir", None)}
        if base == getattr(self, "library_base", None):
            # The library project's own Runtime: the only place its skills are read.
            self.library_reads.append(path)
            response = self.library.get(path)
            if response.is_error:
                raise HubFailure(response.status_code, response.json()["code"], response.json()["detail"])
            return response.json()
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

    def test_chat_saves_and_revokes_project_memory_from_the_users_words(self):
        """#252: memory is its own owner; the chat binds the user's words to it as it does for feedback."""

        self.user("以后查材料先去 A 建材库、B 手册，别用 C 网站。")
        policy = {"projectId": self.session["projectId"], "kind": "source_policy",
                  "value": {"topic": "材料", "keys": ["materials"], "prefer": ["A 建材库", "B 手册"], "avoid": ["C 网站"]}}
        for change in ({"rawLanguage": "made up"}, {"sourceKind": "human"},
                       {"messageSource": {"sessionId": "other", "messageId": "fake"}}, {"kind": "preference"}):
            with self.subTest(change=change), self.assertRaises(HubFailure) as refused:
                self.tool("/api/memory", {**policy, **change})
            self.assertEqual(refused.exception.error.code, "CHAT_FEEDBACK_INVALID")
        self.assertEqual(self.writes, [])
        saved = self.tool("/api/memory", deepcopy(policy))
        self.assertEqual(saved["provenance"], {
            "rawLanguage": self.session["messages"][-1]["content"], "sourceKind": "agent", "evidenceRefs": [],
            "messageSource": {"sessionId": self.session["id"], "messageId": self.session["messages"][-1]["id"]}})
        self.assertEqual((saved["kind"], saved["authority"], saved["scope"]), ("source_policy", "explicit", "project"))
        # A later turn's prepared (default) context carries it when its words are about materials.
        context = {"projectId": self.session["projectId"], "utterance": "查一下这种砖的材料性能",
                   **{key: self.body["source"][key] for key in ("sourceRunId", "stateDigest")}}
        with TestClient(create_app(self.settings)) as cold:
            pack = cold.post("/api/intents/context", json=context).json()
            self.assertEqual([row["memory"]["memoryId"] for row in pack["memory"]], [saved["memoryId"]])
            self.assertEqual(pack["scopedDecisions"], [])
            self.assertEqual(cold.post("/api/intents/context", json={**context, "utterance": "把檐口压低一点"})
                             .json()["memory"], [])
        # The chat reads memory, and a where-is question, through the product tool.
        self.assertEqual([row["memoryId"] for row in self.tool("/api/memory", method="GET")["memory"]], [saved["memoryId"]])
        self.assertEqual(self.tool("/api/memory/locate?q=图框在哪", method="GET")["locators"], [])
        # A locator passes the chat's gate; the Runtime still checks its target (a path here, refused there).
        with self.assertRaises(HubFailure) as target:
            self.tool("/api/memory", {"projectId": self.session["projectId"], "kind": "locator",
                                      "value": {"label": "项目图框", "target": "D:/drawings/frame.dwg"}})
        self.assertEqual(target.exception.error.code, "LOCATOR_TARGET_INVALID")
        self.assertEqual(len(self.writes), 2, "the policy and the locator reached the Runtime; the refused ones did not")
        # Decisions stay avoid/keep only: memory is not a decision disposition.
        for disposition in ("refer", "require"):
            with self.subTest(disposition=disposition), self.assertRaises(HubFailure):
                self.tool("/api/decisions", {**self.body, "disposition": disposition})
        # Revoked from the user's later words, which the chat binds as the reason.
        later = self.user("撤销刚才的查材料来源。")
        with self.assertRaises(HubFailure):
            self.tool(f"/api/memory/{saved['memoryId']}/revisions",
                      {"projectId": self.session["projectId"], "expectedRevisionRef": saved["revisionRef"],
                       "action": "revoke", "reason": "made up"})
        revoked = self.tool(f"/api/memory/{saved['memoryId']}/revisions",
                            {"projectId": self.session["projectId"], "expectedRevisionRef": saved["revisionRef"],
                             "action": "revoke"})
        self.assertEqual((revoked["status"], revoked["reason"], revoked["revisionMessageSource"], revoked["provenance"]),
                         ("revoked", later["content"], {"sessionId": self.session["id"], "messageId": later["id"]},
                          saved["provenance"]))
        # An item a person saved in the Studio is theirs: the chat cannot revoke it.
        person = self.client.post("/api/memory", json={**policy, "value": {**policy["value"], "topic": "规范",
                                                                            "keys": ["regulations"]},
                                                       "rawLanguage": "规范只查官方的", "sourceKind": "human"}).json()
        with self.assertRaises(HubFailure) as theirs:
            self.tool(f"/api/memory/{person['memoryId']}/revisions",
                      {"projectId": self.session["projectId"], "expectedRevisionRef": person["revisionRef"],
                       "action": "revoke"})
        self.assertEqual(theirs.exception.error.code, "CHAT_FEEDBACK_UNAVAILABLE")

    def test_chat_saves_a_recipe_pinned_to_the_librarys_current_skill_version(self):
        """#252 3c: a recipe names a library skill; the Hub fills the exact version from the library's index."""

        words = self.user("以后出平面图前都按事务所的填充标准检查一下。")
        recipe = {"projectId": self.session["projectId"], "kind": "recipe",
                  "value": {"task": "出平面图前检查填充", "skill": "monkeyhub-library:hatch-review"},
                  "appliesWhen": {"domains": ["drawing"]}}

        # No library set: refused with the reason, and nothing reaches the Runtime.
        with self.assertRaises(HubFailure) as unset:
            self.tool("/api/memory", deepcopy(recipe))
        self.assertEqual(unset.exception.error.code, "CHAT_RECIPE_NO_LIBRARY")
        self.assertIn("No skill library is set", unset.exception.error.detail)

        library_dir = Path(self.project).parent / "skill-library"
        FilesystemProjectRepository.initialize(library_dir, project_id="skill-library",
                                               initial_state={"project_id": "skill-library", "version": 0})
        self.library = TestClient(create_app(StudioSettings(project_dir=library_dir, cad_export="off")))
        self.addCleanup(self.library.close)
        self.library_dir, self.library_base, self.library_reads = str(library_dir), "http://127.0.0.1:8702", []
        bound = []

        def bound_studio(hub, chat_id, *args, project_id=None, project_dir=None, **kwargs):
            bound.append(project_id)
            if project_id == "skill-library":
                return self.library_base, {}
            return self.base, deepcopy(self.session)
        patch.object(chat, "_bound_studio", side_effect=bound_studio).start()
        prepared = []
        patch.object(chat, "_prepare_studio", side_effect=lambda hub, session, budget: prepared.append(session)).start()

        # A library without that skill: refused, naming what it holds.
        self.library.post("/api/skills", json={"projectId": "skill-library", "name": "section-sheet",
                                               "description": "Lay out a section sheet.", "body": "A1.\n"})
        with self.assertRaises(HubFailure) as unknown:
            self.tool("/api/memory", deepcopy(recipe))
        self.assertEqual(unknown.exception.error.code, "CHAT_RECIPE_SKILL_UNKNOWN")
        self.assertIn("'hatch-review'", unknown.exception.error.detail)
        self.assertIn("section-sheet", unknown.exception.error.detail)
        self.assertEqual(self.writes, [])

        # The library holds hatch-review v1: the stored value is skill:hatch-review@1.
        self.library.post("/api/skills", json={"projectId": "skill-library", "name": "hatch-review",
                                               "description": "Review a plan's hatching.", "body": "ZX7.\n"})
        saved = self.tool("/api/memory", deepcopy(recipe))
        self.assertEqual(saved["value"], {"task": "出平面图前检查填充", "skill": "skill:hatch-review@1", "note": None})
        self.assertEqual((saved["kind"], saved["appliesWhen"]["domains"]), ("recipe", ["drawing"]))
        self.assertEqual(saved["provenance"]["rawLanguage"], words["content"])
        self.assertEqual(saved["provenance"]["messageSource"],
                         {"sessionId": self.session["id"], "messageId": words["id"]})
        # Read through the library's own Runtime, index only; the chat project's Studio never read it.
        self.assertEqual(prepared[-1]["projectId"], "skill-library")
        self.assertEqual(set(self.library_reads), {"/api/skills"})
        self.assertNotIn("/api/skills", [path for path, *_ in self.writes])
        # A named version that is not the current one is refused; a bare or ref name is pinned alike.
        self.library.post("/api/skills", json={"projectId": "skill-library", "name": "hatch-review",
                                               "description": "Review a plan's hatching.", "body": "ZX8.\n",
                                               "supersedesVersion": 1})
        with self.assertRaises(HubFailure) as old:
            self.tool("/api/memory", {**deepcopy(recipe), "value": {"task": "出立面图", "skill": "hatch-review@1"}})
        self.assertEqual(old.exception.error.code, "CHAT_RECIPE_SKILL_VERSION")
        other = self.tool("/api/memory", {**deepcopy(recipe), "value": {"task": "出立面图",
                                                                         "skill": "skill:hatch-review"}})
        self.assertEqual(other["value"]["skill"], "skill:hatch-review@2")
        with self.assertRaises(HubFailure) as spelled:
            self.tool("/api/memory", {**deepcopy(recipe), "value": {"task": "出立面图", "skill": "../escape"}})
        self.assertEqual(spelled.exception.error.code, "CHAT_RECIPE_SKILL_INVALID")

    def test_memory_schema_is_the_runtime_contract_with_narrow_chat_inputs(self):
        self.user("项目图框在第一页。")
        saved = self.client.post("/api/memory", json={
            "projectId": self.session["projectId"], "kind": "source_policy", "rawLanguage": "查材料先去 A",
            "sourceKind": "human", "value": {"topic": "材料", "keys": ["materials"], "prefer": ["A"]}}).json()
        for path, key, hidden in (
            ("/api/memory", "MemoryRequestDto", {"rawLanguage", "messageSource", "sourceKind"}),
            (f"/api/memory/{saved['memoryId']}/revisions", "MemoryRevisionRequestDto",
             {"reason", "revisionMessageSource", "replacement"}),
        ):
            schema = self.tool(path, name="studio_schema")["components"]["schemas"][key]
            self.assertFalse(hidden.intersection(schema["properties"]))
            self.assertFalse(hidden.intersection(schema.get("required", [])))
        self.assertEqual(schema["properties"]["action"]["enum"], ["revoke"])
        self.assertIn("/api/memory/locate", chat._GUIDES["/api/memory"])

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
