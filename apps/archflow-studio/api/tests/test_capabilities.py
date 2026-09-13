"""Finding the entry point, and using the one that was found.

The path these tests walk is the one an agent walks: ask what serves a goal,
ask what that means for this project and this target, send the request the
answer handed back, and read the model the run exported. Nothing here reads
source, writes a script or invents a field name — if any of that were needed,
these tests would be the place it showed.
"""

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.application import capability as capability_module
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings

from .support import PROJECT_ID, RECORD_PAYLOAD, SEATS_PAYLOAD, make_project

CAPABILITY = "candidate.modify_existing"
SQUARE = [[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]]


class CapabilityIndexTestCase(unittest.TestCase):
    """The index and the description, against a real bound project."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_a_goal_in_either_language_finds_the_capability(self) -> None:
        for goal in ("change a height", "改高度", "修改已有构件", "make the main volume taller"):
            answer = self.client.get("/api/capabilities", params={"goal": goal})
            self.assertEqual(answer.status_code, 200, answer.text)
            body = answer.json()
            self.assertEqual([row["capabilityId"] for row in body["capabilities"]], [CAPABILITY], goal)
            self.assertIsNone(body["note"], "a match needs no explanation")
            entry = body["capabilities"][0]
            self.assertEqual(entry["owner"], "studio.intent")
            self.assertIn("POST /api/capabilities/{capability_id}/run", entry["entrypoints"])

    def test_the_whole_index_is_one_read(self) -> None:
        body = self.client.get("/api/capabilities").json()
        self.assertEqual(body["matchCount"], body["registered"])
        self.assertGreaterEqual(body["registered"], 1)

    def test_empty_project_can_discover_and_execute_initial_modeling_preparation(self) -> None:
        from archflow.state.state_record import StateRecord
        root = self.root / "empty"
        FilesystemProjectRepository.initialize(
            root, project_id="empty", initial_state={"project_id": "empty", "version": 0},
            authored_record=StateRecord(project_id="empty", run_id="authored", entities=()).to_dict(),
        )
        with TestClient(create_app(StudioSettings(project_dir=root, cad_export="off"))) as client:
            matches = client.get("/api/capabilities", params={"goal": "初始化空项目"}).json()
            self.assertIn("project.initialize_modeling", [row["capabilityId"] for row in matches["capabilities"]])
            described = client.get("/api/capabilities/project.initialize_modeling").json()
            self.assertFalse(described["source"]["actionable"])
            request = described["request"]
            result = client.request(request["method"], request["path"], json=request["body"])
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["initialized"])
            self.assertIsNotNone(client.get("/api/state").json()["stateDigest"])
            self.assertFalse((root / "runs" / "studio-projection").exists())

    def test_initialization_description_handles_only_missing_authored_input(self) -> None:
        for project_id, payload, error in (
            ("missing-input", None, "STATE_RECORD_NOT_FOUND"),
            ("invalid-input", {"schema": "invalid"}, "STATE_RECORD_INVALID"),
        ):
            with self.subTest(project=project_id):
                root = self.root / project_id
                repository = FilesystemProjectRepository.initialize(
                    root, project_id=project_id, initial_state={"project_id": project_id, "version": 0},
                    authored_record=payload,
                )
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with TestClient(create_app(StudioSettings(project_dir=root, cad_export="off"))) as client:
                    unchanged = client.get(f"/api/capabilities/{CAPABILITY}")
                    self.assertEqual(unchanged.json()["code"], error)
                    unknown_run = client.get("/api/capabilities/project.initialize_modeling",
                                             params={"run": "missing-run"})
                    self.assertEqual(unknown_run.status_code, 404, unknown_run.text)
                    self.assertEqual(unknown_run.json()["code"], "RUN_NOT_FOUND")
                    described = client.get("/api/capabilities/project.initialize_modeling")
                    self.assertEqual(before, {p.relative_to(root): p.read_bytes()
                                              for p in root.rglob("*") if p.is_file()})
                    if payload is not None:
                        self.assertEqual(described.status_code, 422, described.text)
                        self.assertEqual(described.json()["code"], error)
                        continue
                    self.assertEqual(described.status_code, 200, described.text)
                    detail = described.json()
                    self.assertFalse(detail["source"]["actionable"])
                    self.assertFalse(detail["source"]["exactSource"])
                    self.assertIsNone(detail["source"]["stateDigest"])
                    request = detail["request"]
                    result = client.request(request["method"], request["path"], json=request["body"])
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertTrue(result.json()["initialized"])
                    self.assertIsNotNone(client.get("/api/state").json()["stateDigest"])
                    self.assertEqual(list(repository.layout.runs.iterdir()), [])
                    self.assertEqual(repository.read_head().version, 0)

    def test_no_match_says_what_the_index_is_and_never_that_it_is_impossible(self) -> None:
        body = self.client.get("/api/capabilities", params={"goal": "出施工图"}).json()
        self.assertEqual(body["capabilities"], [])
        self.assertEqual(body["matchCount"], 0)
        self.assertIn("not the list of everything the system does", body["note"])
        self.assertIn(str(body["registered"]), body["note"])
        self.assertIn("before concluding", body["note"])
        self.assertNotIn("MISSING", body["note"], "no hit is not the MISSING status")

    def test_describe_names_the_numbers_that_can_move_on_this_target(self) -> None:
        body = self.client.get(f"/api/capabilities/{CAPABILITY}", params={"target": "portico"}).json()
        fields = {(row["elementId"], row["field"]): row for row in body["target"]["editable"]}
        self.assertIn(("portico-base", "height"), fields)
        self.assertEqual(fields[("portico-base", "height")]["value"], 0.6)
        self.assertEqual(fields[("portico-base", "height")]["utterance"], "set height to 0.6")
        self.assertTrue(all(row["status"] == "editable" for row in body["target"]["editable"]))

    def test_describe_hands_back_the_exact_base_and_the_next_request(self) -> None:
        state = self.client.get("/api/state").json()
        body = self.client.get(
            f"/api/capabilities/{CAPABILITY}",
            params={"target": "portico", "elementId": "portico-base"},
        ).json()
        self.assertEqual(body["source"]["stateDigest"], state["stateDigest"])
        self.assertEqual(body["source"]["projectId"], PROJECT_ID)
        self.assertTrue(body["source"]["actionable"])
        request = body["request"]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], f"/api/capabilities/{CAPABILITY}/run")
        self.assertEqual(request["body"]["stateDigest"], state["stateDigest"])
        self.assertEqual(request["body"]["targetComponentId"], "portico")
        self.assertEqual(request["body"]["elementId"], "portico-base")
        self.assertEqual(request["body"]["keep"], [])
        # Only the one element was asked about, so only its fields are offered.
        self.assertEqual({row["elementId"] for row in body["target"]["editable"]}, {"portico-base"})

    def test_the_keep_scope_is_what_the_record_will_accept_minus_the_target(self) -> None:
        body = self.client.get(
            f"/api/capabilities/{CAPABILITY}",
            params={"target": "portico", "elementId": "portico-base"},
        ).json()
        accepted = body["keep"]["accepted"]
        self.assertIn("entity:portico-cornice", accepted)
        self.assertIn("parameter:module", accepted)
        self.assertNotIn("entity:portico-base", accepted, "keeping what you are changing is not offered")

    def test_the_written_down_status_never_speaks_for_this_project(self) -> None:
        body = self.client.get(f"/api/capabilities/{CAPABILITY}", params={"target": "portico"}).json()
        self.assertEqual(body["capability"]["status"], "PARTIAL")
        self.assertTrue(any("not yet certified" in line for line in body["honesty"]), body["honesty"])
        # Runtime availability is a separate answer, read off the live binding.
        self.assertTrue(body["source"]["actionable"])

    def test_what_a_person_actually_types_finds_it(self) -> None:
        """Not search terms: the sentence the architect wrote, in either language."""

        for said in ("把这个体块高度改成4.2米，雨棚不动",
                     "change the main body height to 4.2 m and keep the canopy",
                     "帮我把主体调整高度",
                     "I want to adjust the height of an existing volume"):
            body = self.client.get("/api/capabilities", params={"goal": said}).json()
            self.assertEqual(body["capabilities"][0]["capabilityId"], CAPABILITY, said)
            self.assertIsNone(body["note"], said)
            self.assertTrue(body["capabilities"][0]["matched"], f"{said}: no evidence for the match")

    def test_a_goal_this_index_has_no_words_for_still_answers_honestly(self) -> None:
        body = self.client.get("/api/capabilities", params={"goal": "出一套施工图纸并送审"}).json()
        self.assertEqual(body["matchCount"], 0)
        self.assertIn("not the list of everything the system does", body["note"])

    def test_describing_a_parent_hands_back_a_request_that_names_the_right_component(self) -> None:
        """The failure this closes: a parent's ready request that POST /api/proposals must refuse."""

        body = self.client.get(f"/api/capabilities/{CAPABILITY}", params={"target": "building"}).json()
        rows = body["target"]["editable"]
        self.assertTrue(rows, "building's descendants have editable numbers")
        self.assertEqual({row["componentId"] for row in rows}, {"portico"},
                         "each row carries the component the element really belongs to")
        self.assertEqual(body["target"]["componentId"], "building", "what was asked about is kept")
        self.assertEqual(body["request"]["body"]["targetComponentId"], "portico",
                         "the request names the element's own component, not the parent")
        self.assertTrue(any("is a parent here" in line for line in body["honesty"]), body["honesty"])
        # And it is a request that runs: the proposal route accepts it.
        sent = self.client.post(body["request"]["path"], json=body["request"]["body"])
        self.assertEqual(sent.status_code, 202, sent.text)

    def test_the_description_says_how_to_read_and_how_to_write_the_same_base(self) -> None:
        body = self.client.get(f"/api/capabilities/{CAPABILITY}", params={"target": "portico"}).json()
        run_id = body["source"]["runId"]
        self.assertEqual(body["source"]["readWith"], f"GET /api/state?run={run_id}")
        self.assertIn("sourceRunId", body["source"]["writeWith"])
        self.assertEqual(body["request"]["body"]["sourceRunId"], run_id)
        self.assertEqual(body["request"]["body"]["projectId"], PROJECT_ID)

    def test_a_selected_stage_travels_into_both_halves_of_the_same_base(self) -> None:
        """The hint must lead back to this projection, not to the default one."""

        stage_ref = self.client.get("/api/state").json()["sourceStageRef"]
        if stage_ref is None:
            # This fixture's projection names no Stage, so the hint builder is
            # held to directly: a Stage in it has to survive into the URL.
            ref = "archflow-project://demo-project/design_stage/main/stage 1"
            built = capability_module._read_with("run-001", ref)
            self.assertIn("run=run-001", built)
            self.assertIn(quote(ref, safe=""), built.replace("+", "%20"))
            self.assertEqual(capability_module._read_with("run-001", None), "GET /api/state?run=run-001")
            return
        body = self.client.get(f"/api/capabilities/{CAPABILITY}", params={
            "target": "portico", "sourceStageRef": stage_ref}).json()
        self.assertEqual(body["source"]["sourceStageRef"], stage_ref)
        read = body["source"]["readWith"]
        self.assertIn("sourceStageRef=", read)
        self.assertIn(quote(stage_ref, safe=""), read.replace("+", "%20"))
        self.assertIn("sourceStageRef", body["source"]["writeWith"])
        self.assertEqual(body["request"]["body"]["sourceStageRef"], stage_ref)
        # And the read it names answers for the same state it was read from.
        answered = self.client.get(read.removeprefix("GET "))
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["stateDigest"], body["source"]["stateDigest"])

    def test_the_entry_keeps_saying_what_it_reads_writes_and_effects(self) -> None:
        entry = self.client.get(f"/api/capabilities/{CAPABILITY}").json()["capability"]
        for section in ("reads", "writes", "effects", "requires", "composes", "produces",
                        "validators", "works", "missing"):
            self.assertTrue(entry.get(section), f"{section} is part of the contract")
        self.assertEqual(entry["inputs_ref"], "/openapi.json#/components/schemas/CapabilityRunRequestDto")

    def test_the_inputs_ref_resolves_in_the_schema_this_service_serves(self) -> None:
        entry = self.client.get(f"/api/capabilities/{CAPABILITY}").json()["capability"]
        document = self.client.get("/openapi.json").json()
        for ref in (entry["inputs_ref"], entry["inputs_extends"]):
            # A URL and a JSON pointer, both resolved the way any client would:
            # fetch the document the reference names, then walk the pointer.
            url, pointer = ref.split("#", 1)
            served = self.client.get(url)
            self.assertEqual(served.status_code, 200, ref)
            node = served.json()
            for step in pointer.strip("/").split("/"):
                self.assertIn(step, node, ref)
                node = node[step]
            self.assertIn("properties", node, ref)
        run_schema = document["components"]["schemas"]["CapabilityRunRequestDto"]
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator({**run_schema, "components": document["components"]})
        state_digest = self.client.get("/api/state").json()["stateDigest"]
        numeric = {"stateDigest": state_digest, "targetComponentId": "portico",
                   "utterance": "set module to 1.5", "keep": ["entity:portico-base"]}
        validator.validate(numeric)
        self.assertFalse(validator.is_valid({
            "stateDigest": state_digest,
            "semanticEdit": {"summary": "Change the module.", "parameters": [{"key": "module", "value": 1.5}]},
        }), "the numeric capability must not advertise the semantic proposal alternative")
        refused = self.client.post(f"/api/capabilities/{CAPABILITY}/run", json={
            "stateDigest": state_digest,
            "semanticEdit": {"summary": "Change the module.", "parameters": [{"key": "module", "value": 1.5}]},
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertEqual(refused.json()["code"], "REQUEST_INVALID")

    def test_an_unregistered_capability_is_refused_with_the_index(self) -> None:
        answer = self.client.get("/api/capabilities/candidate.invent_everything")
        self.assertEqual(answer.status_code, 404)
        body = answer.json()
        self.assertEqual(body["code"], "CAPABILITY_UNKNOWN")
        self.assertIn(CAPABILITY, body["detail"])
        self.assertIn("may still exist as an API entry point", body["detail"])

    def test_a_target_this_record_does_not_declare_is_refused(self) -> None:
        answer = self.client.get(f"/api/capabilities/{CAPABILITY}", params={"target": "gatehouse"})
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.json()["code"], "TARGET_UNKNOWN")
        self.assertIn("portico", answer.json()["detail"])


class AuthoredCapabilityTestCase(unittest.TestCase):
    def test_described_authored_input_can_make_its_first_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / PROJECT_ID
            repository = FilesystemProjectRepository.initialize(
                project, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0},
                authored_record=RECORD_PAYLOAD, seat_pack=SEATS_PAYLOAD,
            )
            original_head = (project / "HEAD").read_bytes()
            original_record = repository.layout.authored_record.read_bytes()
            with TestClient(create_app(StudioSettings(cad_export="off", project_dir=project))) as client:
                described = client.get(f"/api/capabilities/{CAPABILITY}", params={
                    "target": "portico", "elementId": "portico-base",
                })
                self.assertEqual(described.status_code, 200, described.text)
                detail = described.json()
                source = detail["source"]
                self.assertEqual(source["readWith"], "GET /api/state")
                self.assertIn("omit sourceRunId", source["writeWith"])
                self.assertNotIn(source["runId"], source["writeWith"])
                state = client.get(source["readWith"].removeprefix("GET "))
                self.assertEqual(state.status_code, 200, state.text)
                self.assertEqual(state.json()["stateDigest"], source["stateDigest"])
                self.assertFalse(source["exactSource"])
                self.assertTrue(source["actionable"])
                self.assertEqual(list(repository.layout.runs.iterdir()), [])

                body = {**detail["request"]["body"], "utterance": "set height to 1.2"}
                self.assertNotIn("sourceRunId", body)
                started = client.post(detail["request"]["path"], json=body)
                self.assertEqual(started.status_code, 202, started.text)
                job_id = started.json()["jobId"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    job = client.get(f"/api/jobs/{job_id}").json()
                    if job["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(job["status"], "succeeded", job)
                result = client.get("/api/state", params={"run": job["candidateId"]})
                self.assertEqual(result.status_code, 200, result.text)
                base = next(e for e in result.json()["elements"] if e["elementId"] == "portico-base")
                self.assertEqual(base["numericFields"]["height"], 1.2)
            self.assertEqual((project / "HEAD").read_bytes(), original_head)
            self.assertEqual(repository.layout.authored_record.read_bytes(), original_record)
            self.assertEqual(repository.read_design_branches(), {})


class DescribedButNotPerformedTestCase(unittest.TestCase):
    """Writing an entry into the index describes something; it runs nothing.

    The registry is replaced with one that holds a second, deliberately
    unimplemented capability, because that is the drift being guarded against:
    a future entry must not inherit this route's numeric edit by being listed.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        real = json.loads(capability_module.registry_path().read_text(encoding="utf-8"))
        entries = list(real["capabilities"]) + [{
            "capability_id": "drawing.section",
            "owner": "studio.drawings",
            "kind": "representation",
            "status": "MISSING",
            "purpose": "Cut a section through the selected candidate.",
            "goals": ["cut a section", "出剖面"],
            "entrypoints": ["POST /api/drawings/elevations"],
        }]
        registry = self.root / "module_registry.json"
        registry.write_text(json.dumps({"schema": "ArchFlowModuleRegistry@1", "modules": [],
                                        "capabilities": entries}), encoding="utf-8")
        patch = mock.patch.object(capability_module, "registry_path", lambda start=None: registry)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = TestClient(
            create_app(StudioSettings(cad_export="off", project_dir=self.root / PROJECT_ID))
        )
        self.addCleanup(self.client.close)

    def test_the_second_entry_is_readable_and_refuses_to_be_run_here(self) -> None:
        index = self.client.get("/api/capabilities", params={"goal": "出剖面"}).json()
        self.assertEqual([row["capabilityId"] for row in index["capabilities"]], ["drawing.section"])
        described = self.client.get("/api/capabilities/drawing.section")
        self.assertEqual(described.status_code, 200, described.text)
        self.assertEqual(described.json()["capability"]["status"], "MISSING")

        before = {path.name for path in (self.root / PROJECT_ID / "runs").iterdir()}
        refused = self.client.post("/api/capabilities/drawing.section/run", json={
            "stateDigest": self.client.get("/api/state").json()["stateDigest"],
            "targetComponentId": "portico", "elementId": "portico-base",
            "utterance": "set height to 0.9"})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertEqual(refused.json()["code"], "CAPABILITY_NOT_RUNNABLE_HERE")
        self.assertIn("POST /api/drawings/elevations", refused.json()["detail"])
        self.assertIn("Nothing was run", refused.json()["detail"])
        self.assertEqual({path.name for path in (self.root / PROJECT_ID / "runs").iterdir()}, before)

    def test_describing_it_offers_no_target_and_no_request(self) -> None:
        """Described is not implemented: no numbers of this project, no ready request."""

        body = self.client.get("/api/capabilities/drawing.section",
                               params={"target": "portico", "elementId": "portico-base"}).json()
        self.assertEqual(body["capability"]["capability_id"], "drawing.section")
        self.assertIsNone(body["target"], "another capability's work is not read off this catalog")
        self.assertIsNone(body["keep"])
        self.assertIsNone(body["request"], "a request this route would refuse is not offered")
        self.assertTrue(any("is not performed by this API" in line for line in body["honesty"]),
                        body["honesty"])
        self.assertTrue(any("POST /api/drawings/elevations" in line for line in body["honesty"]))
        self.assertFalse(any("implemented as described" in line for line in body["honesty"]),
                         "nothing here may claim this API implements it")
        # The base it was read against is still answered: that is this project's
        # fact, not a promise about the capability.
        self.assertEqual(body["source"]["projectId"], PROJECT_ID)

    def test_the_implemented_one_still_runs_beside_it(self) -> None:
        started = self.client.post(f"/api/capabilities/{CAPABILITY}/run", json={
            "stateDigest": self.client.get("/api/state").json()["stateDigest"],
            "targetComponentId": "portico", "elementId": "portico-base",
            "utterance": "set height to 0.9"})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["capabilityId"], CAPABILITY)


class CapabilityRunTestCase(unittest.TestCase):
    """One capability, run twice on the same object, with real geometry read back."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.project = self.root / PROJECT_ID
        self.client = TestClient(create_app(StudioSettings(cad_export="occt", project_dir=self.project)))
        self.addCleanup(self.client.close)

    # ---- the same helpers the drawing tests use, so the base is a real run

    def digest(self, run: str | None = None) -> str:
        query = f"?run={run}" if run else ""
        return self.client.get(f"/api/state{query}").json()["stateDigest"]

    def finish(self, job_id: str) -> dict:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed"}:
                return job
            time.sleep(0.05)
        raise AssertionError("the candidate never finished")

    def first_candidate(self, height: float = 3.3) -> str:
        """A real candidate to edit: a new volume beside the portico."""

        drawn = self.client.post("/api/proposals/sketch", json={
            "stateDigest": self.digest(), "componentId": "small-house",
            "parentComponentId": "portico", "semanticKind": "building",
            "elementId": "small-house-main", "profile": SQUARE, "height": height,
            "baseLevel": "level-ground",
        })
        self.assertEqual(drawn.status_code, 201, drawn.text)
        started = self.client.post(f"/api/proposals/{drawn.json()['proposalId']}/candidate")
        self.assertEqual(started.status_code, 202, started.text)
        job = self.finish(started.json()["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        return job["candidateId"]

    def run_capability(self, **body: object):
        return self.client.post(f"/api/capabilities/{CAPABILITY}/run", json=body)

    def runs(self) -> set[str]:
        """The runs this project has retained, as the repository holds them."""

        return {path.name for path in (self.project / "runs").iterdir() if path.is_dir()}

    def exported_spans(self, run_id: str) -> dict[str, list[float]]:
        """What the run saved, through archflow's own ``.3dm`` inspector."""

        models = sorted((self.project / "runs" / run_id).rglob("*.3dm"))
        self.assertTrue(models, f"the run exported no model under runs/{run_id}")
        spans: dict[str, list[float]] = {}
        for path in models:
            for row in inspect_three_dm(path).named_object_bboxes:
                bbox = row["bbox"]
                spans[str(row["name"])] = sorted(
                    round(high - low, 3) for low, high in zip(bbox["min"], bbox["max"])
                )
        return spans

    # ---- the path itself

    def test_the_capability_changes_the_number_and_leaves_the_rest_standing(self) -> None:
        base = self.first_candidate(3.3)
        described = self.client.get(f"/api/capabilities/{CAPABILITY}", params={
            "target": "small-house", "elementId": "small-house-main", "run": base}).json()
        self.assertEqual(described["source"]["runId"], base)
        heights = [row for row in described["target"]["editable"] if row["field"] == "height"]
        self.assertEqual([row["value"] for row in heights], [3.3], described["target"])
        self.assertIn("entity:portico-base", described["keep"]["accepted"])

        # The request the description handed back, with the one number changed
        # and the portico named as what must not move.
        body = dict(described["request"]["body"])
        body["utterance"] = "set height to 4.2"
        body["keep"] = ["entity:portico-base"]
        started = self.run_capability(**body)
        self.assertEqual(started.status_code, 202, started.text)
        answer = started.json()
        self.assertEqual(answer["capabilityId"], CAPABILITY)
        self.assertEqual(answer["change"]["old"], 3.3)
        self.assertEqual(answer["change"]["new"], 4.2)
        self.assertEqual(answer["kept"], ["entity:portico-base"])
        self.assertEqual(answer["target"]["elementId"], "small-house-main")
        self.assertIn(f"GET /api/jobs/{answer['jobId']}", answer["next"])
        self.assertIn(f"?against={base}", " ".join(answer["next"]))

        job = self.finish(answer["jobId"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["candidateId"], answer["candidateId"])
        spans = self.exported_spans(answer["candidateId"])
        self.assertEqual(spans["obj-small-house-main"], sorted([3.0, 2.0, 4.2]),
                         "the exported solid is the height the capability was asked for")
        self.assertEqual(spans["obj-portico-base"], sorted([4.0, 2.0, 0.6]),
                         "what the request kept is unchanged in the export")

    def test_the_next_round_starts_from_the_candidate_it_just_made(self) -> None:
        base = self.first_candidate(3.3)
        first = self.run_capability(
            stateDigest=self.digest(base), sourceRunId=base, targetComponentId="small-house",
            elementId="small-house-main", utterance="set height to 4.2", keep=["entity:portico-base"],
        ).json()
        self.assertEqual(self.finish(first["jobId"])["status"], "succeeded")
        run_two = first["candidateId"]

        # Describing the new candidate reports the number it now holds, which
        # is what makes a second round possible without remembering anything.
        described = self.client.get(f"/api/capabilities/{CAPABILITY}", params={
            "target": "small-house", "elementId": "small-house-main", "run": run_two}).json()
        self.assertEqual([row["value"] for row in described["target"]["editable"]
                          if row["field"] == "height"], [4.2])

        second = self.run_capability(**{**described["request"]["body"], "utterance": "set height to 5.0",
                                        "keep": ["entity:portico-base"]})
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(self.finish(second.json()["jobId"])["status"], "succeeded")
        spans = self.exported_spans(second.json()["candidateId"])
        self.assertEqual(spans["obj-small-house-main"], sorted([3.0, 2.0, 5.0]))
        self.assertEqual(spans["obj-portico-base"], sorted([4.0, 2.0, 0.6]))

    def test_keeping_what_the_change_reaches_runs_nothing(self) -> None:
        base = self.first_candidate(3.3)
        before = self.runs()
        refused = self.run_capability(
            stateDigest=self.digest(base), sourceRunId=base, targetComponentId="small-house",
            elementId="small-house-main", utterance="set height to 4.2",
            keep=["entity:small-house-main"],
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "PROPOSAL_NOT_RUNNABLE")
        self.assertIn("keep", refused.json()["detail"])
        self.assertEqual(self.runs(), before, "a refused change leaves no run behind")

    def test_a_stale_base_runs_nothing(self) -> None:
        base = self.first_candidate(3.3)
        refused = self.run_capability(
            stateDigest="f" * 64, sourceRunId=base, targetComponentId="small-house",
            elementId="small-house-main", utterance="set height to 4.2",
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "STALE_BASE")

    def test_a_sentence_the_grammar_does_not_take_is_refused_with_the_forms(self) -> None:
        base = self.first_candidate(3.3)
        refused = self.run_capability(
            stateDigest=self.digest(base), sourceRunId=base, targetComponentId="small-house",
            elementId="small-house-main", utterance="make it a bit taller",
        )
        self.assertEqual(refused.status_code, 422, refused.text)
        body = refused.json()
        self.assertEqual(body["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertTrue(body["acceptedForms"], body)

    def test_a_keep_ref_this_record_does_not_declare_is_refused(self) -> None:
        base = self.first_candidate(3.3)
        refused = self.run_capability(
            stateDigest=self.digest(base), sourceRunId=base, targetComponentId="small-house",
            elementId="small-house-main", utterance="set height to 4.2", keep=["entity:gatehouse"],
        )
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertEqual(refused.json()["code"], "BLOCKED_NEEDS_HUMAN")
        self.assertIn("gatehouse", refused.json()["question"])

    def test_an_unregistered_capability_never_runs(self) -> None:
        base = self.first_candidate(3.3)
        refused = self.client.post("/api/capabilities/candidate.invent_everything/run", json={
            "stateDigest": self.digest(base), "sourceRunId": base, "targetComponentId": "small-house",
            "elementId": "small-house-main", "utterance": "set height to 4.2"})
        self.assertEqual(refused.status_code, 404, refused.text)
        self.assertEqual(refused.json()["code"], "CAPABILITY_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
