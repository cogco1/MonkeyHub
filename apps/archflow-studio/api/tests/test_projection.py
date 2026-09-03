"""The StateRecord projection: base-attached, and digest-comparable to a run."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
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
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    STRIPPED_RECORD_PAYLOAD,
    add_harness_run,
    add_later_run,
    add_unreadable_run,
    make_empty_project,
    make_project,
    missing_workflow_ref,
    unlistable_run,
    write_runner_record,
)

# The kernel's own sentence when a record carries nothing to stand the
# developed-design view on. Pinned here because the API repeats it verbatim.
NO_EVIDENCE = "a developed-design view needs at least one evidence ref"


class StateProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
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
        # The record's content identity, read off the same bound record.
        self.assertEqual(
            self.payload["recordDigest"], authored.bound_to(run).digest
        )

    def test_the_projection_names_its_source_and_phase(self) -> None:
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
        self.assertEqual(receipt["receiptSchema"], "RunnerRunReceipt@3")
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
                "dependencyEdges": 5,
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
                    "semanticKind": "controlled-entry",
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
                    "upstreamRef": "entity:portico-base",
                    "downstreamRef": "entity:portico-cornice",
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
                {
                    "upstreamRef": "entity:level-ground",
                    "downstreamRef": "entity:portico-base",
                    "relation": "base",
                    "effect": "requires_revalidation",
                },
                {
                    "upstreamRef": "entity:portico-base",
                    "downstreamRef": "entity:portico-cornice",
                    "relation": "base",
                    "effect": "invalidates",
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
        self.addCleanup(shutil.rmtree, self.root, True)
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
        self.addCleanup(shutil.rmtree, self.root, True)
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

    def test_a_record_declaring_nothing_says_what_it_cannot_check(
        self,
    ) -> None:
        write_runner_record(self.repository, STRIPPED_RECORD_PAYLOAD)

        payload = self.client.get("/api/state").json()

        self.assertEqual(payload["counts"]["parameters"], 0)
        self.assertEqual(payload["counts"]["relations"], 0)
        # the two elements still reference level-ground: those are edges the kernel derives
        self.assertEqual(payload["counts"]["dependencyEdges"], 2)
        for line in (
            "0 parameters declared: parameter intents will be "
            "BLOCKED_NEEDS_HUMAN",
            "0 relations declared: relation checks are unchecked by "
            "construction",
        ):
            self.assertIn(line, payload["honesty"])


class UnreadableRunTests(unittest.TestCase):
    """One corrupt run directory must not cost the client every answer."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.broken = add_unreadable_run(self.repository)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_the_binding_still_answers_with_the_rules_pick(self) -> None:
        response = self.client.get("/api/project")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["referenceRun"]["runId"], REFERENCE_RUN_ID
        )

    def test_the_projection_answers_and_names_what_it_skipped(self) -> None:
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["referenceRun"]["runId"], REFERENCE_RUN_ID
        )
        self.assertIn(
            "1 run directory could not be read and was skipped by the "
            f"reference-run rule: {self.broken}",
            payload["honesty"],
        )

    def test_two_skipped_runs_are_counted_in_the_plural(self) -> None:
        """The line is shown verbatim; it has to be a sentence."""

        second = add_unreadable_run(self.repository, run_id="broken-two")

        payload = self.client.get("/api/state").json()

        self.assertIn(
            "2 run directories could not be read and were skipped by the "
            f"reference-run rule: {self.broken}, {second}",
            payload["honesty"],
        )

    def test_a_named_run_names_the_skipped_runs_too(self) -> None:
        """Answering for the run the caller asked for hides nothing else.

        The survey's tolerance and its confession are one thing: a projection
        that skipped a run directory says so whether the run it answers for
        was chosen by the rule or named in the request.
        """

        payload = self.client.get(
            "/api/state", params={"run": REFERENCE_RUN_ID}
        ).json()

        self.assertEqual(payload["referenceRunSource"], "query")
        self.assertIn(
            "1 run directory could not be read and was skipped by the "
            f"reference-run rule: {self.broken}",
            payload["honesty"],
        )

    def test_a_run_the_caller_names_must_still_exist(self) -> None:
        response = self.client.get(
            "/api/state", params={"run": self.broken}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "RUN_NOT_FOUND")

    def test_a_named_run_whose_records_will_not_list_is_a_404(self) -> None:
        """The named-run path gets the survey's tolerance, not a 500.

        This run's manifest is intact, so nothing refuses it before its
        records are read; the repository then refuses those. That is a fact
        about a run, and it arrives as the refusal that names it.
        """

        add_later_run(self.repository, run_id="run-005")
        unlistable_run(self.repository, run_id="run-005")

        response = self.client.get("/api/state", params={"run": "run-005"})

        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertEqual(body["code"], "RUN_NOT_FOUND")
        self.assertIn("run-005", body["detail"])


