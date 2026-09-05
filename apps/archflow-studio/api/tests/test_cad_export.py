"""The ordinary candidate exports in process, and what that leaves behind.

A process nothing configured runs a candidate through the kernel's own runner
with the OCCT executor: one exact STEP file and one mesh ``.3dm`` preview per
seat, retained as ``seat-occt-execution`` in the candidate's own run. These
tests go through the real routes — propose, run, read the candidate, fetch the
bytes, restart, continue from the candidate — and read the STEP file back with
the real OCCT reader and the preview with the real 3dm inspector, so what is
asserted is what a client would get and what a CAD program would open.

Rhino is never started on this path. Every test here fails the moment anything
calls the Rhino export entry points or starts a process, and the explicit
``rhino`` backend is shown to be the only thing that routes there.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from archflow_studio_api.application import candidate as candidate_module
from archflow_studio_api.application.binding import record_kind
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import (
    CAD_EXPORT_ENV,
    CAD_EXPORT_OFF,
    CAD_EXPORT_RHINO,
    PROJECT_DIR_ENV,
    RHINO_EXPORT_ENV,
    StudioSettings,
)

from archflow.adapters import cad_execution, occt_backend
from archflow.adapters.cad_execution import CadCapabilityError
from archflow.adapters.three_dm_inspector import inspect_three_dm
from archflow.capabilities.geometry_proposal import load_compiled_geometry_program
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import record_ref_from_uri

from .support import (
    EVIDENCE,
    PROJECT_ID,
    RECORD_PAYLOAD,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    RUNNER_SEATS_PATH,
    make_empty_project,
    make_project,
    runner_state_digest,
    write_runner_record,
)

NEEDS_OCCT = unittest.skipUnless(
    occt_backend.occt_available(), "cadquery-ocp is not installed"
)
JOB_DEADLINE = 180.0
TERMINAL = ("succeeded", "failed")
OCCT_RECEIPT = "seat-occt-execution"
RHINO_RECEIPT = "seat-rhino-execution"
INSPECTION = "seat-3dm-inspection"


def _refuse_rhino(*args, **kwargs):
    raise AssertionError(f"the ordinary candidate must never reach Rhino: {args[:1]}")


def no_rhino():
    """Fail the test if anything reaches the Rhino entry points or starts a process."""

    return mock.patch.multiple(
        "archflow.adapters.cad_execution",
        prepare_rhino_three_dm_export=_refuse_rhino,
        execute_rhino_three_dm_export=_refuse_rhino,
    )


def no_process():
    return mock.patch.multiple(subprocess, Popen=_refuse_rhino, run=_refuse_rhino)


def no_cad_at_all():
    """Fail the test if a read path runs any CAD executor."""

    return mock.patch(
        "archflow.adapters.cad_execution.execute_occt_export",
        side_effect=AssertionError("reading retained artifacts must not run CAD"),
    )


class OcctCandidateTestCase(unittest.TestCase):
    """One real project and a client built from settings nothing configured."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repository, _ = make_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = self.open_client(self.settings)
        self.state_digest = runner_state_digest(self.repository, REFERENCE_RUN_ID)
        rhino = no_rhino()
        rhino.start()
        self.addCleanup(rhino.stop)

    def open_client(self, settings: StudioSettings) -> TestClient:
        client = TestClient(create_app(settings))
        self.addCleanup(client.close)
        return client

    def propose(self, client: TestClient, utterance: str, **body: object) -> dict:
        body.setdefault("stateDigest", self.state_digest)
        body.setdefault("targetComponentId", "portico")
        body["utterance"] = utterance
        response = client.post("/api/proposals", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def finished(self, client: TestClient, job_id: str) -> dict:
        deadline = time.monotonic() + JOB_DEADLINE
        while time.monotonic() < deadline:
            response = client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            if payload["status"] in TERMINAL:
                return payload
            time.sleep(0.02)
        raise AssertionError(f"job {job_id} never finished")

    def run_candidate(self, client: TestClient, utterance: str, **body: object) -> tuple[dict, dict]:
        proposal = self.propose(client, utterance, **body)
        response = client.post(f"/api/proposals/{proposal['proposalId']}/candidate")
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        return accepted, self.finished(client, accepted["jobId"])

    def candidate(self, client: TestClient, candidate_id: str) -> dict:
        response = client.get(f"/api/candidates/{candidate_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def records_of(self, run_id: str) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for ref in self.repository.list_json(
            run=self.repository.load_run(run_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
        ):
            kind = record_kind(ref)
            if kind is not None:
                kinds[kind] = kinds.get(kind, 0) + 1
        return kinds

    def load_kind(self, run_id: str, kind: str) -> dict:
        for ref in self.repository.list_json(
            run=self.repository.load_run(run_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
        ):
            if record_kind(ref) == kind:
                return dict(self.repository.load_json(ref))
        raise AssertionError(f"run {run_id} retained no {kind}")

    def bytes_of(self, client: TestClient, artifact: dict) -> bytes:
        response = client.get(f"/api/artifacts/{artifact['sha256']}/bytes")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), artifact["sha256"])
        self.assertIn(artifact["fileName"], response.headers["content-disposition"])
        return response.content

    def step_entries(self, data: bytes) -> dict[str, occt_backend.StepEntry]:
        """The named shapes of a STEP file, cold-read by the real OCCT reader, for measuring and point probes."""

        path = self.root / f"readback-{hashlib.sha256(data).hexdigest()[:8]}.step"
        path.write_bytes(data)
        entries = occt_backend.read_step(path, length_unit="meter")
        names = [entry.name for entry in entries]
        self.assertEqual(len(set(names)), len(names), f"duplicate names in STEP: {names}")
        return {entry.name: entry for entry in entries}

    def step_shapes(self, data: bytes) -> dict[str, occt_backend.ShapeMeasure]:
        """The named solids of a STEP file, re-read by the real OCCT reader."""

        return {name: occt_backend.measure_shape(entry.shape) for name, entry in self.step_entries(data).items()}

    @staticmethod
    def split(artifacts: list[dict]) -> tuple[dict, dict]:
        exact = [row for row in artifacts if row["representation"] == "exact"]
        preview = [row for row in artifacts if row["representation"] == "preview"]
        assert len(exact) == 1 and len(preview) == 1, artifacts
        return exact[0], preview[0]


@NEEDS_OCCT
class DefaultCandidateExportTests(OcctCandidateTestCase):
    """Settings nothing configured: the candidate exports through OCCT, on the ordinary lane."""

    def test_the_candidate_leaves_an_exact_step_and_a_preview_and_serves_both(self) -> None:
        with no_process():
            accepted, job = self.run_candidate(self.client, "set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        # An OCCT export is ordinary worker work: no Rhino lane was claimed.
        self.assertEqual(job["lane"], "parallel")
        candidate_id = accepted["candidateId"]

        kinds = self.records_of(candidate_id)
        self.assertEqual(kinds.get(OCCT_RECEIPT), 1, kinds)
        self.assertNotIn(RHINO_RECEIPT, kinds)
        self.assertEqual(kinds.get(INSPECTION), 1, kinds)

        candidate = self.candidate(self.client, candidate_id)
        self.assertTrue(candidate["seatExecutionComplete"], candidate["seatResults"])
        seat = candidate["seatResults"][0]
        self.assertEqual(seat["status"], "proposal_accepted")
        exact, preview = self.split(candidate["artifacts"])
        # Two files of one receipt: the same receipt, stage, program and state;
        # the exact one is a STEP the viewer cannot load, the preview a 3dm it can.
        self.assertEqual(exact["receiptRef"], preview["receiptRef"])
        self.assertIn(f"/runs/{candidate_id}/records/{OCCT_RECEIPT}-", exact["receiptRef"])
        self.assertEqual((exact["format"], preview["format"]), ("step", "3dm"))
        self.assertTrue(exact["fileName"].endswith(".step"))
        self.assertTrue(preview["fileName"].endswith(".preview.3dm"))
        for row in (exact, preview):
            self.assertIs(row["available"], True, row)
            self.assertEqual(row["status"], "succeeded")
            self.assertIs(row["readbackVerified"], True)
            self.assertEqual(row["runId"], candidate_id)
            self.assertEqual(row["programDigest"], seat["programDigest"])
            self.assertEqual(row["designStateDigest"], candidate["stateDigest"])
            self.assertEqual(row["base"]["version"], 0)
            self.assertEqual(row["objectCount"], seat["objects"])
            self.assertEqual(row["lengthUnit"], "meter")
        self.assertNotEqual(exact["sha256"], preview["sha256"])

        # The receipt binds the exact run, base, branch and program, and the
        # files it certifies are the ones in the candidate's own workspace.
        receipt = self.load_kind(candidate_id, OCCT_RECEIPT)
        binding = receipt["identity"]["binding"]
        run = self.repository.load_run(candidate_id)
        self.assertEqual(binding["run_id"], candidate_id)
        self.assertEqual(binding["base"], {"project_id": PROJECT_ID, "version": run.base.version, "state_sha256": run.base.state_sha256})
        self.assertTrue(binding["program_ref"]["relative_path"].startswith(f"runs/{candidate_id}/branches/runner-v1/records/studio-candidate-seat-portico-geometry-program-"))
        # The branch-retained program is the seat's compiled program.
        retained_program = load_compiled_geometry_program(
            self.repository.load_json(record_ref_from_uri(binding["program_ref"]["uri"], PROJECT_ID))
        )
        self.assertEqual(retained_program.program_digest, seat["programDigest"])
        self.assertEqual(binding["program_digest"], seat["programDigest"])
        self.assertEqual(receipt["exact_artifact"]["sha256"], exact["sha256"])
        self.assertEqual(receipt["preview_artifact"]["sha256"], preview["sha256"])
        self.assertFalse(receipt["preview_artifact"]["exact_brep"])
        self.assertTrue(exact["relativePath"].startswith(f"runs/{candidate_id}/workspaces/cad-studio-candidate-seat-portico/"))

        # The exact geometry, re-read by OCCT: the two prisms, and the edit in them.
        shapes = self.step_shapes(self.bytes_of(self.client, exact))
        self.assertEqual(set(shapes), set(receipt["physical_object_ids"]))
        base_shape = next(measure for name, measure in shapes.items() if "portico-base" in name)
        self.assertTrue(base_shape.valid and base_shape.closed and base_shape.solid_count == 1)
        self.assertAlmostEqual(base_shape.bbox_max[2] - base_shape.bbox_min[2], 2.2, places=6)
        self.assertAlmostEqual(base_shape.volume, 4.0 * 2.0 * 2.2, places=6)
        cornice = next(measure for name, measure in shapes.items() if "portico-cornice" in name)
        self.assertAlmostEqual(cornice.bbox_min[2], 2.2, places=6)  # it stands on the edited base

        # The preview, re-read by the 3dm inspector: the same names, on the
        # semantic layers, carrying the run's identity as document user text.
        preview_path = self.root / "preview.3dm"
        preview_path.write_bytes(self.bytes_of(self.client, preview))
        inspection = inspect_three_dm(preview_path)
        self.assertEqual({row["name"] for row in inspection.named_object_bboxes}, set(receipt["physical_object_ids"]))
        self.assertEqual({row["type"] for row in inspection.named_object_bboxes}, {"Mesh"})
        document = {row["key"]: row["value"] for row in inspection.document_user_strings}
        self.assertEqual(document["archflow:run_id"], candidate_id)
        self.assertEqual(document["archflow:program_digest"], seat["programDigest"])
        self.assertEqual(document["archflow:design_state_digest"], candidate["stateDigest"])
        self.assertEqual(document["archflow:export_path"], "occt")

        # The listing route agrees with the candidate readout.
        listed = [row for row in self.client.get("/api/artifacts").json()["artifacts"] if row["runId"] == candidate_id]
        self.assertEqual({row["sha256"] for row in listed}, {exact["sha256"], preview["sha256"]})

    def test_a_restart_reads_the_retained_files_without_running_cad_and_continues_from_them(self) -> None:
        with no_process():
            accepted, job = self.run_candidate(self.client, "set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        first = accepted["candidateId"]
        before_head = self.repository.read_head()
        authored = {
            path: self.repository.layout.resolve_relative(path).read_bytes()
            for path in (RUNNER_RECORD_PATH, RUNNER_SEATS_PATH)
        }

        restarted = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID))
        with no_cad_at_all(), no_process():
            candidate = self.candidate(restarted, first)
            exact, preview = self.split(candidate["artifacts"])
            self.assertEqual(candidate["status"], "succeeded")
            self.assertIsNone(candidate["jobId"])
            exact_bytes = self.bytes_of(restarted, exact)
            self.bytes_of(restarted, preview)
            source = restarted.get("/api/state", params={"run": first}).json()
        self.assertEqual(source["referenceRun"]["runId"], first)
        self.assertTrue(source["matchesReferenceReceipt"])

        # A -> B: the second edit starts from A's retained record, keeps A's
        # edit and A's exact base, and its exact geometry says so.
        with no_process():
            accepted_b, job_b = self.run_candidate(
                restarted, "set height to 0.5",
                stateDigest=source["stateDigest"], sourceRunId=first, elementId="portico-cornice",
            )
        self.assertEqual(job_b["status"], "succeeded", job_b)
        second = accepted_b["candidateId"]
        record = self.load_kind(second, "state-record")
        heights = {e["entity_id"]: e["fields"]["params"]["height"] for e in record["entities"] if e.get("schema") == "Element@1"}
        self.assertEqual(heights, {"portico-base": 2.2, "portico-cornice": 0.5})
        candidate_b = self.candidate(restarted, second)
        self.assertEqual(candidate_b["base"]["version"], 0)
        exact_b, _ = self.split(candidate_b["artifacts"])
        self.assertNotEqual(exact_b["sha256"], exact["sha256"])
        shapes = self.step_shapes(self.bytes_of(restarted, exact_b))
        cornice = next(m for name, m in shapes.items() if "portico-cornice" in name)
        self.assertAlmostEqual(cornice.bbox_min[2], 2.2, places=6)
        self.assertAlmostEqual(cornice.bbox_max[2], 2.7, places=6)
        # A's own files are untouched by B, and the project moved nothing.
        self.assertEqual(hashlib.sha256(exact_bytes).hexdigest(), exact["sha256"])
        self.assertEqual(self.repository.read_head(), before_head)
        for path, content in authored.items():
            self.assertEqual(self.repository.layout.resolve_relative(path).read_bytes(), content)
        self.assertEqual(restarted.get("/api/state").json()["referenceRun"]["runId"], REFERENCE_RUN_ID)

    def test_a_corrupted_exact_file_is_unavailable_and_never_served_as_current(self) -> None:
        with no_process():
            accepted, job = self.run_candidate(self.client, "set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        candidate = self.candidate(self.client, accepted["candidateId"])
        exact, preview = self.split(candidate["artifacts"])
        path = self.repository.layout.root / exact["relativePath"]
        path.write_bytes(b"ISO-10303-21; edited by hand")

        with no_cad_at_all():
            candidate = self.candidate(self.client, accepted["candidateId"])
            exact_now, preview_now = self.split(candidate["artifacts"])
            self.assertIs(exact_now["available"], False)
            self.assertEqual(exact_now["unavailableReason"], "digest mismatch")
            self.assertEqual(exact_now["sha256"], exact["sha256"])
            response = self.client.get(f"/api/artifacts/{exact['sha256']}/bytes")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "ARTIFACT_DIGEST_MISMATCH")
            # The preview is a different file and still answers for itself.
            self.assertIs(preview_now["available"], True)
            self.bytes_of(self.client, preview)
        validation = self.client.get(f"/api/candidates/{accepted['candidateId']}/validation").json()
        self.assertFalse(validation["reviewReady"])
        self.assertIn("runner.exports_available", validation["blockedBy"])


class UnsupportedOperationTests(OcctCandidateTestCase):
    """An operation OCCT does not realize fails that seat's export by name: no file, no Rhino, no stand-in.

    Every producer today emits extrusions and lofts, so no record reaches the
    executor's capability boundary through the runner; the executor's own
    tests raise ``CadCapabilityError`` for a real array. Here it is raised at
    the executor for the candidate's seat, and what is tested is what the
    candidate, its readout and its review readiness do with it.
    """

    def test_the_seat_reports_the_unsupported_operation_and_writes_nothing(self) -> None:
        refusal = CadCapabilityError(
            "OCCT executor cannot realize portico-colonnade (array): block instancing is not realized",
            op_id="portico-colonnade", kind="array",
        )
        with no_process(), mock.patch.object(cad_execution, "execute_occt_export", side_effect=refusal):
            accepted, job = self.run_candidate(self.client, "set height to 2.2", elementId="portico-base")
        # The run finished and retained its receipt; the export is what failed.
        self.assertEqual(job["status"], "succeeded", job)
        candidate_id = accepted["candidateId"]
        candidate = self.candidate(self.client, candidate_id)
        self.assertFalse(candidate["seatExecutionComplete"])
        self.assertEqual(candidate["seatResults"][0]["status"], "export_failed")
        self.assertEqual(candidate["artifacts"], [])
        kinds = self.records_of(candidate_id)
        self.assertNotIn(OCCT_RECEIPT, kinds)
        self.assertNotIn(RHINO_RECEIPT, kinds)
        receipt = self.load_kind(candidate_id, "runner-run-receipt")
        cad = receipt["seat_results"][0]["cad"]
        self.assertEqual((cad["status"], cad["backend"], cad["execution_ref"]), ("unsupported", "occt", None))
        self.assertEqual(cad["failures"][0]["kind"], "array")
        self.assertIn("array", cad["failures"][0]["detail"])
        workspace = self.repository.layout.run(candidate_id).workspaces
        self.assertEqual([p for p in workspace.rglob("*") if p.is_file()], [])
        validation = self.client.get(f"/api/candidates/{candidate_id}/validation").json()
        self.assertFalse(validation["reviewReady"])
        self.assertIn("runner.exports_available", validation["blockedBy"])


class ExplicitCadSettingsTests(OcctCandidateTestCase):
    """The one setting decides: off writes nothing, rhino is named or not taken at all."""

    def test_a_legacy_explicit_disable_runs_no_cad(self) -> None:
        with mock.patch.dict(os.environ, {PROJECT_DIR_ENV: str(self.root / PROJECT_ID), RHINO_EXPORT_ENV: "0"}, clear=False):
            os.environ.pop(CAD_EXPORT_ENV, None)
            settings = StudioSettings.from_env()
        self.assertEqual(settings.cad_export, CAD_EXPORT_OFF)
        client = self.open_client(settings)
        with no_cad_at_all(), no_process():
            accepted, job = self.run_candidate(client, "set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["lane"], "parallel")
        candidate = self.candidate(client, accepted["candidateId"])
        self.assertEqual(candidate["artifacts"], [])
        self.assertEqual(candidate["seatResults"][0]["status"], "proposal_accepted")
        kinds = self.records_of(accepted["candidateId"])
        self.assertNotIn(OCCT_RECEIPT, kinds)
        self.assertNotIn(RHINO_RECEIPT, kinds)
        self.assertEqual([p for p in self.repository.layout.run(accepted["candidateId"]).workspaces.rglob("*") if p.is_file()], [])
        self.assertNotIn("cad-export", client.get("/api/protocol").json()["capabilities"])

    def test_the_rhino_backend_is_routed_and_serialized_only_when_named(self) -> None:
        seen: list[object] = []

        def capture(repository, *, run, stage_guard, record, seats, options):
            seen.append(options)
            raise RuntimeError("stopped before Rhino would start")

        client = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export=CAD_EXPORT_RHINO))
        with mock.patch.object(candidate_module, "run_project", side_effect=capture):
            accepted, job = self.run_candidate(client, "set height to 2.2", elementId="portico-base")
        self.assertEqual(job["status"], "failed")
        self.assertIn("stopped before Rhino", job["error"])
        self.assertEqual(job["lane"], "exclusive")
        (options,) = seen
        self.assertTrue(options.export)
        self.assertEqual(options.cad_backend, "rhino")
        self.assertIn("rhino-export", client.get("/api/protocol").json()["capabilities"])

    def test_the_default_and_the_legacy_enable_route_to_occt_not_rhino(self) -> None:
        seen: list[object] = []

        def capture(repository, *, run, stage_guard, record, seats, options):
            seen.append(options)
            raise RuntimeError("stopped before any export")

        with mock.patch.dict(os.environ, {PROJECT_DIR_ENV: str(self.root / PROJECT_ID), RHINO_EXPORT_ENV: "1"}, clear=False):
            os.environ.pop(CAD_EXPORT_ENV, None)
            legacy = StudioSettings.from_env()
        for settings in (self.settings, legacy):
            with self.subTest(cad_export=settings.cad_export):
                client = self.open_client(settings)
                with mock.patch.object(candidate_module, "run_project", side_effect=capture):
                    _, job = self.run_candidate(client, "set height to 2.2", elementId="portico-base")
                self.assertEqual(job["lane"], "parallel")
                options = seen.pop()
                self.assertTrue(options.export)
                self.assertEqual(options.cad_backend, "occt")


