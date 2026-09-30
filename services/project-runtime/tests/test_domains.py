"""``/api/domains``: the registry's own description, and one bound readiness answer.

``GET /api/domains`` needs no project at all: it is
``monkeyarch.capabilities.domain_readiness.DOMAINS``, described.
``GET /api/domains/{domain}/readiness`` asks whether a domain can evaluate the
bound project's geometry-bearing components now; the fixture's ``portico``
component owns two elements and starts with no facets at all, so the default
projection is expected to ask for its ``architectural.role`` before anything
else. Facets are added through the existing record-edit route
(``POST /api/proposals`` with a ``semanticEdit``), the same way an architect's
own facets route will, once one exists (spec §3.2's table lists it as a
Stage C route this task does not add).
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from project_runtime.application.binding import bound_project
from project_runtime.application.projection import project_state
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, make_project, runner_state_digest


class DomainsTestCase(unittest.TestCase):
    """One real project, shaped like ``test_sketch.py``'s ``SketchTestCase``."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)

    def add_facets(self, component_id: str, facets: dict[str, str]) -> str:
        """Set ``component_id``'s facets through the generic record-edit route, keeping its other fields.

        Runs the edit as a candidate and answers the run id it produced, so a
        caller can ask ``?run=`` about the record that now carries them
        without disturbing the reference run.
        """

        record = project_state(bound_project(self.client.app.state)).record
        entity = record.entity(component_id)
        edited = {
            "entity_id": entity.entity_id, "schema": entity.schema, "parent_id": entity.parent_id,
            "fields": {**entity.fields, "facets": facets},
        }
        proposed = self.client.post("/api/proposals", json={
            "stateDigest": self.state_digest, "sourceRunId": REFERENCE_RUN_ID,
            "semanticEdit": {"summary": f"Add facets to {component_id}.", "entities": [edited]},
        })
        self.assertEqual(proposed.status_code, 201, proposed.text)
        started = self.client.post(f"/api/proposals/{proposed.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job_id = started.json()["jobId"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the facets candidate never finished")
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def test_the_index_describes_structure_and_envelope_with_no_project_question_asked(self) -> None:
        response = self.client.get("/api/domains")
        self.assertEqual(response.status_code, 200, response.text)
        by_name = {entry["domain"]: entry for entry in response.json()["domains"]}
        self.assertEqual(set(by_name), {"structure", "envelope"})
        self.assertIn("wall", by_name["structure"]["reads"])
        self.assertNotIn("window", by_name["structure"]["reads"])
        self.assertEqual(set(by_name["structure"]["needs"]), {"structural.role", "material.name"})
        self.assertIn("window", by_name["envelope"]["reads"])
        self.assertEqual(set(by_name["envelope"]["needs"]), {"architectural.enclosure", "material.name"})
        for entry in by_name.values():
            self.assertEqual(entry["known_facets"]["material.name"], "free text")
            self.assertIn("wall", entry["known_facets"]["architectural.role"])

    def test_an_unknown_domain_readiness_is_refused_naming_the_known_domains(self) -> None:
        response = self.client.get("/api/domains/acoustics/readiness")
        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertEqual(body["code"], "DOMAIN_UNKNOWN")
        self.assertIn("acoustics", body["detail"])
        self.assertIn("structure", body["detail"])
        self.assertIn("envelope", body["detail"])

    def test_an_unknown_domain_in_the_index_route_is_not_confused_with_a_real_one(self) -> None:
        # /api/domains itself never 404s: only the per-domain readiness route names one.
        self.assertEqual(self.client.get("/api/domains").status_code, 200)

    def test_the_default_projection_asks_for_the_unfaceted_porticos_role(self) -> None:
        response = self.client.get("/api/domains/structure/readiness")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["domain"], "structure")
        self.assertEqual(body["status"], "enrichment_required")
        [request] = [row for row in body["requests"] if row["id"] == "portico"]
        self.assertEqual(request["missing"], ["architectural.role"])
        self.assertEqual(
            request["reason"],
            "no architectural.role: the structure domain cannot tell whether portico carries load",
        )
        self.assertNotIn("portico", body["reads"])

    def test_omitting_run_matches_the_reference_run_named_explicitly(self) -> None:
        default = self.client.get("/api/domains/envelope/readiness")
        named = self.client.get(f"/api/domains/envelope/readiness?run={REFERENCE_RUN_ID}")
        self.assertEqual(default.status_code, 200, default.text)
        self.assertEqual(named.status_code, 200, named.text)
        self.assertEqual(default.json(), named.json())

    def test_run_resolves_a_separate_projection_the_way_state_does(self) -> None:
        before = self.client.get(f"/api/domains/structure/readiness?run={REFERENCE_RUN_ID}").json()
        self.assertEqual(before["status"], "enrichment_required")

        candidate_run = self.add_facets("portico", {
            "architectural.role": "wall", "structural.role": "load_bearing", "material.name": "concrete",
        })

        after = self.client.get(f"/api/domains/structure/readiness?run={candidate_run}").json()
        self.assertEqual(after["status"], "ready")
        self.assertIn("portico", after["reads"])
        self.assertEqual(after["requests"], [])

        # The reference run itself is untouched by running the candidate.
        still_before = self.client.get(f"/api/domains/structure/readiness?run={REFERENCE_RUN_ID}").json()
        self.assertEqual(still_before, before)


if __name__ == "__main__":
    unittest.main()
