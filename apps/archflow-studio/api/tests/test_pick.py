"""A picked object resolves to one semantic component, or says why not.

The strings posted here are the ones a real villa export writes: the M088
``archflow:*`` set, read off ``RhinoCadExecutionReceipt@4``'s
``object_user_strings`` and ``document_user_strings``. The viewer forwards them
verbatim, so these tests post exactly what a ``.3dm`` carries and never a shape
the API invented for itself.
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

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    make_empty_project,
    make_project,
    retain_runner_receipt,
    runner_state_digest,
    write_runner_record,
)

# The program digest a document carries. Opaque here: the test asserts it
# travels, not what it means.
PROGRAM_DIGEST = "e" * 64
OTHER_DIGEST = "0" * 64


def _prefixed_elements_payload() -> dict:
    """The fixture record with ``portico`` demoted from component to element.

    ``building`` then holds elements ``portico``, ``portico-base`` and
    ``portico-cornice``: one element id is a prefix of another under one
    component, which is the only shape in which the tie-break rule can be
    observed at all.
    """

    payload = json.loads(json.dumps(RECORD_PAYLOAD))
    entities = []
    for entity in payload["entities"]:
        if entity["entity_id"] == "portico":
            continue
        if entity.get("parent_id") == "portico":
            entity["parent_id"] = "building"
            entity["fields"]["component_id"] = "building"
        entities.append(entity)
    entities.append(
        {
            "entity_id": "portico",
            "schema": "Element@1",
            "parent_id": "building",
            "fields": {
                "component_id": "building",
                "producer": "prism",
                "references": {"base": {"level": "level-ground"}},
                "params": {
                    "profile": [[0, 0], [6, 0], [6, 4], [0, 4]],
                    "height": 4.0,
                },
            },
            "basis_refs": [EVIDENCE],
        }
    )
    payload["entities"] = entities
    return payload


def object_strings(
    component: str,
    *,
    object_name: str | None,
    producer_op: str,
) -> dict[str, str]:
    """Every string a villa export stamps on one object it produced."""

    strings = {
        "archflow:bindings": f"binding-{component}",
        "archflow:commitments": "commitment:as-built-current-massing",
        "archflow:component": component,
        "archflow:evidence": f"project://{PROJECT_ID}/runs/x/records/y.json",
        "archflow:operation_ref": f"cad-operation:{producer_op}",
        "archflow:producer_op": producer_op,
    }
    if object_name is not None:
        strings["archflow:object_ref"] = f"cad-object:{object_name}"
    return strings


def document_strings(
    digest: str | None,
    *,
    run_id: str = REFERENCE_RUN_ID,
) -> dict[str, str]:
    """The document-level strings the exporter writes on the file itself."""

    strings = {
        "archflow:project_id": PROJECT_ID,
        "archflow:run_id": run_id,
        "archflow:program_digest": PROGRAM_DIGEST,
        "archflow:length_unit": "meter",
        "archflow:up_axis": "Z-up",
    }
    if digest is not None:
        strings["archflow:design_state_digest"] = digest
    return strings


class PickTestCase(unittest.TestCase):
    """One real project, and the digest its projection currently answers with."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        # Computed from the kernel with the runner's own view kwargs, so the
        # base a pick is checked against is production's number, not the API's.
        self.state_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID
        )

    def resolve(self, **body: object) -> tuple[int, dict]:
        body.setdefault("stateDigest", self.state_digest)
        response = self.client.post("/api/pick/resolve", json=body)
        return response.status_code, response.json()