# ---- C: one whole assembly through the candidate API, A, then restart and A -> B -------------
#
# A synthetic temporary project (not the villa, no adopted conditions): a
# straight flight of ten steps from ground to a declared upper level, a
# landing bound to that same fixed level with a declared embed so its top is
# the level and its near edge is the flight's far edge, and a wall two metres
# to the side carrying one typed window whose width is bound to a record
# parameter. Verified ids and aliases only: the level roles and the ``front``
# axis are the fixture's, the numbered wall axis is the producer fixture's.
#
# The record also declares the one block of massing, two zones in it and the
# interface between them, because a typed window's assembly cites an interface
# and the runner offers only the interfaces the record's own ``Connection@1``
# entities name (``relation:<declared relation>``); a record with no massing
# projects to a spatial option with no connections, and the seat is exhausted
# before any CAD. That interface relation binds no validator, so the run
# reports it ``unchecked`` ("no validator bound"), and the test says so.

UPPER_LEVEL = "level-upper"
MASSING_LEVEL, BLOCK, INSIDE, OUTSIDE = "massing-ground", "block", "zone-inside", "zone-outside"
INTERFACE = "inside-to-outside"
INTERFACE_REF = f"relation:{INTERFACE}"
UPPER_ELEVATION = 1.8
STAIR_ID, LANDING_ID, WALL_ID = "stair-flight", "landing", "wall-east"
FLIGHT_OBJECT, LANDING_OBJECT = "obj-stair-flight", "obj-landing"
WALL_CUT, APERTURE = "obj-wall-east-cut", "obj-wall-east-aperture-window"
FRAME, PANE = "obj-frame-wall-east-window", "obj-glazing-wall-east-window"
ASSEMBLY_OBJECTS = (FRAME, PANE, LANDING_OBJECT, FLIGHT_OBJECT, APERTURE, WALL_CUT)
CLEARANCE = "rel-stair-clear-of-wall"
CLEARANCE_INTERVAL = (1.0, 1.6)
COUNT, RISE, GOING = 10, 0.18, 0.3                 # 10 x 0.18 = 1.8 = the upper level; 10 x 0.3 = the 3 m line
WALL_ALONG, WALL_THICKNESS, WALL_HEIGHT = 4.2, 0.3, 3.0
WALL_X = 2.0                                       # the wall's line: axis "1" through (2, 0, 0), direction +z
SILL, HEAD, WINDOW_ALONG = 0.9, 2.4, 2.0
LANDING_THICKNESS = 0.2

