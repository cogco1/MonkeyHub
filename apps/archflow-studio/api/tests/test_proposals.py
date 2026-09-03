"""A selection plus one sentence becomes a typed proposal, or a question.

Nothing here is executed. What ``POST /api/proposals`` returns is a real
``DecisionOperator@2`` — the kernel parses its own operator back out of the
response in these tests — carrying an exact base, no write authority, and the
closure the record itself computes. The other half of the file is the refusals:
every case where the honest answer is a question, asked with this record's real
numbers in it.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from archflow.state.decision_operator import DecisionOperator

from .support import (
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    STRIPPED_RECORD_PAYLOAD,
    make_project,
    runner_state_digest,
    write_runner_record,
)

OTHER_DIGEST = "0" * 64
PERSISTENCE = "in-memory (not version history)"

# The exact question a record with no parameters has to ask, verbatim: it names
# the count and the file somebody would have to author into.
NO_PARAMETERS = (
    "the record declares 0 parameters; parameter intents need parameters "
    "authored into input/runner/state-record.json — which element field did "
    "you mean?"
)


class ProposalTestCase(unittest.TestCase):
    """One real project, and the base its projection currently answers with."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID
        )

    def propose(self, utterance: str, **body: object) -> tuple[int, dict]:
        body.setdefault("stateDigest", self.state_digest)
        body.setdefault("targetComponentId", "portico")
        body["utterance"] = utterance
        response = self.client.post("/api/proposals", json=body)
        return response.status_code, response.json()

    def accepted(self, utterance: str, **body: object) -> dict:
        status, payload = self.propose(utterance, **body)
        self.assertEqual(status, 201, payload)
        return payload

    def blocked(self, utterance: str, **body: object) -> dict:
        status, payload = self.propose(utterance, **body)
        self.assertEqual(status, 422, payload)
        self.assertEqual(payload["code"], "BLOCKED_NEEDS_HUMAN")
        return payload


class ElementFieldProposalTests(ProposalTestCase):
    def test_a_scalar_element_param_becomes_an_exact_base_proposal(
        self,
    ) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(payload["status"], "proposed")
        self.assertEqual(payload["baseStateDigest"], self.state_digest)
        self.assertEqual(
            payload["target"],
            {
                "componentId": "portico",
                "elementId": "portico-base",
                "ref": "entity:portico-base",
                "key": "height",
            },
        )
        # The old value is the record's, not the client's.
        self.assertEqual(
            payload["change"], {"old": 0.6, "new": 2.2, "unit": None}
        )
        self.assertEqual(payload["utterance"], "set height to 2.2")
        self.assertEqual(payload["persistence"], PERSISTENCE)

    def test_the_proposal_carries_a_real_decision_operator(self) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        # The kernel reads its own operator back: nothing about it is a shape
        # this API invented.
        operator = DecisionOperator.from_dict(payload["decisionOperator"])

        self.assertEqual(operator.decision_id, payload["proposalId"])
        self.assertEqual(
            operator.decision_type, "studio.element_param_change"
        )
        self.assertEqual(operator.base_state_digest, self.state_digest)
        self.assertEqual(operator.authority_id, "studio:proposal-only")
        self.assertEqual(operator.intent, "set height to 2.2")
        self.assertEqual(
            operator.preconditions[0].ref, "entity:portico-base"
        )
        self.assertEqual(
            operator.preconditions[0].expected_value.to_python(), 0.6
        )
        self.assertEqual(operator.bindings[0].key, "params.height")
        self.assertEqual(operator.bindings[0].source_ref, "studio:intent")
        self.assertEqual(
            operator.evidence_refs,
            (f"record:{payload['recordDigest']}",),
        )
        # No write authority and nothing compiled: the operator adds no facts
        # and discharges nothing.
        self.assertEqual(operator.add_facts, ())
        self.assertEqual(operator.discharge_obligation_ids, ())

    def test_a_percentage_change_is_computed_from_the_records_value(
        self,
    ) -> None:
        payload = self.accepted(
            "increase height by 20 %", elementId="portico-base"
        )

        self.assertEqual(payload["change"]["old"], 0.6)
        self.assertEqual(payload["change"]["new"], 0.72)

    def test_a_decrease_is_the_same_arithmetic_downwards(self) -> None:
        payload = self.accepted(
            "decrease height by 50 %", elementId="portico-base"
        )

        self.assertEqual(payload["change"]["new"], 0.3)

    def test_an_element_change_reports_what_the_record_says_it_reaches(
        self,
    ) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-cornice"
        )

        self.assertEqual(
            payload["impact"]["direct"], ["entity:portico-cornice"]
        )
        self.assertEqual(
            payload["impact"]["propagated"], ["entity:portico-base"]
        )
        self.assertEqual(
            payload["impact"]["unknownCoverage"],
            {"count": 2, "componentIds": ["building", "portico"]},
        )


