"""The StateRecord projection: base-attached, and digest-comparable to a run."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from archflow.project.refs import RunRef
from archflow.state.state_record import StateRecord, developed_design_view

from .support import (
    HARNESS_RUN_ID,
    PROJECT_ID,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    add_harness_run,
    make_empty_project,
    make_project,
)


class StateProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, self.root)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.payload = self.client.get("/api/state").json()

    def test_the_projection_reproduces_the_runners_state_digest(self) -> None:
        # The number the runner's receipt carries, recomputed here from the
        # kernel with the runner's own view kwargs.
        run = RunRef(
            PROJECT_ID, REFERENCE_RUN_ID, self.repository.read_head()
        )
        authored = StateRecord.from_dict(
            json.loads(
                self.repository.layout.resolve_relative(
                    RUNNER_RECORD_PATH
                ).read_text(encoding="utf-8")
            )
        )
        expected = developed_design_view(
            authored.bound_to(run),
            run=run,
            portfolio_id="declared-schematic",
            branch_id="runner-v1",
            selection_decision_ref="decision:declared-schematic-selection",
        ).state_digest

        self.assertEqual(self.payload["stateDigest"], expected)
        self.assertEqual(
            self.payload["recordDigest"], authored.bound_to(run).digest
        )
        self.assertEqual(
            self.payload["authoredRecordDigest"], authored.digest
        )
        self.assertNotEqual(
            self.payload["authoredRecordDigest"],
            self.payload["recordDigest"],
        )

    def test_the_projection_names_the_schema_source_and_phase(self) -> None:
        self.assertEqual(
            self.payload["schema"], "StudioStateProjection@2"
        )
        self.assertEqual(self.payload["projectId"], PROJECT_ID)
        self.assertEqual(
            self.payload["recordSource"], RUNNER_RECORD_PATH
        )
        self.assertEqual(
            self.payload["activePhase"], "design_development"
        )

    def test_the_reference_receipt_agrees_with_the_projection(self) -> None:
        self.assertEqual(self.payload["referenceRunSource"], "rule")
        self.assertEqual(
            self.payload["referenceRun"]["runId"], REFERENCE_RUN_ID
        )
        receipt = self.payload["referenceReceipt"]
        self.assertEqual(receipt["runId"], REFERENCE_RUN_ID)
        self.assertEqual(receipt["schema"], "RunnerRunReceipt@3")
        self.assertEqual(
            receipt["designStateDigest"], self.payload["stateDigest"]
        )
        self.assertIs(self.payload["matchesReferenceReceipt"], True)

    def test_the_counts_are_the_records_own(self) -> None:
        self.assertEqual(
            self.payload["counts"],
            {
                "entities": 6,
                "components": 2,
                "parameters": 3,
                "relations": 1,
                "obligations": 0,
                "dependencyEdges": 3,
            },
        )

    def test_the_component_tree_is_the_kernels(self) -> None:
        self.assertIsNone(self.payload["componentTreeError"])
        self.assertEqual(
            self.payload["componentTree"],
            [
                {
                    "componentId": "building",
                    "parentComponentId": None,
                    "semanticKind": "building",
                    "intent": "the demo building",
                    "maturity": "schematic",
                    "revision": 1,
                },
                {
                    "componentId": "portico",
                    "parentComponentId": "building",
                    "semanticKind": "portico",
                    "intent": "the demo portico",
                    "maturity": "schematic",
                    "revision": 1,
                },
            ],
        )

    def test_elements_carry_only_their_scalar_params(self) -> None:
        elements = {
            item["elementId"]: item for item in self.payload["elements"]
        }

        self.assertEqual(
            set(elements), {"portico-base", "portico-cornice"}
        )
        base = elements["portico-base"]
        self.assertEqual(base["componentId"], "portico")
        self.assertEqual(base["producer"], "prism")
        # ``profile`` is a list of points, not a number the UI can offer.
        self.assertEqual(base["numericFields"], {"height": 0.6})
        self.assertEqual(
            elements["portico-cornice"]["numericFields"], {"height": 0.3}
        )

    def test_parameters_carry_their_lock_and_their_inputs(self) -> None:
        parameters = {
            item["key"]: item for item in self.payload["parameters"]
        }

        self.assertEqual(parameters["module"]["lockAuthority"], "client")
        self.assertEqual(parameters["module"]["unit"], "m")
        self.assertEqual(parameters["module"]["value"], 1.2)
        self.assertIsNone(parameters["module"]["expr"])
        self.assertEqual(parameters["module"]["inputs"], [])
        self.assertIsNone(parameters["bay"]["lockAuthority"])
        self.assertEqual(parameters["bay"]["expr"], "2 * module")
        self.assertEqual(parameters["bay"]["inputs"], ["module"])
        self.assertEqual(
            parameters["bay"]["epistemicStatus"], "derived"
        )

    def test_dependency_edges_keep_the_kernels_prefixed_refs(self) -> None:
        self.assertEqual(
            self.payload["dependencyEdges"],
            [
                {
                    "upstreamRef": "entity:portico-cornice",
                    "downstreamRef": "entity:portico-base",
                    "relation": "support",
                    "effect": "requires_revalidation",
                },
                {
                    "upstreamRef": "parameter:module",
                    "downstreamRef": "parameter:bay",
                    "relation": "derives",
                    "effect": "requires_revalidation",
                },
                {
                    "upstreamRef": "parameter:bay",
                    "downstreamRef": "parameter:span",
                    "relation": "derives",
                    "effect": "requires_revalidation",
                },
            ],
        )

    def test_the_stage_binding_keeps_its_nulls(self) -> None:
        self.assertEqual(
            self.payload["stageBinding"],
            {"workflowRef": None, "envelopeRef": None, "stageId": None},
        )

    def test_honesty_says_the_record_carries_no_stage_binding(self) -> None:
        self.assertIn(
            "no stage binding on the authored record",
            self.payload["honesty"],
        )
        self.assertNotIn(
            "0 parameters declared: parameter intents will be "
            "BLOCKED_NEEDS_HUMAN",
            self.payload["honesty"],
        )

    def test_a_query_run_overrides_the_rule(self) -> None:
        add_harness_run(self.repository)

        payload = self.client.get(
            "/api/state", params={"run": HARNESS_RUN_ID}
        ).json()

        self.assertEqual(payload["referenceRunSource"], "query")
        self.assertEqual(
            payload["referenceRun"]["runId"], HARNESS_RUN_ID
        )
        self.assertNotEqual(
            payload["stateDigest"], self.payload["stateDigest"]
        )

    def test_an_unknown_query_run_is_a_404(self) -> None:
        response = self.client.get(
            "/api/state", params={"run": "run-nowhere"}
        )

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["schema"], "StudioError@1")
        self.assertEqual(payload["code"], "RUN_NOT_FOUND")
        self.assertIn("run-nowhere", payload["detail"])

    def test_a_missing_authored_record_names_the_path_it_wanted(
        self,
    ) -> None:
        self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).unlink()

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["code"], "STATE_RECORD_NOT_FOUND")
        self.assertIn(RUNNER_RECORD_PATH, payload["detail"])


class DisagreeingReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, self.root)
        self.repository, _ = make_project(
            self.root, design_state_digest="0" * 64
        )
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_a_receipt_for_another_state_is_reported_as_a_mismatch(
        self,
    ) -> None:
        payload = self.client.get("/api/state").json()

        self.assertEqual(
            payload["referenceReceipt"]["designStateDigest"], "0" * 64
        )
        self.assertIs(payload["matchesReferenceReceipt"], False)
        self.assertIn(
            "projection digest differs from the reference receipt: the "
            f"authored record is not what run {REFERENCE_RUN_ID} executed",
            payload["honesty"],
        )


class ProjectionWithoutAnyRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(_remove_tree, self.root)
        self.repository = make_empty_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_the_projection_binds_to_the_studio_run_and_says_so(
        self,
    ) -> None:
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["referenceRunSource"], "none")
        self.assertEqual(
            payload["referenceRun"]["runId"], "studio-projection"
        )
        self.assertIsNone(payload["referenceReceipt"])
        self.assertIsNone(payload["matchesReferenceReceipt"])
        self.assertIn(
            "no eligible reference run: projection bound to the studio "
            "run id; its digests are not comparable to any receipt",
            payload["honesty"],
        )


def _remove_tree(root: Path) -> None:
    import shutil

    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