# The same window type the producer tests declare (frame 0.09 wide, 0.18 deep,
# projecting 0.1 outward; 25 mm glass 10 mm in from the line), reproduced
# here rather than imported across the test namespaces.
WINDOW_TYPE = {"schema": "WindowType@1", "type_id": "window-type-1", "frame_width": 0.09, "frame_depth": 0.18,
               "frame_projection": 0.1, "glazing_thickness": 0.025, "glazing_offset": 0.01}
FRAME_WIDTH, FRAME_DEPTH, FRAME_PROJECTION = 0.09, 0.18, 0.1
GLASS_THICKNESS, GLASS_OFFSET = 0.025, 0.01


def _element(entity_id: str, producer: str, references: dict, params: dict) -> dict:
    return {
        "entity_id": entity_id, "schema": "Element@1", "parent_id": "portico",
        "fields": {"component_id": "portico", "producer": producer, "references": references, "params": params},
        "basis_refs": [EVIDENCE],
    }


def _on(axis_role: str, along: float) -> dict:
    return {"axis_point": {"axis": axis_role, "along": along}}


def _with_block(entity: dict) -> dict:
    """The root component owns the one massing volume; every other copied entity is taken as is."""

    if entity["entity_id"] != "building":
        return entity
    return {**entity, "fields": {**entity["fields"], "volume_ids": [BLOCK]}}