class ParameterProposalTests(ProposalTestCase):
    def test_a_parameter_change_propagates_along_the_records_expressions(
        self,
    ) -> None:
        payload = self.accepted("set bay to 3")

        self.assertEqual(payload["target"]["ref"], "parameter:bay")
        self.assertEqual(payload["target"]["elementId"], None)
        self.assertEqual(payload["target"]["key"], "bay")
        self.assertEqual(
            payload["change"], {"old": 2.4, "new": 3, "unit": "m"}
        )
        self.assertEqual(
            payload["impact"]["propagated"], ["parameter:span"]
        )
        operator = DecisionOperator.from_dict(payload["decisionOperator"])
        self.assertEqual(operator.decision_type, "studio.parameter_change")
        self.assertEqual(operator.bindings[0].key, "bay")
        self.assertEqual(operator.invalidates, ("parameter:span",))

    def test_the_parameter_prefix_names_a_parameter_and_never_a_field(
        self,
    ) -> None:
        payload = self.accepted(
            "set parameter:bay to 3", elementId="portico-base"
        )

        self.assertEqual(payload["target"]["ref"], "parameter:bay")

    def test_a_matching_unit_is_accepted(self) -> None:
        payload = self.accepted("set bay to 3 m")

        self.assertEqual(payload["change"]["unit"], "m")

    def test_a_unit_the_parameter_does_not_use_is_a_question(self) -> None:
        payload = self.blocked("set bay to 3000 mm")

        self.assertIn("mm", payload["question"])
        self.assertIn("bay", payload["question"])


class ProtectionTests(ProposalTestCase):
    def test_keeping_something_downstream_makes_the_proposal_a_conflict(
        self,
    ) -> None:
        payload = self.accepted("set bay to 3 keep parameter:span")

        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["protected"], ["parameter:span"])
        self.assertEqual(
            payload["impact"]["conflicts"], ["parameter:span"]
        )
        # A conflict is still a proposal: the user resolves it, the server
        # does not silently drop the change.
        self.assertEqual(payload["change"]["new"], 3)

    def test_keeping_the_very_thing_being_changed_is_a_conflict(self) -> None:
        payload = self.accepted(
            "set height to 2.2 keep entity:portico-base",
            elementId="portico-base",
        )

        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["protected"], ["entity:portico-base"])
        # The status is not a claim beside the impact: the conflicting ref is
        # named in the row that describes the collision.
        self.assertEqual(
            payload["impact"]["conflicts"], ["entity:portico-base"]
        )

    def test_the_status_is_exactly_whether_the_impact_names_a_conflict(
        self,
    ) -> None:
        for utterance, element_id in (
            ("set bay to 3 keep parameter:span", None),
            ("set height to 2.2 keep entity:portico-base", "portico-base"),
            ("set bay to 3 keep entity:portico-base", None),
            ("set height to 2.2", "portico-base"),
        ):
            with self.subTest(utterance=utterance):
                body = {} if element_id is None else {"elementId": element_id}
                payload = self.accepted(utterance, **body)

                self.assertEqual(
                    payload["status"] == "conflict",
                    bool(payload["impact"]["conflicts"]),
                )

    def test_keeping_something_out_of_reach_leaves_the_proposal_proposed(
        self,
    ) -> None:
        payload = self.accepted("set bay to 3 keep entity:portico-base")

        self.assertEqual(payload["status"], "proposed")
        self.assertEqual(payload["impact"]["conflicts"], [])

    def test_a_protected_ref_becomes_a_lock_the_operator_would_add(
        self,
    ) -> None:
        payload = self.accepted("set bay to 3 keep parameter:span")
        operator = DecisionOperator.from_dict(payload["decisionOperator"])

        self.assertEqual(operator.add_locks[0].target_ref, "parameter:span")
        self.assertEqual(operator.add_locks[0].authority_id, "studio:user")

    def test_a_bare_ref_resolves_to_the_one_thing_that_answers_to_it(
        self,
    ) -> None:
        payload = self.accepted("set bay to 3 keep span")

        self.assertEqual(payload["protected"], ["parameter:span"])

    def test_a_keep_ref_the_record_does_not_declare_is_a_question(
        self,
    ) -> None:
        payload = self.blocked("set bay to 3 keep parameter:column-spacing")

        self.assertIn("column-spacing", payload["question"])


