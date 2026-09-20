"""Drawing on the model: a profile and a height, through the proposal path.

What the tests check is that a drawn action is the *same* design edit the
record already knows — one ``Element@1`` row whose producer is ``prism`` — and
that it therefore keeps everything that already holds for an edit: one exact
base, an editable outline and height afterwards, and the existing candidate
route as the only thing that runs.
"""

from pathlib import Path
import math
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest

SQUARE = [[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]]


class SketchTestCase(unittest.TestCase):
    """One real project, drawn on at the base its projection answers with."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def draw(self, **body: object) -> tuple[int, dict]:
        payload: dict[str, object] = {
            "stateDigest": self.state_digest,
            "componentId": "portico",
            "elementId": "portico-porch",
            "profile": SQUARE,
            "height": 2.4,
            "baseLevel": "level-ground",
        }
        payload.update(body)
        response = self.client.post("/api/proposals/sketch", json=payload)
        return response.status_code, response.json()

    def test_a_drawn_profile_and_height_become_one_editable_element(self) -> None:
        status, proposal = self.draw()
        self.assertEqual(status, 201, proposal)
        self.assertEqual(proposal["baseStateDigest"], self.state_digest)
        self.assertEqual(proposal["change"]["kind"], "edit_components")
        [entity] = proposal["change"]["edits"]["entities"]
        self.assertEqual(entity["entity_id"], "portico-porch")
        self.assertEqual(entity["schema"], "Element@1")
        # The outline and the height stay the record's own parameters, which is
        # what makes them changeable later rather than baked into geometry.
        self.assertEqual(entity["fields"]["producer"], "prism")
        self.assertEqual(entity["fields"]["params"]["profile"], SQUARE)
        self.assertEqual(entity["fields"]["params"]["height"], 2.4)
        self.assertEqual(entity["fields"]["references"], {"base": {"level": "level-ground"}})
        [change] = [row for row in proposal["change"]["changes"] if row["entityId"] == "portico-porch"]
        self.assertEqual(change["action"], "add")
        # The same proposal shape the candidate route already runs.
        self.assertEqual(
            self.client.get(f"/api/proposals/{proposal['proposalId']}").json()["proposalId"],
            proposal["proposalId"],
        )

    def test_the_same_element_id_changes_the_outline_and_the_height(self) -> None:
        first = self.draw()[1]
        self.assertEqual(first["change"]["changes"][0]["action"], "add")
        taller = [[0.0, 0.0], [5.0, 0.0], [5.0, 2.0], [0.0, 2.0]]
        status, again = self.draw(elementId="portico-base", profile=taller, height=0.9)
        self.assertEqual(status, 201, again)
        [entity] = again["change"]["edits"]["entities"]
        self.assertEqual(entity["fields"]["params"], {"profile": taller, "height": 0.9})
        [change] = [row for row in again["change"]["changes"] if row["entityId"] == "portico-base"]
        self.assertEqual(change["action"], "update", "an existing element is changed, not duplicated")

    def test_an_element_may_stand_on_another_elements_published_top(self) -> None:
        status, proposal = self.draw(baseDatum="portico-base-top", baseLevel=None)
        self.assertEqual(status, 201, proposal)
        [entity] = proposal["change"]["edits"]["entities"]
        self.assertEqual(entity["fields"]["references"], {"base": {"datum": "portico-base-top"}})

    def test_a_drawing_against_another_state_is_refused_as_stale(self) -> None:
        status, body = self.draw(stateDigest="0" * 64)
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "STALE_BASE")
        self.assertIn("/api/state", body["detail"])

    def test_open_paths_keep_their_points_and_only_a_finished_candidate_writes(self) -> None:
        head = self.repository.read_head().version
        runs_before = sorted(path.name for path in (self.root / PROJECT_ID / "runs").iterdir())
        for points in ([[0, 0], [3, 4]], [[0, 0], [1, 1], [2, 0]], SQUARE):
            with self.subTest(points=points):
                status, proposal = self.draw(profile=points, closed=False, height=0)
                self.assertEqual(status, 201, proposal)
                [entity] = proposal["change"]["edits"]["entities"]
                self.assertEqual(entity["fields"]["producer"], "curve")
                self.assertEqual(entity["fields"]["params"], {"profile": points})
                self.assertEqual(proposal["baseStateDigest"], self.state_digest)
        self.assertEqual(self.repository.read_head().version, head)
        self.assertEqual(sorted(path.name for path in (self.root / PROJECT_ID / "runs").iterdir()), runs_before)

    def test_open_paths_refuse_height_and_degenerate_segments_before_running(self) -> None:
        for invalid in ({"height": 1}, {"profile": [[0, 0]]},
                        {"profile": [[0, 0], [0, 0]]}, {"profile": [[0, 0], [1, 0], [0, 0]]}):
            with self.subTest(invalid=invalid):
                status, _ = self.draw(**{"closed": False, "height": 0, **invalid})
                self.assertEqual(status, 422)

    def test_a_profile_that_is_not_a_profile_is_refused_before_anything_runs(self) -> None:
        for invalid, why in (
            ({"profile": [[0.0, 0.0], [1.0, 0.0]]}, "two points are not a profile"),
            ({"profile": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.0]]}, "a repeated point"),
            ({"baseLevel": None}, "no base at all"),
            ({"baseDatum": "portico-base-top"}, "two bases at once"),
        ):
            with self.subTest(why=why):
                status, _ = self.draw(**invalid)
                self.assertEqual(status, 422)

    def test_an_unknown_base_is_refused_by_the_record_not_by_the_drawing(self) -> None:
        status, body = self.draw(baseLevel="level-that-does-not-exist")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")

    def test_zero_height_creates_a_real_editable_face_and_negative_height_reverses_pull(self) -> None:
        status, face = self.draw(height=0)
        self.assertEqual(status, 201, face)
        fields = face["change"]["edits"]["entities"][0]["fields"]
        self.assertEqual(fields["producer"], "planar-surface")
        self.assertNotIn("height", fields["params"])
        self.assertEqual(fields["params"]["profile"], SQUARE + [SQUARE[0]])
        status, negative = self.draw(height=-2)
        self.assertEqual(status, 201, negative)
        params = negative["change"]["edits"]["entities"][0]["fields"]["params"]
        self.assertEqual(params["height"], 2)
        self.assertEqual(params["work_plane"]["normal"], [0, -1, 0])

    def test_direct_actions_read_the_definition_and_preserve_exact_base(self) -> None:
        for route, action in (("transform", {"kind": "copy", "translation": [5, 0, 0]}),
                              ("push-pull", {"distance": 0.4})):
            body = {"stateDigest": self.state_digest, "elementId": "portico-base", **action}
            response = self.client.post(f"/api/proposals/{route}", json=body)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["change"]["kind"], "edit_components")
            stale = self.client.post(f"/api/proposals/{route}", json={**body, "stateDigest": "0" * 64})
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(stale.json()["code"], "STALE_BASE")

    def test_direct_transform_refuses_dependencies_and_cannot_detach_a_host(self) -> None:
        before = self.client.get("/api/state").json()
        runs_before = sorted(path.name for path in (self.root / PROJECT_ID / "runs").iterdir())
        head = self.repository.read_head().version
        for element, kind, expected_code in (("portico-base", "move", "ELEMENT_HAS_DEPENDENTS"),
                                              ("portico-cornice", "rotate", "ELEMENT_HAS_DEPENDENTS"),
                                              ("portico-cornice", "copy", "DIRECT_EDIT_UNSUPPORTED")):
            with self.subTest(element=element, kind=kind):
                response = self.client.post("/api/proposals/transform", json={
                    "stateDigest": self.state_digest, "elementId": element, "kind": kind,
                    "translation": [2, 0, 0], "angleDegrees": 90,
                })
                self.assertIn(response.status_code, (409, 422), response.text)
                self.assertEqual(response.json()["code"], expected_code)
        self.assertEqual(self.client.get("/api/state").json()["stateDigest"], before["stateDigest"])
        self.assertEqual(self.repository.read_head().version, head)
        self.assertEqual(sorted(path.name for path in (self.root / PROJECT_ID / "runs").iterdir()), runs_before)


class SketchCandidateTestCase(unittest.TestCase):
    """The drawn action, run the only way anything runs here: as a candidate."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def test_a_drawn_prism_runs_through_the_existing_candidate_route(self) -> None:
        drawn = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.state_digest, "componentId": "portico",
            "elementId": "portico-porch", "profile": SQUARE, "height": 2.4,
            "baseLevel": "level-ground",
        })
        self.assertEqual(drawn.status_code, 201, drawn.text)
        started = self.client.post(f"/api/proposals/{drawn.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job_id = started.json()["jobId"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the drawn candidate never finished")
        self.assertEqual(job["status"], "succeeded", job)
        run_id = job["candidateId"]
        candidate = self.client.get(f"/api/candidates/{run_id}")
        self.assertEqual(candidate.status_code, 200, candidate.text)
        self.assertTrue(candidate.json()["changedVsProjection"], "a new element changes the design")
        # The element the drawing authored is in the run's own record.
        state = self.client.get(f"/api/state?run={run_id}").json()
        drawn_ids = [row["elementId"] for row in state.get("elements", []) if row.get("elementId")]
        self.assertIn("portico-porch", drawn_ids, state)


