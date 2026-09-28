"""The construction routes (#419): one script in, one proposal out.

An agent authors geometry with one bounded construction script
(``POST /api/proposals/construction``), learns the language from
``GET /api/construction``, reads the model back in the same words from
``GET /api/construction/model`` and adds meaning later with
``POST /api/proposals/facets``. The tests check that a script becomes the
ordinary proposal the candidate route already runs (the runtime choosing how
each shape is realised), that every refusal names the script line that caused
it, that the agent-facing text keeps the layer rule, and that facets change a
component's meaning and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.state.state_record import apply_state_record_operator, component_facets
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from monkeyarch.construction import vocabulary
from monkeyarch.construction.vocabulary import layer_rule_violations

from .support import (
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    SEATS_PAYLOAD,
    make_empty_project,
    make_project,
    write_runner_record,
    write_runner_seats,
)

TWO_BLOCKS = "\n".join([
    "mass = extrude(rect(0, 0, 6, 4), 3)",
    "block = extrude(rect(1, 1, 2, 2), 2, at=top(mass))",
    "print(bounds(block))",
])

LOOP_CUTS = "\n".join([
    "mass = extrude(rect(0, 0, 12, 8), 3)",
    "block = extrude(rect(3, 2, 6, 4), 2.5, at=top(mass))",
    "for i in range(4):",
    "    cutter = extrude(rect(1 + 2.8 * i, -0.2, 1.2, 0.6), 1.5, at=0.9)",
    "    cut(mass, cutter)",
])

CONSTRUCTION_PATHS = ("/api/proposals/construction", "/api/proposals/facets", "/api/construction",
                      "/api/construction/model")


def _exported(project: Path, run_id: str) -> dict[str, list[list[float]]]:
    """Every named object a run exported, with its bounds in construction axes.

    The export is read through archflow's own ``.3dm`` inspector. It is Z-up,
    ``(x, plan z, height)``; construction bounds are Y-up, ``(x, height, plan z)``.
    """

    found: dict[str, list[list[float]]] = {}
    for path in sorted((project / "runs" / run_id).rglob("*.3dm")):
        for row in inspect_three_dm(path).named_object_bboxes:
            low, high = row["bbox"]["min"], row["bbox"]["max"]
            found[str(row["name"])] = [[round(corner[i], 6) for i in (0, 2, 1)] for corner in (low, high)]
    return found


def _texts(node: object, schemas: dict, seen: set[str]):
    """Every description, summary, title and property name reachable from ``node`` in an OpenAPI document."""

    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[1]
            if name not in seen:
                seen.add(name)
                yield name
                yield from _texts(schemas[name], schemas, seen)
        for key, value in node.items():
            if key in ("description", "summary", "title") and isinstance(value, str):
                yield value
            elif key == "properties" and isinstance(value, dict):
                yield from value
                yield from _texts(value, schemas, seen)
            else:
                yield from _texts(value, schemas, seen)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item, schemas, seen)


class ConstructionTestCase(unittest.TestCase):
    """One real project, authored on through the construction routes."""

    cad_export = "off"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export=self.cad_export, project_dir=self.project)))
        self.addCleanup(self.client.close)

    def digest(self, run: str | None = None) -> str:
        query = f"?run={run}" if run else ""
        return self.client.get(f"/api/state{query}").json()["stateDigest"]

    def construct(self, script: str, *, expect: int = 201, **body: object) -> dict:
        run = body.get("sourceRunId")
        payload = {"stateDigest": self.digest(run if isinstance(run, str) else None), "script": script, **body}
        response = self.client.post("/api/proposals/construction", json=payload)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def facets(self, targets: list[dict], *, expect: int = 201, **body: object) -> dict:
        run = body.get("sourceRunId")
        payload = {"stateDigest": self.digest(run if isinstance(run, str) else None), "targets": targets, **body}
        response = self.client.post("/api/proposals/facets", json=payload)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def model(self, run: str | None = None) -> dict:
        response = self.client.get("/api/construction/model" + (f"?run={run}" if run else ""))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_candidate(self, proposal_id: str) -> str:
        started = self.client.post(f"/api/proposals/{proposal_id}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{started.json()['jobId']}").json()
            if job["status"] in {"succeeded", "failed"}:
                self.assertEqual(job["status"], "succeeded", job)
                return job["candidateId"]
            time.sleep(0.05)
        raise AssertionError("the candidate never finished")

    def record(self, run: str | None = None):
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.projection import project_state

        return project_state(bound_project(self.client.app.state), run).record

    def successor(self, proposal: dict, run: str | None = None):
        """The record a proposal would run, derived by the kernel from its own operator."""

        stored = self.client.app.state.proposals.get(proposal["proposalId"])
        return apply_state_record_operator(self.record(run), stored.state_record_operator)

    def assert_refused_at(self, body: dict, line: int, source_line: str) -> None:
        self.assertEqual(body["code"], "CONSTRUCTION_INVALID", body)
        self.assertEqual(body["line"], line, body)
        self.assertEqual(body["sourceLine"], source_line, body)
        self.assertIsInstance(body["column"], int, body)
        self.assertEqual(body["detail"], body["message"], body)
        self.assertEqual(layer_rule_violations(body["message"]), (), body)


class ConstructionProposalTestCase(ConstructionTestCase):
    """``POST /api/proposals/construction``: a script becomes the proposal the candidate route runs."""

    cad_export = "occt"

    def test_a_script_becomes_one_proposal_and_its_candidate_delivers_the_reported_bounds(self) -> None:
        base = self.digest()
        proposal = self.construct(TWO_BLOCKS)
        self.assertEqual(proposal["baseStateDigest"], base)
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(proposal["change"]["kind"], "edit_components")
        self.assertEqual(proposal["utterance"], "construction: create mass, block")
        self.assertEqual(proposal["change"]["summary"], proposal["utterance"])
        rows = {row["entity_id"]: row for row in proposal["change"]["edits"]["entities"]}
        self.assertEqual(sorted(rows), ["block", "block-body", "mass", "mass-body"])
        for identifier in ("mass", "block"):
            # A geometry id is a component under the modelling root, made without meaning.
            self.assertEqual(rows[identifier]["schema"], "Component@1")
            self.assertEqual(rows[identifier]["parent_id"], "portico")
            self.assertEqual(rows[identifier]["fields"], {"intent": identifier})
            # Its geometry is one element, realised as the runtime chose.
            body = rows[f"{identifier}-body"]
            self.assertEqual(body["schema"], "Element@1")
            self.assertEqual(body["fields"]["producer"], "prism")
        self.assertEqual(rows["block-body"]["fields"]["references"]["base"], {"datum": "mass-body-top"})
        report = {row["id"]: row for row in proposal["construction"]["report"]}
        self.assertEqual({key: report["mass"][key] for key in ("form", "status", "line")},
                         {"form": "solid", "status": "created", "line": 1})
        self.assertEqual(report["mass"]["bounds"], [[0, 0, 0], [6, 3, 4]])
        self.assertEqual(report["block"]["bounds"], [[1, 3, 1], [3, 5, 3]])
        self.assertEqual(report["block"]["line"], 2)
        self.assertEqual(proposal["construction"]["log"], ["((1.0, 3.0, 1.0), (3.0, 5.0, 3.0))"])
        self.assertEqual(self.client.get(f"/api/proposals/{proposal['proposalId']}").json()["proposalId"],
                         proposal["proposalId"])

        run = self.run_candidate(proposal["proposalId"])
        exported = _exported(self.project, run)
        # What the run delivered is what the script said it made.
        self.assertEqual(exported["obj-mass-body"], report["mass"]["bounds"])
        self.assertEqual(exported["obj-block-body"], report["block"]["bounds"])

    def test_every_cutter_made_in_a_loop_reaches_its_host_and_the_candidate_runs(self) -> None:
        proposal = self.construct(LOOP_CUTS)
        rows = {row["entity_id"]: row for row in proposal["change"]["edits"]["entities"]}
        cutters = [f"cutter-{index}" for index in range(1, 5)]
        self.assertEqual(sorted(rows), sorted(["mass", "mass-body", "block", "block-body"]
                                              + cutters + [f"{cutter}-body" for cutter in cutters]))
        self.assertEqual(rows["mass-body"]["fields"]["references"]["voids"], [f"{cutter}-body" for cutter in cutters])
        report = {row["id"]: row for row in proposal["construction"]["report"]}
        self.assertEqual(report["mass"]["cuts"], cutters)
        run = self.run_candidate(proposal["proposalId"])
        model = {row["id"]: row for row in self.model(run)["entities"]}
        self.assertEqual(model["mass"]["cuts"], cutters)
        for cutter in cutters:
            self.assertEqual((model[cutter]["hidden"], model[cutter]["cutBy"]), (True, ["mass"]), cutter)
        exported = _exported(self.project, run)
        self.assertEqual(exported["obj-block-body"], report["block"]["bounds"])
        self.assertIn("obj-mass-body", exported)

    def test_a_script_continues_a_proposal_and_a_rerun_updates_the_same_geometry(self) -> None:
        first = self.construct("mass = extrude(rect(0, 0, 6, 4), 3)")
        original = self.client.get(f"/api/proposals/{first['proposalId']}").json()
        second = self.construct('block = extrude(rect(1, 1, 2, 2), 2, at=top(get("mass")))',
                                stateDigest=first["baseStateDigest"], sourceProposalId=first["proposalId"])
        self.assertEqual(second["baseStateDigest"], first["baseStateDigest"])
        self.assertEqual(second["sourceRunId"], first["sourceRunId"])
        self.assertEqual(sorted(row["entity_id"] for row in second["change"]["edits"]["entities"]),
                         ["block", "block-body", "mass", "mass-body"])
        self.assertEqual([(row["id"], row["status"]) for row in second["construction"]["report"]], [("block", "created")])
        # The same names again update the same geometry: no second component, no duplicate.
        third = self.construct("mass = extrude(rect(0, 0, 6, 4), 4)",
                               stateDigest=first["baseStateDigest"], sourceProposalId=second["proposalId"])
        self.assertEqual([(row["id"], row["status"]) for row in third["construction"]["report"]], [("mass", "updated")])
        rows = {row["entity_id"]: row for row in third["change"]["edits"]["entities"]}
        self.assertEqual(sorted(rows), ["block", "block-body", "mass", "mass-body"])
        self.assertEqual(rows["mass-body"]["fields"]["params"]["height"], 4)
        self.assertEqual(self.client.get(f"/api/proposals/{first['proposalId']}").json(), original)

        run = self.run_candidate(third["proposalId"])
        exported = _exported(self.project, run)
        self.assertEqual(exported["obj-mass-body"], [[0, 0, 0], [6, 4, 4]])
        self.assertEqual(exported["obj-block-body"], [[1, 4, 1], [3, 6, 3]], "the block follows the top it stands on")


class ConstructionRefusalTestCase(ConstructionTestCase):
    """Every refusal names the script line that caused it, and nothing is kept."""

    def test_a_script_that_does_not_parse_answers_its_line_and_saves_nothing(self) -> None:
        base = self.digest()
        held = self.client.app.state.proposals.for_state(base)
        body = self.construct("mass = extrude(rect(0, 0, 6, 4), 3\n", expect=422)
        self.assert_refused_at(body, 1, "mass = extrude(rect(0, 0, 6, 4), 3")
        self.assertEqual(self.client.app.state.proposals.for_state(base), held)

    def test_a_refused_verb_answers_the_line_that_called_it(self) -> None:
        body = self.construct("mass = extrude(rect(0, 0, 6, 4), 3)\n\ncut(mass)\n", expect=422)
        self.assert_refused_at(body, 3, "cut(mass)")
        self.assertIn("cutter", body["message"])

    def test_a_refusal_from_the_record_is_answered_at_the_script_line_in_construction_words(self) -> None:
        # The record already has a solid standing on the top of ``lower``. A
        # script that turns ``lower`` into a cutter never reaches ``upper``, so
        # the runtime refuses it; the answer still names the script's line.
        first = self.construct("lower = extrude(rect(0, 0, 2, 2), 1)\n"
                               "upper = extrude(rect(0.5, 0.5, 1, 1), 2, at=top(lower))")
        held = self.client.app.state.proposals.for_state(first["baseStateDigest"])
        script = 'mass = extrude(rect(-1, -1, 6, 6), 3)\ncut(mass, name(extrude(rect(0, 0, 2, 2), 1), "lower"))'
        body = self.construct(script, expect=422, stateDigest=first["baseStateDigest"],
                              sourceProposalId=first["proposalId"])
        self.assert_refused_at(body, 2, 'cut(mass, name(extrude(rect(0, 0, 2, 2), 1), "lower"))')
        self.assertIn("upper", body["message"])
        self.assertIn("top of lower", body["message"])
        self.assertEqual(self.client.app.state.proposals.for_state(first["baseStateDigest"]), held)

    def test_a_record_refusal_is_answered_at_the_first_line_it_names_and_otherwise_passes_through(self) -> None:
        from unittest import mock

        from archflow_studio_api.application import construction
        from archflow_studio_api.application.binding import bound_project
        from archflow_studio_api.application.projection import project_state
        from archflow_studio_api.transport.errors import StudioError

        binding = bound_project(self.client.app.state)
        projection = project_state(binding)
        script = "mass = extrude(rect(0, 0, 6, 4), 3)\n\n  # the block\nblock = extrude(rect(1, 1, 2, 2), 2, at=top(mass))"

        def refused(error: StudioError):
            with mock.patch.object(construction, "component_edit_proposal", side_effect=error):
                return construction.construction_proposal(binding, projection, script)

        for detail, line in (("block-body: the prism needs a height", 4),
                             ("block-body: base datum 'mass-body-top' is not published yet", 1),
                             ("mass: component needs an intent", 1)):
            with self.subTest(detail=detail), self.assertRaises(construction.ConstructionRefused) as raised:
                refused(StudioError(422, "SEMANTIC_EDIT_INVALID", detail))
            self.assertEqual((raised.exception.line, raised.exception.column), (line, 1))
            self.assertEqual(raised.exception.source_line, script.splitlines()[line - 1])
            self.assertEqual(layer_rule_violations(raised.exception.detail), ())
        self.assertEqual(raised.exception.body()["message"], "mass: component needs an intent")
        for error in (StudioError(422, "SEMANTIC_EDIT_INVALID", "parameter storey: expression cycle"),
                      StudioError(422, "SEMANTIC_EDIT_INVALID", "blocks-body and mass-bodyx are not shapes here"),
                      StudioError(409, "PROPOSAL_CHAIN_CONFLICT", "The next edit reaches protected refs: entity:mass")):
            with self.subTest(detail=error.detail), self.assertRaises(StudioError) as raised:
                refused(error)
            self.assertIs(raised.exception, error, "a refusal that names no shape of the script is not a script line")

    def test_runtime_realisation_names_become_construction_words(self) -> None:
        from archflow_studio_api.application.construction import in_construction_words

        self.assertEqual(in_construction_words("mass-body: only a prism, a capped loft or a wall can host voids"),
                         "mass-body: only a solid, a capped loft solid or a wall-realized solid can host voids")
        for runtime, words in (
            ("sheet-body: planar-surface profile must explicitly close at its first point",
             "sheet-body: face profile must explicitly close at its first point"),
            ("trace-body: curve requires 2 to 512 points", "trace-body: path requires 2 to 512 points"),
            ("an absolute base belongs to a drawn face or prism", "an absolute base belongs to a drawn face or solid"),
        ):
            with self.subTest(runtime=runtime):
                self.assertEqual(in_construction_words(runtime), words)
                self.assertEqual(layer_rule_violations(in_construction_words(runtime)), ())
        # Ids are the script's own words and are never rewritten, whatever they contain.
        self.assertEqual(in_construction_words("prism-1-body: height 'prism' is not a number; see wall-a"),
                         "prism-1-body: height 'solid' is not a number; see wall-a")

    def test_a_name_that_belongs_to_something_else_is_refused_never_overwritten(self) -> None:
        for script, line, taken in (
            ("portico = extrude(rect(0, 0, 1, 1), 1)", 1, "portico"),
            ('mass = extrude(rect(0, 0, 1, 1), 1)\nname(mass, "level-ground")', 2, "level-ground"),
            ('mass = extrude(rect(0, 0, 1, 1), 1)\nname(mass, "building")', 2, "building"),
        ):
            with self.subTest(taken=taken):
                body = self.construct(script, expect=422)
                self.assertEqual((body["code"], body["line"]), ("CONSTRUCTION_INVALID", line), body)
                self.assertIn(taken, body["message"])

    def test_a_script_that_changes_nothing_has_nothing_to_propose(self) -> None:
        body = self.construct('print(bounds(get("portico-base")))', expect=422)
        self.assertEqual(body["code"], "CONSTRUCTION_INVALID", body)
        self.assertIsNone(body["line"])
        self.assertIn("nothing to propose", body["message"])

    def test_a_script_against_another_state_is_stale(self) -> None:
        body = self.construct(TWO_BLOCKS, expect=409, stateDigest="0" * 64)
        self.assertEqual(body["code"], "STALE_BASE")
        self.assertIn("/api/construction/model", body["detail"])

    def test_a_script_the_request_shape_refuses(self) -> None:
        for extra in ({"script": ""}, {"script": "x = 1\n" * 4000}, {"stateDigest": "stale"}, {"producer": "prism"}):
            with self.subTest(extra=sorted(extra)):
                payload = {"stateDigest": self.digest(), "script": TWO_BLOCKS, **extra}
                response = self.client.post("/api/proposals/construction", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "REQUEST_INVALID")

    def test_a_project_whose_seats_build_nothing_refuses_the_script(self) -> None:
        # The one seat owns a component the record does not have: nothing here is built.
        seat = {**SEATS_PAYLOAD["seats"][0], "owned_component_ids": ["nothing-here"]}  # type: ignore[index]
        write_runner_seats(self.repository, {**SEATS_PAYLOAD, "seats": [seat]})
        body = self.construct(TWO_BLOCKS, expect=422)
        self.assertEqual(body["code"], "COMPONENT_NOT_BUILT")
        self.assertIn("portico", body["detail"])


class ConstructionRootTestCase(unittest.TestCase):
    """New geometry goes under ``model`` when a seat builds it, whatever else is buildable."""

    def test_new_geometry_goes_under_the_model_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        repository = make_empty_project(root)
        write_runner_record(repository, {**RECORD_PAYLOAD, "entities": [
            *RECORD_PAYLOAD["entities"],  # type: ignore[misc]
            {"entity_id": "model", "schema": "Component@1", "fields": {"intent": "Root for candidate modeling"}},
        ]})
        seat = {**SEATS_PAYLOAD["seats"][0], "owned_component_ids": ["building", "model"]}  # type: ignore[index]
        write_runner_seats(repository, {**SEATS_PAYLOAD, "seats": [seat]})
        with TestClient(create_app(StudioSettings(cad_export="off", project_dir=root / PROJECT_ID))) as client:
            digest = client.get("/api/state").json()["stateDigest"]
            response = client.post("/api/proposals/construction", json={
                "stateDigest": digest, "script": "mass = extrude(rect(0, 0, 1, 1), 1)"})
        self.assertEqual(response.status_code, 201, response.text)
        [component] = [row for row in response.json()["change"]["edits"]["entities"] if row["entity_id"] == "mass"]
        self.assertEqual(component["parent_id"], "model")


class ConstructionDecisionTestCase(ConstructionTestCase):
    def test_a_script_proposal_is_an_ordinary_option_a_decision_can_close(self) -> None:
        proposal = self.construct(TWO_BLOCKS)
        response = self.client.post(f"/api/proposals/{proposal['proposalId']}/decision",
                                    json={"decision": "rejected", "reason": "too tall for the street"})
        self.assertEqual(response.status_code, 201, response.text)
        [option] = response.json()["proposals"]
        self.assertEqual((option["proposalId"], option["decision"], option["change"]["kind"]),
                         (proposal["proposalId"], "rejected", "edit_components"))


class ConstructionParametersTestCase(ConstructionTestCase):
    def test_parameters_travel_with_the_script_and_param_binds_them(self) -> None:
        proposal = self.construct('mass = extrude(rect(0, 0, 4, 4), param("block_height"))', parameters=[
            {"key": "block_height", "value": 3.2, "unit": "m", "epistemic_status": "declared"},
        ], summary="a mass as tall as the block height")
        self.assertEqual(proposal["utterance"], "a mass as tall as the block height")
        [parameter] = proposal["change"]["edits"]["parameters"]
        self.assertEqual((parameter["key"], parameter["value"]), ("block_height", 3.2))
        [body] = [row for row in proposal["change"]["edits"]["entities"] if row["entity_id"] == "mass-body"]
        self.assertEqual(body["fields"]["params"]["height"], "@block_height")
        [row] = proposal["construction"]["report"]
        self.assertEqual(row["bounds"], [[0, 0, 0], [4, 3.2, 4]])

    def test_a_parameter_the_record_refuses_is_not_a_script_line(self) -> None:
        body = self.construct('mass = extrude(rect(0, 0, 4, 4), 3)', parameters=[{"key": "block_height"}],
                              expect=422)
        self.assertEqual(body["code"], "SEMANTIC_EDIT_INVALID")


class ConstructionVocabularyTestCase(ConstructionTestCase):
    """``GET /api/construction`` and the construction contract keep to construction words."""

    def test_the_vocabulary_is_served_as_the_package_states_it(self) -> None:
        response = self.client.get("/api/construction")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), json.loads(json.dumps(vocabulary())))
        self.assertEqual(layer_rule_violations(response.text), ())
        for token in ("prism", "semanticKind", "boolean", "aperture", "topology", "OCCT"):
            self.assertNotIn(token.lower(), response.text.lower())

    def test_the_construction_contract_in_openapi_keeps_the_layer_rule(self) -> None:
        schema = self.client.get("/openapi.json").json()
        schemas = schema["components"]["schemas"]
        for path in CONSTRUCTION_PATHS:
            self.assertIn(path, schema["paths"])
            texts = list(_texts(schema["paths"][path], schemas, set()))
            self.assertTrue(texts, path)
            with self.subTest(path=path):
                self.assertEqual([text for text in texts if layer_rule_violations(text)], [])


class ConstructionModelTestCase(ConstructionTestCase):
    """``GET /api/construction/model``: the model in construction terms, and the capabilities facets unlock."""

    cad_export = "occt"

    def assert_same_identity(self, model: dict, run: str | None = None) -> None:
        """The model view names its run and issued design exactly as ``GET /api/state`` does."""

        state = self.client.get("/api/state" + (f"?run={run}" if run else "")).json()
        for key in ("projectId", "published", "referenceRun", "sourceStageRef", "stateDigest", "recordDigest"):
            self.assertEqual(model[key], state.get(key), key)

    def test_the_model_view_reads_the_selected_state(self) -> None:
        model = self.model()
        self.assert_same_identity(model)
        self.assertEqual(model["projectId"], PROJECT_ID)
        self.assertEqual(model["referenceRun"]["runId"], REFERENCE_RUN_ID)
        self.assertEqual(model["levels"], [{"id": "level-ground", "elevation": 0.0}])
        self.assertEqual({row["key"]: (row["value"], row["unit"]) for row in model["parameters"]},
                         {"module": (1.2, "m"), "bay": (2.4, "m"), "span": (4.8, "m"), "plinth": (0.6, "m")})
        [portico] = model["entities"]
        self.assertEqual(portico, {
            "id": "portico", "form": "solid", "bounds": [[0, 0, 0], [4, 0.9, 2]], "cuts": [], "cutBy": [],
            "hidden": False, "parts": ["portico-base", "portico-cornice"], "facets": {}, "capabilities": [],
            "openings": [], "alongLine": None,
        })

    def test_forms_bounds_cuts_and_facets_read_back_and_capabilities_follow_facets(self) -> None:
        proposal = self.construct("\n".join([
            "mass = extrude(rect(0, 0, 6, 4), 3)",
            "cutter = extrude(rect(2, -1, 2, 2), 2)",
            "cut(mass, cutter)",
            "sheet = face(rect(0, 5, 6, 1), at=4)",
            "trace = path([(0, 6, 0), (6, 6, 0)])",
        ]))
        first = self.run_candidate(proposal["proposalId"])
        model = self.model(first)
        # The Hub opens a candidate read by the run the answer names.
        self.assertEqual(model["referenceRun"]["runId"], first)
        self.assert_same_identity(model, first)
        rows = {row["id"]: row for row in model["entities"]}
        self.assertEqual({key: rows["mass"][key] for key in ("form", "cuts", "cutBy", "hidden", "facets", "capabilities")},
                         {"form": "solid", "cuts": ["cutter"], "cutBy": [], "hidden": False, "facets": {},
                          "capabilities": []})
        self.assertEqual({key: rows["cutter"][key] for key in ("form", "cuts", "cutBy", "hidden")},
                         {"form": "solid", "cuts": [], "cutBy": ["mass"], "hidden": True})
        self.assertEqual((rows["sheet"]["form"], rows["trace"]["form"]), ("face", "path"))
        self.assertEqual(rows["sheet"]["bounds"], [[0, 4, 5], [6, 4, 6]])
        exported = _exported(self.project, first)
        self.assertEqual(rows["mass"]["bounds"], exported["obj-mass-body"])

        # Meaning arrives later, on the same identity, and unlocks a capability.
        enriched = self.facets([{"id": "mass", "set": {"architectural.role": "wall", "material.name": "  brick "}}],
                               sourceRunId=first)
        self.assertEqual(enriched["utterance"], "facets: mass +architectural.role=wall, +material.name=brick")
        [row] = enriched["change"]["edits"]["entities"]
        self.assertEqual((row["entity_id"], row["schema"]), ("mass", "Component@1"))
        self.assertEqual(row["fields"], {"intent": "mass",
                                         "facets": {"architectural.role": "wall", "material.name": "brick"}})
        self.assertEqual(enriched["change"]["edits"]["removeEntityIds"], [])
        second = self.run_candidate(enriched["proposalId"])
        before, after = self.record(first), self.record(second)
        # Every other entity (each element and level) and every dependency edge
        # is byte-identical; the component itself gained its facets and nothing else.
        self.assertEqual([entity.to_dict() for entity in before.entities if entity.entity_id != "mass"],
                         [entity.to_dict() for entity in after.entities if entity.entity_id != "mass"])
        self.assertEqual(before.dependency_edges(), after.dependency_edges())
        [was] = [entity for entity in before.entities if entity.entity_id == "mass"]
        [now] = [entity for entity in after.entities if entity.entity_id == "mass"]
        self.assertEqual({**now.to_dict(), "fields": {**now.fields, "facets": None}},
                         {**was.to_dict(), "fields": {**was.fields, "facets": None}})
        # And the candidate delivers the same objects under the same ids.
        self.assertEqual(_exported(self.project, second), exported)

        rows = {row["id"]: row for row in self.model(second)["entities"]}
        self.assertEqual(rows["mass"]["facets"], {"architectural.role": "wall", "material.name": "brick"})
        self.assertEqual(rows["mass"]["capabilities"],
                         [{"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}])
        for other in ("cutter", "sheet", "trace", "portico"):
            self.assertEqual(rows[other]["capabilities"], [], other)
        # A facet without that meaning unlocks nothing.
        other = self.facets([{"id": "mass", "set": {"architectural.role": "column"}}], sourceRunId=second)
        stored = self.successor(other, second)
        self.assertEqual(component_facets(next(e for e in stored.entities if e.entity_id == "mass")),
                         {"architectural.role": "column", "material.name": "brick"})


class FacetsProposalTestCase(ConstructionTestCase):
    """``POST /api/proposals/facets``: meaning on a component, and nothing else."""

    def test_facets_upsert_only_the_component_and_keep_its_other_fields(self) -> None:
        before = self.record()
        proposal = self.facets([{"id": "portico", "set": {"structural.role": "load_bearing"}}])
        self.assertEqual(proposal["utterance"], "facets: portico +structural.role=load_bearing")
        [row] = proposal["change"]["edits"]["entities"]
        previous = next(entity for entity in before.entities if entity.entity_id == "portico")
        self.assertEqual(row["fields"], json.loads(json.dumps(
            {**previous.fields, "facets": {"structural.role": "load_bearing"}})))
        after = self.successor(proposal)
        self.assertEqual([entity.to_dict() for entity in before.entities if entity.entity_id != "portico"],
                         [entity.to_dict() for entity in after.entities if entity.entity_id != "portico"])
        self.assertEqual(before.dependency_edges(), after.dependency_edges())

    def test_facets_continue_a_script_proposal_before_anything_runs(self) -> None:
        made = self.construct("mass = extrude(rect(0, 0, 6, 4), 3)")
        enriched = self.facets([{"id": "mass", "set": {"architectural.role": "wall"}}],
                               stateDigest=made["baseStateDigest"], sourceProposalId=made["proposalId"])
        self.assertEqual(enriched["baseStateDigest"], made["baseStateDigest"])
        rows = {row["entity_id"]: row for row in enriched["change"]["edits"]["entities"]}
        self.assertEqual(sorted(rows), ["mass", "mass-body"])
        self.assertEqual(rows["mass"]["fields"]["facets"], {"architectural.role": "wall"})
        [made_body] = [row for row in made["change"]["edits"]["entities"] if row["entity_id"] == "mass-body"]
        self.assertEqual(rows["mass-body"], made_body, "the facet changed nothing the script made")

    def test_removing_the_last_facet_leaves_none_and_a_summary_can_be_given(self) -> None:
        first = self.facets([{"id": "portico", "set": {"material.name": "brick"}},
                             {"id": "building", "set": {"architectural.enclosure": "exterior"}}])
        self.assertEqual(first["utterance"],
                         "facets: portico +material.name=brick; building +architectural.enclosure=exterior")
        second = self.facets([{"id": "portico", "remove": ["material.name"]}], summary="brick was a guess",
                             stateDigest=first["baseStateDigest"], sourceProposalId=first["proposalId"])
        self.assertEqual(second["utterance"], "brick was a guess")
        after = {entity.entity_id: entity for entity in self.successor(second).entities}
        self.assertEqual(component_facets(after["portico"]), {})
        self.assertEqual(component_facets(after["building"]), {"architectural.enclosure": "exterior"})
        before = self.record()
        self.assertEqual([entity.to_dict() for entity in before.entities if entity.schema != "Component@1"],
                         [entity.to_dict() for entity in after.values() if entity.schema != "Component@1"])

    def test_facets_refusals_name_what_is_wrong(self) -> None:
        for targets, status, code, said in (
            ([{"id": "nothing-here", "set": {"material.name": "brick"}}], 404, "ENTITY_UNKNOWN", "nothing-here"),
            ([{"id": "portico-base", "set": {"material.name": "brick"}}], 422, "FACETS_TARGET_INVALID", "Element@1"),
            ([{"id": "level-ground", "set": {"material.name": "brick"}}], 422, "FACETS_TARGET_INVALID", "Level@1"),
            ([{"id": "portico", "set": {"material.name": "   "}}], 422, "FACETS_INVALID", "material.name"),
            ([{"id": "portico", "set": {"architectural.rol": "column"}}], 422, "FACETS_INVALID", "architectural.role"),
            ([{"id": "portico", "set": {"architectural.role": "tower"}}], 422, "FACETS_INVALID", "column"),
            ([{"id": "portico"}], 422, "FACETS_INVALID", "set or remove"),
            ([{"id": "portico", "set": {"material.name": "brick"}, "remove": ["material.name"]}], 422,
             "FACETS_INVALID", "material.name"),
            ([{"id": "portico", "set": {"material.name": "brick"}}, {"id": "portico", "remove": ["material.name"]}],
             422, "FACETS_INVALID", "portico"),
        ):
            with self.subTest(targets=targets):
                body = self.facets(targets, expect=status)
                self.assertEqual(body["code"], code, body)
                self.assertIn(said, body["detail"])
        stale = self.facets([{"id": "portico", "set": {"material.name": "brick"}}], expect=409, stateDigest="0" * 64)
        self.assertEqual(stale["code"], "STALE_BASE")
        for invalid in ([], [{"id": f"c{i}", "remove": ["material.name"]} for i in range(51)],
                        [{"id": "portico", "set": {"material.name": 3}}], [{"id": "portico", "add": {}}]):
            with self.subTest(invalid=invalid[:1]):
                response = self.client.post("/api/proposals/facets", json={"stateDigest": self.digest(),
                                                                          "targets": invalid})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["code"], "REQUEST_INVALID")


if __name__ == "__main__":
    unittest.main()
