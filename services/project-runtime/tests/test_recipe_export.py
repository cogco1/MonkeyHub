"""A project recipe travels as a file and arrives in another project as a person's preference (#252).

The export carries the recipe's values and identity and nothing else of the
project it was confirmed in. The import reads the file, checks its content
against its own digest and retains one decision in another project: a
person's ``require``, held as soft_preference for the whole project and
evidenced by that digest. Both projects are real P036 fixtures.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile

from fastapi.testclient import TestClient

from archflow.contracts.canonical import canonical_digest
from archflow.project.refs import record_ref_from_uri
from archflow_studio_api.application.authentication import LOCAL_ACTOR_ID, ORIGIN_STUDIO, ActorAttribution
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.decisions import (
    DECISIONS_RUN_ID,
    RECIPE_EXPORT_SCHEMA,
    import_recipe,
    project_recipe,
    read_recipe_export,
    recipe_export,
    revise_decision,
    save_decision,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import PROJECT_ID, make_project
from .test_decisions import DecisionFixture, document_source, message
from .test_documents import two_page_pdf

HATCH_RECIPE_WORDS = "以后的剖面填充都用 3 mm 间距"
IMPORT_WORDS = "Import drawing recipe as this project's preference: hatchSpacingMm 3 mm."
PERSON = ActorAttribution(LOCAL_ACTOR_ID, False, ORIGIN_STUDIO)


def rehashed(document: dict) -> dict:
    """An export whose digest was recomputed over whatever it now says."""

    body = {key: value for key, value in document.items() if key != "sha256"}
    return {**body, "sha256": canonical_digest(body)}


class RecipeTravelFixture(DecisionFixture):
    """Two real projects: the one a recipe is confirmed in, and the one it travels to."""

    def setUp(self) -> None:
        super().setUp()
        self.page = self.upload(two_page_pdf())
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        self.second_repository, _ = make_project(other)
        self.second = TestClient(create_app(StudioSettings(project_dir=other / PROJECT_ID, cad_export="off")))
        self.addCleanup(self.second.close)

    def recipe(self, graphics: dict | None = None, **overrides) -> dict:
        body = {"rawLanguage": HATCH_RECIPE_WORDS, "disposition": "require", "strength": "strong_preference",
                "targetRef": "drawing:hatch", "source": document_source(self.page),
                "typedBinding": {"kind": "recipe", "graphics": graphics or {"hatchSpacingMm": 3}}}
        body.update(overrides)
        return self.save(**body)

    def revise(self, client: TestClient, current: dict, *, action: str, expect: int = 201, **body) -> dict:
        response = client.post(f"/api/decisions/{current['decisionId']}/revisions", json={
            "projectId": PROJECT_ID, "expectedRevisionRef": current["revisionRef"], "action": action, **body})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def exported(self, decision: dict) -> dict:
        return recipe_export(bound_project(self.client.app.state), decision["decisionId"])

    def imported(self, document, *, source_kind: str = "human"):
        return import_recipe(bound_project(self.second.app.state), document, raw_language=IMPORT_WORDS,
                             source_kind=source_kind, attribution=PERSON)

    def refused(self, call, *args, **kwargs) -> StudioError:
        with self.assertRaises(StudioError) as refusal:
            call(*args, **kwargs)
        return refusal.exception

    def layer(self, client: TestClient) -> dict:
        return {key: (row.value, row.decision_id, row.revision_ref, row.strength)
                for key, row in project_recipe(bound_project(client.app.state)).items()}

    def second_is_untouched(self) -> None:
        self.assertEqual(self.decisions(self.second), [])
        self.assertFalse(self.second_repository.layout.run(DECISIONS_RUN_ID).root.exists())


class RecipeExportTests(RecipeTravelFixture):
    def test_an_export_carries_the_recipe_and_its_identity_and_nothing_else_of_its_project(self) -> None:
        decision = self.recipe()
        before = self.decisions()
        document = self.exported(decision)
        self.assertEqual(document, {
            "schema": RECIPE_EXPORT_SCHEMA,
            "recipe": {"targetRef": "drawing:hatch", "strength": "strong_preference",
                       "graphics": {"hatchSpacingMm": 3.0}},
            "source": {"decisionId": decision["decisionId"],
                       "revisionSha256": record_ref_from_uri(decision["revisionRef"], PROJECT_ID).sha256},
            "sha256": canonical_digest({key: value for key, value in document.items() if key != "sha256"}),
        })
        # Not the project's id, its page, the person's words or who they are,
        # the chat they said it in, the record's path or where the project lives.
        text = json.dumps(document, ensure_ascii=False)
        page = [self.page[key] for key in ("runId", "assetSha256", "revisionRef", "fileName") if self.page[key]]
        private = [PROJECT_ID, decision["rawLanguage"], decision["attribution"]["actorId"],
                   *decision["messageSource"].values(), *page, decision["revisionRef"], self.root.name]
        for value in private:
            with self.subTest(value=value):
                self.assertNotIn(value, text)
        # One revision always exports to the same content; exporting writes nothing.
        self.assertEqual(self.exported(decision), document)
        self.assertEqual(self.decisions(), before)
        self.assertEqual(read_recipe_export(json.loads(json.dumps(document))).sha256, document["sha256"])

        # A superseded recipe travels as its current revision.
        replacement = self.spec(rawLanguage="项目填充改成 2.5 mm", disposition="require", messageSource=message(2),
                                source=document_source(self.page),
                                typedBinding={"kind": "recipe", "graphics": {"hatchSpacingMm": 2.5}})
        changed = self.revise(self.client, decision, action="supersede", replacement=replacement, reason="改口")
        current = self.exported(decision)
        self.assertEqual((current["recipe"]["graphics"], current["source"]["revisionSha256"]),
                         ({"hatchSpacingMm": 2.5}, record_ref_from_uri(changed["revisionRef"], PROJECT_ID).sha256))
        self.assertNotEqual(current["sha256"], document["sha256"])

    def test_only_an_active_recipe_travels(self) -> None:
        binding = bound_project(self.client.app.state)
        words = self.save(source=document_source(self.page))
        self.assertEqual(self.refused(recipe_export, binding, words["decisionId"]).code, "DECISION_INVALID")
        self.assertEqual(self.refused(recipe_export, binding, "no-such-decision").code, "DECISION_NOT_FOUND")
        revoked = self.revise(self.client, self.recipe(), action="revoke", reason="不用了")
        refusal = self.refused(recipe_export, binding, revoked["decisionId"])
        self.assertEqual((refusal.status, refusal.code), (409, "DECISION_REVOKED"))


class RecipeImportTests(RecipeTravelFixture):
    def test_an_import_is_one_persons_preference_for_the_whole_project_evidenced_by_the_export(self) -> None:
        # However the first project held it, the second holds it as a preference.
        document = self.exported(self.recipe(strength="hard"))
        head, branches = self.second_repository.read_head(), self.second_repository.read_design_branches()
        revision = self.imported(document)
        [decision] = self.decisions(self.second)
        self.assertEqual((decision["decisionId"], decision["revisionRef"]), (revision.decision_id, revision.ref))
        expected = {
            "projectId": PROJECT_ID, "status": "active", "rawLanguage": IMPORT_WORDS, "messageSource": None,
            "disposition": "require", "strength": "soft_preference", "targetRef": "drawing:hatch",
            "scope": {"domain": "drawing", "extent": "project", "stageRef": None, "targetRefs": None},
            "source": {"kind": "recipe-export", "exportSha256": document["sha256"]},
            "applicability": "scope", "sourceKind": "human",
            "typedBinding": {"kind": "recipe", "graphics": {"cutLineMm": None, "visibleLineMm": None,
                                                            "hatchSpacingMm": 3.0}},
            "attribution": {"actorId": LOCAL_ACTOR_ID, "authenticated": False, "origin": ORIGIN_STUDIO},
        }
        self.assertEqual({key: decision[key] for key in expected}, expected)
        retained = self.second_repository.load_json(record_ref_from_uri(revision.ref, PROJECT_ID))
        self.assertEqual((retained["source"], retained["retainedSourceRefs"]),
                         ({"kind": "recipe-export", "exportSha256": document["sha256"]}, []))
        # A new drawing there starts from it, and the next turn is handed it.
        self.assertEqual(self.layer(self.second),
                         {"hatchSpacingMm": (3.0, revision.decision_id, revision.ref, "soft_preference")})
        self.assertEqual(self.context(self.second)["scopedDecisions"], [decision])
        # It writes the one decision: nothing in the first project, no design state.
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(self.second_repository.read_head(), head)
        self.assertEqual(self.second_repository.read_design_branches(), branches)

        # A person confirming a value on the project's own page holds it more strongly.
        upload = self.second.post("/api/documents", json={
            "projectId": PROJECT_ID, "fileName": "plan.pdf", "mimeType": "application/pdf",
            "contentBase64": base64.b64encode(two_page_pdf()).decode()})
        self.assertEqual(upload.status_code, 201, upload.text)
        own = self.second.post("/api/decisions", json=self.spec(
            rawLanguage="这个项目填充 4 mm", disposition="require", source=document_source(upload.json()),
            typedBinding={"kind": "recipe", "graphics": {"hatchSpacingMm": 4}}))
        self.assertEqual(own.status_code, 201, own.text)
        self.assertEqual(self.layer(self.second)["hatchSpacingMm"],
                         (4.0, own.json()["decisionId"], own.json()["revisionRef"], "strong_preference"))

    def test_a_changed_or_foreign_export_is_refused_and_nothing_is_retained(self) -> None:
        document = self.exported(self.recipe())
        edited = deepcopy(document)
        edited["recipe"]["graphics"]["hatchSpacingMm"] = 4.0
        recipe = document["recipe"]
        for case in (
            # Changed after export, with or without a digest to match.
            edited,
            {**document, "sha256": "0" * 64},
            # Not this format, even when re-hashed.
            rehashed({**document, "projectId": PROJECT_ID}),
            rehashed({**document, "schema": "DrawingRecipeExport@2"}),
            rehashed({**document, "recipe": {**recipe, "rawLanguage": HATCH_RECIPE_WORDS}}),
            rehashed({**document, "source": {**document["source"], "revisionSha256": "not-a-digest"}}),
            [document],
            # Values a drawing request would refuse, or under another target.
            rehashed({**document, "recipe": {**recipe, "graphics": {"hatchSpacingMm": 0.4}}}),
            rehashed({**document, "recipe": {**recipe, "graphics": {"hatchSpacingMm": "3"}}}),
            rehashed({**document, "recipe": {**recipe, "graphics": {"hatchSpacingMm": None}}}),
            rehashed({**document, "recipe": {**recipe, "graphics": {"cutLineMm": 0.5}}}),
            rehashed({**document, "recipe": {**recipe, "graphics": {}}}),
            rehashed({**document, "recipe": {**recipe, "strength": "temporary"}}),
        ):
            with self.subTest(case=case):
                refusal = self.refused(self.imported, case)
                self.assertEqual((refusal.status, refusal.code), (422, "RECIPE_EXPORT_INVALID"))
        self.assertIn("changed after it was exported", self.refused(self.imported, edited).detail)
        self.second_is_untouched()

    def test_only_a_persons_confirmation_imports_a_recipe(self) -> None:
        document = self.exported(self.recipe())
        for source_kind in ("agent", "evaluator", "deterministic-rule"):
            with self.subTest(source_kind=source_kind):
                refusal = self.refused(self.imported, document, source_kind=source_kind)
                self.assertEqual((refusal.status, refusal.code), (422, "DECISION_INVALID"))
                self.assertIn("person's explicit confirmation", refusal.detail)
        self.second_is_untouched()

    def test_a_key_the_project_already_holds_as_a_preference_is_the_recipe_conflict(self) -> None:
        document = self.exported(self.recipe())
        first = self.imported(document)
        # The same export again, or another value of the same key.
        other = self.exported(self.recipe(graphics={"hatchSpacingMm": 4}, strength="hard", messageSource=message(2)))
        for case in (document, other):
            with self.subTest(sha256=case["sha256"]):
                refusal = self.refused(self.imported, case)
                self.assertEqual((refusal.status, refusal.code), (409, "DECISION_RECIPE_CONFLICT"))
                self.assertIn(first.decision_id, refusal.detail)
        # Other keys travel beside it.
        lines = self.exported(self.recipe(targetRef="drawing:lineweight", messageSource=message(3),
                                          graphics={"cutLineMm": 0.5, "visibleLineMm": 0.25}))
        self.imported(lines)
        # Revoking the imported preference makes room for another value.
        [held] = [row for row in self.decisions(self.second) if row["decisionId"] == first.decision_id]
        self.revise(self.second, held, action="revoke", reason="换成另一版")
        replaced = self.imported(other)
        self.assertEqual(self.layer(self.second)["hatchSpacingMm"],
                         (4.0, replaced.decision_id, replaced.ref, "soft_preference"))

    def test_only_an_import_writes_an_export_source_and_the_decision_revises_like_any_other(self) -> None:
        document = self.exported(self.recipe())
        source = {"kind": "recipe-export", "exportSha256": document["sha256"]}
        spec = self.spec(rawLanguage=IMPORT_WORDS, disposition="require", strength="soft_preference", source=source,
                         typedBinding={"kind": "recipe", "graphics": {"hatchSpacingMm": 3}})
        # The decision route names a board, a page or a design run, never an export.
        self.assertEqual(self.second.post("/api/decisions", json=spec).status_code, 422)
        binding = bound_project(self.second.app.state)
        claimed = {**spec, "scope": {**spec["scope"], "stageRef": None, "targetRefs": None}}
        self.assertIn("only by importing", self.refused(save_decision, binding, claimed, PERSON).detail)
        self.second_is_untouched()

        imported = self.imported(document)
        [current] = self.decisions(self.second)
        # A supersession cannot cite the export either: only the import has read it.
        refusal = self.refused(revise_decision, binding, imported.decision_id, expected_revision_ref=imported.ref,
                               action="supersede", reason="换", replacement=claimed, attribution=PERSON)
        self.assertEqual((refusal.status, refusal.code), (422, "DECISION_INVALID"))
        # Revoking keeps its words and its source; a new drawing is back to the default.
        revoked = self.revise(self.second, current, action="revoke", reason="不要这个偏好")
        self.assertEqual((revoked["status"], revoked["source"], revoked["rawLanguage"]),
                         ("revoked", source, IMPORT_WORDS))
        self.assertEqual(self.layer(self.second), {})


class RecipeTransferRouteTests(RecipeTravelFixture):
    def export_route(self, recipe):
        return self.client.get(f"/api/decisions/{recipe['decisionId']}/recipe-export",
                               params={"expectedRevisionRef": recipe["revisionRef"]})

    def import_body(self, document):
        return {"projectId": PROJECT_ID, "content": json.dumps(document), "confirmed": True,
                "sourceKind": "human", "rawLanguage": IMPORT_WORDS}

    def test_inspection_does_not_save_and_confirmed_import_uses_the_boundary_actor(self):
        recipe = self.recipe()
        response = self.export_route(recipe)
        self.assertEqual(response.status_code, 200, response.text)
        document = json.loads(response.json()["content"])
        self.assertEqual(document, self.exported(recipe))
        inspect = self.second.post("/api/drawing-recipes/inspect", json={"projectId": PROJECT_ID, "content": json.dumps(document)})
        self.assertEqual(inspect.status_code, 200, inspect.text)
        preview = inspect.json()
        self.assertEqual((preview["sourceDecisionId"], preview["sourceRevisionSha256"], preview["exportSha256"]),
                         (recipe["decisionId"], document["source"]["revisionSha256"], document["sha256"]))
        self.assertEqual(preview["importStrength"], "soft_preference")
        self.second_is_untouched()
        for confirmed in (False, "true", 1, None):
            body = {**self.import_body(document), "confirmed": confirmed}
            self.assertEqual(self.second.post("/api/drawing-recipes/import", json=body).status_code, 422)
        for fields in ({"sourceKind": "agent"}, {"attribution": {"actorId": "pretend"}}):
            self.assertEqual(self.second.post("/api/drawing-recipes/import", json={**self.import_body(document), **fields}).status_code, 422)
        self.second_is_untouched()
        self.second.app.state.managed_instance_id = "isolated-hub"
        imported = self.second.post("/api/drawing-recipes/import", json=self.import_body(document))
        self.assertEqual(imported.status_code, 201, imported.text)
        decision = imported.json()
        self.assertEqual(decision["attribution"], {"actorId": LOCAL_ACTOR_ID, "authenticated": False, "origin": "hub"})
        self.assertEqual(decision["source"], {"kind": "recipe-export", "exportSha256": document["sha256"]})
        conflict = self.second.post("/api/drawing-recipes/import", json=self.import_body(document))
        self.assertEqual((conflict.status_code, conflict.json()["code"]), (409, "DECISION_RECIPE_CONFLICT"))
        self.assertEqual(self.decisions(self.second), [decision])

    def test_selected_revision_must_still_be_current_and_files_cannot_change_before_import(self):
        recipe = self.recipe()
        document = json.loads(self.export_route(recipe).json()["content"])
        self.revise(self.client, recipe, action="revoke", reason="withdraw")
        stale = self.export_route(recipe)
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "DECISION_STALE"))
        tampered = deepcopy(document)
        tampered["recipe"]["graphics"]["hatchSpacingMm"] = 4
        for path in ("inspect", "import"):
            body = self.import_body(tampered) if path == "import" else {"projectId": PROJECT_ID, "content": json.dumps(tampered)}
            refused = self.second.post(f"/api/drawing-recipes/{path}", json=body)
            self.assertEqual((refused.status_code, refused.json()["code"]), (422, "RECIPE_EXPORT_INVALID"))
            mismatch = self.second.post(f"/api/drawing-recipes/{path}", json={**body, "projectId": "another-project"})
            self.assertEqual((mismatch.status_code, mismatch.json()["code"]), (403, "PROJECT_MISMATCH"))
        self.second_is_untouched()
        for content in ("not json", "[]", "null"):
            refused = self.second.post("/api/drawing-recipes/inspect", json={"projectId": PROJECT_ID, "content": content})
            self.assertEqual((refused.status_code, refused.json()["code"]), (422, "RECIPE_EXPORT_INVALID"))