class RefusalTests(ProposalTestCase):
    def test_an_utterance_outside_the_grammar_is_answered_with_the_forms(
        self,
    ) -> None:
        payload = self.blocked(
            "make the portico a bit taller", elementId="portico-base"
        )

        # Exactly the four forms a person can type. ``keep`` modifies all four
        # rather than being a fifth thing to try, so it is explained in the
        # sentence instead of listed as an utterance.
        self.assertEqual(
            payload["acceptedForms"],
            [
                "set <field> to <number>[ <unit>]",
                "set <field> = <number>[ <unit>]",
                "increase <field> by <number> %",
                "decrease <field> by <number> %",
            ],
        )
        self.assertIn("keep <ref>", payload["question"])
        # The question is about this element, not about grammar in general.
        self.assertIn("height", payload["question"])

    def test_a_field_the_element_does_not_declare_lists_the_ones_it_does(
        self,
    ) -> None:
        payload = self.blocked("set width to 3", elementId="portico-base")

        self.assertIn("width", payload["question"])
        self.assertIn("height", payload["question"])

    def test_a_polyline_vertex_is_not_a_target(self) -> None:
        # ``params.profile`` is a list of points. Moving a coordinate by prose
        # is the invented number this seam exists to refuse.
        payload = self.blocked(
            "set params.profile to 3", elementId="portico-base"
        )

        self.assertIn("profile", payload["question"])
        self.assertIn("height", payload["question"])

    def test_a_locked_parameter_is_a_question_about_its_authority(
        self,
    ) -> None:
        payload = self.blocked("set module to 1.5")

        self.assertEqual(
            payload["question"],
            "parameter module is locked by client; release it explicitly?",
        )

    def test_an_element_of_another_component_asks_which_was_meant(
        self,
    ) -> None:
        payload = self.blocked(
            "set height to 2.2",
            targetComponentId="building",
            elementId="portico-base",
        )

        self.assertEqual(
            payload["question"],
            "element portico-base belongs to component portico, not "
            "building; which did you mean?",
        )

    def test_a_component_the_record_does_not_declare_is_named(self) -> None:
        payload = self.blocked(
            "set height to 2.2", targetComponentId="east-loggia"
        )

        self.assertIn("east-loggia", payload["question"])

    def test_an_element_the_record_does_not_declare_is_named(self) -> None:
        payload = self.blocked(
            "set height to 2.2", elementId="portico-architrave"
        )

        self.assertIn("portico-architrave", payload["question"])


class BaseTests(ProposalTestCase):
    def test_a_proposal_against_another_state_is_refused(self) -> None:
        status, payload = self.propose(
            "set bay to 3", stateDigest=OTHER_DIGEST
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "STALE_BASE")
        self.assertIn(OTHER_DIGEST, payload["detail"])
        self.assertIn(self.state_digest, payload["detail"])

    def test_a_proposal_naming_another_project_is_refused(self) -> None:
        status, payload = self.propose(
            "set bay to 3", projectId="villa-rotonda-reconstruction"
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "PROJECT_MISMATCH")
        self.assertIn("villa-rotonda-reconstruction", payload["detail"])
        self.assertIn(PROJECT_ID, payload["detail"])

    def test_naming_the_bound_project_is_accepted(self) -> None:
        payload = self.accepted("set bay to 3", projectId=PROJECT_ID)

        self.assertEqual(payload["status"], "proposed")


