"""Drawing on the model: a profile and a height, through the proposal path.

What the tests check is that a drawn action is the *same* design edit the
record already knows — one ``Element@1`` row whose producer is ``prism`` — and
that it therefore keeps everything that already holds for an edit: one exact
base, an editable outline and height afterwards, and the existing candidate
route as the only thing that runs.
"""

from pathlib import Path
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

    def test_a_profile_that_is_not_a_profile_is_refused_before_anything_runs(self) -> None:
        for invalid, why in (
            ({"profile": [[0.0, 0.0], [1.0, 0.0]]}, "two points are not a profile"),
            ({"profile": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.0]]}, "a repeated point"),
            ({"height": 0.0}, "a height of nothing"),
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


if __name__ == "__main__":
    unittest.main()
