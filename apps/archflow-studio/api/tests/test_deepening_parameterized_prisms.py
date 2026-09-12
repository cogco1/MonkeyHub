"""Deepening experiment: one semantic edit leaves locally editable producers.

The single compiled edit below authors ordinary ``prism`` producers plus an
explicit StateRecord parameter relation. Candidate execution, acceptance,
restart and later scalar edits all use the production Studio/P036 path. No
second model pass reconstructs dependencies: the retained StateRecord is the
source.

This deliberately binds only producer inputs the public prism signature owns
today. Profile coordinates remain numeric literals; a separate contract gap is
that nested ``@parameter`` bindings in ``profile`` are resolved by StateRecord
but rejected by the advertised prism profile schema. That gap blocks the W2
width/inset benchmark sequence and must not be hidden by this test.
"""

from __future__ import annotations

from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_cad_export import NEEDS_OCCT, OcctCandidateTestCase, no_process
from .test_intents import scripted


def parameterized_prism_edit() -> dict:
    """Four ordinary prisms with a retained elevation/height dependency."""

    parameters = [
        {"key": "main_height", "value": 3.3, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_elevation", "value": 2.8, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_thickness", "value": 0.18, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "support_height", "value": 2.8, "unit": "m", "expr": "canopy_elevation", "inputs": ["canopy_elevation"], "source_ref": "studio:intent"},
    ]

    def prism(entity_id: str, profile: list[list[float]], height: object, *, elevation: object | None = None) -> dict:
        params: dict[str, object] = {"profile": profile, "height": height}
        if elevation is not None:
            params["elevation"] = elevation
        return {
            "entity_id": entity_id,
            "schema": "Element@1",
            "parent_id": "portico",
            "basis_refs": ["studio:intent"],
            "fields": {
                "component_id": "portico",
                "producer": "prism",
                "references": {"base": {"level": "level-ground"}},
                "params": params,
            },
        }

    entities = [
        prism("deep-main", [[0, 0], [8, 0], [8, 6], [0, 6]], "@main_height"),
        prism("deep-canopy", [[2.5, -1.8], [5.5, -1.8], [5.5, 0], [2.5, 0]], "@canopy_thickness", elevation="@canopy_elevation"),
        prism("deep-support-left", [[2.7, -1.6], [3.0, -1.6], [3.0, -1.3], [2.7, -1.3]], "@support_height"),
        prism("deep-support-right", [[5.0, -1.6], [5.3, -1.6], [5.3, -1.3], [5.0, -1.3]], "@support_height"),
    ]
    return {
        "summary": "Add one editable mass and porch whose support height follows the canopy elevation.",
        "entities": entities,
        "parameters": parameters,
        "relations": [],
        "removeEntityIds": [],
        "removeParameterKeys": [],
        "removeRelationIds": [],
        "protected": ["entity:portico-base", "entity:portico-cornice"],
        "kept": ["The existing portico fixture remains unchanged."],
    }


