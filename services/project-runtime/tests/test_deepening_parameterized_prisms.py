"""One direct semantic proposal leaves locally editable, linked producers.

The single submitted edit below authors ordinary ``prism`` producers plus an
explicit StateRecord parameter relation. Candidate execution, acceptance,
restart and later scalar edits all use the production Studio/P036 path. No
second model pass reconstructs dependencies: the retained StateRecord is the
source.

Profile coordinates bind the same retained parameters as height and elevation.
The current Agent submits its design data directly; Studio performs no second
model call to translate or reconstruct those dependencies.
"""

from __future__ import annotations

from unittest.mock import Mock

from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID
from .test_cad_export import NEEDS_OCCT, OcctCandidateTestCase, no_process


def parameterized_prism_edit() -> dict:
    """Four ordinary prisms linked by width, inset and elevation controls."""

    parameters = [
        {"key": "main_height", "value": 3.3, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_elevation", "value": 2.8, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_thickness", "value": 0.18, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_width", "value": 3.0, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "support_inset", "value": 0.2, "unit": "m", "epistemic_status": "declared", "source_ref": "studio:intent"},
        {"key": "canopy_left", "value": 2.5, "unit": "m", "expr": "4 - canopy_width / 2", "inputs": ["canopy_width"]},
        {"key": "canopy_right", "value": 5.5, "unit": "m", "expr": "4 + canopy_width / 2", "inputs": ["canopy_width"]},
        {"key": "left_support_min", "value": 2.7, "unit": "m", "expr": "canopy_left + support_inset", "inputs": ["canopy_left", "support_inset"]},
        {"key": "left_support_max", "value": 3.0, "unit": "m", "expr": "left_support_min + 0.3", "inputs": ["left_support_min"]},
        {"key": "right_support_max", "value": 5.3, "unit": "m", "expr": "canopy_right - support_inset", "inputs": ["canopy_right", "support_inset"]},
        {"key": "right_support_min", "value": 5.0, "unit": "m", "expr": "right_support_max - 0.3", "inputs": ["right_support_max"]},
        {"key": "support_height", "value": 2.8, "unit": "m", "expr": "canopy_elevation", "inputs": ["canopy_elevation"], "source_ref": "studio:intent"},
    ]

    def prism(entity_id: str, profile: list[list[object]], height: object, *, elevation: object | None = None) -> dict:
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
        prism("deep-canopy", [["@canopy_left", -1.8], ["@canopy_right", -1.8], ["@canopy_right", 0], ["@canopy_left", 0]], "@canopy_thickness", elevation="@canopy_elevation"),
        prism("deep-support-left", [["@left_support_min", -1.6], ["@left_support_max", -1.6], ["@left_support_max", -1.3], ["@left_support_min", -1.3]], "@support_height"),
        prism("deep-support-right", [["@right_support_min", -1.6], ["@right_support_max", -1.6], ["@right_support_max", -1.3], ["@right_support_min", -1.3]], "@support_height"),
    ]
    return {
        "summary": "Add one editable mass and porch with linked canopy width, support inset and elevation.",
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
        compiler = Mock()
        compiler.compile.side_effect = AssertionError("a direct semantic proposal must not call a model")
        self.client.app.state.intent_compiler = compiler
        before_runs = set((self.root / PROJECT_ID / "runs").iterdir())
        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "sourceRunId": REFERENCE_RUN_ID,
                "semanticEdit": parameterized_prism_edit(),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        compiler.compile.assert_not_called()
        self.assertEqual(set((self.root / PROJECT_ID / "runs").iterdir()), before_runs)
        proposal_id = response.json()["proposalId"]
        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        job = self.finished(self.client, accepted["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        return proposal_id, accepted["candidateId"]

    def _assert_shape(self, shapes, entity_id: str, *, x: float, y: float, z: float, z0: float = 0.0, x0: float | None = None) -> None:
        shape = next(value for name, value in shapes.items() if entity_id in name)
        self.assertTrue(shape.valid and shape.closed and shape.solid_count == 1)
        self.assertAlmostEqual(shape.bbox_max[0] - shape.bbox_min[0], x, places=6)
        self.assertAlmostEqual(shape.bbox_max[1] - shape.bbox_min[1], y, places=6)
        self.assertAlmostEqual(shape.bbox_max[2] - shape.bbox_min[2], z, places=6)
        self.assertAlmostEqual(shape.bbox_min[2], z0, places=6)
        if x0 is not None:
            self.assertAlmostEqual(shape.bbox_min[0], x0, places=6)

    def _run_scalars(self, client, source_run: str, state_digest: str, *utterances: str) -> str:
        before_runs = set((self.root / PROJECT_ID / "runs").iterdir())
        previous = None
        for utterance in utterances:
            response = client.post("/api/proposals", json={
                "stateDigest": state_digest,
                "sourceRunId": source_run,
                "targetComponentId": "portico",
                "utterance": utterance,
                **({"sourceProposalId": previous["proposalId"]} if previous is not None else {}),
            })
            self.assertEqual(response.status_code, 201, response.text)
            previous = response.json()
            self.assertEqual(previous["sourceRunId"], source_run)
            self.assertEqual(previous["baseStateDigest"], state_digest)
        self.assertEqual(set((self.root / PROJECT_ID / "runs").iterdir()), before_runs)
        started = client.post(f"/api/proposals/{previous['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        self.assertEqual(self.finished(client, accepted["jobId"])["status"], "succeeded")
        self.assertEqual(set((self.root / PROJECT_ID / "runs").iterdir()) - before_runs,
                         {self.root / PROJECT_ID / "runs" / accepted["candidateId"]})
        return accepted["candidateId"]

    def test_direct_creation_width_inset_elevation_chain_and_restart_keep_live_bindings(self) -> None:
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

        # Three linked edits share the retained base and make one checkpoint.
        with no_process():
            second = self._run_scalars(
                restarted, first, state_a.json()["stateDigest"],
                "set canopy_width to 4.2 m",
                "set support_inset to 0.4 m",
                "set canopy_elevation to 3.1 m",
            )
        candidate_b = self.candidate(restarted, second)
        exact_b, _ = self.split(candidate_b["artifacts"])
        shapes_b = self.step_shapes(self.bytes_of(restarted, exact_b))
        self._assert_shape(shapes_b, "deep-canopy", x=4.2, y=1.8, z=0.18, z0=3.1, x0=1.9)
        self._assert_shape(shapes_b, "deep-support-left", x=0.3, y=0.3, z=3.1, x0=2.3)
        self._assert_shape(shapes_b, "deep-support-right", x=0.3, y=0.3, z=3.1, x0=5.4)
        self._assert_shape(shapes_b, "deep-main", x=8.0, y=6.0, z=3.3)
        record_b = self.load_kind(second, "state-record")
        values_b = {row["key"]: (row["value"], row["expr"]) for row in record_b["parameters"]}
        self.assertEqual(values_b["canopy_elevation"], (3.1, None))
        self.assertEqual(values_b["support_height"], (3.1, "canopy_elevation"))
        self.assertEqual(values_b["canopy_width"], (4.2, None))
        self.assertEqual(values_b["support_inset"], (0.4, None))
        self.assertEqual(values_b["canopy_left"], (1.9, "4 - canopy_width / 2"))

        restarted_again = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID))
        state_b = restarted_again.get("/api/state", params={"run": second})
        self.assertEqual(state_b.status_code, 200, state_b.text)
        with no_process():
            third = self._run_scalars(
                restarted_again, second, state_b.json()["stateDigest"],
                "set canopy_width to 5.2 m",
                "set main_height to 4.5 m",
            )
        candidate_c = self.candidate(restarted_again, third)
        exact_c, _ = self.split(candidate_c["artifacts"])
        shapes_c = self.step_shapes(self.bytes_of(restarted_again, exact_c))
        self._assert_shape(shapes_c, "deep-main", x=8.0, y=6.0, z=4.5)
        self._assert_shape(shapes_c, "deep-canopy", x=5.2, y=1.8, z=0.18, z0=3.1, x0=1.4)
        self._assert_shape(shapes_c, "deep-support-left", x=0.3, y=0.3, z=3.1, x0=1.8)
        self._assert_shape(shapes_c, "deep-support-right", x=0.3, y=0.3, z=3.1, x0=5.9)
