"""Project memory is scoped decisions (#252, ADR-009): where things are, where to look first.

A locator says where one piece of retained project content is, in the user's
words; a later turn that asks for it in other words finds it, and the target
is read again every time. A source policy says where to look first for a
research topic; a turn whose words are about that topic is handed it, and a
new project can hold one from the user's words alone. Neither is a second
store: both are revisioned decisions, retained in their own ``studio-memory``
run so that a build predating them never reads one.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_SCOPED_DECISION
from archflow_studio_api.application.decisions import DECISIONS_RUN_ID, MEMORY_RUN_ID, lexical_terms
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, make_empty_project
from .test_decisions import DecisionFixture, board_source, document_source, message
from .test_documents import two_page_pdf

LOCATOR_WORDS = "项目图框在这份文件里,第一页就是"
POLICY_WORDS = "查材料先去 A、B,别用 C"
WORDS = {"kind": "words"}


def page_target(document: dict, page_index: int | None = 0) -> dict:
    return {"kind": "document", "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": page_index}


class MemoryFixture(DecisionFixture):
    def setUp(self) -> None:
        super().setUp()
        self.page = self.upload(two_page_pdf())

    def locator(self, expect: int = 201, *, label: str = "项目图框", target=None, **overrides) -> dict:
        body = {"rawLanguage": LOCATOR_WORDS, "messageSource": message(1), "disposition": "refer",
                "strength": "hard", "targetRef": "locator:content",
                "scope": {"domain": "locator", "extent": "project"}, "source": document_source(self.page),
                "sourceKind": "agent",
                "typedBinding": {"kind": "locator", "label": label,
                                 "target": page_target(self.page) if target is None else target}}
        body.update(overrides)
        return self.save(expect, **body)

    def policy(self, expect: int = 201, *, binding: dict | None = None, **overrides) -> dict:
        body = {"rawLanguage": POLICY_WORDS, "messageSource": message(2), "disposition": "require",
                "strength": "soft_preference", "targetRef": "research:sources",
                "scope": {"domain": "research", "extent": "project"}, "source": self.design_source(),
                "sourceKind": "agent",
                "typedBinding": binding or {"kind": "source-policy", "topic": "材料", "keys": ["materials"],
                                            "prefer": ["https://www.a-materials.example/db", "B 建材手册"],
                                            "avoid": ["C.example"], "note": None}}
        body.update(overrides)
        return self.save(expect, **body)

    def revise(self, current: dict, *, action: str, expect: int = 201, **body) -> dict:
        response = self.client.post(f"/api/decisions/{current['decisionId']}/revisions", json={
            "projectId": PROJECT_ID, "expectedRevisionRef": current["revisionRef"], "action": action, **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def lookup(self, query: str, client=None, **params) -> list[dict]:
        response = (client or self.client).get("/api/locators", params={"q": query, **params})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["projectId"], PROJECT_ID)
        return response.json()["locators"]


class LocatorTests(MemoryFixture):
    def test_a_locator_from_a_page_is_found_by_other_words_and_read_again_every_time(self) -> None:
        saved = self.locator()
        self.assertEqual(saved["typedBinding"], {"kind": "locator", "label": "项目图框",
                                                 "target": page_target(self.page)})
        self.assertEqual((saved["rawLanguage"], saved["messageSource"], saved["sourceKind"]),
                         (LOCATOR_WORDS, message(1), "agent"))
        board = self.save_board("title-block", "note")
        other = self.locator(label="设计说明", rawLanguage="设计说明是白板上那个框",
                             source=board_source(board, "note"),
                             target={"kind": "board", "revisionSha256": board, "elementId": "note"})

        # A fresh app, no transcript: other words, the same content.
        cold = self.new_client()
        paraphrase = "上次说的那个图框放在哪个文件了?"
        found = self.lookup(paraphrase, cold)
        self.assertEqual([(row["decision"]["decisionId"], row["status"], row["staleReason"]) for row in found],
                         [(saved["decisionId"], "current", None)])
        self.assertIn("图框", found[0]["matchedTerms"])
        # Width and case fold: a full-width query finds an ASCII label.
        latin = self.locator(label="Title Block", rawLanguage="the title block is this page",
                             messageSource=message(3))
        self.assertEqual([row["decision"]["decisionId"] for row in self.lookup("ＴＩＴＬＥ block?", cold)],
                         [latin["decisionId"]])
        self.assertEqual(self.lookup("白板上的设计说明", cold)[0]["decision"]["decisionId"], other["decisionId"])
        self.assertEqual(self.lookup("接着往下调", cold), [])

        # The context read hands the same match for the turn's own words,
        # without adding the locator to the decisions a design turn obeys.
        pack = self.context(cold, utterance=paraphrase)
        self.assertEqual([row["decision"]["decisionId"] for row in pack["locators"]], [saved["decisionId"]])
        self.assertNotIn(saved["decisionId"], {row["decisionId"] for row in pack["scopedDecisions"]})
        self.assertEqual(self.context(cold, utterance="接着往下调")["locators"], [])

        # The bytes change under the registration: the locator is stale, with
        # its reason, and nothing is guessed in its place.
        blob = self.repository.layout.resolve_relative(
            f"objects/sha256/{self.page['assetSha256'][:2]}/{self.page['assetSha256']}")
        blob.write_bytes(b"not the registered pdf")
        stale = self.lookup(paraphrase, self.new_client())
        self.assertEqual([(row["decision"]["decisionId"], row["status"]) for row in stale],
                         [(saved["decisionId"], "stale")])
        self.assertIn("DOCUMENT_DIGEST_MISMATCH", stale[0]["staleReason"])
        self.assertEqual(self.context(self.new_client(), utterance=paraphrase)["locators"], stale)
        # The decision itself is untouched: the words and the pointer stand.
        self.assertIn(saved, self.decisions(self.new_client()))

    def test_a_path_or_url_target_is_refused_with_its_reason(self) -> None:
        for target, reason in (
            ("D:\\项目\\图框.dwg", "machine path"),
            ("/home/architect/图框.pdf", "machine path"),
            ("\\\\server\\share\\图框.pdf", "machine path"),
            ("file:///C:/图框.pdf", "machine path"),
            ("https://example.com/图框.pdf", "URL"),
            ({**page_target(self.page), "runId": "C:\\runs\\docs"}, "machine path"),
            ("项目图框", "retained project content"),
        ):
            with self.subTest(target=target):
                refused = self.locator(422, target=target)
                self.assertEqual(refused["code"], "LOCATOR_TARGET_INVALID")
                self.assertIn(reason, refused["detail"])
                self.assertIn("Register" if reason != "retained project content" else "document", refused["detail"])
        # Content that does not resolve now is refused, not saved as stale.
        missing = self.locator(404, target={"kind": "artifact", "sha256": "d" * 64})
        self.assertEqual(missing["code"], "LOCATOR_TARGET_UNKNOWN")
        self.assertEqual(self.locator(404, target=page_target(self.page, 7))["code"], "LOCATOR_TARGET_UNKNOWN")
        self.assertEqual(self.decisions(), [])

    def test_only_the_users_words_or_a_persons_action_save_one(self) -> None:
        for status, overrides in (
            (422, {"messageSource": None}),                    # an agent without the user's message
            (422, {"sourceKind": "evaluator"}),                # never inferred
            (422, {"sourceKind": "deterministic-rule"}),
            (422, {"strength": "temporary"}),
            (422, {"applicability": "exact-source"}),
            (422, {"disposition": "keep"}),
            (422, {"scope": {"domain": "drawing", "extent": "project"}}),
            (422, {"scope": {"domain": "locator", "extent": "targets", "targetRefs": ["entity:portico-base"]}}),
            (422, {"targetRef": "drawing:hatch"}),
        ):
            with self.subTest(overrides=overrides):
                self.locator(status, **overrides)
        # 'refer' belongs to a locator alone.
        self.save(422, disposition="refer", source=document_source(self.page))
        person = self.locator(sourceKind="human", messageSource=None)
        self.assertEqual(person["sourceKind"], "human")

    def test_one_label_has_one_place_and_moving_it_supersedes(self) -> None:
        first = self.locator()
        clash = self.locator(409, label=" 项目图框 ", messageSource=message(4))
        self.assertEqual(clash["code"], "DECISION_LOCATOR_CONFLICT")
        replacement = {"projectId": PROJECT_ID, "rawLanguage": "图框换成第二页那个", "messageSource": message(5),
                       "disposition": "refer", "strength": "hard", "targetRef": "locator:content",
                       "scope": {"domain": "locator", "extent": "project"}, "source": document_source(self.page, 1),
                       "applicability": "scope", "sourceKind": "agent",
                       "typedBinding": {"kind": "locator", "label": "项目图框", "target": page_target(self.page, 1)}}
        moved = self.revise(first, action="supersede", replacement=replacement, reason="换页")
        found = self.lookup("图框在哪")
        self.assertEqual([(row["decision"]["revisionRef"], row["decision"]["typedBinding"]["target"]["pageIndex"])
                          for row in found], [(moved["revisionRef"], 1)])
        self.revise(moved, action="revoke", reason="不用了")
        self.assertEqual(self.lookup("图框在哪"), [])

    def test_lookup_terms_are_cjk_bigrams_and_folded_words(self) -> None:
        self.assertEqual(lexical_terms("项目图框"), {"项目", "目图", "图框"})
        self.assertEqual(lexical_terms("ＴＩＴＬＥ Block 在哪"), {"title", "block"})
        self.assertEqual(lexical_terms("门"), {"门"})


class SourcePolicyTests(MemoryFixture):
    def research(self, utterance: str, client=None) -> list[dict]:
        return self.context(client, utterance=utterance, decisionContext={"domain": "research"})["scopedDecisions"]

    def test_a_materials_policy_reaches_a_materials_research_turn_and_no_design_turn(self) -> None:
        policy = self.policy()
        self.assertEqual(policy["typedBinding"], {
            "kind": "source-policy", "topic": "材料", "keys": ["materials"],
            "prefer": ["www.a-materials.example", "B 建材手册"], "avoid": ["c.example"], "note": None})
        regulations = self.policy(rawLanguage="规范只查官方的", messageSource=message(3), strength="hard",
                                  binding={"kind": "source-policy", "topic": "防火规范", "keys": ["regulations"],
                                           "prefer": ["官方规范库"], "avoid": [], "note": "以现行版本为准"})
        keep = self.save(rawLanguage="module 就保持 1.2 m,别动", disposition="keep", strength="hard",
                         targetRef="parameter:module", scope={"domain": "design", "extent": "project"},
                         source=self.design_source())

        cold = self.new_client()
        self.assertEqual(self.research("查一下这种砖的材料性能", cold), [policy])
        self.assertEqual(self.research("查一下疏散的防火规范", cold), [regulations])
        self.assertEqual(self.research("找几个类似的住宅案例", cold), [])
        # Never a turn that named design or drawing.
        for body in ({"decisionContext": {"domain": "design"}}, {"decisionContext": {"domain": "drawing"}}):
            with self.subTest(body=body):
                handed = self.context(cold, utterance="查一下这种砖的材料性能", **body)["scopedDecisions"]
                self.assertNotIn(policy["decisionId"], {row["decisionId"] for row in handed})
        # A default read - the Hub's prepared per-turn context - carries the
        # policy its words are about, beside the design decisions, and no other.
        self.assertEqual(self.context(cold, utterance="查一下这种砖的材料性能")["scopedDecisions"], [policy, keep])
        self.assertEqual(self.context(cold, utterance="把檐口压低一点")["scopedDecisions"], [keep])

        # A supersede replaces it; a revoke removes it.
        replacement = {"projectId": PROJECT_ID, "rawLanguage": "材料还是先查 D", "messageSource": message(6),
                       "disposition": "require", "strength": "soft_preference", "targetRef": "research:sources",
                       "scope": {"domain": "research", "extent": "project"}, "source": self.design_source(),
                       "applicability": "scope", "sourceKind": "agent",
                       "typedBinding": {"kind": "source-policy", "topic": "材料", "keys": ["materials"],
                                        "prefer": ["d.example"], "avoid": [], "note": None}}
        replaced = self.revise(policy, action="supersede", replacement=replacement, reason="换来源")
        self.assertEqual(self.research("查一下这种砖的材料性能", self.new_client()), [replaced])
        self.revise(replaced, action="revoke", reason="不用了")
        self.assertEqual(self.research("查一下这种砖的材料性能", self.new_client()), [])

    def test_a_policy_is_the_users_and_names_each_source_once(self) -> None:
        base = {"kind": "source-policy", "topic": "材料", "keys": ["materials"], "prefer": ["a.example"],
                "avoid": [], "note": None}
        for status, overrides in (
            (422, {"sourceKind": "evaluator"}),
            (422, {"messageSource": None}),
            (422, {"strength": "temporary"}),
            (422, {"disposition": "avoid"}),
            (422, {"binding": {**base, "prefer": [], "avoid": []}}),
            (422, {"binding": {**base, "avoid": ["A.example"]}}),
            (422, {"binding": {**base, "keys": ["materials", "materials"]}}),
            (422, {"binding": {**base, "keys": ["weather"]}}),
            (422, {"scope": {"domain": "drawing", "extent": "project"}}),
        ):
            with self.subTest(overrides=overrides):
                self.policy(status, **overrides)
        # A locator domain is not a turn's domain: its words look it up.
        self.context(utterance="图框在哪", decisionContext={"domain": "locator"}, expect=422)

    def test_the_users_words_alone_evidence_a_policy_and_nothing_else(self) -> None:
        saved = self.policy(source=WORDS, targetRef=None)
        self.assertEqual((saved["source"], saved["targetRef"], saved["messageSource"]),
                         (WORDS, "research:sources", message(2)))
        self.assertEqual(self.research("查一下这种砖的材料性能", self.new_client()), [saved])
        # Words are a message: a person's action without one names what it was taken on.
        self.assertIn("messageSource", self.policy(422, source=WORDS, sourceKind="human", messageSource=None)["detail"])
        self.assertEqual(self.policy(source=WORDS, sourceKind="human", messageSource=message(4),
                                     binding={"kind": "source-policy", "topic": "规范", "keys": ["regulations"],
                                              "prefer": ["官方规范库"], "avoid": [], "note": None})["sourceKind"],
                         "human")
        # Only a source policy: a locator, a drawing or a design decision names what it was said about.
        self.locator(422, source=WORDS)
        self.save(422, source=WORDS)
        self.save(422, source=WORDS, disposition="keep", targetRef="parameter:module",
                  scope={"domain": "design", "extent": "project"})
        # A context read names what it is looking at; its words are its utterance.
        self.context(utterance="查材料", decisionContext={"domain": "research", "source": WORDS}, expect=422)

    def test_a_new_project_with_no_design_saves_a_policy_from_the_users_words(self) -> None:
        with TemporaryDirectory() as directory:
            make_empty_project(Path(directory))
            with TestClient(create_app(StudioSettings(project_dir=Path(directory) / PROJECT_ID,
                                                      cad_export="off"))) as client:
                response = client.post("/api/decisions", json={
                    "projectId": PROJECT_ID, "rawLanguage": "以后查材料先去 A 建材库、B 手册,别用 C 网站。",
                    "messageSource": message(1), "disposition": "require", "strength": "soft_preference",
                    "scope": {"domain": "research", "extent": "project"}, "source": WORDS,
                    "applicability": "scope", "sourceKind": "agent",
                    "typedBinding": {"kind": "source-policy", "topic": "材料", "keys": ["materials"],
                                     "prefer": ["A 建材库", "B 手册"], "avoid": ["C 网站"]}})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual([row["decisionId"] for row in client.get("/api/decisions").json()["decisions"]],
                                 [response.json()["decisionId"]])
                # No modeling base appeared to hold it: only the memory run exists.
                runs = Path(directory) / PROJECT_ID / "runs"
                self.assertEqual(sorted(path.name for path in runs.iterdir()), [MEMORY_RUN_ID])


class OmittedTargetTests(MemoryFixture):
    def test_a_locator_and_a_policy_omit_their_one_target(self) -> None:
        locator = self.locator(targetRef=None)
        policy = self.policy(targetRef=None, source=WORDS)
        self.assertEqual((locator["targetRef"], policy["targetRef"]), ("locator:content", "research:sources"))
        # The trial's first save: a locator's own name is not its target.
        self.assertIn("locator:content", self.locator(422, targetRef="locator:项目图框", label="设计说明")["detail"])
        # Every other domain still names what it is about.
        self.assertIn("targetRef", self.save(422, targetRef=None, source=document_source(self.page))["detail"])
        self.save(422, targetRef=None, disposition="keep", scope={"domain": "design", "extent": "project"},
                  source=self.design_source())


class MemoryRunTests(MemoryFixture):
    """A build that predates project memory reads studio-decisions alone and must not meet it."""

    def reviews(self, run_id: str) -> list[tuple[str, str]]:
        """What one run's review area holds, read as an older build would: that run alone."""

        run = self.repository.load_run(run_id)
        refs = self.repository.list_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run_id),
            record_kind=STUDIO_SCOPED_DECISION)
        return sorted((payload["decisionId"], payload["status"])
                      for payload in map(self.repository.load_json, refs))

    def test_memory_lives_in_its_own_run_and_reads_back_with_the_rest(self) -> None:
        hatch = self.save(source=board_source(self.save_board("wall"), "wall"))
        locator = self.locator()
        policy = self.policy(source=WORDS)
        revoked = self.revise(locator, action="revoke", reason="不用了")
        self.assertEqual(self.reviews(DECISIONS_RUN_ID), [(hatch["decisionId"], "active")])
        self.assertEqual(self.reviews(MEMORY_RUN_ID), sorted([
            (locator["decisionId"], "active"), (locator["decisionId"], "revoked"), (policy["decisionId"], "active")]))
        # This build lists them with the rest, exactly as before for callers.
        self.assertEqual(self.decisions(self.new_client()), [hatch, policy, revoked])
        history = self.new_client().get(f"/api/decisions/{locator['decisionId']}").json()["revisions"]
        self.assertEqual([row["status"] for row in history], ["superseded", "revoked"])
        # A supersession keeps its kind, so a chain never spans the two runs.
        crossing = {**self.spec(), "source": board_source(self.board_revision, "wall")}
        refused = self.revise(policy, action="supersede", replacement=crossing, expect=422)
        self.assertIn("keeps its kind", refused["detail"])