class SketchContinuationTestCase(unittest.TestCase):
    """Several finished actions in a row, each continuing on the last run."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        self.head = self.repository.read_head().version

    def digest_of(self, run_id: str | None) -> str:
        query = f"?run={run_id}" if run_id else ""
        state = self.client.get(f"/api/state{query}")
        self.assertEqual(state.status_code, 200, state.text)
        return state.json()["stateDigest"]

    def draw(self, *, run: str | None, **body: object) -> str:
        """One finished action on top of ``run``; answers the run it produced."""

        payload: dict[str, object] = {"stateDigest": self.digest_of(run), "componentId": "portico"}
        if run is not None:
            payload["sourceRunId"] = run
        payload.update(body)
        drawn = self.client.post("/api/proposals/sketch", json=payload)
        self.assertEqual(drawn.status_code, 201, drawn.text)
        started = self.client.post(f"/api/proposals/{drawn.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job_id = started.json()["jobId"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("a drawn candidate never finished")
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def elements_of(self, run_id: str) -> dict[str, dict]:
        state = self.client.get(f"/api/state?run={run_id}")
        self.assertEqual(state.status_code, 200, state.text)
        return {row["elementId"]: row for row in state.json()["elements"]}

    def test_three_actions_in_a_row_each_continue_on_the_run_before(self) -> None:
        first = self.draw(run=None, elementId="block-a", profile=SQUARE, height=3.0,
                          baseLevel="level-ground")
        self.assertIn("block-a", self.elements_of(first))

        # Stack the second on the first element's own published top.
        second = self.draw(run=first, elementId="block-b", profile=SQUARE, height=1.5,
                           baseDatum="block-a-top")
        both = self.elements_of(second)
        self.assertIn("block-a", both)
        self.assertIn("block-b", both, "the second action keeps what the first made")

        # Change the first block's outline and height, still continuing forward.
        wider = [[0.0, 0.0], [6.0, 0.0], [6.0, 2.0], [0.0, 2.0]]
        third = self.draw(run=second, elementId="block-a", profile=wider, height=4.0,
                          baseLevel="level-ground")
        after = self.elements_of(third)
        self.assertIn("block-b", after, "changing one form does not drop the other")
        record = self.client.get(f"/api/candidates/{third}")
        self.assertEqual(record.status_code, 200, record.text)
        self.assertTrue(record.json()["changedVsProjection"])
        self.assertNotEqual(self.digest_of(third), self.digest_of(second))

        # Nothing here published anything: the project's own version stands.
        self.assertEqual(self.repository.read_head().version, self.head)

    def test_going_back_to_an_earlier_run_and_carrying_on_from_it(self) -> None:
        first = self.draw(run=None, elementId="block-a", profile=SQUARE, height=3.0,
                          baseLevel="level-ground")
        second = self.draw(run=first, elementId="block-b", profile=SQUARE, height=1.5,
                           baseDatum="block-a-top")
        self.assertIn("block-b", self.elements_of(second))

        # Undo, as this project means it: continue from the run before, which
        # is still there. Nothing is deleted and nothing is rewritten.
        undone = self.draw(run=first, elementId="block-c", profile=SQUARE, height=2.0,
                           baseDatum="block-a-top")
        carried = self.elements_of(undone)
        self.assertIn("block-a", carried)
        self.assertIn("block-c", carried)
        self.assertNotIn("block-b", carried, "the abandoned action is not carried forward")
        # The run that was stepped back from is still readable, unchanged.
        self.assertIn("block-b", self.elements_of(second))
        self.assertEqual(self.repository.read_head().version, self.head)


class SketchNewComponentTestCase(unittest.TestCase):
    """A component nobody has built before: refused, or built and exported."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)

    def digest(self, run: str | None = None) -> str:
        query = f"?run={run}" if run else ""
        return self.client.get(f"/api/state{query}").json()["stateDigest"]

    def draw(self, **body: object):
        payload: dict[str, object] = {"stateDigest": self.digest(), "profile": SQUARE, "height": 2.4,
                                      "baseLevel": "level-ground"}
        payload.update(body)
        response = self.client.post("/api/proposals/sketch", json=payload)
        return response.status_code, response.json()

    def run_candidate(self, proposal_id: str) -> dict:
        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started.json()['jobId']}").json()
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.05)
        raise AssertionError("the candidate never finished")

    def exported_spans(self, run_id: str) -> dict[str, list[float]]:
        """Read what the run saved, through archflow's own ``.3dm`` inspector.

        The saved file is the evidence here, so it is read the way the rest of
        the system reads it: the inspector owns rhino3dm and reports named
        world bounds per object.
        """

        models = sorted((self.project / "runs" / run_id).rglob("*.3dm"))
        self.assertTrue(models, f"the run exported no model under runs/{run_id}")
        spans: dict[str, list[float]] = {}
        for path in models:
            for row in inspect_three_dm(path).named_object_bboxes:
                bbox = row["bbox"]
                spans[str(row["name"])] = sorted(
                    round(high - low, 3) for low, high in zip(bbox["min"], bbox["max"])
                )
        return spans

    def test_a_component_no_seat_builds_is_refused_before_anything_runs(self) -> None:
        # The failure this reproduces: a component the record would happily
        # carry, that no seat produces. It used to run, report success, and
        # export nothing of it.
        status, body = self.draw(componentId="building", elementId="small-house-main")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "COMPONENT_NOT_BUILT")
        self.assertIn("portico", body["detail"], body["detail"])
        self.assertIn("Nothing was run", body["detail"])

    def test_a_new_component_with_no_stated_kind_is_asked_what_it_is(self) -> None:
        status, body = self.draw(componentId="small-house", parentComponentId="portico",
                                 elementId="small-house-main")
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "COMPONENT_KIND_REQUIRED")
        self.assertIn("semanticKind", body["detail"])

    def test_a_new_component_under_a_built_one_is_created_and_exported(self) -> None:
        status, proposal = self.draw(componentId="small-house", parentComponentId="portico", semanticKind="building",
                                     elementId="small-house-main", height=3.3)
        self.assertEqual(status, 201, proposal)
        kinds = {entity["entity_id"]: entity["schema"] for entity in proposal["change"]["edits"]["entities"]}
        self.assertEqual(kinds.get("small-house"), "Component@1", "the new component is authored")
        self.assertEqual(kinds.get("small-house-main"), "Element@1")

        job = self.run_candidate(proposal["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        run_id = job["candidateId"]
        # The record carries it...
        state = self.client.get(f"/api/state?run={run_id}").json()
        self.assertIn("small-house-main", [row["elementId"] for row in state["elements"]])
        # ...and so does what the run exported, which is the half that was missing.
        spans = self.exported_spans(run_id)
        names = sorted(spans)
        self.assertIn("obj-small-house-main", names, f"the new element is not in the export: {names}")
        self.assertEqual(spans["obj-small-house-main"], sorted([3.0, 2.0, 3.3]),
                         "the exported solid is the profile and height that were asked for")
        # The objects that were already there are still there.
        for existing in ("obj-portico-base", "obj-portico-cornice"):
            self.assertIn(existing, names, f"{existing} was dropped")

    def test_a_run_that_built_none_of_its_own_change_is_not_a_candidate(self) -> None:
        """The failure the user met: the record carried it, the export had none of it.

        The drawing route refuses this before anything runs, so this drives the
        same operator straight at the candidate layer — which is where a run
        that produced nothing of its own change has to say so.
        """

        from archflow_studio_api.application.binding import ProjectBinding
        from archflow_studio_api.application.candidate import run_operator
        from archflow_studio_api.transport.errors import StudioError
        from archflow.state.state_record import Entity, StateRecordEditKind, StateRecordOperator

        settings = StudioSettings(cad_export="occt", project_dir=self.project)
        binding = ProjectBinding.open(settings)
        from archflow_studio_api.application.projection import project_state

        record = project_state(binding).record
        operator = StateRecordOperator(
            kind=StateRecordEditKind.EDIT_COMPONENTS,
            base_record_digest=record.digest,
            base_state_digest=record.state_digest,
            entities=(
                Entity("small-house-main", "Element@1", {
                    "component_id": "building", "producer": "prism",
                    "references": {"base": {"level": "level-ground"}},
                    "params": {"profile": SQUARE, "height": 3.3},
                }, parent_id="building"),
            ),
        )
        with self.assertRaises(StudioError) as refused:
            run_operator(binding, settings, operator, "studio-cand-unowned-check")
        self.assertEqual(refused.exception.code, "COMPONENT_NOT_BUILT")
        self.assertIn("building", refused.exception.detail)

    def test_continuing_on_that_run_changes_the_new_element_and_keeps_the_rest(self) -> None:
        first = self.draw(componentId="small-house", parentComponentId="portico", semanticKind="building",
                          elementId="small-house-main", height=3.3)[1]
        run_one = self.run_candidate(first["proposalId"])["candidateId"]
        taller = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.digest(run_one), "sourceRunId": run_one,
            "componentId": "small-house", "elementId": "small-house-main",
            "profile": SQUARE, "height": 4.2, "baseLevel": "level-ground",
        })
        self.assertEqual(taller.status_code, 201, taller.text)
        job = self.run_candidate(taller.json()["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        spans = self.exported_spans(job["candidateId"])
        self.assertEqual(spans["obj-small-house-main"], sorted([3.0, 2.0, 4.2]), "the height change is in the export")
        self.assertEqual(spans["obj-portico-base"], sorted([4.0, 2.0, 0.6]), "the old objects are unchanged")


class SketchDirectGeometryTestCase(unittest.TestCase):
    setUp = SketchNewComponentTestCase.setUp
    digest = SketchNewComponentTestCase.digest
    run_candidate = SketchNewComponentTestCase.run_candidate

    def action(self, route: str, run: str | None = None, **body) -> str:
        response = self.client.post(f"/api/proposals/{route}", json={
            "stateDigest": self.digest(run), **({"sourceRunId": run} if run else {}), **body,
        })
        self.assertEqual(response.status_code, 201, response.text)
        job = self.run_candidate(response.json()["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def bounds(self, run: str, name: str) -> tuple[list[float], list[float]]:
        found = {}
        for path in sorted((self.project / "runs" / run).rglob("*.3dm")):
            for row in inspect_three_dm(path).named_object_bboxes:
                found[str(row["name"])] = row["bbox"]
        self.assertIn(name, found, found)
        return tuple([round(c, 5) for c in found[name][key]] for key in ("min", "max"))

    def semantic(self, edit: dict, run: str | None = None) -> str:
        response = self.client.post("/api/proposals", json={
            "stateDigest": self.digest(run), **({"sourceRunId": run} if run else {}),
            "semanticEdit": edit, "keep": ["entity:portico-base", "parameter:plinth"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        job = self.run_candidate(response.json()["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def record(self, run: str):
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.projection import project_state

        return project_state(bound_project(self.client.app.state), run).record

    def loft(self, *, bound: bool = False) -> dict:
        left, right = ("@loft_left", "@loft_right") if bound else (0, 3)
        return {"entity_id": "editable-loft", "schema": "Element@1", "parent_id": "portico", "fields": {
            "component_id": "portico", "producer": "loft", "references": {"base": {"level": "level-ground"}},
            "params": {"profiles": [[[left, h, 0], [right, h, 0], [right, h, 2], [left, h, 2]] for h in (0, 2)],
                       "profile_size": 4, "loft_type": "straight", "cap_ends": True},
        }}

    def test_loft_move_rotate_scale_copy_reopen_and_rollback_keep_source_identity(self):
        head = self.repository.read_head().version
        first = self.semantic({"summary": "Create an editable loft.", "entities": [self.loft()]})
        original = self.record(first)
        moved = self.action("transform", first, elementId="editable-loft", kind="move", translation=[2, 3, 4],
                            keep=["entity:portico-base", "parameter:plinth"])
        self.assertEqual(self.bounds(moved, "obj-editable-loft"), ([2, 4, 3], [5, 6, 5]))
        rotated = self.action("transform", moved, elementId="editable-loft", kind="rotate", axis=[0, 1, 0],
                              angleDegrees=90, origin=[0, 0, 0])
        scaled = self.action("transform", rotated, elementId="editable-loft", kind="scale", scale=[2, 1, 0.5], origin=[0, 0, 0])
        self.assertEqual(self.bounds(scaled, "obj-editable-loft"), ([8, -2.5, 3], [12, -1, 5]))
        copied = self.action("transform", scaled, elementId="editable-loft", kind="copy", translation=[10, 0, 0],
                             copyElementId="loft-copy")
        self.assertEqual(self.bounds(copied, "obj-loft-copy"), ([18, -2.5, 3], [22, -1, 5]))
        saved = self.record(copied)
        original_by_id = {e.entity_id: e for e in original.entities}
        for entity in saved.entities:
            if entity.entity_id in {"editable-loft", "loft-copy"}:
                source = original_by_id["editable-loft"]
                self.assertEqual((entity.schema, entity.parent_id, entity.basis_refs, entity.fields["producer"], entity.fields["references"]),
                                 (source.schema, source.parent_id, source.basis_refs, "loft", source.fields["references"]))
            else:
                self.assertEqual(entity, original_by_id[entity.entity_id])
        self.assertEqual(saved.parameters, original.parameters)
        self.assertEqual(saved.relations, original.relations)
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)
        reopened = self.action("transform", copied, elementId="loft-copy", kind="move", translation=[1, 0, 0])
        self.assertEqual(self.bounds(reopened, "obj-loft-copy"), ([19, -2.5, 3], [23, -1, 5]))
        rolled_back = self.action("transform", first, elementId="editable-loft", kind="move", translation=[-1, 0, 0])
        self.assertNotIn("loft-copy", {e.entity_id for e in self.record(rolled_back).entities})
        self.assertEqual(self.bounds(rolled_back, "obj-editable-loft"), ([-1, 0, 0], [2, 2, 2]))
        self.assertEqual(self.record(first).digest, original.digest)
        self.assertEqual(self.repository.read_head().version, head)

    def test_bound_loft_moves_through_existing_controls_without_detaching_bindings_or_locks(self):
        head = self.repository.read_head().version
        first = self.semantic({"summary": "Create a loft with a retained position control.", "entities": [self.loft(bound=True)],
                               "parameters": [{"key": "loft_x", "value": 0, "unit": "m"},
                                              {"key": "loft_left", "value": 0, "unit": "m", "expr": "loft_x", "inputs": ["loft_x"]},
                                              {"key": "loft_right", "value": 3, "unit": "m", "expr": "loft_x + 3", "inputs": ["loft_x"]}]})
        original = self.record(first)
        direct = self.client.post("/api/proposals/transform", json={
            "sourceRunId": first, "stateDigest": self.digest(first), "elementId": "editable-loft",
            "kind": "move", "translation": [5, 0, 0],
        })
        self.assertEqual(direct.status_code, 422, direct.text)
        self.assertEqual(direct.json()["code"], "DIRECT_EDIT_UNSUPPORTED")
        self.assertIn("parameter-bound", direct.json()["detail"])
        moved = self.semantic({"summary": "Move the loft five metres through its existing position control.",
                               "parameters": [{"key": "loft_x", "value": 5}]}, first)
        result = self.record(moved)
        self.assertEqual(result.entities, original.entities)
        self.assertEqual(result.relations, original.relations)
        self.assertEqual(result.dependency_edges(), original.dependency_edges())
        self.assertEqual(result.parameter("plinth"), original.parameter("plinth"))
        self.assertEqual(result.parameter("loft_right").expr, "loft_x + 3")
        self.assertEqual(self.bounds(moved, "obj-editable-loft"), ([5, 0, 0], [8, 2, 2]))
        # The requested keep is enforceable even when the change is indirect.
        kept = self.client.post("/api/proposals", json={
            "sourceRunId": moved, "stateDigest": self.digest(moved), "keep": ["entity:editable-loft"],
            "semanticEdit": {"summary": "Attempt to move the kept loft.", "parameters": [{"key": "loft_x", "value": 6}]},
        })
        self.assertEqual(kept.status_code, 201, kept.text)
        self.assertEqual(kept.json()["status"], "conflict")
        blocked = self.client.post(f"/api/proposals/{kept.json()['proposalId']}/candidate")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.record(first).digest, original.digest)
        self.assertEqual(self.repository.read_head().version, head)

    def test_model_curves_save_reopen_resolve_and_delete_on_the_same_source(self):
        head = self.repository.read_head().version
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0], "normal": [0, 0, 1]}
        profiles = {"model-line": [[0, 0], [3, 4]],
                    "model-freehand": [[0, 0], [1, 1], [2, 0], [3, 2]],
                    "model-arc": [[1 - math.cos(i * math.pi / 12), math.sin(i * math.pi / 12)] for i in range(13)]}
        run = self.action("sketch", sketches=[{
            "componentId": "portico", "elementId": name, "profile": points,
            "height": 0, "closed": False, "plane": plane, "baseLevel": "level-ground",
        } for name, points in profiles.items()])
        self.assertEqual(self.bounds(run, "obj-model-line"), ([10, 20, 4], [13, 20, 8]))
        self.assertEqual(self.bounds(run, "obj-model-arc"), ([10, 20, 4], [12, 20, 5]))
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)
        for name in profiles:
            for path in (self.project / "runs" / run).rglob("*.3dm"):
                inspection = inspect_three_dm(path)
                rows = [row for row in inspection.object_user_strings if row["name"] == f"obj-{name}"]
                if not rows:
                    continue
                response = self.client.post("/api/pick/resolve", json={
                    "stateDigest": self.digest(run), "sourceRunId": run, "objectName": f"obj-{name}",
                    "userStrings": {row["key"]: row["value"] for row in rows[0]["attributes"]},
                    "documentUserStrings": {row["key"]: row["value"] for row in inspection.document_user_strings},
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["elementId"], name)
                self.assertEqual(response.json()["status"], "resolved")
                break
            else:
                self.fail(f"saved model curve is missing: {name}")
        deleted = self.action("delete", run, elementId="model-arc")
        state = self.client.get("/api/state", params={"run": deleted}).json()
        ids = {row["elementId"] for row in state["elements"]}
        self.assertNotIn("model-arc", ids)
        self.assertTrue({"model-line", "model-freehand"}.issubset(ids))
        self.assertEqual(self.bounds(run, "obj-model-arc"), ([10, 20, 4], [12, 20, 5]))
        self.assertEqual(self.repository.read_head().version, head)

    def test_face_push_pull_move_rotate_scale_copy_are_in_the_saved_model_and_reopen(self):
        head = self.repository.read_head().version
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0], "normal": [0, 0, 1]}
        face = self.action("sketch", componentId="portico", elementId="drawn-face", profile=SQUARE,
                           height=0, plane=plane, baseLevel="level-ground")
        self.assertEqual(self.bounds(face, "obj-drawn-face"), ([10, 20, 4], [13, 20, 6]))
        pulled = self.action("push-pull", face, elementId="drawn-face", distance=1.5, normal=[0, 0, 1])
        self.assertEqual(self.bounds(pulled, "obj-drawn-face"), ([10, 20, 4], [13, 21.5, 6]))
        moved = self.action("transform", pulled, elementId="drawn-face", kind="move", translation=[2, 3, 4])
        self.assertEqual(self.bounds(moved, "obj-drawn-face"), ([12, 24, 7], [15, 25.5, 9]))
        rotated = self.action("transform", moved, elementId="drawn-face", kind="rotate", axis=[0, 1, 0],
                              angleDegrees=90, origin=[0, 0, 0])
        self.assertEqual(self.bounds(rotated, "obj-drawn-face"), ([24, -15, 7], [25.5, -12, 9]))
        scaled = self.action("transform", rotated, elementId="drawn-face", kind="scale", scale=[2, 2, 2],
                             origin=[0, 0, 0])
        self.assertEqual(self.bounds(scaled, "obj-drawn-face"), ([48, -30, 14], [51, -24, 18]))
        copied = self.action("transform", scaled, elementId="drawn-face", kind="copy", translation=[10, 0, 0],
                             copyElementId="drawn-copy")
        self.assertEqual(self.bounds(copied, "obj-drawn-copy"), ([58, -30, 14], [61, -24, 18]))
        self.assertEqual(self.bounds(copied, "obj-drawn-face"), ([48, -30, 14], [51, -24, 18]))
        self.assertEqual(self.repository.read_head().version, head)
        # Restart the application and continue from the retained typed record.
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)
        continued = self.action("push-pull", copied, elementId="drawn-copy", distance=1)
        self.assertEqual(self.bounds(continued, "obj-drawn-copy"), ([58, -30, 14], [62, -24, 18]))
        self.assertEqual(self.repository.read_head().version, head)
        self.assertEqual(self.bounds(face, "obj-drawn-face"), ([10, 20, 4], [13, 20, 6]))

    def test_negative_pull_on_a_picked_work_plane_exports_in_the_stated_direction(self):
        run = self.action("sketch", componentId="portico", elementId="reversed-prism", profile=SQUARE,
                          height=-2, baseLevel="level-ground",
                          plane={"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, 1, 0], "normal": [0, 0, 1]})
        self.assertEqual(self.bounds(run, "obj-reversed-prism"), ([10, 18, 4], [13, 20, 6]))

    def test_all_six_box_faces_push_pull_in_the_saved_model(self):
        head = self.repository.read_head().version
        run = self.action("sketch", componentId="portico", elementId="six-face-box", profile=SQUARE,
                          height=2.4, baseLevel="level-ground")
        cases = (([1, 0, 0], ([0, 0, 0], [4, 2, 2.4])),
                 ([-1, 0, 0], ([-1, 0, 0], [4, 2, 2.4])),
                 ([0, 0, 1], ([-1, 0, 0], [4, 3, 2.4])),
                 ([0, 0, -1], ([-1, -1, 0], [4, 3, 2.4])),
                 ([0, 1, 0], ([-1, -1, 0], [4, 3, 3.4])),
                 ([0, -1, 0], ([-1, -1, -1], [4, 3, 3.4])))
        for normal, expected in cases:
            with self.subTest(normal=normal):
                run = self.action("push-pull", run, elementId="six-face-box", distance=1, normal=normal)
                self.assertEqual(self.bounds(run, "obj-six-face-box"), expected)
        self.assertEqual(self.repository.read_head().version, head)

    def test_negative_scale_mirrors_the_saved_solid_and_its_next_side_pull(self):
        run = self.action("sketch", componentId="portico", elementId="mirrored-box", profile=SQUARE,
                          height=2.4, baseLevel="level-ground")
        mirrored = self.action("transform", run, elementId="mirrored-box", kind="scale", scale=[-1, -1, 1],
                               origin=[0, 0, 0])
        self.assertEqual(self.bounds(mirrored, "obj-mirrored-box"), ([-3, 0, -2.4], [0, 2, 0]))
        pulled = self.action("push-pull", mirrored, elementId="mirrored-box", normal=[-1, 0, 0], distance=1)
        self.assertEqual(self.bounds(pulled, "obj-mirrored-box"), ([-4, 0, -2.4], [0, 2, 0]))

    def test_tilted_plane_rotation_then_normal_pull_preserves_its_true_placement(self):
        s = math.sqrt(0.5)
        plane = {"origin": [10, 4, 20], "xAxis": [1, 0, 0], "yAxis": [0, s, s], "normal": [0, -s, s]}
        run = self.action("sketch", componentId="portico", elementId="tilted-box", profile=SQUARE,
                          height=2, baseLevel="level-ground", plane=plane)
        rotated = self.action("transform", run, elementId="tilted-box", kind="rotate", axis=[0, 1, 0],
                              angleDegrees=90, origin=[0, 0, 0])
        pulled = self.action("push-pull", rotated, elementId="tilted-box", normal=[s, -s, 0], distance=1)
        expected = ([20, -13, round(4 - 3 * s, 5)], [round(20 + 5 * s, 5), -10, round(4 + 2 * s, 5)])
        self.assertEqual(self.bounds(pulled, "obj-tilted-box"), expected)


class ProposalCheckpointTestCase(unittest.TestCase):
    setUp = SketchNewComponentTestCase.setUp
    digest = SketchNewComponentTestCase.digest
    run_candidate = SketchNewComponentTestCase.run_candidate
    bounds = SketchDirectGeometryTestCase.bounds

    def edit(self, route: str, previous: dict | None = None, **body) -> dict:
        payload = {"stateDigest": self.digest() if previous is None else previous["baseStateDigest"], **body}
        if previous is not None:
            payload["sourceProposalId"] = previous["proposalId"]
        response = self.client.post("/api/proposals" + (f"/{route}" if route else ""), json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def first(self, **body) -> dict:
        return self.edit("sketch", componentId="portico", elementId="chain-a", profile=SQUARE,
                         height=2, baseLevel="level-ground", **body)

    def test_mixed_edit_chain_writes_only_the_final_checkpoint_and_reopens(self):
        before_runs = set((self.project / "runs").iterdir())
        before_models = set(self.project.rglob("*.3dm"))
        head = self.repository.read_head()
        first = self.first(keep=["entity:portico-base"])
        original = self.client.get(f"/api/proposals/{first['proposalId']}").json()
        proposal = self.edit("sketch", first, componentId="portico", elementId="chain-b",
                             profile=SQUARE, height=1, baseDatum="chain-a-top")
        proposal = self.edit("", proposal, targetComponentId="portico", elementId="chain-a", utterance="set height to 3")
        proposal = self.edit("sketch", proposal, componentId="portico", elementId="chain-temp",
                             profile=SQUARE, height=1, baseLevel="level-ground")
        proposal = self.edit("transform", proposal, elementId="chain-temp", kind="move", translation=[5, 0, 0])
        proposal = self.edit("push-pull", proposal, elementId="chain-temp", distance=1)
        proposal = self.edit("transform", proposal, elementId="chain-temp", kind="copy",
                             copyElementId="chain-copy", translation=[4, 0, 0])
        proposal = self.edit("delete", proposal, elementId="chain-temp")
        self.assertEqual(set((self.project / "runs").iterdir()), before_runs)
        self.assertEqual(set(self.project.rglob("*.3dm")), before_models)
        self.assertEqual(self.client.get(f"/api/proposals/{first['proposalId']}").json(), original)
        self.assertEqual(proposal["sourceRunId"], first["sourceRunId"])
        self.assertEqual(proposal["baseStateDigest"], first["baseStateDigest"])
        self.assertIn("entity:portico-base", proposal["protected"])
        job = self.run_candidate(proposal["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        run_id = job["candidateId"]
        self.assertEqual(set((self.project / "runs").iterdir()) - before_runs, {self.project / "runs" / run_id})
        models = set(self.project.rglob("*.3dm")) - before_models
        self.assertEqual(len(models), 1, models)
        self.assertEqual(self.bounds(run_id, "obj-chain-a"), ([0, 0, 0], [3, 2, 3]))
        self.assertEqual(self.bounds(run_id, "obj-chain-b"), ([0, 0, 3], [3, 2, 4]))
        self.assertEqual(self.bounds(run_id, "obj-chain-copy"), ([9, 0, 0], [12, 2, 2]))
        self.assertEqual(self.repository.read_head(), head)
        self.assertEqual(self.client.get("/api/design-history").json()["stages"], [])
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="off", project_dir=self.project)))
        self.addCleanup(self.client.close)
        elements = {row["elementId"]: row for row in self.client.get(f"/api/state?run={run_id}").json()["elements"]}
        self.assertTrue({"chain-a", "chain-b", "chain-copy"} <= set(elements))
        self.assertNotIn("chain-temp", elements)
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.candidate import replay_candidate
        from archflow_studio_api.application.projection import project_state

        binding = bound_project(self.client.app.state)
        self.assertEqual(replay_candidate(binding, run_id).digest, project_state(binding, run_id).record.digest)

    def test_proposal_continuation_keeps_an_explicit_stage_selection(self):
        from .test_working_copies import register_model

        model = register_model(self.client, REFERENCE_RUN_ID, self.digest(),
                               (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes())["modelSource"]
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        stage_ref = initialized.json()["stageRef"]
        first = self.first(sourceStageRef=stage_ref)
        continued = self.edit("push-pull", first, elementId="chain-a", distance=1)
        self.assertEqual(continued["sourceStageRef"], stage_ref)
        self.assertEqual(continued["sourceRunId"], first["sourceRunId"])
        self.assertEqual(continued["baseStateDigest"], first["baseStateDigest"])

    def test_thirty_planned_forms_need_one_request_and_one_final_model(self):
        before_runs = set((self.project / "runs").iterdir())
        before_models = set(self.project.rglob("*.3dm"))
        before_proposals = len(self.client.app.state.proposals.for_state(self.digest()))
        response = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.digest(), "keep": ["entity:portico-base"], "summary": "Thirty planned forms",
            "sketches": [
                {"componentId": "portico", "elementId": f"batch-{i}",
                 "profile": [[x + i * 4, z] for x, z in SQUARE], "height": i / 10 + 1,
                 "baseLevel": "level-ground"} for i in range(30)
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(set((self.project / "runs").iterdir()), before_runs)
        self.assertEqual(set(self.project.rglob("*.3dm")), before_models)
        self.assertEqual(len(self.client.app.state.proposals.for_state(self.digest())), before_proposals + 1)
        proposal = response.json()
        self.assertEqual(proposal["change"]["summary"], "Thirty planned forms")
        self.assertEqual(len(proposal["change"]["edits"]["entities"]), 30)
        job = self.run_candidate(proposal["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(set((self.project / "runs").iterdir()) - before_runs,
                         {self.project / "runs" / job["candidateId"]})
        self.assertEqual(len(set(self.project.rglob("*.3dm")) - before_models), 1)
        elements = self.client.get(f"/api/state?run={job['candidateId']}").json()["elements"]
        self.assertEqual(sum(row["elementId"].startswith("batch-") for row in elements), 30)
        self.assertEqual(self.bounds(job["candidateId"], "obj-batch-29"), ([116, 0, 0], [119, 2, 3.9]))

    def test_batch_failure_retains_neither_an_executable_prefix_nor_a_run(self):
        first = self.first()
        before = self.client.app.state.proposals.for_state(first["baseStateDigest"])
        before_runs = set((self.project / "runs").iterdir())
        response = self.client.post("/api/proposals/sketch", json={
            "stateDigest": first["baseStateDigest"], "sourceProposalId": first["proposalId"],
            "sketches": [
                {"componentId": "portico", "elementId": "batch-ok", "profile": SQUARE,
                 "height": 1, "baseDatum": "chain-a-top"},
                {"componentId": "unbuilt", "elementId": "batch-bad", "profile": SQUARE,
                 "height": 1, "baseLevel": "level-ground"},
            ],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "COMPONENT_NOT_BUILT")
        self.assertEqual(self.client.app.state.proposals.for_state(first["baseStateDigest"]), before)
        self.assertEqual(set((self.project / "runs").iterdir()), before_runs)
        continued = self.edit("push-pull", first, elementId="chain-a", distance=1)
        self.assertNotIn("batch-ok", {row["entity_id"] for row in continued["change"]["edits"]["entities"]})

    def test_bad_continuation_keeps_the_previous_proposal_and_exact_source(self):
        first = self.first()
        original = self.client.get(f"/api/proposals/{first['proposalId']}").json()
        for extra, code in (
            ({"sourceProposalId": "unknown"}, "PROPOSAL_NOT_FOUND"),
            ({"projectId": "different-project"}, "PROJECT_MISMATCH"),
            ({"sourceRunId": "different-run"}, "PROPOSAL_SOURCE_MISMATCH"),
            ({"stateDigest": "0" * 64}, "STALE_BASE"),
        ):
            with self.subTest(code=code):
                response = self.client.post("/api/proposals/push-pull", json={
                    "stateDigest": first["baseStateDigest"], "sourceProposalId": first["proposalId"],
                    "elementId": "chain-a", "distance": 1, **extra,
                })
                self.assertEqual(response.json()["code"], code, response.text)
        self.assertEqual(self.client.get(f"/api/proposals/{first['proposalId']}").json(), original)
        continued = self.edit("push-pull", first, elementId="chain-a", distance=1)
        self.assertEqual(continued["sourceRunId"], first["sourceRunId"])

    def test_keep_on_a_new_form_survives_later_edits_without_protecting_its_old_base(self):
        first = self.first()
        second = self.edit("sketch", first, componentId="portico", elementId="chain-b", profile=SQUARE,
                           height=1, baseLevel="level-ground", keep=["entity:chain-a"])
        protected = self.client.get(f"/api/proposals/{second['proposalId']}").json()
        failed = self.client.post("/api/proposals/push-pull", json={
            "stateDigest": first["baseStateDigest"], "sourceProposalId": second["proposalId"],
            "elementId": "chain-a", "distance": 1,
        })
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(failed.json()["code"], "PROPOSAL_CHAIN_CONFLICT")
        self.assertEqual(self.client.get(f"/api/proposals/{second['proposalId']}").json(), protected)
        self.assertEqual(second["status"], "proposed")
        self.assertIn("entity:chain-a", second["protected"])
        third = self.edit("push-pull", second, elementId="chain-b", distance=1)
        self.assertIn("entity:chain-a", third["protected"])
        job = self.run_candidate(third["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(self.bounds(job["candidateId"], "obj-chain-a"), ([0, 0, 0], [3, 2, 2]))


if __name__ == "__main__":
    unittest.main()
