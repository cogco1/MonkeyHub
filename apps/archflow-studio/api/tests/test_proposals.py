"""A selection plus one sentence becomes a typed proposal, or a question.

Nothing here is executed. What ``POST /api/proposals`` returns is a real
``DecisionOperator@2`` — the kernel parses its own operator back out of the
response in these tests — carrying an exact base, no write authority, and the
closure the record itself computes. The other half of the file is the refusals:
every case where the honest answer is a question, asked with this record's real
numbers in it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.application.proposals import read_refs_of, write_refs_of

from archflow.state.decision_operator import DecisionOperator

from .support import (
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    STRIPPED_RECORD_PAYLOAD,
    make_project,
    retain_runner_receipt,
    runner_state_digest,
    write_runner_record,
)

OTHER_DIGEST = "0" * 64
PERSISTENCE = "in-memory (not version history)"

# The fixture with its one source parameter locked: the chain bay -> span then
# has no control anybody may set, and the refusal has to say so.
LOCKED_SOURCE_PAYLOAD: dict[str, object] = {
    **RECORD_PAYLOAD,
    "parameters": [
        {**parameter, "lock_authority": "client"}  # type: ignore[dict-item]
        if parameter["key"] == "module"  # type: ignore[index]
        else parameter
        for parameter in RECORD_PAYLOAD["parameters"]  # type: ignore[union-attr]
    ],
}

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
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
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


class ProposalAccessTests(ProposalTestCase):
    def test_protected_geometry_is_read_while_declared_parameter_dependents_are_written(self) -> None:
        payload = self.accepted("set module to 1.5 keep entity:portico-base")
        proposal = self.client.app.state.proposals.get(payload["proposalId"])
        self.assertEqual(read_refs_of(proposal), {"entity:portico-base"})
        self.assertEqual(write_refs_of(proposal), {"parameter:module", "parameter:bay", "parameter:span"})

    def test_an_element_edit_does_not_claim_the_whole_component(self) -> None:
        payload = self.accepted("set height to 2.2", elementId="portico-cornice")
        proposal = self.client.app.state.proposals.get(payload["proposalId"])
        self.assertEqual(read_refs_of(proposal), frozenset())
        self.assertEqual(write_refs_of(proposal), {"entity:portico-cornice"})


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
            payload["change"], {"kind": "set_scalar", "old": 0.6, "new": 2.2, "unit": None}
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

    def test_a_unit_on_an_element_field_is_a_question(self) -> None:
        """An element param is a bare number; a unit word cannot be dropped.

        ``set height to 2200 mm`` against a field the record holds in metres
        would propose 2200 into it. The seam converts nothing, so it asks
        instead — the same refusal a parameter gets when the utterance's unit
        is not the record's.
        """

        payload = self.blocked(
            "set height to 2200 mm", elementId="portico-base"
        )

        self.assertEqual(
            payload["detail"], "the element field is a unit-less number"
        )
        self.assertEqual(
            payload["question"],
            "height on portico-base is a bare number in the record and this "
            "seam converts nothing; what is the value in the record's own "
            "units?",
        )

    def test_a_bare_number_on_an_element_field_still_proposes(self) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(payload["change"]["new"], 2.2)
        self.assertIsNone(payload["change"]["unit"])

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
        # the cornice sits on the base; nothing sits on the cornice
        self.assertEqual(payload["impact"]["propagated"], [])
        self.assertEqual(
            payload["impact"]["unknownCoverage"],
            {"count": 2, "componentIds": ["building", "portico"]},
        )


class ParameterProposalTests(ProposalTestCase):
    def test_a_parameter_change_propagates_along_the_records_expressions(
        self,
    ) -> None:
        payload = self.accepted("set module to 1.5")

        self.assertEqual(payload["target"]["ref"], "parameter:module")
        self.assertEqual(payload["target"]["elementId"], None)
        self.assertEqual(payload["target"]["key"], "module")
        self.assertEqual(
            payload["change"], {"kind": "set_scalar", "old": 1.2, "new": 1.5, "unit": "m"}
        )
        # the whole declared chain: bay = 2 * module, span = 2 * bay
        self.assertEqual(
            payload["impact"]["propagated"], ["parameter:bay", "parameter:span"]
        )
        operator = DecisionOperator.from_dict(payload["decisionOperator"])
        self.assertEqual(operator.decision_type, "studio.parameter_change")
        self.assertEqual(operator.bindings[0].key, "module")
        self.assertEqual(operator.invalidates, ("parameter:bay", "parameter:span"))

    def test_the_parameter_prefix_names_a_parameter_and_never_a_field(
        self,
    ) -> None:
        payload = self.accepted(
            "set parameter:module to 1.5", elementId="portico-base"
        )

        self.assertEqual(payload["target"]["ref"], "parameter:module")

    def test_a_matching_unit_is_accepted(self) -> None:
        payload = self.accepted("set module to 1.5 m")

        self.assertEqual(payload["change"]["unit"], "m")

    def test_a_unit_the_parameter_does_not_use_is_a_question(self) -> None:
        payload = self.blocked("set module to 1500 mm")

        self.assertIn("mm", payload["question"])
        self.assertIn("module", payload["question"])


class DerivedParameterTests(ProposalTestCase):
    """A derived parameter is not a control, and the refusal says what is.

    The kernel refuses a scalar written over an expression as a conflicting
    declaration; asking that of a candidate job would fail it after the fact.
    The proposal boundary refuses first, naming the expression, the source
    parameter to set instead with its current value, and the file a
    re-declaration would go into.
    """

    def test_a_derived_parameter_is_refused_with_its_source_named(self) -> None:
        payload = self.blocked("set bay to 3")

        self.assertEqual(payload["detail"], "the parameter is derived, not a control")
        self.assertEqual(
            payload["question"],
            "parameter bay is derived by '2 * module'; its value follows "
            "module. set module (= 1.2 m) instead, or re-declare bay without "
            "an expression in input/runner/state-record.json.",
        )

    def test_a_source_that_is_itself_derived_is_said_to_be(self) -> None:
        payload = self.blocked("set span to 6")

        self.assertIn("parameter span is derived by '2 * bay'", payload["question"])
        self.assertIn("bay (= 2.4 m, itself derived by '2 * module')", payload["question"])

    def test_the_parameter_prefix_is_refused_the_same_way(self) -> None:
        payload = self.blocked("set parameter:bay to 3", elementId="portico-base")

        self.assertIn("parameter bay is derived by '2 * module'", payload["question"])

    def test_a_locked_source_is_named_as_locked(self) -> None:
        write_runner_record(self.repository, LOCKED_SOURCE_PAYLOAD)
        retain_runner_receipt(
            self.repository,
            self.repository.load_run(REFERENCE_RUN_ID),
            design_state_digest=runner_state_digest(
                self.repository, REFERENCE_RUN_ID, LOCKED_SOURCE_PAYLOAD
            ),
            record_payload=LOCKED_SOURCE_PAYLOAD,
        )
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

        payload = self.blocked("set bay to 3")

        self.assertIn("module (= 1.2 m, locked by client)", payload["question"])


class ProtectionTests(ProposalTestCase):
    def test_keeping_something_downstream_makes_the_proposal_a_conflict(
        self,
    ) -> None:
        payload = self.accepted("set module to 1.5 keep parameter:span")

        self.assertEqual(payload["status"], "conflict")
        self.assertEqual(payload["protected"], ["parameter:span"])
        self.assertEqual(
            payload["impact"]["conflicts"], ["parameter:span"]
        )
        # A conflict is still a proposal: the user resolves it, the server
        # does not silently drop the change.
        self.assertEqual(payload["change"]["new"], 1.5)

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
            ("set module to 1.5 keep parameter:span", None),
            ("set height to 2.2 keep entity:portico-base", "portico-base"),
            ("set module to 1.5 keep entity:portico-base", None),
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
        payload = self.accepted("set module to 1.5 keep entity:portico-base")

        self.assertEqual(payload["status"], "proposed")
        self.assertEqual(payload["impact"]["conflicts"], [])

    def test_a_protected_ref_becomes_a_lock_the_operator_would_add(
        self,
    ) -> None:
        payload = self.accepted("set module to 1.5 keep parameter:span")
        operator = DecisionOperator.from_dict(payload["decisionOperator"])

        self.assertEqual(operator.add_locks[0].target_ref, "parameter:span")
        self.assertEqual(operator.add_locks[0].authority_id, "studio:user")

    def test_a_bare_ref_resolves_to_the_one_thing_that_answers_to_it(
        self,
    ) -> None:
        payload = self.accepted("set module to 1.5 keep span")

        self.assertEqual(payload["protected"], ["parameter:span"])

    def test_a_keep_ref_the_record_does_not_declare_is_a_question(
        self,
    ) -> None:
        payload = self.blocked("set module to 1.5 keep parameter:column-spacing")

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
        payload = self.blocked("set plinth to 0.7")

        self.assertEqual(
            payload["question"],
            "parameter plinth is locked by client; release it explicitly?",
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
            "set module to 1.5", stateDigest=OTHER_DIGEST
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "STALE_BASE")
        self.assertIn(OTHER_DIGEST, payload["detail"])
        self.assertIn(self.state_digest, payload["detail"])

    def test_a_proposal_naming_another_project_is_refused(self) -> None:
        status, payload = self.propose(
            "set module to 1.5", projectId="villa-rotonda-reconstruction"
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "PROJECT_MISMATCH")
        self.assertIn("villa-rotonda-reconstruction", payload["detail"])
        self.assertIn(PROJECT_ID, payload["detail"])

    def test_naming_the_bound_project_is_accepted(self) -> None:
        payload = self.accepted("set module to 1.5", projectId=PROJECT_ID)

        self.assertEqual(payload["status"], "proposed")


class NoParametersTests(ProposalTestCase):
    """The villa's shape today: 96 entities and not one parameter."""

    def setUp(self) -> None:
        super().setUp()
        write_runner_record(self.repository, STRIPPED_RECORD_PAYLOAD)
        retain_runner_receipt(
            self.repository,
            self.repository.load_run(REFERENCE_RUN_ID),
            design_state_digest=runner_state_digest(
                self.repository, REFERENCE_RUN_ID, STRIPPED_RECORD_PAYLOAD
            ),
            record_payload=STRIPPED_RECORD_PAYLOAD,
        )
        self.state_digest = self.client.get("/api/state").json()["stateDigest"]

    def test_a_parameter_intent_names_the_count_and_what_to_author(
        self,
    ) -> None:
        payload = self.blocked("set parameter:module to 1.5")

        self.assertEqual(payload["question"], NO_PARAMETERS)

    def test_a_bare_field_with_no_element_selected_asks_the_same_question(
        self,
    ) -> None:
        payload = self.blocked("set plinth to 0.7")

        self.assertEqual(payload["question"], NO_PARAMETERS)

    def test_an_element_field_still_proposes_on_such_a_record(self) -> None:
        payload = self.accepted(
            "set height to 2.2", elementId="portico-base"
        )

        self.assertEqual(payload["status"], "proposed")
        # no parameters, but the cornice still sits on the base: that edge is the record's own
        self.assertEqual(payload["impact"]["propagated"], ["entity:portico-cornice"])
        self.assertTrue(
            any("appear in no dependency edge" in line for line in payload["impact"]["honesty"]),
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
        retain_runner_receipt(
            self.repository,
            self.repository.load_run(REFERENCE_RUN_ID),
            design_state_digest=runner_state_digest(
                self.repository, REFERENCE_RUN_ID, payload
            ),
            record_payload=payload,
        )
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

        self.assertEqual(payload["change"], {"kind": "set_scalar", "old": 0, "new": 2.2, "unit": None})


class ProposalStoreTests(ProposalTestCase):
    def test_a_proposal_can_be_read_back_by_its_id(self) -> None:
        created = self.accepted("set module to 1.5")

        response = self.client.get(f"/api/proposals/{created['proposalId']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)

    def test_an_id_no_proposal_answers_to_is_a_404_that_names_it(self) -> None:
        response = self.client.get("/api/proposals/studio-000000000000")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "PROPOSAL_NOT_FOUND")
        self.assertIn("studio-000000000000", response.json()["detail"])

    def test_two_proposals_are_two_ids(self) -> None:
        first = self.accepted("set module to 1.5")
        second = self.accepted("set module to 1.6")

        self.assertNotEqual(first["proposalId"], second["proposalId"])


class ProposalOnlyTests(ProposalTestCase):
    """Proposal-only is a mechanical property, not a promise in a docstring."""

    def _project_files(self) -> dict[str, str]:
        """Every file under the bound project, by path and content digest.

        Content, not size: a route that rewrote one authored number in place
        would leave every path and every byte count exactly as it found them,
        and that is the one write this test exists to catch.
        """

        root = self.root / PROJECT_ID
        return {
            str(path.relative_to(root)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_proposing_writes_nothing_to_the_project(self) -> None:
        before = self._project_files()

        # One of each kind: an element field, a parameter, a conflict, and a
        # refusal — no path through this route may touch the project.
        self.accepted("set height to 2.2", elementId="portico-base")
        self.accepted("set module to 1.5 keep parameter:span")
        self.blocked("set plinth to 0.7")
        stored = self.accepted("set module to 1.6")
        self.client.get(f"/api/proposals/{stored['proposalId']}")

        self.assertEqual(self._project_files(), before)
        # Not vacuously true: the fixture project really has files to disturb.
        self.assertGreater(len(before), 0)


class DirectSemanticProposalTests(ProposalTestCase):
    def submit(self, edit: dict, **body) -> dict:
        response = self.client.post("/api/proposals", json={
            "stateDigest": self.state_digest, "semanticEdit": edit, **body,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_existing_entity_metadata_upserts_preserve_omitted_fields(self):
        from archflow.state.state_record import apply_state_record_operator
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.projection import project_state

        base = project_state(bound_project(self.client.app.state)).record
        for edit in (
            {"entity_id": "portico-base", "basis_refs": ["input:review-evidence"]},
            {"entity_id": "portico", "parent_id": None},
        ):
            with self.subTest(edit=edit):
                payload = self.submit({"summary": "Revise the declared entity context.", "entities": [edit]})
                proposal = self.client.app.state.proposals.get(payload["proposalId"])
                after = apply_state_record_operator(base, proposal.state_record_operator)
                entity = next(row for row in after.entities if row.entity_id == edit["entity_id"])
                original = next(row for row in base.entities if row.entity_id == edit["entity_id"])
                self.assertEqual(entity.fields, original.fields)
                for field, value in edit.items():
                    self.assertEqual(entity.to_dict()[field], value)
                self.assertEqual(payload["change"]["edits"]["entities"][0]["fields"], {})

    def test_semantic_upserts_removals_and_scalar_continuations_share_one_unexecuted_base(self):
        compiler = Mock()
        compiler.compile.side_effect = AssertionError("direct edits must not invoke the compiler")
        self.client.app.state.intent_compiler = compiler
        before_runs = set((self.root / PROJECT_ID / "runs").iterdir())
        first = self.submit({
            "summary": "Adjust the module, retaining the portico base.",
            "parameters": [{"key": "module", "value": 1.5}, {"key": "temporary-control", "value": 2, "unit": "m"}],
            "protected": ["entity:portico-base"], "kept": ["The portico base remains unchanged."],
        }, sourceRunId=REFERENCE_RUN_ID)
        original = self.client.get(f"/api/proposals/{first['proposalId']}").json()
        second = self.submit({
            "summary": "Revise the same module and remove the unused control.",
            "parameters": [{"key": "module", "value": 1.8}],
            "removeParameterKeys": ["temporary-control"],
        }, sourceProposalId=first["proposalId"])
        third = self.accepted("set module to 2 m", sourceProposalId=second["proposalId"])
        edits = third["change"]["edits"]
        self.assertEqual({row["key"]: row["value"] for row in edits["parameters"]}, {"module": 2, "bay": 4, "span": 8})
        self.assertEqual(edits["removeParameterKeys"], [])
        self.assertEqual(third["sourceRunId"], REFERENCE_RUN_ID)
        self.assertEqual(third["baseStateDigest"], self.state_digest)
        self.assertIn("entity:portico-base", third["protected"])
        self.assertEqual(third["change"]["kept"], ["The portico base remains unchanged."])
        self.assertEqual(self.client.get(f"/api/proposals/{first['proposalId']}").json(), original)
        self.assertEqual(set((self.root / PROJECT_ID / "runs").iterdir()), before_runs)
        compiler.compile.assert_not_called()

    def test_semantic_input_refuses_stale_base_mixed_modes_and_undeclared_fields_without_writes(self):
        edit = {"summary": "Adjust the module.", "parameters": [{"key": "module", "value": 1.5}]}
        before = self.client.app.state.proposals.for_state(self.state_digest)
        for additions, expected, code in (
            ({"stateDigest": OTHER_DIGEST}, 409, "STALE_BASE"),
            ({"projectId": "wrong-project"}, 403, "PROJECT_MISMATCH"),
            ({"targetComponentId": "portico", "utterance": "set module to 1.5"}, 422, "REQUEST_INVALID"),
            ({"semanticEdit": {**edit, "geometryProgram": {}}}, 422, "REQUEST_INVALID"),
            ({"semanticEdit": {"summary": "Invalid parameter.", "parameters": [{"key": "new-control", "value": 2}]}}, 422, "SEMANTIC_EDIT_INVALID"),
        ):
            with self.subTest(code=code, additions=additions):
                response = self.client.post("/api/proposals", json={
                    "stateDigest": self.state_digest, "semanticEdit": edit, **additions,
                })
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(response.json()["code"], code, response.text)
        self.assertEqual(self.client.app.state.proposals.for_state(self.state_digest), before)

    def test_direct_semantic_keep_and_original_stage_survive_continuation(self):
        from .test_working_copies import register_model

        model = register_model(self.client, REFERENCE_RUN_ID, self.state_digest,
                               (Path(__file__).parent / "fixtures/model-source-a.3dm").read_bytes())["modelSource"]
        initialized = self.client.post("/api/design-stages/initialize", json={"projectId": PROJECT_ID, "modelSource": model})
        self.assertEqual(initialized.status_code, 201, initialized.text)
        stage_ref = initialized.json()["stageRef"]
        first = self.submit({"summary": "Adjust module.", "parameters": [{"key": "module", "value": 1.5}]},
                            sourceStageRef=stage_ref, keep=["entity:portico-base"])
        second = self.submit({"summary": "Adjust module again.", "parameters": [{"key": "module", "value": 1.8}]},
                             sourceProposalId=first["proposalId"])
        self.assertEqual(second["sourceStageRef"], stage_ref)
        self.assertEqual(second["sourceRunId"], first["sourceRunId"])
        status, refused = self.propose("set height to 2.2", elementId="portico-base", sourceProposalId=second["proposalId"])
        self.assertEqual(status, 409, refused)
        self.assertEqual(refused["code"], "PROPOSAL_CHAIN_CONFLICT")

    def test_openapi_accepts_minimal_upserts_and_exposes_bound_profile_coordinates(self):
        from jsonschema import Draft202012Validator

        openapi = self.client.app.openapi()
        schema = openapi["components"]["schemas"]["ProposalRequestDto"]
        validator = Draft202012Validator({**schema, "components": openapi["components"]})
        payload = {"stateDigest": self.state_digest, "semanticEdit": {
            "summary": "Adjust the existing module.", "parameters": [{"key": "module", "value": 1.5}],
        }}
        validator.validate(payload)
        self.submit(payload["semanticEdit"])
        self.assertFalse(validator.is_valid({**payload, "utterance": "set module to 1.5", "targetComponentId": "portico"}))
        edit = openapi["components"]["schemas"]["SemanticEditRequestDto"]
        entities = edit["properties"]["entities"]["items"]["anyOf"]
        element = next(item for item in entities if item["properties"]["schema"]["enum"] == ["Element@1"])
        prism = next(item for item in element["properties"]["fields"]["anyOf"]
                     if item["properties"]["producer"]["enum"] == ["prism"])
        profile = prism["properties"]["params"]["properties"]["profile"]
        Draft202012Validator(profile).validate([[0, 0], ["@canopy_width", 0], ["@canopy_width", 2], [0, 2]])


class RequestShapeTests(ProposalTestCase):
    def test_a_malformed_state_digest_is_a_request_error(self) -> None:
        status, payload = self.propose(
            "set module to 1.5", stateDigest="not-a-digest"
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
