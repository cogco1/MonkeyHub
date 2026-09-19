"""Explicit massing elevations share the existing reversible proposal/candidate path."""

import unittest

from fastapi.testclient import TestClient

from archflow.state.state_record import apply_state_record_operator
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.projection import project_proposed_record, project_state
from archflow_studio_api.application.proposals import operator_of
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.state import to_dto

from . import test_sketch as sketch_tests


class ElevationTests(unittest.TestCase):
    setUp = sketch_tests.SketchNewComponentTestCase.setUp
    digest = sketch_tests.SketchNewComponentTestCase.digest
    run_candidate = sketch_tests.SketchNewComponentTestCase.run_candidate
    bounds = sketch_tests.SketchDirectGeometryTestCase.bounds

    def request(self, route, previous=None, **values):
        payload = {"stateDigest": self.digest() if previous is None else previous["baseStateDigest"], **values}
        if previous is not None:
            payload["sourceProposalId"] = previous["proposalId"]
        return self.client.post("/api/proposals" + route, json=payload)

    def action(self, previous=None, **values):
        response = self.request("/elevation", previous, **values)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def sketch(self, element_id="mass", previous=None, **values):
        response = self.request("/sketch", previous, componentId="portico", elementId=element_id,
                                profile=sketch_tests.SQUARE, height=values.pop("height", 2),
                                **({"baseLevel": "level-ground"} if "baseDatum" not in values else {}), **values)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def state(self, proposal):
        value = self.client.app.state.proposals.get(proposal["proposalId"])
        base = project_state(bound_project(self.client.app.state), run_id=value.source_run_id)
        successor = apply_state_record_operator(base.record, operator_of(value, base.record))
        return to_dto(project_proposed_record(base, successor)).model_dump(by_alias=True, mode="json")

    def elevation(self, proposal, element="mass"):
        return next(e["elevation"] for e in self.state(proposal)["elements"] if e["elementId"] == element)

    def assert_elevation(self, proposal, base, top, height, element="mass"):
        elevation = self.elevation(proposal, element)
        self.assertIsNotNone(elevation)
        self.assertEqual([elevation[k] for k in ("base", "top", "height")], [base, top, height])
        return elevation

    def test_numeric_edits_top_binding_and_detach_keep_the_height_equation(self):
        first = self.sketch()
        original = self.client.get(f"/api/proposals/{first['proposalId']}").json()
        free = self.action(first, elementId="mass", action="detach-base")
        self.assertIsNone(self.elevation(free)["baseReference"])
        current = self.action(free, elementId="mass", action="set-base", value=2)
        self.assert_elevation(current, 2, 4, 2)
        current = self.action(current, elementId="mass", action="set-top", value=7)
        self.assert_elevation(current, 2, 7, 5)
        current = self.action(current, elementId="mass", action="set-height", value=3)
        self.assert_elevation(current, 2, 5, 3)
        current = self.action(current, action="set-datum", levelId="upper-datum", name="Upper datum", value=8)
        current = self.action(current, elementId="mass", action="bind-top", reference={"kind": "level", "id": "upper-datum"})
        self.assert_elevation(current, 2, 8, 6)
        current = self.action(current, elementId="mass", action="set-base", value=3)
        self.assert_elevation(current, 3, 8, 5)
        current = self.action(current, elementId="mass", action="set-height", value=4)
        self.assertEqual(self.assert_elevation(current, 3, 7, 4)["topReference"],
                         {"kind": "level", "id": "upper-datum", "offset": -1})
        current = self.action(current, action="set-datum", levelId="upper-datum", value=10)
        self.assert_elevation(current, 3, 9, 6)
        current = self.action(current, elementId="mass", action="detach-top")
        current = self.action(current, action="set-datum", levelId="upper-datum", value=11)
        self.assertIsNone(self.assert_elevation(current, 3, 9, 6)["topReference"])
        # Every earlier proposal is an unchanged undo point; continuing one branches safely.
        self.assertEqual(self.client.get(f"/api/proposals/{first['proposalId']}").json(), original)
        self.assert_elevation(free, 0, 2, 2)
        branch = self.action(free, elementId="mass", action="set-height", value=1)
        self.assert_elevation(branch, 0, 1, 1)
        self.assert_elevation(current, 3, 9, 6)

    def test_explicit_stack_propagates_while_free_mass_ignores_level_changes(self):
        current = self.sketch("lower")
        current = self.sketch("upper", current, height=1, baseDatum="lower-top")
        current = self.sketch("free", current, height=1)
        current = self.action(current, elementId="free", action="set-base", value=2)
        current = self.action(current, elementId="free", action="detach-base")
        current = self.action(current, elementId="lower", action="set-height", value=4)
        self.assert_elevation(current, 4, 5, 1, "upper")
        self.assert_elevation(current, 2, 3, 1, "free")
        current = self.action(current, action="set-datum", levelId="stage-datum", name="Stage datum", value=6)
        current = self.action(current, elementId="lower", action="bind-base", reference={"kind": "level", "id": "stage-datum"})
        self.assert_elevation(current, 6, 10, 4, "lower")
        self.assert_elevation(current, 10, 11, 1, "upper")
        current = self.action(current, elementId="upper", action="set-base", value=12)
        self.assertEqual(self.elevation(current, "upper")["baseReference"],
                         {"kind": "element-top", "id": "lower", "offset": 2})
        current = self.action(current, action="set-datum", levelId="stage-datum", value=7)
        self.assert_elevation(current, 7, 11, 4, "lower")
        self.assert_elevation(current, 13, 14, 1, "upper")
        self.assert_elevation(current, 2, 3, 1, "free")
        self.assertEqual(next(level for level in self.state(current)["levels"] if level["levelId"] == "stage-datum"),
                         {"levelId": "stage-datum", "name": "Stage datum", "elevation": 7})

    def test_invalid_cycle_missing_target_and_nonpositive_height_keep_prior_state(self):
        first = self.sketch("lower")
        first = self.sketch("upper", first, height=1, baseDatum="lower-top")
        original = self.state(first)
        proposals = self.client.app.state.proposals.for_state(first["baseStateDigest"])
        before_runs = set((self.project / "runs").iterdir())
        for values in (
            {"elementId": "lower", "action": "bind-base", "reference": {"kind": "element-top", "id": "upper"}},
            {"elementId": "lower", "action": "bind-base", "reference": {"kind": "element-top", "id": "lower"}},
            {"elementId": "lower", "action": "bind-base", "reference": {"kind": "element-top", "id": "missing"}},
            {"elementId": "lower", "action": "bind-top", "reference": {"kind": "level", "id": "missing"}},
            {"elementId": "lower", "action": "set-height", "value": 0},
            {"elementId": "lower", "action": "set-top", "value": -1},
            {"elementId": "missing", "action": "set-base", "value": 1},
        ):
            with self.subTest(values=values):
                response = self.request("/elevation", first, **values)
                self.assertEqual(response.status_code, 422, response.text)
        stale = self.request("/elevation", first, elementId="lower", action="set-height", value=3, stateDigest="0" * 64)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.app.state.proposals.for_state(first["baseStateDigest"]), proposals)
        self.assertEqual(set((self.project / "runs").iterdir()), before_runs)
        self.assertEqual(self.state(first), original)

    def test_keep_is_preserved_and_a_bound_top_cannot_cross_the_base(self):
        first = self.sketch()
        first = self.action(first, action="set-datum", levelId="roof", value=3)
        first = self.action(first, elementId="mass", action="bind-top", reference={"kind": "level", "id": "roof"})
        bad = self.request("/elevation", first, elementId="mass", action="set-base", value=4)
        self.assertEqual(bad.status_code, 422, bad.text)
        bad = self.request("/elevation", first, action="set-datum", levelId="roof", value=-1)
        self.assertEqual(bad.status_code, 422, bad.text)
        self.assert_elevation(first, 0, 3, 3)
        protected = self.request("/elevation", first, elementId="mass", action="set-height", value=4, keep=["entity:mass"])
        self.assertEqual(protected.status_code, 409, protected.text)
        self.assertEqual(protected.json()["code"], "PROPOSAL_CHAIN_CONFLICT")
        self.assert_elevation(first, 0, 3, 3)

    def test_saved_model_reopens_with_free_and_linked_elevations_and_continues(self):
        current = self.sketch("lower")
        current = self.action(current, elementId="lower", action="detach-base")
        current = self.action(current, elementId="lower", action="set-base", value=1)
        current = self.sketch("upper", current, height=1, baseDatum="lower-top")
        current = self.action(current, action="set-datum", levelId="roof", value=6)
        current = self.action(current, elementId="upper", action="bind-top", reference={"kind": "level", "id": "roof"})
        before_runs = set((self.project / "runs").iterdir())
        job = self.run_candidate(current["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        run = job["candidateId"]
        self.assertEqual(set((self.project / "runs").iterdir()) - before_runs, {self.project / "runs" / run})
        self.assertEqual(self.bounds(run, "obj-lower"), ([0, 0, 1], [3, 2, 3]))
        self.assertEqual(self.bounds(run, "obj-upper"), ([0, 0, 3], [3, 2, 6]))
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)
        reopened = self.client.get(f"/api/state?run={run}").json()
        elements = {e["elementId"]: e for e in reopened["elements"]}
        self.assertIsNone(elements["lower"]["elevation"]["baseReference"])
        self.assertEqual(elements["upper"]["elevation"]["topReference"]["id"], "roof")
        self.assertEqual(elements["upper"]["drawnShape"]["height"], 3)
        self.assertEqual(elements["upper"]["drawnShape"]["workPlane"]["origin"], [0, 3, 0])
        response = self.request("/elevation", stateDigest=reopened["stateDigest"], sourceRunId=run,
                                elementId="lower", action="set-height", value=3)
        self.assertEqual(response.status_code, 201, response.text)
        proposal = response.json()
        self.assert_elevation(proposal, 1, 4, 3, "lower")
        self.assert_elevation(proposal, 4, 6, 2, "upper")
        second = self.run_candidate(proposal["proposalId"])
        self.assertEqual(second["status"], "succeeded", second)
        self.assertEqual(self.bounds(second["candidateId"], "obj-upper"), ([0, 0, 4], [3, 2, 6]))

    def test_drawing_origin_and_authored_parameter_bindings_survive_numeric_edits(self):
        current = self.sketch(plane={"origin": [10, 4, 20], "xAxis": [0, 0, 1],
                                     "yAxis": [-1, 0, 0], "normal": [0, 1, 0]})
        response = self.request("", current, semanticEdit={
            "summary": "Retain the authored height control",
            "parameters": [{"key": "mass-height", "value": 2, "unit": "m", "source_ref": "studio:intent"}],
            "entities": [{"entity_id": "mass", "fields": {"params": {
                "profile": sketch_tests.SQUARE, "height": "@mass-height",
                "work_plane": {"origin": [10, 4, 20], "xAxis": [0, 0, 1], "yAxis": [-1, 0, 0], "normal": [0, 1, 0]},
            }}}],
        })
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        current = self.action(current, elementId="mass", action="detach-base")
        self.assert_elevation(current, 4, 6, 2)
        current = self.action(current, elementId="mass", action="set-height", value=3)
        self.assert_elevation(current, 4, 7, 3)
        record_entity = next(e for e in current["change"]["edits"]["entities"] if e["entity_id"] == "mass")
        self.assertEqual(record_entity["fields"]["params"]["height"], "@mass-height")
        current = self.action(current, action="set-datum", levelId="datum", value=10)
        current = self.action(current, elementId="mass", action="bind-base", reference={"kind": "level", "id": "datum", "offset": 1})
        self.assert_elevation(current, 11, 14, 3)
        shape = next(e["drawnShape"] for e in self.state(current)["elements"] if e["elementId"] == "mass")
        self.assertEqual(shape["workPlane"]["origin"], [10, 11, 20])
        self.assertEqual(shape["workPlane"]["xAxis"], [0, 0, 1])
        self.assertEqual(shape["profile"], sketch_tests.SQUARE)
        response = self.request("/elevation", current, elementId="mass", action="bind-top", reference={"kind": "level", "id": "datum", "offset": 8})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("parameter-bound", response.json()["detail"])

    def test_existing_explicit_height_and_top_constraints_refuse_a_conflicting_datum_change(self):
        current = self.sketch()
        current = self.action(current, action="set-datum", levelId="roof", value=2)
        response = self.request("", current, semanticEdit={"summary": "Retain both existing constraints", "entities": [
            {"entity_id": "mass", "fields": {"references": {"base": {"level": "level-ground"}, "top": {"level": "roof"}}}},
        ]})
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        response = self.request("/elevation", current, action="set-datum", levelId="roof", value=3)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("conflicts", response.json()["detail"])
        self.assert_elevation(current, 0, 2, 2)
        current = self.action(current, elementId="mass", action="set-height", value=3)
        self.assert_elevation(current, 0, 3, 3)

    def test_elevation_edit_keeps_unmodified_type_defaults_inherited(self):
        response = self.request("", semanticEdit={"summary": "One typed mass", "entities": [
            {"entity_id": "mass-type", "schema": "Type@1", "fields": {
                "producer": "prism", "params": {"profile": sketch_tests.SQUARE, "height": 2},
                "references": {"base": {"level": "level-ground"}},
            }},
            {"entity_id": "mass", "schema": "Element@1", "parent_id": "portico", "fields": {
                "component_id": "portico", "producer": "prism", "type_ref": "mass-type",
            }, "basis_refs": ["studio:intent"]},
        ]})
        self.assertEqual(response.status_code, 201, response.text)
        current = self.action(response.json(), elementId="mass", action="set-base", value=1)
        fields = next(e["fields"] for e in current["change"]["edits"]["entities"] if e["entity_id"] == "mass")
        self.assertEqual(fields["params"], {"elevation": 1})
        self.assertEqual(fields["references"], {})
        response = self.request("", current, semanticEdit={"summary": "Change the same type", "entities": [
            {"entity_id": "mass-type", "fields": {"params": {"profile": sketch_tests.SQUARE, "height": 4}}},
        ]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assert_elevation(response.json(), 1, 5, 4)

    def test_flattened_bound_face_follows_datum_until_explicitly_detached(self):
        current = self.sketch()
        current = self.action(current, action="set-datum", levelId="pad", value=5)
        current = self.action(current, elementId="mass", action="bind-base", reference={"kind": "level", "id": "pad"})
        response = self.request("/push-pull", current, elementId="mass", distance=-2)
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        self.assertEqual(self.assert_elevation(current, 5, 5, 0)["baseReference"],
                         {"kind": "level", "id": "pad", "offset": 0})
        self.assertIsNone(self.elevation(current)["topReference"])
        current = self.action(current, action="set-datum", levelId="pad", value=7)
        self.assert_elevation(current, 7, 7, 0)
        # A zero-height face never pretends to publish a bearing top.
        response = self.request("/sketch", current, componentId="portico", elementId="on-flat",
                                profile=sketch_tests.SQUARE, height=1, baseDatum="mass-top")
        self.assertEqual(response.status_code, 422, response.text)
        for action in ("set-base", "set-top", "set-height"):
            response = self.request("/elevation", current, elementId="mass", action=action, value=8)
            self.assertEqual(response.status_code, 422, response.text)
        current = self.action(current, elementId="mass", action="detach-base")
        current = self.action(current, action="set-datum", levelId="pad", value=9)
        self.assertIsNone(self.assert_elevation(current, 7, 7, 0)["baseReference"])
        job = self.run_candidate(current["proposalId"])
        self.assertEqual(job["status"], "succeeded", job)
        run = job["candidateId"]
        self.assertEqual(self.bounds(run, "obj-mass"), ([0, 0, 7], [3, 2, 7]))
        self.client.close()
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)
        reopened = self.client.get(f"/api/state?run={run}").json()
        element = next(e for e in reopened["elements"] if e["elementId"] == "mass")
        self.assertEqual(element["producer"], "planar-surface")
        self.assertEqual(element["elevation"], {"base": 7, "top": 7, "height": 0, "baseReference": None, "topReference": None})

    def test_new_horizontal_face_can_detach_without_becoming_a_prism(self):
        current = self.sketch(height=0)
        current = self.action(current, elementId="mass", action="detach-base")
        current = self.action(current, action="set-datum", levelId="level-ground", value=3)
        self.assertIsNone(self.assert_elevation(current, 0, 0, 0)["baseReference"])
        element = next(e for e in self.state(current)["elements"] if e["elementId"] == "mass")
        self.assertEqual(element["producer"], "planar-surface")
        self.assertEqual(element["drawnShape"]["height"], 0)