@NEEDS_OCCT
class RetainedParameterizedPrismTests(OcctCandidateTestCase):
    def _compile_once(self) -> tuple[str, str]:
        self.client.app.state.intent_compiler = scripted(
            semantic_edit=parameterized_prism_edit(), component_id="portico"
        )
        response = self.client.post(
            "/api/intents",
            json={
                "stateDigest": self.state_digest,
                "sourceRunId": REFERENCE_RUN_ID,
                "targetComponentId": "portico",
                "utterance": "Create the mass and porch as one editable design proposal.",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        proposal_id = response.json()["proposal"]["proposalId"]
        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        job = self.finished(self.client, accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        return proposal_id, accepted["candidateId"]

    def _assert_shape(self, shapes, entity_id: str, *, x: float, y: float, z: float, z0: float = 0.0) -> None:
        shape = next(value for name, value in shapes.items() if entity_id in name)
        self.assertTrue(shape.valid and shape.closed and shape.solid_count == 1)
        self.assertAlmostEqual(shape.bbox_max[0] - shape.bbox_min[0], x, places=6)
        self.assertAlmostEqual(shape.bbox_max[1] - shape.bbox_min[1], y, places=6)
        self.assertAlmostEqual(shape.bbox_max[2] - shape.bbox_min[2], z, places=6)
        self.assertAlmostEqual(shape.bbox_min[2], z0, places=6)

    def _run_scalar(self, client, source_run: str, state_digest: str, utterance: str) -> str:
        response = client.post(
            "/api/proposals",
            json={
                "stateDigest": state_digest,
                "sourceRunId": source_run,
                "targetComponentId": "portico",
                "utterance": utterance,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        started = client.post(f"/api/proposals/{response.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        self.assertEqual(self.finished(client, accepted["jobId"])["status"], "succeeded")
        return accepted["candidateId"]

    def test_one_compiled_edit_survives_acceptance_restart_and_two_local_parameter_edits(self) -> None:
        with no_process():
            proposal_id, first = self._compile_once()
        candidate = self.candidate(self.client, first)
        exact, _ = self.split(candidate["artifacts"])
        shapes = self.step_shapes(self.bytes_of(self.client, exact))
        self._assert_shape(shapes, "deep-main", x=8.0, y=6.0, z=3.3)
        self._assert_shape(shapes, "deep-canopy", x=3.0, y=1.8, z=0.18, z0=2.8)
        self._assert_shape(shapes, "deep-support-left", x=0.3, y=0.3, z=2.8)
        self._assert_shape(shapes, "deep-support-right", x=0.3, y=0.3, z=2.8)

        record = self.load_kind(first, "state-record")
        main = next(row for row in record["entities"] if row["entity_id"] == "deep-main")
        canopy = next(row for row in record["entities"] if row["entity_id"] == "deep-canopy")
        self.assertEqual(main["fields"]["params"]["height"], "@main_height")
        self.assertEqual(canopy["fields"]["params"]["elevation"], "@canopy_elevation")
        params = {row["key"]: row for row in record["parameters"]}
        self.assertEqual(params["support_height"]["expr"], "canopy_elevation")

        decision = self.client.post(
            f"/api/proposals/{proposal_id}/decision",
            json={"decision": "accepted", "candidateId": first, "reason": "deepening checkpoint"},
        )
        self.assertEqual(decision.status_code, 201, decision.text)

        restarted = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID))
        state_a = restarted.get("/api/state", params={"run": first})
        self.assertEqual(state_a.status_code, 200, state_a.text)
        self.assertTrue(state_a.json()["matchesReferenceReceipt"])

        # No compiler/model is installed in the restarted process. This typed
        # parameter edit locally re-evaluates support_height and rebuilds all
        # bound producers from the retained record.
        with no_process():
            second = self._run_scalar(
                restarted, first, state_a.json()["stateDigest"],
                "set canopy_elevation to 3.1 m",
            )
        candidate_b = self.candidate(restarted, second)
        exact_b, _ = self.split(candidate_b["artifacts"])
        shapes_b = self.step_shapes(self.bytes_of(restarted, exact_b))
        self._assert_shape(shapes_b, "deep-canopy", x=3.0, y=1.8, z=0.18, z0=3.1)
        self._assert_shape(shapes_b, "deep-support-left", x=0.3, y=0.3, z=3.1)
        self._assert_shape(shapes_b, "deep-support-right", x=0.3, y=0.3, z=3.1)
        self._assert_shape(shapes_b, "deep-main", x=8.0, y=6.0, z=3.3)
        record_b = self.load_kind(second, "state-record")
        values_b = {row["key"]: (row["value"], row["expr"]) for row in record_b["parameters"]}
        self.assertEqual(values_b["canopy_elevation"], (3.1, None))
        self.assertEqual(values_b["support_height"], (3.1, "canopy_elevation"))

        restarted_again = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID))
        state_b = restarted_again.get("/api/state", params={"run": second})
        self.assertEqual(state_b.status_code, 200, state_b.text)
        with no_process():
            third = self._run_scalar(
                restarted_again, second, state_b.json()["stateDigest"],
                "set main_height to 4.5 m",
            )
        candidate_c = self.candidate(restarted_again, third)
        exact_c, _ = self.split(candidate_c["artifacts"])
        shapes_c = self.step_shapes(self.bytes_of(restarted_again, exact_c))
        self._assert_shape(shapes_c, "deep-main", x=8.0, y=6.0, z=4.5)
        self._assert_shape(shapes_c, "deep-canopy", x=3.0, y=1.8, z=0.18, z0=3.1)
        self._assert_shape(shapes_c, "deep-support-left", x=0.3, y=0.3, z=3.1)
