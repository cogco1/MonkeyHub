"""Who asked for a drawing revision, and what the revision chain says was corrected (05-S2).

Correction capture keeps one memory owner, the decisions, so nothing here is
recorded: every pair, class and suggestion is derived again from the retained
revisions. The pure cases build revisions as the document listing reads them.
The real cases draw an imported model's cut plans, so every revision is a real
one; the Agent's requests go through the Hub's own studio_request, answered by
this Studio in process exactly as the forwarded request would reach it.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi.testclient import TestClient

from archflow.adapters.model_formats import ThreeDM
from archflow.adapters.occt_backend import occt_available
from archflow.adapters.three_dm_inspector import inspect_three_dm_index
from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.artifacts import DocumentPage, ModelSource, SourceDocument
from archflow_studio_api.application.drawing_corrections import (
    classify, corrections, recipe_diff, recipe_suggestions,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project
from .test_agent_drawing_dressing import files, hub_chat

HUB = "http://127.0.0.1:8790"
STUDIO = "http://127.0.0.1:8791"
MODEL = Path(__file__).parent / "fixtures/model-source-a.3dm"

PLAN = {"kind": "cut-plan", "name": "plan",
        "frame": {"name": "plan", "origin": [0, 0, 1.2], "crop_uv": [0, 0, 4, 3], "far_depth": 1.2, "scale": "1:50"},
        "graphics": {"cutLineMm": .35, "visibleLineMm": .18, "hatchSpacingMm": 2},
        "hiddenObjectIds": [], "dimensions": []}
PERSON = {"id": "person-a", "assetId": "person-plan", "positionUv": [2, 2], "size": .6, "flipped": False,
          "anchorObjectId": None}
TREE = {"id": "tree-a", "assetId": "tree-plan", "positionUv": [1, 1], "size": 1, "flipped": False, "anchorObjectId": None}
# A cleanup report as a V0 receipt keeps it: counts per rule, no object.
V0_CLEANUP = {"tolerance": .025, "input_lines": 40, "output_lines": 31, "micro": 2, "collinear": 3, "cut_precedence": 4,
              "duplicate": 0, "hidden_under_cut": 0}


def recipe(**graphics):
    """The plan recipe with these graphics values."""
    changed = deepcopy(PLAN)
    changed["graphics"].update(graphics)
    return changed


def revision(drawing, ref, view, previous=None, *, at, kind=None, model="model-a"):
    """One retained cut-plan revision as list_documents reads it."""
    return SourceDocument(
        PROJECT_ID, "drawing-run", ref[-1] * 64, f"{drawing}.png", "image/png", 100, (DocumentPage(0, 800, 600),),
        model_source=ModelSource(model, "d" * 64, "e" * 64), drawing_id=drawing, revision_ref=ref,
        view_recipe=view, generated_at=f"2026-09-25T10:{at:02d}:00+00:00", previous_revision_ref=previous,
        source_kind=kind)


def nothing_covered(pair, key):
    return False


class RecipeDiffTests(unittest.TestCase):
    def test_each_paper_value_object_and_frame_change_is_one_entry_by_what_it_names(self):
        before = deepcopy(PLAN)
        before.update(hiddenObjectIds=["obj-1"], dressing=[PERSON, TREE], dimensions=[
            {"id": "door-width", "entityRef": "entity:wall", "openingId": "door", "placement": {"offsetMm": 8}}])
        after = deepcopy(before)
        after["graphics"].update(hatchSpacingMm=3, beyond={"fade": .4},
                                 hatch={"byMaterial": {"timber": {"spacingMm": 3, "angleDeg": 135, "poche": False}}})
        after["hiddenObjectIds"] = ["obj-2"]
        after["dimensions"][0]["placement"]["offsetMm"] = 4
        person_b = {**PERSON, "id": "person-b"}
        after["dressing"] = [{**PERSON, "positionUv": [3, 2]}, person_b]
        after["frame"].update(crop_uv=[-1, 0, 5, 3], scale="1:100")
        after["follow"] = "frozen"
        self.assertEqual(recipe_diff(before, after), {
            "dimensions.door-width.placement.offsetMm": [8, 4],
            "dressing.person-a.positionUv": [[2, 2], [3, 2]],
            "dressing.person-b": [None, person_b],
            "dressing.tree-a": [TREE, None],
            "follow": [None, "frozen"],
            "frame.crop_uv": [[0, 0, 4, 3], [-1, 0, 5, 3]],
            "frame.scale": ["1:50", "1:100"],
            "graphics.beyond.fade": [None, .4],
            "graphics.hatch.byMaterial.timber": [None, {"spacingMm": 3, "angleDeg": 135, "poche": False}],
            "graphics.hatchSpacingMm": [2, 3],
            "hiddenObjectIds.obj-1": [True, False],
            "hiddenObjectIds.obj-2": [False, True],
        })
        self.assertEqual(list(recipe_diff(before, after)), sorted(recipe_diff(before, after)))

    def test_the_same_values_differ_in_nothing(self):
        self.assertEqual(recipe_diff(PLAN, deepcopy(PLAN)), {})
        # An integer and a float of the same value are the same paper value, and
        # an absent list holds what an empty one holds.
        self.assertEqual(recipe_diff(recipe(hatchSpacingMm=2), recipe(hatchSpacingMm=2.0)), {})
        self.assertEqual(recipe_diff({**PLAN, "dressing": []}, {key: value for key, value in PLAN.items()
                                                                  if key != "hiddenObjectIds"}), {})


class ClassifyTests(unittest.TestCase):
    def test_each_change_is_the_first_class_that_holds(self):
        timber = {"spacingMm": 3.0, "angleDeg": 135.0, "poche": False}
        person_b = {**PERSON, "id": "person-b"}
        for diff, cause, expected in (
            ({"graphics.hatchSpacingMm": [2, 3]}, "source", "compiler_defect"),
            ({"graphics.hatch.byMaterial.timber": [None, timber]}, "representation", "semantic_rule"),
            ({"graphics.hatch.byMaterial.timber": [None, timber], "graphics.hatchSpacingMm": [2, 3]},
             "representation", "semantic_rule"),
            ({"graphics.cutLineMm": [.35, .5]}, "representation", "recipe"),
            ({"graphics.beyond.fade": [None, .4]}, "representation", "recipe"),
            ({"graphics.cutLineMm": [.35, .5], "hiddenObjectIds.obj-1": [False, True]}, "representation", "recipe"),
            ({"dressing.person-b": [None, person_b]}, "representation", "recipe"),
            ({"dressing.person-b": [None, person_b], "dressing.tree-a": [TREE, None]}, "representation",
             "local_override"),
            ({"dressing.person-a.positionUv": [[2, 2], [3, 2]], "dressing.person-a.flipped": [False, True]},
             "representation", "local_override"),
            ({"dressing.person-a.anchorObjectId": [None, "obj-1"]}, "representation", "local_override"),
            ({"hiddenObjectIds.obj-1": [False, True]}, "representation", "local_override"),
            ({"frame.crop_uv": [[0, 0, 4, 3], [-1, 0, 5, 3]], "frame.scale": ["1:50", "1:100"]}, "representation",
             "local_override"),
            ({"dimensions.door-width.placement.offsetMm": [8, 4]}, "representation", "local_override"),
            ({}, "representation", "local_override"),
        ):
            with self.subTest(diff=diff, cause=cause):
                self.assertEqual(classify(diff, None, cause), expected)

    def test_v0_cannot_tell_that_a_hidden_object_was_one_the_cleanup_flagged(self):
        # The report counts the lines each rule dropped and names no object, so
        # hiding only what the cleanup flagged cannot be told from any other
        # hide: it stays a local override, and only a source rebuild is a
        # compiler defect.
        hide = {"hiddenObjectIds.obj-1": [False, True]}
        for cleanup in (V0_CLEANUP, None):
            with self.subTest(cleanup=cleanup):
                self.assertEqual(classify(hide, cleanup, "representation"), "local_override")
                self.assertEqual(classify(hide, cleanup, "source"), "compiler_defect")


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        self.a1 = revision("plan-a", "rev-a1", PLAN, at=1)
        self.a2 = revision("plan-a", "rev-a2", recipe(hatchSpacingMm=3), "rev-a1", at=2, kind="human")
        self.a3 = revision("plan-a", "rev-a3", recipe(hatchSpacingMm=3, cutLineMm=.5), "rev-a2", at=9, kind="human")
        self.b1 = revision("plan-b", "rev-b1", PLAN, at=3)
        # A request that does not say who asked still counts: only an agent's never does.
        self.b2 = revision("plan-b", "rev-b2", recipe(hatchSpacingMm=4), "rev-b1", at=4)
        self.b3 = revision("plan-b", "rev-b3", recipe(hatchSpacingMm=4, cutLineMm=.45), "rev-b2", at=10, kind="human")
        self.c1 = revision("plan-c", "rev-c1", PLAN, at=5)
        self.c2 = revision("plan-c", "rev-c2", recipe(hatchSpacingMm=5), "rev-c1", at=6, kind="agent")
        self.d1 = revision("plan-d", "rev-d1", PLAN, at=7)
        self.d2 = revision("plan-d", "rev-d2", recipe(hatchSpacingMm=5), "rev-d1", at=8, kind="human", model="model-b")
        self.e1 = revision("plan-e", "rev-e1", PLAN, at=11)
        self.e2 = revision("plan-e", "rev-e2", recipe(hatchSpacingMm=1.5), "rev-e1", at=12, kind="human")
        self.documents = [self.a1, self.a2, self.a3, self.b1, self.b2, self.b3, self.c1, self.c2, self.d1, self.d2,
                          self.e1, self.e2]

    def evidence(self, *pairs):
        return [{"drawingId": after.drawing_id, "beforeRevisionRef": before.revision_ref,
                 "afterRevisionRef": after.revision_ref} for before, after in pairs]

    def offer(self, key, direction, value, drawings, evidence, page):
        return {"suggestionId": canonical_digest({"field": key, "direction": direction, "value": value,
                                                  "drawingIds": drawings}),
                "field": key, "direction": direction, "value": value, "drawingIds": drawings, "evidence": evidence,
                "page": {"runId": page.run_id, "assetSha256": page.asset_sha256, "revisionRef": page.revision_ref,
                         "pageIndex": 0}}

    def test_each_revision_pairs_with_the_one_it_names_in_the_order_drawn(self):
        pairs = corrections(self.documents)
        self.assertEqual([(pair.before.revision_ref, pair.after.revision_ref) for pair in pairs],
                         [("rev-a1", "rev-a2"), ("rev-a2", "rev-a3"), ("rev-b1", "rev-b2"), ("rev-b2", "rev-b3"),
                          ("rev-c1", "rev-c2"), ("rev-d1", "rev-d2"), ("rev-e1", "rev-e2")])
        self.assertEqual({pair.after.revision_ref: (pair.cause, pair.correction_class) for pair in pairs}["rev-d2"],
                         ("source", "compiler_defect"))
        # Only a named previous revision of the same drawing makes a pair.
        stray = revision("plan-f", "rev-f1", recipe(hatchSpacingMm=3), "rev-a1", at=13, kind="human")
        orphan = revision("plan-g", "rev-g2", recipe(hatchSpacingMm=3), "rev-g1", at=14, kind="human")
        self.assertEqual(corrections([*self.documents, stray, orphan]), pairs)

    def test_the_same_change_by_people_on_two_drawings_is_one_offer_citing_its_exact_pairs(self):
        self.assertEqual(recipe_suggestions(corrections(self.documents), nothing_covered), [
            # At the value of the group's most recent revision, and on its page.
            self.offer("cutLineMm", "increase", .45, ["plan-a", "plan-b"],
                       self.evidence((self.a2, self.a3), (self.b2, self.b3)), self.b3),
            # 2 to 3 and 2 to 4 are one direction; the agent's plan-c, the source
            # rebuild of plan-d and the single decrease on plan-e are not evidence.
            self.offer("hatchSpacingMm", "increase", 4.0, ["plan-a", "plan-b"],
                       self.evidence((self.a1, self.a2), (self.b1, self.b2)), self.b2),
        ])

    def test_one_drawing_or_a_covered_field_is_no_offer_and_the_output_is_deterministic(self):
        pairs = corrections(self.documents)
        self.assertEqual(recipe_suggestions([pair for pair in pairs if pair.after.drawing_id == "plan-a"],
                                            nothing_covered), [])
        # A key an active recipe already gives a drawing is that decision's to change.
        covered = recipe_suggestions(pairs, lambda pair, key: key == "hatchSpacingMm")
        self.assertEqual([offer["field"] for offer in covered], ["cutLineMm"])
        some = recipe_suggestions(pairs, lambda pair, key: pair.after.drawing_id == "plan-a")
        self.assertEqual(some, [])
        expected = recipe_suggestions(pairs, nothing_covered)
        shuffled = list(self.documents)
        for seed in range(5):
            random.Random(seed).shuffle(shuffled)
            with self.subTest(seed=seed):
                self.assertEqual(recipe_suggestions(corrections(shuffled), nothing_covered), expected)
                self.assertEqual(corrections(shuffled), pairs)


class ImportedPlans(unittest.TestCase):
    """One project, one imported model, and the cut plans a person or the Agent asks for."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.settings = StudioSettings(project_dir=self.project, reference_run=REFERENCE_RUN_ID, cad_export="off")
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.asset = self.upload(MODEL.read_bytes())

    def upload(self, data):
        response = self.client.post("/api/model-assets", json={
            "projectId": PROJECT_ID, "fileName": "model.3dm", "contentBase64": base64.b64encode(data).decode()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def plan(self, drawing, previous=None, *, asset=None, **changes):
        """A cut-plan request for one drawing of an imported model, continuing ``previous`` when given."""
        asset = asset or self.asset
        return {"projectId": PROJECT_ID, "drawingId": drawing, "cutHeight": 1.2, "bottom": 0, "scaleDenominator": 50,
                "sourceAsset": {"runId": asset["runId"], "assetSha256": asset["sha256"]},
                **({} if previous is None else {"previousRevisionRef": previous["revisionRef"]}), **changes}

    def draw(self, drawing, previous=None, **changes):
        """One revision, asked for directly, as the Drawing canvas asks."""
        response = self.client.post("/api/drawings/plans", json=self.plan(drawing, previous, **changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def agent(self, body):
        """One cut-plan request exactly as the Agent's CLI makes it, through the Hub's studio_request."""
        chat, failure = hub_chat()
        project_id, project_dir = chat._project(str(self.project))
        session = {"id": str(uuid4()), "status": "running", "projectId": project_id, "projectDir": project_dir,
                   "messages": [{"id": str(uuid4()), "role": "user", "content": "Open up the hatch on this plan."}]}
        runtime = uuid5(NAMESPACE_URL, f"{project_id}:{os.path.normcase(str(Path(project_dir).resolve()))}")
        admitted = f"/api/runtime/projects/{runtime}/studio"

        def forward(base, path, method="GET", body=None, timeout=None, *, headers=None):
            if path == f"/api/chat/sessions/{session['id']}":
                return session
            if path.startswith("/api/apps?"):
                return [{"appId": "monkeyarch", "state": "running", "apiUrl": STUDIO + "/", "processId": 7}]
            if path == "/api/health":
                return {"processId": 7, "sourceRevision": "same-revision"}
            if path == "/api/project":
                return {"projectId": project_id, "projectDir": project_dir}
            # The plan write, admitted by the Hub's runtime and forwarded as it holds it.
            self.assertEqual((base, method, path), (HUB, "POST", admitted + "/api/drawings/plans"))
            reply = self.client.post("/api/drawings/plans", json=body)
            if reply.status_code >= 400:
                raise failure(reply.status_code, reply.json().get("code", "CHAT_TOOL_FAILED"), str(reply.json().get("detail")))
            return reply.json()

        with patch.object(chat, "_request_json", side_effect=forward):
            return chat.call_tool(HUB, session["id"], "studio_request",
                                  {"method": "POST", "path": "/api/drawings/plans", "body": body})

    def corrections(self, drawing=None, client=None):
        response = (client or self.client).get("/api/drawings/corrections", params={
            "projectId": PROJECT_ID, **({} if drawing is None else {"drawingId": drawing})})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class WhoAskedTests(ImportedPlans):
    def test_a_revision_keeps_whether_a_person_or_the_agent_asked_and_one_that_did_not_say_reads_null(self):
        # A request that names no kind is retained as every revision drawn before
        # sourceKind existed was: without it.
        unknown = self.draw("plan-a")
        person = self.draw("plan-a", unknown, hatchSpacingMm=3, sourceKind="human")
        agent = self.agent(self.plan("plan-a", person, hatchSpacingMm=4))
        self.assertEqual([row["sourceKind"] for row in (unknown, person, agent)], [None, "human", "agent"])
        receipts = [self.repository.load_json(record_ref_from_uri(row["revisionRef"], PROJECT_ID))
                    for row in (unknown, person, agent)]
        self.assertEqual([receipt.get("sourceKind") for receipt in receipts], [None, "human", "agent"])
        self.assertNotIn("sourceKind", person["viewRecipe"])
        self.assertEqual(agent["attribution"]["origin"], "studio", "who asked is still the boundary's own record")
        # The kind never makes or tells revisions apart: the Agent asking for
        # exactly what a person drew gets that person's revision, as they asked.
        with patch("archflow_studio_api.application.drawing_plans.freeze_cut_plan",
                   side_effect=AssertionError("an identical request is the retained revision")):
            self.assertEqual(self.agent(self.plan("plan-a", unknown, hatchSpacingMm=3)), person)
        with TestClient(create_app(self.settings)) as client:
            cold = {row["revisionRef"]: row for row in client.get("/api/documents").json()["documents"]}
        self.assertEqual([cold[row["revisionRef"]] for row in (unknown, person, agent)], [unknown, person, agent])
        for kind in ("robot", "", "Human"):
            response = self.client.post("/api/drawings/plans", json=self.plan("plan-a", agent, sourceKind=kind))
            self.assertEqual(response.status_code, 422, response.text)


@unittest.skipUnless(occt_available(), "cadquery-ocp is not installed")
class CorrectionReadTests(ImportedPlans):
    def test_two_drawings_opening_the_hatch_are_one_offer_until_a_person_saves_it_and_reading_writes_nothing(self):
        a1 = self.draw("plan-a")
        a2 = self.draw("plan-a", a1, hatchSpacingMm=3, sourceKind="human")
        b1 = self.draw("plan-b")
        b2 = self.draw("plan-b", b1, hatchSpacingMm=4, sourceKind="human", reason="The hatch reads too dense.")
        before = files(self.project)
        read = self.corrections("plan-b")
        self.assertEqual((read["projectId"], read["drawingId"]), (PROJECT_ID, "plan-b"))
        self.assertEqual(read["pairs"], [{
            "drawingId": "plan-b", "beforeRevisionRef": b1["revisionRef"], "afterRevisionRef": b2["revisionRef"],
            "cause": "representation", "origin": "studio", "sourceKind": "human", "reason": "The hatch reads too dense.",
            "class": "recipe", "diff": {"graphics.hatchSpacingMm": [2, 4.0]}}])
        [offer] = read["suggestions"]
        self.assertEqual({key: offer[key] for key in ("field", "direction", "value", "drawingIds")},
                         {"field": "hatchSpacingMm", "direction": "increase", "value": 4.0,
                          "drawingIds": ["plan-a", "plan-b"]})
        self.assertEqual(offer["evidence"], [
            {"drawingId": "plan-a", "beforeRevisionRef": a1["revisionRef"], "afterRevisionRef": a2["revisionRef"]},
            {"drawingId": "plan-b", "beforeRevisionRef": b1["revisionRef"], "afterRevisionRef": b2["revisionRef"]}])
        self.assertEqual(offer["page"], {"runId": b2["runId"], "assetSha256": b2["assetSha256"],
                                         "revisionRef": b2["revisionRef"], "pageIndex": 0})
        # Without a drawing it is the project's offers alone, and every read is the same read.
        self.assertEqual(self.corrections(), {"projectId": PROJECT_ID, "drawingId": None, "pairs": [],
                                              "suggestions": read["suggestions"]})
        self.assertEqual(self.corrections("plan-b"), read)
        self.assertEqual(self.corrections("no-such-plan")["pairs"], [])
        self.assertEqual(files(self.project), before, "a correction read writes nothing")
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(self.corrections("plan-b", client), read)
        refused = self.client.get("/api/drawings/corrections", params={"projectId": "another-project"})
        self.assertEqual((refused.status_code, refused.json()["code"]), (403, "PROJECT_MISMATCH"))
        # Saving the offer is a person's decision citing its page; then the
        # recipe gives the value and nothing is offered for it any more.
        saved = self.client.post("/api/decisions", json={
            "projectId": PROJECT_ID, "rawLanguage": "Open up the hatch on this project's plans.",
            "disposition": "require", "strength": "strong_preference", "targetRef": "drawing:hatch",
            "scope": {"domain": "drawing", "extent": "project"}, "source": {"kind": "document", **offer["page"]},
            "applicability": "scope", "sourceKind": "human",
            "typedBinding": {"kind": "recipe", "graphics": {offer["field"]: offer["value"]}}})
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertEqual(self.corrections("plan-b")["suggestions"], [])

    def test_a_hidden_object_is_a_local_override_and_is_never_offered(self):
        hidden = inspect_three_dm_index(MODEL.read_bytes())["objects"][0]["object_id"]
        for drawing in ("plan-a", "plan-b"):
            self.draw(drawing, self.draw(drawing), hiddenObjectIds=[hidden], sourceKind="human")
        read = self.corrections("plan-b")
        [pair] = read["pairs"]
        self.assertEqual((pair["cause"], pair["class"], pair["diff"]),
                         ("representation", "local_override", {f"hiddenObjectIds.{hidden}": [False, True]}))
        self.assertEqual(read["suggestions"], [])
        # V0 cannot say the hidden object was one the cleanup flagged: the
        # report this revision's receipt keeps counts lines and names none.
        cleanup = self.repository.load_json(record_ref_from_uri(pair["afterRevisionRef"], PROJECT_ID))["cleanup"]
        self.assertTrue(all(isinstance(value, (int, float)) for value in cleanup.values()), cleanup)
        self.assertEqual(classify(pair["diff"], cleanup, pair["cause"]), "local_override")

    def test_only_a_representation_change_no_agent_asked_for_counts_toward_an_offer(self):
        a1 = self.draw("plan-a")
        a2 = self.draw("plan-a", a1, hatchSpacingMm=3, sourceKind="human")
        # The same change on another drawing that also moved to another model
        # followed its source: it is no correction.
        adapter = ThreeDM()
        revised = adapter.read(MODEL.read_bytes())
        revised.meshes = revised.meshes[1:]
        other = self.upload(adapter.write(revised))
        b1 = self.draw("plan-b")
        b2 = self.draw("plan-b", b1, asset=other, hatchSpacingMm=5, sourceKind="human")
        # The Agent's reading of the same correction is not the architect's.
        c1 = self.draw("plan-c")
        c2 = self.agent(self.plan("plan-c", c1, hatchSpacingMm=5))
        # A drawing made closer together is the other direction, not more of the same.
        e1 = self.draw("plan-e")
        self.draw("plan-e", e1, hatchSpacingMm=1.5, sourceKind="human")
        pairs = {drawing: self.corrections(drawing)["pairs"] for drawing in ("plan-b", "plan-c")}
        self.assertEqual([(row["afterRevisionRef"], row["cause"], row["class"], row["sourceKind"]) for row in pairs["plan-b"]],
                         [(b2["revisionRef"], "source", "compiler_defect", "human")])
        self.assertEqual([(row["afterRevisionRef"], row["cause"], row["class"], row["sourceKind"]) for row in pairs["plan-c"]],
                         [(c2["revisionRef"], "representation", "recipe", "agent")])
        self.assertEqual(self.corrections()["suggestions"], [])
        # A second drawing whose request did not say who asked counts: only an agent's never does.
        d1 = self.draw("plan-d")
        d2 = self.draw("plan-d", d1, hatchSpacingMm=2.5)
        [offer] = self.corrections()["suggestions"]
        self.assertEqual((offer["field"], offer["direction"], offer["value"], offer["drawingIds"]),
                         ("hatchSpacingMm", "increase", 2.5, ["plan-a", "plan-d"]))
        self.assertEqual([row["afterRevisionRef"] for row in offer["evidence"]], [a2["revisionRef"], d2["revisionRef"]])