ASSEMBLY_RECORD_PAYLOAD: dict[str, object] = {
    **{key: value for key, value in RECORD_PAYLOAD.items() if key not in ("option", "entities", "parameters", "relations")},
    "option": {**RECORD_PAYLOAD["option"], "footprint_cells": [[0, 0]]},  # type: ignore[dict-item]
    "entities": [
        *[_with_block(e) for e in RECORD_PAYLOAD["entities"] if e["schema"] in ("Component@1", "Level@1", "GridAxis@1")],  # type: ignore[index]
        # The massing the record declares (x and z plan, y up, inclusive cells): one block, two zones in it,
        # and the interface between them that the window cites.
        {"entity_id": MASSING_LEVEL, "schema": "MassingLevel@1", "fields": {"base_y": 0, "height": 4}, "basis_refs": [EVIDENCE]},
        {"entity_id": BLOCK, "schema": "Volume@1", "fields": {"min": [-2, 0, -1], "max": [3, 3, 5], "level_ids": [MASSING_LEVEL]}, "basis_refs": [EVIDENCE]},
        {"entity_id": INSIDE, "schema": "Space@1", "fields": {"program_node_refs": ["program-node:inside"], "level_ids": [MASSING_LEVEL], "volume_ids": [BLOCK]}, "basis_refs": [EVIDENCE]},
        {"entity_id": OUTSIDE, "schema": "Space@1", "fields": {"program_node_refs": ["program-node:outside"], "level_ids": [MASSING_LEVEL], "volume_ids": [BLOCK]}, "basis_refs": [EVIDENCE]},
        {"entity_id": f"connection-{INTERFACE}", "schema": "Connection@1",
         "fields": {"source_zone_id": OUTSIDE, "target_zone_id": INSIDE, "relationship_refs": [INTERFACE_REF], "directed": False}, "basis_refs": [EVIDENCE]},
        {"entity_id": UPPER_LEVEL, "schema": "Level@1", "fields": {"role": "piano-nobile", "elevation": UPPER_ELEVATION}, "basis_refs": [EVIDENCE]},
        {"entity_id": "axis-1", "schema": "GridAxis@1", "fields": {"role": "1", "origin": [WALL_X, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}, "basis_refs": [EVIDENCE]},
        _element(STAIR_ID, "stair",
                 {"from": _on("front", 0.0), "to": _on("front", COUNT * GOING), "base": {"level": "level-ground"}, "top": {"level": UPPER_LEVEL}},
                 {"count": COUNT, "rise": RISE, "width": 1.2}),
        # The landing stands on the declared upper level, embedded by its own
        # thickness, so its physical top is that level and it meets the flight's
        # last riser along an edge. It is not carried by the flight, and no
        # relation says so.
        _element(LANDING_ID, "prism",
                 {"base": {"offset_from": {"level": UPPER_LEVEL, "offset": -LANDING_THICKNESS}}},
                 {"profile": [[-0.75, 3.0], [0.75, 3.0], [0.75, 4.2], [-0.75, 4.2]], "height": LANDING_THICKNESS}),
        _element(WALL_ID, "wall",
                 {"base": {"level": "level-ground"}, "line": {"from": _on("1", 0.0), "to": _on("1", WALL_ALONG)}},
                 {"thickness": WALL_THICKNESS, "height": WALL_HEIGHT, "types": [WINDOW_TYPE],
                  "openings": [{"opening_id": "window", "kind": "window", "along": WINDOW_ALONG, "width": "@window_width",
                                "sill": SILL, "head": HEAD, "type_id": "window-type-1", "interface_ref": INTERFACE_REF}]}),
    ],
    "parameters": [{"key": "window_width", "value": 1.2, "unit": "m", "epistemic_status": "declared"}],
    "relations": [
        {
            "relation_id": CLEARANCE, "kind": "clearance", "subject": STAIR_ID, "object": WALL_ID,
            "propagation": "revalidate",
            "validator": {"check_kind": "clearance_interval", "interval_m": list(CLEARANCE_INTERVAL)},
        },
        # The interface the connection names and the window cites. It binds no
        # validator: nothing here measures it, and the run says so.
        {"relation_id": INTERFACE, "kind": "interface", "subject": OUTSIDE, "object": INSIDE, "propagation": "revalidate", "basis_refs": [EVIDENCE]},
    ],
}


@NEEDS_OCCT
class WholeAssemblyCandidateTests(OcctCandidateTestCase):
    """A whole stair, its landing and the adjacent wall and window, made by the real proposal and candidate routes.

    A: ``set width to 1.4`` on the flight, run to an exact STEP and a preview.
    Restart: A is read back without any CAD, and B continues from A's own
    retained record: ``set parameter:window_width to 1.4 m``. Everything
    asserted is read off the files a client fetches and the records the runs
    retained; A's bytes are re-fetched after B and hashed against the originals.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        # No finished run at all: the projection starts from the authored WIP,
        # which is exactly what ``require_actionable`` allows a proposal to base on.
        self.repository = make_empty_project(self.root)
        write_runner_record(self.repository, ASSEMBLY_RECORD_PAYLOAD)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.client = self.open_client(self.settings)
        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["referenceRunSource"], "none")
        self.state_digest = state.json()["stateDigest"]
        rhino = no_rhino()
        rhino.start()
        self.addCleanup(rhino.stop)

    # ---- readers

    def relation_checks_of(self, run_id: str) -> dict[str, dict]:
        """Every measured relation of the run, by id, from its retained relation-check records."""

        checks: dict[str, dict] = {}
        for ref in self.repository.list_json(
            run=self.repository.load_run(run_id),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id),
        ):
            if record_kind(ref) != "seat-relation-check":
                continue
            payload = dict(self.repository.load_json(ref))
            self.assertTrue(payload.get("basis"), payload)          # the report names what it was measured on
            for check in payload["checks"]:
                self.assertNotIn(check["relation_id"], checks)
                checks[check["relation_id"]] = check
        return checks

    def state_of(self, client: TestClient, run_id: str) -> tuple[dict, dict[str, dict], dict[str, dict]]:
        """``GET /api/state?run=`` and its elements and parameters by id."""

        response = client.get("/api/state", params={"run": run_id})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        return (
            payload,
            {row["elementId"]: row for row in payload["elements"]},
            {row["key"]: row for row in payload["parameters"]},
        )

    def bound_candidate(self, client: TestClient, candidate_id: str) -> tuple[dict, dict, dict, dict]:
        """The candidate, its two artifacts and their one receipt, with every identity they share checked."""

        kinds = self.records_of(candidate_id)
        self.assertEqual(kinds.get(OCCT_RECEIPT), 1, kinds)
        self.assertNotIn(RHINO_RECEIPT, kinds)
        self.assertEqual(kinds.get(INSPECTION), 1, kinds)
        candidate = self.candidate(client, candidate_id)
        self.assertEqual(candidate["status"], "succeeded")
        self.assertTrue(candidate["seatExecutionComplete"], candidate["seatResults"])
        (seat,) = candidate["seatResults"]
        self.assertEqual(seat["status"], "proposal_accepted")
        self.assertEqual(candidate["base"]["version"], 0)
        exact, preview = self.split(candidate["artifacts"])
        self.assertEqual(exact["receiptRef"], preview["receiptRef"])
        self.assertIn(f"/runs/{candidate_id}/records/{OCCT_RECEIPT}-", exact["receiptRef"])
        self.assertEqual((exact["format"], preview["format"]), ("step", "3dm"))
        for row in (exact, preview):
            self.assertIs(row["available"], True, row)
            self.assertIs(row["readbackVerified"], True, row)
            self.assertEqual(row["status"], "succeeded")
            self.assertEqual(row["runId"], candidate_id)
            self.assertEqual(row["programDigest"], seat["programDigest"])
            self.assertEqual(row["designStateDigest"], candidate["stateDigest"])
            self.assertEqual(row["base"]["version"], 0)
            self.assertEqual(row["objectCount"], len(ASSEMBLY_OBJECTS))
            self.assertEqual(row["lengthUnit"], "meter")
        self.assertNotEqual(exact["sha256"], preview["sha256"])
        receipt = self.load_kind(candidate_id, OCCT_RECEIPT)
        run = self.repository.load_run(candidate_id)
        binding = receipt["identity"]["binding"]
        self.assertEqual(binding["run_id"], candidate_id)
        self.assertEqual(binding["base"], {"project_id": PROJECT_ID, "version": run.base.version, "state_sha256": run.base.state_sha256})
        self.assertEqual(binding["program_digest"], seat["programDigest"])
        self.assertEqual(binding["design_state_digest"], candidate["stateDigest"])
        retained_program = load_compiled_geometry_program(
            self.repository.load_json(record_ref_from_uri(binding["program_ref"]["uri"], PROJECT_ID))
        )
        self.assertEqual(retained_program.program_digest, seat["programDigest"])
        # The seat counts every object the program produces, the wall's uncut body, its void
        # tool and the four frame rails included; the files carry only the six the booleans left.
        program_objects = {oid for op in retained_program.proposal.operations for oid in op.output_object_ids}
        self.assertEqual(seat["objects"], len(program_objects))
        self.assertEqual(len(program_objects), 2 * len(ASSEMBLY_OBJECTS), sorted(program_objects))
        self.assertTrue(set(ASSEMBLY_OBJECTS) < program_objects)
        self.assertEqual(receipt["exact_artifact"]["sha256"], exact["sha256"])
        self.assertEqual(receipt["preview_artifact"]["sha256"], preview["sha256"])
        self.assertEqual(tuple(receipt["physical_object_ids"]), tuple(sorted(ASSEMBLY_OBJECTS)))
        self.assertTrue(exact["relativePath"].startswith(f"runs/{candidate_id}/workspaces/cad-studio-candidate-seat-portico/"))
        return candidate, exact, preview, receipt

    def retained_bytes(self, artifact: dict) -> bytes:
        """The artifact's bytes straight off the project's disk, not through any route."""

        return (self.repository.layout.root / artifact["relativePath"]).read_bytes()

    # ---- the geometry, probed on the cold-read STEP solids (program frame: x, up, plan z)

    def assert_flight(self, entries: dict[str, occt_backend.StepEntry], width: float) -> None:
        """One closed stepped solid, ``width`` across the line, ten treads up to the fixed upper level."""

        flight = entries[FLIGHT_OBJECT]
        half = width / 2.0
        measure = occt_backend.measure_shape(flight.shape)
        self.assertTrue(measure.valid)
        self.assertEqual((measure.solid_count, measure.closed), (1, True))
        self.assertEqual(measure.face_count, 2 + 2 * COUNT + 2)              # floor, treads, risers, back, two sides
        self.assertAlmostEqual(measure.volume, width * sum((k + 1) * RISE * GOING for k in range(COUNT)), places=6)
        # CAD frame is (x, plan z, up): from z=0 to z=3 along the front axis, +-half across, floor to 1.8
        for actual, expected in zip(measure.bbox_min + measure.bbox_max, (-half, 0.0, 0.0, half, COUNT * GOING, UPPER_ELEVATION)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertAlmostEqual(measure.bbox_max[2], UPPER_ELEVATION, places=6)  # the last tread's physical top is the declared level
        probes: dict[tuple[float, float, float], str] = {}
        for k in range(COUNT):                                                 # inside each tread, outside just above its top
            centre = (k + 0.5) * GOING
            probes[(0.0, (k + 1) * RISE - 0.05, centre)] = "inside"
            probes[(0.0, (k + 1) * RISE + 0.05, centre)] = "outside"
        probes.update({
            (half - 0.01, 0.8, 1.35): "inside", (half + 0.01, 0.8, 1.35): "outside",     # the width, both sides
            (-half + 0.01, 0.8, 1.35): "inside", (-half - 0.01, 0.8, 1.35): "outside",
            (0.0, UPPER_ELEVATION - 0.01, 2.85): "inside", (0.0, UPPER_ELEVATION + 0.01, 2.85): "outside",
            (0.0, 0.1, -0.01): "outside", (0.0, 1.7, COUNT * GOING + 0.01): "outside",  # nothing beyond either end
        })
        for point, expected in probes.items():
            self.assertEqual(occt_backend.classify_program_point(flight.shape, point), expected, point)

    def assert_landing(self, entries: dict[str, occt_backend.StepEntry]) -> None:
        """The landing's top is the upper level and its near edge is the flight's far edge; the two do not overlap."""

        landing, flight = entries[LANDING_OBJECT], entries[FLIGHT_OBJECT]
        measure = occt_backend.measure_shape(landing.shape)
        self.assertTrue(measure.valid and measure.closed and measure.solid_count == 1)
        self.assertAlmostEqual(measure.volume, 1.5 * 1.2 * LANDING_THICKNESS, places=6)
        end = COUNT * GOING
        for actual, expected in zip(measure.bbox_min + measure.bbox_max,
                                    (-0.75, end, UPPER_ELEVATION - LANDING_THICKNESS, 0.75, end + 1.2, UPPER_ELEVATION)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertAlmostEqual(measure.bbox_max[2], occt_backend.measure_shape(flight.shape).bbox_max[2], places=6)
        self.assertAlmostEqual(measure.bbox_min[1], occt_backend.measure_shape(flight.shape).bbox_max[1], places=6)
        for point, expected in {
            (0.0, 1.7, end + 0.6): "inside", (0.0, UPPER_ELEVATION + 0.05, end + 0.6): "outside",
            (0.0, UPPER_ELEVATION - LANDING_THICKNESS - 0.05, end + 0.6): "outside",
            (0.0, 1.7, end - 0.05): "outside", (0.0, 1.7, end + 0.05): "inside",
        }.items():
            self.assertEqual(occt_backend.classify_program_point(landing.shape, point), expected, point)
        # either side of the shared edge: the flight, then the landing, never both
        self.assertEqual(occt_backend.classify_program_point(flight.shape, (0.0, 1.7, end - 0.05)), "inside")
        self.assertEqual(occt_backend.classify_program_point(flight.shape, (0.0, 1.7, end + 0.05)), "outside")

    def assert_window(self, entries: dict[str, occt_backend.StepEntry], width: float) -> None:
        """The wall's hole is ``width`` wide along the wall, and the pane in it is ``width`` less two frame widths."""

        cut = occt_backend.measure_shape(entries[WALL_CUT].shape)
        self.assertTrue(cut.valid and cut.closed and cut.solid_count == 1)
        # The wall body lies on the +x side of its line (normal = (dz, -dx) of a +z line): check that before any position.
        self.assertAlmostEqual(cut.bbox_min[0], WALL_X, places=6, msg="the wall's thickness is not on the +x side of its line")
        self.assertAlmostEqual(cut.bbox_max[0], WALL_X + WALL_THICKNESS, places=6)
        for actual, expected in zip(cut.bbox_min + cut.bbox_max, (WALL_X, 0.0, 0.0, WALL_X + WALL_THICKNESS, WALL_ALONG, WALL_HEIGHT)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(cut.face_count, 10)                                   # six faces and four reveals
        self.assertAlmostEqual(cut.volume, WALL_ALONG * WALL_THICKNESS * WALL_HEIGHT - width * WALL_THICKNESS * (HEAD - SILL), places=6)
        z0, z1 = WINDOW_ALONG - width / 2.0, WINDOW_ALONG + width / 2.0
        mid_x = WALL_X + WALL_THICKNESS / 2.0
        for point, expected in {
            (mid_x, 1.65, WINDOW_ALONG): "outside",                             # through the hole
            (mid_x, 1.65, z0 - 0.01): "inside", (mid_x, 1.65, z0 + 0.01): "outside",   # the hole's two jambs, exactly
            (mid_x, 1.65, z1 + 0.01): "inside", (mid_x, 1.65, z1 - 0.01): "outside",
            (mid_x, SILL - 0.05, WINDOW_ALONG): "inside", (mid_x, HEAD + 0.05, WINDOW_ALONG): "inside",
            (mid_x, 1.65, 0.5): "inside",
        }.items():
            self.assertEqual(occt_backend.classify_program_point(entries[WALL_CUT].shape, point), expected, point)

        aperture = occt_backend.measure_shape(entries[APERTURE].shape)
        self.assertTrue(aperture.valid and aperture.closed and aperture.solid_count == 1)
        self.assertAlmostEqual(aperture.volume, width * WALL_THICKNESS * (HEAD - SILL), places=6)
        for actual, expected in zip(aperture.bbox_min + aperture.bbox_max, (WALL_X, z0, SILL, WALL_X + WALL_THICKNESS, z1, HEAD)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertAlmostEqual(aperture.bbox_max[1] - aperture.bbox_min[1], width, places=6)

        frame = occt_backend.measure_shape(entries[FRAME].shape)
        self.assertTrue(frame.valid and frame.closed and frame.solid_count == 1)
        self.assertEqual(frame.face_count, 10)
        net = width - 2 * FRAME_WIDTH
        self.assertAlmostEqual(frame.volume, (width * (HEAD - SILL) - net * (HEAD - SILL - 2 * FRAME_WIDTH)) * FRAME_DEPTH, places=6)
        fx0, fx1 = WALL_X - FRAME_PROJECTION, WALL_X - FRAME_PROJECTION + FRAME_DEPTH
        for actual, expected in zip(frame.bbox_min + frame.bbox_max, (fx0, z0, SILL, fx1, z1, HEAD)):
            self.assertAlmostEqual(actual, expected, places=5)
        frame_x = (fx0 + fx1) / 2.0
        for point, expected in {
            (frame_x, 1.65, WINDOW_ALONG): "outside",                           # the hole through the frame
            (frame_x, SILL + FRAME_WIDTH / 2.0, WINDOW_ALONG): "inside",         # bottom rail
            (frame_x, HEAD - FRAME_WIDTH / 2.0, WINDOW_ALONG): "inside",         # top rail
            (frame_x, 1.65, z0 + FRAME_WIDTH / 2.0): "inside", (frame_x, 1.65, z1 - FRAME_WIDTH / 2.0): "inside",  # stiles
            (frame_x, 1.65, z0 + FRAME_WIDTH + 0.01): "outside",
        }.items():
            self.assertEqual(occt_backend.classify_program_point(entries[FRAME].shape, point), expected, point)

        pane = occt_backend.measure_shape(entries[PANE].shape)
        self.assertTrue(pane.valid and pane.closed and pane.solid_count == 1)
        self.assertEqual(pane.face_count, 6)
        self.assertAlmostEqual(pane.bbox_max[1] - pane.bbox_min[1], net, places=6)   # the glass net width
        self.assertAlmostEqual(pane.volume, net * (HEAD - SILL - 2 * FRAME_WIDTH) * GLASS_THICKNESS, places=6)
        px0, px1 = WALL_X + GLASS_OFFSET, WALL_X + GLASS_OFFSET + GLASS_THICKNESS
        for actual, expected in zip(pane.bbox_min + pane.bbox_max, (px0, z0 + FRAME_WIDTH, SILL + FRAME_WIDTH, px1, z1 - FRAME_WIDTH, HEAD - FRAME_WIDTH)):
            self.assertAlmostEqual(actual, expected, places=5)
        glass_point = ((px0 + px1) / 2.0, 1.65, WINDOW_ALONG)
        self.assertEqual(occt_backend.classify_program_point(entries[PANE].shape, glass_point), "inside")
        self.assertEqual(occt_backend.classify_program_point(entries[FRAME].shape, glass_point), "outside")
        self.assertEqual(occt_backend.classify_program_point(entries[WALL_CUT].shape, glass_point), "outside")
        self.assertEqual(occt_backend.classify_program_point(entries[PANE].shape, (frame_x, 1.65, WINDOW_ALONG)), "outside")

    def assert_relations(self, run_id: str, entries: dict[str, occt_backend.StepEntry]) -> None:
        """The declared clearance and the producers' own support relations, measured, against the STEP solids."""

        checks = self.relation_checks_of(run_id)
        self.assertEqual(set(checks), {CLEARANCE, INTERFACE, f"{LANDING_ID}-stands-on", f"{STAIR_ID}-stands-on"})
        # The zones' interface binds no validator: it is reported, not measured, and not called held.
        interface = checks.pop(INTERFACE)
        self.assertEqual((interface["kind"], interface["check_kind"], interface["status"]), ("interface", "none", "unchecked"))
        self.assertEqual(interface["detail"], "no validator bound")
        self.assertEqual({check["status"] for check in checks.values()}, {"held"}, checks)
        clearance = checks[CLEARANCE]
        self.assertEqual((clearance["kind"], clearance["check_kind"]), ("clearance", "clearance_interval"))
        measured = clearance["measured"]
        self.assertEqual((measured["interval_low"], measured["interval_high"]), CLEARANCE_INTERVAL)
        # Compiled-predicted bounds are what the run measured; the gap they
        # report is the same one the cold-read solids show between the flight
        # and the nearest of the wall's own objects (the frame's projection).
        wall_objects = (WALL_CUT, APERTURE, FRAME, PANE)
        nearest = min(occt_backend.measure_shape(entries[name].shape).bbox_min[0] for name in wall_objects)
        flight_edge = occt_backend.measure_shape(entries[FLIGHT_OBJECT].shape).bbox_max[0]
        self.assertAlmostEqual(measured["gap"], nearest - flight_edge, places=5)
        self.assertTrue(CLEARANCE_INTERVAL[0] <= measured["gap"] <= CLEARANCE_INTERVAL[1], measured)
        # The landing stands on the fixed upper level with its declared embed;
        # nothing here says the flight bears it.
        landing = checks[f"{LANDING_ID}-stands-on"]
        self.assertEqual((landing["kind"], landing["check_kind"]), ("support", "support_contact"))
        self.assertAlmostEqual(landing["measured"]["engagement_depth"], LANDING_THICKNESS, places=9)
        self.assertAlmostEqual(landing["measured"]["level_elevation"], UPPER_ELEVATION, places=9)
        self.assertAlmostEqual(landing["measured"]["element_bottom"], UPPER_ELEVATION - LANDING_THICKNESS, places=6)
        stair = checks[f"{STAIR_ID}-stands-on"]
        self.assertEqual((stair["check_kind"], stair["measured"]["level_elevation"]), ("support_contact", 0.0))

    def assert_preview(self, data: bytes, candidate: dict, seat: dict, receipt: dict, label: str) -> None:
        """The preview: meshes named as the STEP solids, the run's identity as document text, the glass a native transparent material."""

        path = self.root / f"{label}.preview.3dm"
        path.write_bytes(data)
        inspection = inspect_three_dm(path)
        self.assertEqual({row["name"] for row in inspection.named_object_bboxes}, set(ASSEMBLY_OBJECTS))
        self.assertEqual({row["type"] for row in inspection.named_object_bboxes}, {"Mesh"})
        document = {row["key"]: row["value"] for row in inspection.document_user_strings}
        self.assertEqual(document["archflow:run_id"], candidate["candidateId"])
        self.assertEqual(document["archflow:program_digest"], seat["programDigest"])
        self.assertEqual(document["archflow:design_state_digest"], candidate["stateDigest"])
        self.assertEqual(document["archflow:export_path"], "occt")
        # provenance on the objects themselves: each mesh names the operation that produced it
        strings = {row["name"]: {p["key"]: p["value"] for p in row["attributes"]} for row in inspection.object_user_strings}
        self.assertEqual(strings[PANE]["archflow:producer_op"], "glazing-wall-east-window")
        self.assertEqual(strings[FRAME]["archflow:producer_op"], "frame-wall-east-window")
        self.assertEqual(strings[FLIGHT_OBJECT]["archflow:producer_op"], STAIR_ID)
        # the native material table as the inspector reads it: the glass is transparent, the frame is not, the rest wear their layer
        materials = {row["name"]: row for row in inspection.materials}
        self.assertEqual(sorted(materials), ["frame", "glazing"])
        self.assertGreater(materials["glazing"]["transparency"], 0.0)
        self.assertLess(materials["glazing"]["transparency"], 1.0)
        self.assertEqual(materials["frame"]["transparency"], 0.0)
        self.assertEqual(
            {name: {p["key"]: p["value"] for p in row["user_strings"]}.get("archflow:material_id") for name, row in materials.items()},
            {"frame": "frame", "glazing": "glazing"},
        )
        bindings = {row["name"]: (row["material_source"], row["material_index"]) for row in inspection.object_material_bindings}
        self.assertEqual(bindings[PANE], ("MaterialFromObject", materials["glazing"]["index"]))
        self.assertEqual(bindings[FRAME], ("MaterialFromObject", materials["frame"]["index"]))
        for other in (FLIGHT_OBJECT, LANDING_OBJECT, WALL_CUT, APERTURE):
            self.assertEqual(bindings[other], ("MaterialFromLayer", -1), other)
        declared = receipt["preview_artifact"]["materials"]
        self.assertEqual(declared[PANE]["transparency"], materials["glazing"]["transparency"])
        self.assertEqual(declared[PANE]["diffuse"], materials["glazing"]["diffuse_color_rgba"][:3])
        self.assertEqual(declared[FRAME]["transparency"], 0.0)
        self.assertEqual(declared[FRAME]["diffuse"], materials["frame"]["diffuse_color_rgba"][:3])
        by_name = {row["name"]: row for row in inspection.object_material_bindings}
        self.assertEqual((by_name[PANE]["material_source"], by_name[PANE]["archflow_material_id"]), ("MaterialFromObject", "glazing"))
        self.assertEqual(by_name[PANE]["material_transparency"], declared[PANE]["transparency"])
        self.assertEqual(by_name[FRAME]["material_transparency"], 0.0)

    def assert_assembly(self, client: TestClient, candidate_id: str, *, stair_width: float, window_width: float, label: str) -> tuple[dict, dict, dict, bytes, bytes]:
        candidate, exact, preview, receipt = self.bound_candidate(client, candidate_id)
        (seat,) = candidate["seatResults"]
        # Three measured and held, and the one validator-less interface reported unchecked:
        # the run is not fully checked, and it does not say it is.
        summary = candidate["relationChecks"]
        self.assertEqual((summary["held"], summary["violated"], summary["unchecked"]), (3, 0, 1), summary)
        self.assertIs(summary["fullyChecked"], False)
        exact_bytes = self.bytes_of(client, exact)
        preview_bytes = self.bytes_of(client, preview)
        self.assertEqual(exact_bytes, self.retained_bytes(exact))
        self.assertEqual(preview_bytes, self.retained_bytes(preview))
        entries = self.step_entries(exact_bytes)
        self.assertEqual(set(entries), set(ASSEMBLY_OBJECTS))
        self.assert_flight(entries, stair_width)
        self.assert_landing(entries)
        self.assert_window(entries, window_width)
        self.assert_relations(candidate_id, entries)
        self.assert_preview(preview_bytes, candidate, seat, receipt, label)
        record = self.load_kind(candidate_id, "state-record")
        elements = {e["entity_id"]: e["fields"] for e in record["entities"] if e.get("schema") == "Element@1"}
        self.assertEqual(elements[STAIR_ID]["params"]["width"], stair_width)
        self.assertEqual(elements[WALL_ID]["params"]["openings"][0]["width"], "@window_width")   # still bound, never inlined
        self.assertEqual({p["key"]: p["value"] for p in record["parameters"]}, {"window_width": window_width})
        return candidate, exact, preview, exact_bytes, preview_bytes

    # ---- the test

    def test_a_whole_stair_landing_and_window_survive_a_restart_and_continue_from_a_to_b(self) -> None:
        before_head = self.repository.read_head()
        authored = {
            path: self.repository.layout.resolve_relative(path).read_bytes()
            for path in (RUNNER_RECORD_PATH, RUNNER_SEATS_PATH)
        }

        # A: the flight widened, through the grammar, against the authored WIP.
        with no_process():
            accepted_a, job_a = self.run_candidate(self.client, "set width to 1.4", elementId=STAIR_ID)
        self.assertEqual(job_a["status"], "succeeded", job_a)
        self.assertEqual(job_a["lane"], "parallel")
        first = accepted_a["candidateId"]
        candidate_a, exact_a, preview_a, exact_a_bytes, preview_a_bytes = self.assert_assembly(
            self.client, first, stair_width=1.4, window_width=1.2, label="a"
        )
        self.assertTrue(candidate_a["changedVsProjection"])

        # Restart. Reading A back runs no CAD at all, and A is an exact base to continue from.
        with no_cad_at_all(), no_process():
            restarted = self.open_client(StudioSettings(project_dir=self.root / PROJECT_ID))
            reopened = self.candidate(restarted, first)
            self.assertEqual(reopened["status"], "succeeded")
            self.assertIsNone(reopened["jobId"])
            exact_again, preview_again = self.split(reopened["artifacts"])
            self.assertEqual((exact_again["sha256"], preview_again["sha256"]), (exact_a["sha256"], preview_a["sha256"]))
            self.assertEqual(self.bytes_of(restarted, exact_again), exact_a_bytes)
            self.assertEqual(self.bytes_of(restarted, preview_again), preview_a_bytes)
            source, elements, parameters = self.state_of(restarted, first)
            self.assertEqual(source["referenceRun"]["runId"], first)
            self.assertTrue(source["matchesReferenceReceipt"])
            self.assertEqual(source["stateDigest"], candidate_a["stateDigest"])
            self.assertEqual(elements[STAIR_ID]["numericFields"]["width"], 1.4)
            self.assertEqual(parameters["window_width"]["value"], 1.2)

        # B: the window parameter, through the same grammar, from A's retained record.
        with no_process():
            accepted_b, job_b = self.run_candidate(
                restarted, "set parameter:window_width to 1.4 m",
                stateDigest=source["stateDigest"], sourceRunId=first, elementId=WALL_ID,
            )
        self.assertEqual(job_b["status"], "succeeded", job_b)
        second = accepted_b["candidateId"]
        self.assertNotEqual(second, first)
        candidate_b, exact_b, preview_b, _, _ = self.assert_assembly(
            restarted, second, stair_width=1.4, window_width=1.4, label="b"
        )
        self.assertNotEqual(exact_b["sha256"], exact_a["sha256"])
        self.assertNotEqual(preview_b["sha256"], preview_a["sha256"])
        self.assertNotEqual(candidate_b["stateDigest"], candidate_a["stateDigest"])
        state_b, elements_b, parameters_b = self.state_of(restarted, second)
        self.assertEqual(state_b["stateDigest"], candidate_b["stateDigest"])
        self.assertEqual(elements_b[STAIR_ID]["numericFields"]["width"], 1.4)
        self.assertEqual(parameters_b["window_width"]["value"], 1.4)

        # A after B: re-fetched from the route and from disk, byte for byte what it was.
        with no_cad_at_all(), no_process():
            a_after = self.candidate(restarted, first)
            exact_after, preview_after = self.split(a_after["artifacts"])
            self.assertEqual((exact_after["sha256"], preview_after["sha256"]), (exact_a["sha256"], preview_a["sha256"]))
            for artifact, original in ((exact_after, exact_a_bytes), (preview_after, preview_a_bytes)):
                fetched = self.bytes_of(restarted, artifact)
                self.assertEqual(hashlib.sha256(fetched).hexdigest(), hashlib.sha256(original).hexdigest())
                self.assertEqual(hashlib.sha256(self.retained_bytes(artifact)).hexdigest(), hashlib.sha256(original).hexdigest())
            self.assertEqual(a_after["stateDigest"], candidate_a["stateDigest"])
            # A's geometry is still A's: the narrower window, reopened and re-read after B.
            self.assert_window(self.step_entries(self.bytes_of(restarted, exact_after)), 1.2)
            # Nothing canonical moved, nothing authored was rewritten, and the
            # project still has no reference run: both candidates are detached.
            self.assertEqual(self.repository.read_head(), before_head)
            for path, content in authored.items():
                self.assertEqual(self.repository.layout.resolve_relative(path).read_bytes(), content)
            current = restarted.get("/api/state").json()
            self.assertEqual(current["referenceRunSource"], "none")
            self.assertEqual(current["stateDigest"], self.state_digest)
            listed = restarted.get("/api/artifacts").json()["artifacts"]
            self.assertEqual(
                {row["sha256"] for row in listed if row["runId"] in (first, second)},
                {exact_a["sha256"], preview_a["sha256"], exact_b["sha256"], preview_b["sha256"]},
            )


if __name__ == "__main__":
    unittest.main()