class MalformedRecordTests(unittest.TestCase):
    """A record that is there but unreadable is a project fault, not a bug."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def _write(self, text: str) -> None:
        self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).write_text(text, encoding="utf-8")

    def test_a_truncated_record_is_a_422(self) -> None:
        self._write('{"schema": "StateRecord@1", "project_id": "demo')

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], "STATE_RECORD_INVALID")
        self.assertIn(RUNNER_RECORD_PATH, payload["detail"])

    def test_a_record_whose_schema_drifted_is_a_422(self) -> None:
        self._write(json.dumps({"schema": "StateRecord@99", "entities": []}))

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["code"], "STATE_RECORD_INVALID"
        )

    def test_a_record_missing_a_required_field_is_a_422(self) -> None:
        self._write(json.dumps({"schema": "StateRecord@1"}))

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["code"], "STATE_RECORD_INVALID"
        )

    def test_an_element_without_its_own_fields_is_a_422(self) -> None:
        """A record that parses and then cannot be read is still the record's.

        ``StateRecord`` validates no per-schema fields, so an ``Element@1``
        with an empty ``fields`` mapping parses and fails where the projection
        reads it. That is a fault in what somebody authored, not a bug in the
        API, and it must arrive as the refusal that names the field.
        """

        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        for entity in payload["entities"]:
            if entity["entity_id"] == "portico-base":
                entity["fields"] = {}
        self._write(json.dumps(payload))

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "STATE_RECORD_INVALID")
        self.assertIn(RUNNER_RECORD_PATH, body["detail"])
        self.assertIn("producer", body["detail"])

    def test_a_record_authored_for_another_project_is_a_422(self) -> None:
        """Binding is where that is found out, and it is still the record."""

        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        payload["project_id"] = "some-other-project"
        self._write(json.dumps(payload))

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["code"], "STATE_RECORD_INVALID")
        self.assertIn(RUNNER_RECORD_PATH, body["detail"])
        self.assertIn(
            "cannot bind a record to a run of another project", body["detail"]
        )

    def test_a_record_written_in_another_encoding_is_a_422(self) -> None:
        """Undecodable bytes are a record fault, not an unhandled exception."""

        self.repository.layout.resolve_relative(
            RUNNER_RECORD_PATH
        ).write_bytes(
            json.dumps(RECORD_PAYLOAD)
            .replace("demo option", "d\xe9mo option")
            .encode("latin-1")
        )

        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "STATE_RECORD_INVALID")
        self.assertIn(RUNNER_RECORD_PATH, body["detail"])


class UnviewableRecordTests(unittest.TestCase):
    """A record the kernel will not bind still says what it declares.

    ``developed_design_view`` refuses three authoring faults with one
    ``StateRecordError``. Failing to arrange a record's components is not a
    claim that they are absent, so ``GET /api/state`` answers with the
    entities and names what the kernel refused — and every route that would
    have to *stand on* that view refuses instead of proceeding without it.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        payload["evidence_refs"] = []
        write_runner_record(self.repository, payload)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_the_projection_serves_the_record_and_names_the_refusal(
        self,
    ) -> None:
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["componentTreeError"], NO_EVIDENCE)
        self.assertIsNone(payload["componentTree"])
        # The entities are the record's answer and they are still served.
        self.assertEqual(payload["counts"]["entities"], 6)
        self.assertEqual(payload["counts"]["components"], 2)
        self.assertEqual(len(payload["elements"]), 2)
        self.assertEqual(len(payload["parameters"]), 3)
        self.assertEqual(len(payload["dependencyEdges"]), 5)
        self.assertIn(
            f"component tree unavailable: {NO_EVIDENCE}", payload["honesty"]
        )
        # No view, no bound digest: the number a receipt cites is absent
        # rather than invented, and the comparison stays unmade.
        self.assertIsNone(payload["stateDigest"])
        self.assertIsNone(payload["activePhase"])
        self.assertIsNone(payload["matchesReferenceReceipt"])

    def test_a_proposal_on_a_record_with_no_view_is_refused(self) -> None:
        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": "a" * 64,
                "targetComponentId": "portico",
                "elementId": "portico-base",
                "utterance": "set height to 2.2",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["code"], "STATE_RECORD_INVALID")
        self.assertIn(RUNNER_RECORD_PATH, body["detail"])
        self.assertIn(NO_EVIDENCE, body["detail"])

    def test_a_pick_on_a_record_with_no_view_is_refused(self) -> None:
        response = self.client.post(
            "/api/pick/resolve",
            json={
                "stateDigest": "a" * 64,
                "userStrings": {"archflow:component": "portico"},
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["code"], "STATE_RECORD_INVALID"
        )


class HarnessRuleTests(unittest.TestCase):
    """The rule excludes harness runs positively, and nothing else."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_a_newer_run_with_a_project_workflow_becomes_the_reference(
        self,
    ) -> None:
        add_later_run(
            self.repository,
            run_id="run-003",
            workflow_id="villa-project-workflow",
        )

        payload = self.client.get("/api/state").json()

        self.assertEqual(payload["referenceRun"]["runId"], "run-003")
        self.assertEqual(payload["referenceRunSource"], "rule")

    def test_a_newer_harness_run_is_excluded(self) -> None:
        add_harness_run(self.repository)

        payload = self.client.get("/api/state").json()

        self.assertEqual(
            payload["referenceRun"]["runId"], REFERENCE_RUN_ID
        )

    def test_a_workflow_that_will_not_load_stays_eligible_and_says_so(
        self,
    ) -> None:
        add_later_run(
            self.repository,
            run_id="run-004",
            workflow_ref=missing_workflow_ref("run-004"),
        )

        payload = self.client.get("/api/state").json()

        self.assertEqual(payload["referenceRun"]["runId"], "run-004")
        self.assertIn(
            "reference run's workflow record could not be loaded; harness "
            "status unknown",
            payload["honesty"],
        )


class ReceiptWithoutADigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root, design_state_digest=None)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_a_receipt_claiming_no_digest_leaves_the_check_unmade(
        self,
    ) -> None:
        payload = self.client.get("/api/state").json()

        self.assertEqual(
            payload["referenceRun"]["runId"], REFERENCE_RUN_ID
        )
        self.assertIsNone(
            payload["referenceReceipt"]["designStateDigest"]
        )
        # Unchecked is not violated: no mismatch line either.
        self.assertIsNone(payload["matchesReferenceReceipt"])
        self.assertNotIn(
            "projection digest differs from the reference receipt: the "
            f"authored record is not what run {REFERENCE_RUN_ID} executed",
            payload["honesty"],
        )


if __name__ == "__main__":
    unittest.main()