class NoParametersTests(ProposalTestCase):
    """The villa's shape today: 96 entities and not one parameter."""

    def setUp(self) -> None:
        super().setUp()
        write_runner_record(self.repository, STRIPPED_RECORD_PAYLOAD)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

    def test_a_parameter_intent_names_the_count_and_what_to_author(
        self,
    ) -> None:
        payload = self.blocked("set parameter:module to 1.5")

        self.assertEqual(payload["question"], NO_PARAMETERS)

    def test_a_bare_field_with_no_element_selected_asks_the_same_question(
        self,
    ) -> None:
        payload = self.blocked("set module to 1.5")

        self.assertEqual(payload["question"], NO_PARAMETERS)

    def test_an_element_field_still_proposes_on_such_a_record(self) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(payload["status"], "proposed")
        self.assertEqual(payload["impact"]["propagated"], [])
        self.assertIn(
            "0 dependency edges: impact closure is direct-only",
            payload["impact"]["honesty"],
        )


class ZeroValueTests(ProposalTestCase):
    """A field that is 0 today: no percentage of it is a change."""

    def setUp(self) -> None:
        super().setUp()
        payload = json.loads(json.dumps(RECORD_PAYLOAD))
        for entity in payload["entities"]:
            if entity["entity_id"] == "portico-base":
                entity["fields"]["params"]["height"] = 0
        write_runner_record(self.repository, payload)
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

    def test_a_percentage_of_zero_asks_for_an_absolute_value(self) -> None:
        # Answering 0 to "make it taller" would be a lie with arithmetic in
        # front of it.
        payload = self.blocked(
            "increase height by 20 %", elementId="portico-base"
        )

        self.assertIn("height", payload["question"])
        self.assertIn("set height to", payload["question"])

    def test_setting_it_absolutely_is_still_a_proposal(self) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(payload["change"], {"old": 0, "new": 2.2, "unit": None})


class ProposalStoreTests(ProposalTestCase):
    def test_a_proposal_can_be_read_back_by_its_id(self) -> None:
        created = self.accepted("set bay to 3")

        response = self.client.get(f"/api/proposals/{created['proposalId']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)

    def test_an_id_no_proposal_answers_to_is_a_404_that_names_it(self) -> None:
        response = self.client.get("/api/proposals/studio-000000000000")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "PROPOSAL_NOT_FOUND")
        self.assertIn("studio-000000000000", response.json()["detail"])

    def test_two_proposals_are_two_ids(self) -> None:
        first = self.accepted("set bay to 3")
        second = self.accepted("set bay to 4")

        self.assertNotEqual(first["proposalId"], second["proposalId"])


class ProposalOnlyTests(ProposalTestCase):
    """Proposal-only is a mechanical property, not a promise in a docstring."""

    def _project_files(self) -> dict[str, int]:
        """Every file under the bound project, by path and size."""

        root = self.root / PROJECT_ID
        return {
            str(path.relative_to(root)): path.stat().st_size
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_proposing_writes_nothing_to_the_project(self) -> None:
        before = self._project_files()

        # One of each kind: an element field, a parameter, a conflict, and a
        # refusal — no path through this route may touch the project.
        self.accepted("set height to 2.2", elementId="portico-base")
        self.accepted("set bay to 3 keep parameter:span")
        self.blocked("set module to 1.5")
        stored = self.accepted("set bay to 4")
        self.client.get(f"/api/proposals/{stored['proposalId']}")

        self.assertEqual(self._project_files(), before)
        # Not vacuously true: the fixture project really has files to disturb.
        self.assertGreater(len(before), 0)


class RequestShapeTests(ProposalTestCase):
    def test_a_malformed_state_digest_is_a_request_error(self) -> None:
        status, payload = self.propose(
            "set bay to 3", stateDigest="not-a-digest"
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "REQUEST_INVALID")

    def test_a_body_without_an_utterance_is_a_request_error(self) -> None:
        response = self.client.post(
            "/api/proposals",
            json={
                "stateDigest": self.state_digest,
                "targetComponentId": "portico",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "REQUEST_INVALID")
        self.assertIn("utterance", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