class ResolvedPickTests(PickTestCase):
    def test_an_element_row_resolves_to_its_component_and_element(
        self,
    ) -> None:
        status, payload = self.resolve(
            userStrings=object_strings(
                "portico",
                object_name="obj-portico-cornice-0",
                producer_op="portico-cornice-0",
            ),
            documentUserStrings=document_strings(self.state_digest),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "resolved")
        self.assertEqual(payload["componentId"], "portico")
        self.assertEqual(payload["elementId"], "portico-cornice")
        self.assertEqual(payload["operationId"], "portico-cornice-0")
        self.assertIsNone(payload["detail"])

    def test_an_object_name_that_is_the_element_id_resolves_it(self) -> None:
        status, payload = self.resolve(
            userStrings=object_strings(
                "portico",
                object_name="obj-portico-base",
                producer_op="portico-base",
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["elementId"], "portico-base")

    def test_an_object_that_is_no_element_row_resolves_the_component_only(
        self,
    ) -> None:
        # Openings are the villa's real case: they carry a component and a
        # producer op, and no ``Element@1`` row answers for them.
        status, payload = self.resolve(
            userStrings=object_strings(
                "portico",
                object_name="obj-door-leaf-door-0",
                producer_op="door-leaf-door-0",
            ),
        )

        self.assertEqual(status, 200)
        # Visible in the model, missing from the catalog: the component answers,
        # the element does not, and the word says so rather than "resolved".
        self.assertEqual(payload["status"], "MODEL_VISIBLE_CATALOG_MISSING")
        self.assertEqual(payload["componentId"], "portico")
        self.assertIsNone(payload["elementId"])
        self.assertEqual(payload["operationId"], "door-leaf-door-0")
        self.assertIn("no Element@1 row", payload["detail"])

    def test_the_object_name_answers_when_the_strings_carry_no_ref(
        self,
    ) -> None:
        status, payload = self.resolve(
            userStrings=object_strings(
                "portico", object_name=None, producer_op="portico-base-0"
            ),
            objectName="obj-portico-base-0",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["elementId"], "portico-base")

    def test_a_component_named_by_no_element_still_resolves(self) -> None:
        status, payload = self.resolve(
            userStrings=object_strings(
                "building",
                object_name="obj-building-mass",
                producer_op="building-mass",
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "MODEL_VISIBLE_CATALOG_MISSING")
        self.assertEqual(payload["componentId"], "building")
        self.assertIsNone(payload["elementId"])

    def test_an_element_of_another_component_is_not_this_picks_element(
        self,
    ) -> None:
        # The object claims ``building`` and carries a name only ``portico``'s
        # elements answer to. Naming ``portico-base`` here would put a change on
        # an element of a component nobody clicked, so the element is left
        # unresolved and the component still answers.
        status, payload = self.resolve(
            userStrings=object_strings(
                "building",
                object_name="obj-portico-base-0",
                producer_op="portico-base-0",
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["componentId"], "building")
        self.assertIsNone(payload["elementId"])


class ElementTieBreakTests(unittest.TestCase):
    """Two elements under one component, one of them a prefix of the other."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        payload = _prefixed_elements_payload()
        self.repository = make_empty_project(self.root)
        write_runner_record(self.repository, payload)
        run = self.repository.create_run(REFERENCE_RUN_ID)
        expected_digest = runner_state_digest(
            self.repository, REFERENCE_RUN_ID, payload
        )
        retain_runner_receipt(
            self.repository,
            run,
            design_state_digest=expected_digest,
            record_payload=payload,
        )
        self.client = TestClient(
            create_app(StudioSettings(project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        self.state_digest = state.json()["stateDigest"]
        self.assertEqual(self.state_digest, expected_digest)

    def test_the_longest_element_id_that_prefixes_the_name_wins(self) -> None:
        response = self.client.post(
            "/api/pick/resolve",
            json={
                "stateDigest": self.state_digest,
                "userStrings": object_strings(
                    "building",
                    object_name="obj-portico-base-0",
                    producer_op="portico-base-0",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        # ``portico`` also prefixes the name; ``portico-base`` is the element
        # that actually produced the object.
        self.assertEqual(response.json()["elementId"], "portico-base")

    def test_the_shorter_element_still_answers_for_its_own_objects(
        self,
    ) -> None:
        response = self.client.post(
            "/api/pick/resolve",
            json={
                "stateDigest": self.state_digest,
                "userStrings": object_strings(
                    "building",
                    object_name="obj-portico-0",
                    producer_op="portico-0",
                ),
            },
        )

        self.assertEqual(response.json()["elementId"], "portico")


class UnresolvedPickTests(PickTestCase):
    def test_an_object_with_no_archflow_strings_is_unbound(self) -> None:
        status, payload = self.resolve(
            userStrings={"Layer": "Default", "note": "drawn by hand"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unbound")
        self.assertIsNone(payload["componentId"])
        self.assertIsNone(payload["elementId"])
        self.assertIsNone(payload["operationId"])
        self.assertEqual(
            payload["detail"],
            "the picked object carries no archflow identity; it is not "
            "bound to this project",
        )

    def test_an_object_with_no_strings_at_all_is_unbound(self) -> None:
        status, payload = self.resolve(userStrings={})

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unbound")

    def test_a_component_the_record_does_not_declare_is_named(self) -> None:
        status, payload = self.resolve(
            userStrings=object_strings(
                "east-loggia",
                object_name="obj-east-loggia-slab",
                producer_op="east-loggia-slab",
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unknown_component")
        self.assertIsNone(payload["componentId"])
        self.assertIsNone(payload["elementId"])
        self.assertIn("east-loggia", payload["detail"])

    def test_archflow_strings_that_name_no_component_resolve_nothing(
        self,
    ) -> None:
        status, payload = self.resolve(
            userStrings={"archflow:producer_op": "portico-base-0"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unknown_component")
        self.assertIsNone(payload["componentId"])
        self.assertIn("archflow:component", payload["detail"])

    def test_an_unresolved_pick_still_reports_the_documents_source(
        self,
    ) -> None:
        status, payload = self.resolve(
            userStrings={"Layer": "Default"},
            documentUserStrings=document_strings(self.state_digest),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["sourceState"], "current")
        self.assertEqual(payload["sourceRun"], REFERENCE_RUN_ID)


class SourceStateTests(PickTestCase):
    def _resolve_with(self, documents: dict[str, str] | None) -> dict:
        body: dict[str, object] = {
            "userStrings": object_strings(
                "portico",
                object_name="obj-portico-base",
                producer_op="portico-base",
            )
        }
        if documents is not None:
            body["documentUserStrings"] = documents
        status, payload = self.resolve(**body)
        self.assertEqual(status, 200)
        return payload

    def test_a_document_on_the_current_state_says_current(self) -> None:
        payload = self._resolve_with(document_strings(self.state_digest))

        self.assertEqual(payload["sourceState"], "current")
        self.assertEqual(payload["sourceRun"], REFERENCE_RUN_ID)
        self.assertEqual(payload["sourceProgramDigest"], PROGRAM_DIGEST)

    def test_a_document_from_an_older_state_is_stale_and_not_an_error(
        self,
    ) -> None:
        # Viewing an older export is legitimate; the base is enforced when a
        # proposal is made, not when something is clicked.
        payload = self._resolve_with(
            document_strings(OTHER_DIGEST, run_id="run-000")
        )

        self.assertEqual(payload["status"], "resolved")
        self.assertEqual(payload["sourceState"], "stale")
        self.assertEqual(payload["sourceRun"], "run-000")

    def test_a_document_claiming_no_digest_leaves_the_source_unknown(
        self,
    ) -> None:
        payload = self._resolve_with(document_strings(None))

        self.assertEqual(payload["sourceState"], "unknown")
        self.assertEqual(payload["sourceRun"], REFERENCE_RUN_ID)

    def test_no_document_strings_at_all_leaves_the_source_unknown(
        self,
    ) -> None:
        payload = self._resolve_with(None)

        self.assertEqual(payload["sourceState"], "unknown")
        self.assertIsNone(payload["sourceRun"])
        self.assertIsNone(payload["sourceProgramDigest"])


class StaleBaseTests(PickTestCase):
    def test_a_pick_against_another_state_is_a_conflict(self) -> None:
        status, payload = self.resolve(
            stateDigest=OTHER_DIGEST,
            userStrings=object_strings(
                "portico",
                object_name="obj-portico-base",
                producer_op="portico-base",
            ),
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "STALE_BASE")
        self.assertEqual(sorted(payload), ["code", "detail"])
        # Both numbers are named: the client cannot tell which is which
        # otherwise, and it has to re-project against the one that is current.
        self.assertIn(OTHER_DIGEST, payload["detail"])
        self.assertIn(self.state_digest, payload["detail"])

    def test_a_malformed_state_digest_is_a_request_error(self) -> None:
        status, payload = self.resolve(
            stateDigest="not-a-digest",
            userStrings={"archflow:component": "portico"},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "REQUEST_INVALID")
        self.assertIn("stateDigest", payload["detail"])

    def test_an_uppercase_state_digest_is_a_request_error(self) -> None:
        status, payload = self.resolve(
            stateDigest=self.state_digest.upper(),
            userStrings={"archflow:component": "portico"},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "REQUEST_INVALID")

    def test_a_body_without_user_strings_is_a_request_error(self) -> None:
        status, payload = self.resolve()

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "REQUEST_INVALID")
        self.assertIn("userStrings", payload["detail"])


if __name__ == "__main__":
    unittest.main()
