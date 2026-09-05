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
    PROJECT_ID,
    REFERENCE_RUN_ID,
    RUNNER_RECORD_PATH,
    RUNNER_SEATS_PATH,
    make_project,
    runner_state_digest,
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

    def step_shapes(self, data: bytes) -> dict[str, occt_backend.ShapeMeasure]:
        """The named solids of a STEP file, re-read by the real OCCT reader."""

        path = self.root / f"readback-{hashlib.sha256(data).hexdigest()[:8]}.step"
        path.write_bytes(data)
        entries = occt_backend.read_step(path, length_unit="meter")
        return {entry.name: occt_backend.measure_shape(entry.shape) for entry in entries}

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


if __name__ == "__main__":
    unittest.main()
