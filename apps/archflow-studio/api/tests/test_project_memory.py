"""Project memory is its own owner (#252, ADR-009): where things are, where to look first.

A locator says where one piece of retained project content is, in the user's
words; a later turn that asks for it in other words finds it, and the target
is read again every time. A source policy says where to look first for a
research topic; a turn whose words are about that topic is handed it, and a
new project can hold one from the user's words alone. Both are memory items
(``studio.memory``) in their own ``studio-memory`` run under their own record
kind, never decisions: a build that predates them never reads one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import textwrap
import unittest

from fastapi.testclient import TestClient

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_MEMORY_RECORD
from archflow_studio_api.application.decisions import DECISIONS_RUN_ID
from archflow_studio_api.application.memory import MEMORY_RUN_ID, lexical_terms
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_empty_project
from .test_decisions import DecisionFixture, board_source, message
from .test_documents import two_page_pdf

LOCATOR_WORDS = "项目图框在这份文件里,第一页就是"
POLICY_WORDS = "查材料先去 A、B,别用 C"
REPOSITORY = Path(__file__).resolve().parents[4]
# The last main before project memory existed: the build an older desktop
# install still runs, and can roll back to.
BEFORE_MEMORY = "2a34db0019578d872aabd0a61b175a93db48c859"


def page_target(document: dict, page_index: int | None = 0) -> dict:
    return {"kind": "document", "runId": document["runId"], "assetSha256": document["assetSha256"],
            "revisionRef": document["revisionRef"], "pageIndex": page_index}


def policy_value(**overrides) -> dict:
    value = {"topic": "材料", "keys": ["materials"],
             "prefer": ["https://www.a-materials.example/db", "B 建材手册"], "avoid": ["C.example"], "note": None}
    value.update(overrides)
    return value


class MemoryFixture(DecisionFixture):
    def setUp(self) -> None:
        super().setUp()
        self.page = self.upload(two_page_pdf())

    def remember(self, body: dict, expect: int = 201, client=None) -> dict:
        response = (client or self.client).post("/api/memory", json={"projectId": PROJECT_ID, **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def locator(self, expect: int = 201, *, label: str = "项目图框", target=None, **overrides) -> dict:
        body = {"kind": "locator", "rawLanguage": LOCATOR_WORDS, "messageSource": message(1), "sourceKind": "agent",
                "value": {"label": label, "target": page_target(self.page) if target is None else target}}
        body.update(overrides)
        return self.remember(body, expect)

    def policy(self, expect: int = 201, *, value: dict | None = None, **overrides) -> dict:
        body = {"kind": "source_policy", "rawLanguage": POLICY_WORDS, "messageSource": message(2),
                "sourceKind": "agent", "value": value or policy_value()}
        body.update(overrides)
        return self.remember(body, expect)

    def revise(self, current: dict, *, action: str, expect: int = 201, **body) -> dict:
        response = self.client.post(f"/api/memory/{current['memoryId']}/revisions", json={
            "projectId": PROJECT_ID, "expectedRevisionRef": current["revisionRef"], "action": action, **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def lookup(self, query: str, client=None, **params) -> list[dict]:
        response = (client or self.client).get("/api/memory/locate", params={"q": query, **params})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["projectId"], PROJECT_ID)
        return response.json()["locators"]

    def memory(self, client=None, **params) -> list[dict]:
        response = (client or self.client).get("/api/memory", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["memory"]

    def handed(self, utterance: str, client=None, **body) -> list[str]:
        return [row["memory"]["memoryId"] for row in self.context(client, utterance=utterance, **body)["memory"]]


class ShapeTests(MemoryFixture):
    def test_an_item_has_the_owners_shape_and_no_computed_fields(self) -> None:
        saved = self.locator(evidenceRefs=[page_target(self.page)])
        self.assertEqual(set(saved), {
            "projectId", "memoryId", "revisionRef", "previousRevisionRef", "version", "status", "key", "kind",
            "scope", "appliesWhen", "value", "authority", "provenance", "attribution", "createdAt", "reason",
            "revisionMessageSource"})
        self.assertEqual((saved["key"], saved["kind"], saved["scope"], saved["authority"], saved["version"],
                          saved["status"]), ("locator:项目图框", "locator", "project", "explicit", 1, "active"))
        self.assertEqual(saved["appliesWhen"], {"domains": [], "topics": ["项目图框"], "keys": [], "stageRef": None})
        self.assertEqual(saved["value"], {"label": "项目图框", "target": page_target(self.page)})
        self.assertEqual(saved["provenance"], {"rawLanguage": LOCATOR_WORDS, "messageSource": message(1),
                                               "sourceKind": "agent", "evidenceRefs": [page_target(self.page)]})
        policy = self.policy()
        self.assertEqual((policy["key"], policy["appliesWhen"]),
                         ("source_policy:材料", {"domains": ["research"], "topics": ["材料"], "keys": ["materials"],
                                                "stageRef": None}))
        self.assertEqual(policy["value"], {"topic": "材料", "keys": ["materials"],
                                           "prefer": ["www.a-materials.example", "B 建材手册"],
                                           "avoid": ["c.example"], "note": None})
        # Reserved kinds, scopes and authorities are named and refused; nothing
        # computes confidence or support yet, so neither is accepted.
        for overrides in ({"kind": "recipe"}, {"kind": "preference"}, {"scope": "user"},
                          {"authority": "inferred"}, {"confidence": 0.9}, {"supportCount": 3}):
            with self.subTest(overrides=overrides):
                self.policy(422, **overrides)
        self.assertEqual([row["memoryId"] for row in self.memory(kind="locator")], [saved["memoryId"]])
        self.assertEqual([row["memoryId"] for row in self.memory(kind="source_policy")], [policy["memoryId"]])

    def test_memory_is_not_a_decision_and_lives_in_its_own_run_and_kind(self) -> None:
        hatch = self.save(source=board_source(self.save_board("wall"), "wall"))
        locator = self.locator()
        policy = self.policy()
        self.assertEqual(self.decisions(self.new_client()), [hatch])
        run = self.repository.load_run(MEMORY_RUN_ID)
        refs = self.repository.list_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=MEMORY_RUN_ID),
            record_kind=STUDIO_MEMORY_RECORD)
        self.assertEqual(sorted(self.repository.load_json(ref)["memoryId"] for ref in refs),
                         sorted([locator["memoryId"], policy["memoryId"]]))
        self.assertTrue(all(ref.uri.startswith(f"project://{PROJECT_ID}/runs/{MEMORY_RUN_ID}/") for ref in refs))
        # And the decisions routes know nothing of it.
        self.assertEqual(self.client.get(f"/api/decisions/{locator['memoryId']}").status_code, 404)
        self.assertEqual(self.client.post("/api/decisions", json=self.spec(disposition="refer")).status_code, 422)
        self.assertEqual(self.client.get("/api/locators", params={"q": "图框"}).status_code, 404)


class LocatorTests(MemoryFixture):
    def test_a_locator_from_a_page_is_found_by_other_words_and_read_again_every_time(self) -> None:
        saved = self.locator()
        board = self.save_board("title-block", "note")
        other = self.locator(label="设计说明", rawLanguage="设计说明是白板上那个框", messageSource=message(3),
                             target={"kind": "board", "revisionSha256": board, "elementId": "note"})

        # A fresh app, no transcript: other words, the same content.
        cold = self.new_client()
        paraphrase = "上次说的那个图框放在哪个文件了?"
        found = self.lookup(paraphrase, cold)
        self.assertEqual([(row["memory"]["memoryId"], row["status"], row["staleReason"]) for row in found],
                         [(saved["memoryId"], "current", None)])
        self.assertIn("图框", found[0]["matchedTerms"])
        # Width and case fold: a full-width query finds an ASCII label.
        latin = self.locator(label="Title Block", rawLanguage="the title block is this page", messageSource=message(4))
        self.assertEqual([row["memory"]["memoryId"] for row in self.lookup("ＴＩＴＬＥ block?", cold)],
                         [latin["memoryId"]])
        self.assertEqual(self.lookup("白板上的设计说明", cold)[0]["memory"]["memoryId"], other["memoryId"])
        self.assertEqual(self.lookup("接着往下调", cold), [])

        # Every context read carries the same match for its own words, in
        # ContextPack.memory and never among the decisions a turn obeys.
        pack = self.context(cold, utterance=paraphrase)
        self.assertEqual([row["memory"]["memoryId"] for row in pack["memory"]], [saved["memoryId"]])
        self.assertEqual(pack["scopedDecisions"], [])
        self.assertEqual(self.handed(paraphrase, cold, decisionContext={"domain": "drawing"}), [saved["memoryId"]])
        self.assertEqual(self.handed("接着往下调", cold), [])

        # The bytes change under the registration: the locator is stale, with
        # its reason, and nothing is guessed in its place.
        blob = self.repository.layout.resolve_relative(
            f"objects/sha256/{self.page['assetSha256'][:2]}/{self.page['assetSha256']}")
        blob.write_bytes(b"not the registered pdf")
        stale = self.lookup(paraphrase, self.new_client())
        self.assertEqual([(row["memory"]["memoryId"], row["status"]) for row in stale],
                         [(saved["memoryId"], "stale")])
        self.assertIn("DOCUMENT_DIGEST_MISMATCH", stale[0]["staleReason"])
        self.assertEqual(self.context(self.new_client(), utterance=paraphrase)["memory"], stale)
        # The item itself is untouched: the words and the pointer stand.
        self.assertIn(saved, self.memory(self.new_client()))

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
        self.assertEqual(self.memory(), [])

    def test_only_the_users_words_or_a_persons_action_save_one(self) -> None:
        for overrides in ({"messageSource": None},           # an agent without the user's message
                          {"sourceKind": "evaluator"},       # never inferred
                          {"sourceKind": "deterministic-rule"},
                          {"authority": "observed"},
                          {"value": policy_value()}):         # a locator's value is {label, target}
            with self.subTest(overrides=overrides):
                self.locator(422, **overrides)
        person = self.locator(sourceKind="human", messageSource=None)
        self.assertEqual(person["provenance"]["sourceKind"], "human")

    def test_one_label_has_one_place_and_moving_it_supersedes(self) -> None:
        first = self.locator()
        clash = self.locator(409, label=" 项目图框 ", messageSource=message(4))
        self.assertEqual(clash["code"], "MEMORY_KEY_CONFLICT")
        replacement = {"projectId": PROJECT_ID, "kind": "locator", "rawLanguage": "图框换成第二页那个",
                       "messageSource": message(5), "sourceKind": "agent",
                       "value": {"label": "项目图框", "target": page_target(self.page, 1)}}
        moved = self.revise(first, action="supersede", replacement=replacement, reason="换页")
        self.assertEqual((moved["memoryId"], moved["version"], moved["previousRevisionRef"]),
                         (first["memoryId"], 2, first["revisionRef"]))
        found = self.lookup("图框在哪")
        self.assertEqual([(row["memory"]["revisionRef"], row["memory"]["value"]["target"]["pageIndex"])
                          for row in found], [(moved["revisionRef"], 1)])
        revoked = self.revise(moved, action="revoke", reason="不用了", revisionMessageSource=message(6))
        self.assertEqual((revoked["status"], revoked["version"], revoked["provenance"]), ("revoked", 3, moved["provenance"]))
        self.assertEqual(self.lookup("图框在哪"), [])
        history = self.client.get(f"/api/memory/{first['memoryId']}").json()["revisions"]
        self.assertEqual([(row["version"], row["status"]) for row in history],
                         [(1, "superseded"), (2, "superseded"), (3, "revoked")])
        self.assertEqual(self.revise(revoked, action="revoke", expect=409)["code"], "MEMORY_REVOKED")
        self.assertEqual(self.revise(first, action="revoke", expect=409)["code"], "MEMORY_STALE")
        # A supersession keeps its kind.
        policy = self.policy()
        crossing = self.revise(policy, action="supersede", replacement=replacement, expect=422)
        self.assertIn("keeps its kind", crossing["detail"])

    def test_lookup_terms_are_cjk_bigrams_and_folded_words(self) -> None:
        self.assertEqual(lexical_terms("项目图框"), {"项目", "目图", "图框"})
        self.assertEqual(lexical_terms("ＴＩＴＬＥ Block 在哪"), {"title", "block"})
        self.assertEqual(lexical_terms("门"), {"门"})


class SourcePolicyTests(MemoryFixture):
    def test_a_materials_policy_reaches_a_turn_about_materials_and_no_other(self) -> None:
        policy = self.policy()
        regulations = self.policy(rawLanguage="规范只查官方的", messageSource=message(3),
                                  value={"topic": "防火规范", "keys": ["regulations"], "prefer": ["官方规范库"],
                                         "avoid": [], "note": "以现行版本为准"})
        keep = self.save(rawLanguage="module 就保持 1.2 m,别动", disposition="keep", strength="hard",
                         targetRef="parameter:module", scope={"domain": "design", "extent": "project"},
                         source=self.design_source())

        cold = self.new_client()
        # The default read - the Hub's prepared per-turn context - carries the
        # policy its words are about, beside the decisions, and no other.
        pack = self.context(cold, utterance="查一下这种砖的材料性能")
        self.assertEqual([row["memory"]["memoryId"] for row in pack["memory"]], [policy["memoryId"]])
        self.assertEqual(pack["memory"][0]["status"], "current")
        self.assertEqual(pack["scopedDecisions"], [keep])
        self.assertEqual(self.handed("查一下疏散的防火规范", cold), [regulations["memoryId"]])
        self.assertEqual(self.handed("找几个类似的住宅案例", cold), [])
        self.assertEqual(self.handed("把檐口压低一点", cold), [])
        # A turn that named design or drawing is not a research turn.
        for domain in ("design", "drawing"):
            with self.subTest(domain=domain):
                self.assertEqual(self.handed("查一下这种砖的材料性能", cold, decisionContext={"domain": domain}), [])

        # A supersede replaces it; a revoke removes it.
        replacement = {"projectId": PROJECT_ID, "kind": "source_policy", "rawLanguage": "材料还是先查 D",
                       "messageSource": message(6), "sourceKind": "agent",
                       "value": policy_value(prefer=["d.example"], avoid=[])}
        replaced = self.revise(policy, action="supersede", replacement=replacement, reason="换来源")
        self.assertEqual(self.context(self.new_client(), utterance="查一下这种砖的材料性能")["memory"][0]["memory"],
                         replaced)
        self.revise(replaced, action="revoke", reason="不用了")
        self.assertEqual(self.handed("查一下这种砖的材料性能", self.new_client()), [])

    def test_a_policy_is_the_users_and_names_each_source_once(self) -> None:
        for overrides in ({"sourceKind": "evaluator"}, {"messageSource": None},
                          {"value": policy_value(prefer=[], avoid=[])},
                          {"value": policy_value(prefer=["a.example"], avoid=["A.example"])},
                          {"value": policy_value(keys=["materials", "materials"])},
                          {"value": policy_value(keys=["weather"])},
                          {"value": {"label": "图框", "target": page_target(self.page)}}):
            with self.subTest(overrides=overrides):
                self.policy(422, **overrides)
        self.policy()
        first = self.memory()
        self.assertEqual(self.policy(409, messageSource=message(3))["code"], "MEMORY_KEY_CONFLICT")
        self.assertEqual(self.memory(), first)

    def test_a_new_project_with_no_design_saves_a_policy_from_the_users_words(self) -> None:
        with TemporaryDirectory() as directory:
            make_empty_project(Path(directory))
            with TestClient(create_app(StudioSettings(project_dir=Path(directory) / PROJECT_ID,
                                                      cad_export="off"))) as client:
                saved = self.remember({
                    "kind": "source_policy", "rawLanguage": "以后查材料先去 A 建材库、B 手册,别用 C 网站。",
                    "messageSource": message(1), "sourceKind": "agent",
                    "value": {"topic": "材料", "keys": ["materials"], "prefer": ["A 建材库", "B 手册"],
                              "avoid": ["C 网站"]}}, client=client)
                self.assertEqual([row["memoryId"] for row in client.get("/api/memory").json()["memory"]],
                                 [saved["memoryId"]])
                self.assertEqual(client.get("/api/decisions").json()["decisions"], [])
                # No modeling base appeared to hold it: only the memory run exists.
                runs = Path(directory) / PROJECT_ID / "runs"
                self.assertEqual(sorted(path.name for path in runs.iterdir()), [MEMORY_RUN_ID])


class CrossVersionTests(MemoryFixture):
    """The build before memory reads a project holding memory, as an older desktop install would."""

    def test_the_build_before_memory_reads_decisions_and_context_of_a_project_holding_memory(self) -> None:
        git = shutil.which("git")
        if git is None or subprocess.run([git, "-C", str(REPOSITORY), "cat-file", "-e", f"{BEFORE_MEMORY}^{{commit}}"],
                                         capture_output=True).returncode != 0:
            raise unittest.SkipTest(f"{BEFORE_MEMORY[:8]} is not in this checkout's history")
        hatch = self.save(source=board_source(self.save_board("wall"), "wall"))
        self.locator()
        self.policy()
        self.assertEqual(sorted(path.name for path in (self.root / PROJECT_ID / "runs").iterdir()
                                if path.name in {DECISIONS_RUN_ID, MEMORY_RUN_ID}), [DECISIONS_RUN_ID, MEMORY_RUN_ID])
        with TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "base.tar"
            with archive.open("wb") as handle:
                subprocess.run([git, "-C", str(REPOSITORY), "archive", "--format=tar", BEFORE_MEMORY,
                                "archflow", "monkeyarch", "monkeydiagram", "monkeymonitor", "governance",
                                "apps/archflow-studio/api"],
                               stdout=handle, check=True)
            with tarfile.open(archive) as tar:
                tar.extractall(base, filter="data")
            script = textwrap.dedent("""
                import json, pathlib, sys
                import archflow, archflow_studio_api
                from fastapi.testclient import TestClient
                base = pathlib.Path(sys.argv[1]).resolve()
                assert all(pathlib.Path(m.__file__).resolve().is_relative_to(base) for m in (archflow, archflow_studio_api))
                from archflow_studio_api.main import create_app
                from archflow_studio_api.settings import StudioSettings

                project_dir, project_id, run_id = sys.argv[2:5]
                with TestClient(create_app(StudioSettings(project_dir=project_dir, cad_export="off"))) as client:
                    decisions = client.get("/api/decisions")
                    state = client.get("/api/state", params={"run": run_id})
                    pack = client.post("/api/intents/context", json={
                        "projectId": project_id, "sourceRunId": run_id, "stateDigest": state.json()["stateDigest"],
                        "utterance": "项目图框在哪?查一下这种砖的材料性能"})
                print(json.dumps({"decisions": [decisions.status_code, decisions.json()],
                                  "pack": [pack.status_code, pack.json()]}))
            """)
            path = os.pathsep.join((str(base), str(base / "apps" / "archflow-studio" / "api")))
            ran = subprocess.run([sys.executable, "-c", script, str(base), str(self.root / PROJECT_ID), PROJECT_ID,
                                  REFERENCE_RUN_ID], capture_output=True, text=True, timeout=300, cwd=directory,
                                 env={**os.environ, "PYTHONPATH": path})
        self.assertEqual(ran.returncode, 0, ran.stderr)
        old = json.loads(ran.stdout.strip().splitlines()[-1])
        self.assertEqual(old["decisions"][0], 200, old["decisions"][1])
        self.assertEqual([row["decisionId"] for row in old["decisions"][1]["decisions"]], [hatch["decisionId"]])
        self.assertEqual(old["pack"][0], 200, old["pack"][1])
        self.assertNotIn("memory", old["pack"][1])