class RetainedDecisionTests(MemoryFixture):
    def test_decisions_retained_before_memory_read_back_exactly(self) -> None:
        """A decision written before locators existed reads back exactly as it did."""

        retained = {
            "schema": "StudioScopedDecision@1", "projectId": PROJECT_ID, "decisionId": "retained-hatch",
            "previousRevisionRef": None, "status": "active", "reason": None, "revisionMessageSource": None,
            "createdAt": "2026-09-22T08:00:00+00:00",
            "attribution": {"actorId": "studio:explicit-user-action", "authenticated": False, "origin": "studio"},
            "retainedSourceRefs": ["studio-board"], "rawLanguage": "这面墙不要打填充——留白就行",
            "messageSource": message(1), "disposition": "avoid", "strength": "strong_preference",
            "targetRef": "drawing:hatch", "scope": {"domain": "drawing", "extent": "project", "stageRef": None,
                                                    "targetRefs": None},
            "source": board_source("a" * 64, "wall-outline"), "applicability": "scope", "sourceKind": "human",
            "typedBinding": None,
        }
        run = self.repository.create_run(DECISIONS_RUN_ID)
        ref = self.repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=DECISIONS_RUN_ID),
            record_kind=STUDIO_SCOPED_DECISION, payload=retained)
        stored = self.repository.layout.resolve_relative(ref.relative_path).read_bytes()
        expected = {
            "projectId": PROJECT_ID, "decisionId": "retained-hatch", "revisionRef": ref.uri,
            "previousRevisionRef": None, "status": "active", "rawLanguage": retained["rawLanguage"],
            "messageSource": message(1), "disposition": "avoid", "strength": "strong_preference",
            "targetRef": "drawing:hatch", "scope": retained["scope"], "source": retained["source"],
            "applicability": "scope", "sourceKind": "human", "typedBinding": None,
            "attribution": retained["attribution"], "createdAt": retained["createdAt"], "reason": None,
            "revisionMessageSource": None,
        }
        cold = self.new_client()
        self.assertEqual(self.decisions(cold), [expected])
        self.assertEqual(json.loads(cold.get("/api/decisions/retained-hatch").content)["revisions"], [expected])
        self.assertEqual(self.context(cold, utterance="项目图框在哪")["scopedDecisions"], [expected])
        # Saving memory beside it rewrites nothing already retained.
        self.locator()
        self.policy()
        self.assertEqual(self.repository.layout.resolve_relative(ref.relative_path).read_bytes(), stored)
        self.assertEqual(self.decisions(self.new_client())[0], expected)
