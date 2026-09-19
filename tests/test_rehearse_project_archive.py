"""The fresh-environment rehearsal, driven against a real restored copy.

Nothing here rehearses the rehearsal. Each test builds one real P036 project,
lets the driver export and restore it through the landed
``tools/create_project.py`` command, and then reads the restored copy with the
ordinary project readers. The runtime phase is the same: one test drives it
with a fake client whose shapes are the API's, and one binds the real project
runtime to the restored directory and asks it for a candidate, because "the
project survived the machine" is only true if the restored copy can keep
designing.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE, STUDIO_SOURCE_DOCUMENT
from archflow.project.repository import FilesystemProjectRepository

import tools.rehearse_project_archive as driver
from tools.rehearse_project_archive import (
    export_and_restore,
    main,
    rehearse,
    verify_restored,
)

REPO = Path(__file__).resolve().parents[1]
STUDIO_API = REPO / "apps/archflow-studio/api"
DOCUMENT_BYTES = b"the original registered source bytes"


def studio_support():
    """The Studio's own project fixture, loaded by path.

    ``apps/archflow-studio/api/tests`` may not import ``tools``, so the
    rehearsal's test lives here, where both sides are importable, and the
    fixture module is loaded by path the way the Hub's lifecycle test loads
    its own support file.
    """

    if str(STUDIO_API) not in sys.path:
        sys.path.insert(0, str(STUDIO_API))
    spec = importlib.util.spec_from_file_location(
        "studio_fixture_support", STUDIO_API / "tests" / "support.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_source_project(parent: Path) -> Path:
    """One retained project with a run, a board revision and a document."""

    support = studio_support()
    repository, _ = support.make_project(parent)
    project_id = support.PROJECT_ID
    documents = repository.create_run("studio-documents")
    artifact = repository.ingest(
        run=documents,
        destination=PersistenceDestination(PersistenceArea.OBJECT),
        artifact_id="registered-source",
        media_type="image/png",
        source=BytesIO(DOCUMENT_BYTES),
    )
    repository.put_json(
        run=documents,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id="studio-documents"
        ),
        record_kind=STUDIO_SOURCE_DOCUMENT,
        payload={
            "schema": "StudioSourceDocument@1",
            "project_id": project_id,
            "run_id": "studio-documents",
            "asset_sha256": artifact.sha256,
            "file_name": "reference.png",
            "mime_type": "image/png",
            "size_bytes": len(DOCUMENT_BYTES),
            "pages": [{"index": 0, "width": 2.0, "height": 2.0, "rotation": 0}],
        },
    )
    board = repository.create_run("studio-board")
    repository.put_json(
        run=board,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD, run_id="studio-board"
        ),
        record_kind=STUDIO_BOARD_SCENE,
        payload={
            "schema": "StudioBoardScene@1",
            "projectId": project_id,
            "title": "MonkeyBoard",
            "elements": [],
            "seenDocuments": [],
            "previousRevisionSha256": None,
        },
    )
    return parent / project_id


class RehearsalDriverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gh56-rehearsal-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = make_source_project(self.root / "source")
        self.archive = self.root / "archive" / "project.monkeyhub.zip"
        self.restore_parent = self.root / "restore"
        self.head = FilesystemProjectRepository.open(self.source).read_head()

    def rehearse(self, **kwargs):
        return rehearse(
            self.source,
            self.archive,
            self.restore_parent,
            python=sys.executable,
            environment="isolated folder on this machine",
            **kwargs,
        )


class RehearsalWithoutRuntimeTests(RehearsalDriverTestCase):
    def test_rehearse_without_runtime_matches_identities(self) -> None:
        report = self.rehearse(request=None)

        self.assertEqual(report.checks["project identity"], "MATCH")
        self.assertEqual(report.checks["HEAD/state digest"], "MATCH")
        self.assertEqual(report.checks["retained runs"], "MATCH")
        self.assertEqual(report.checks["Board revision"], "MATCH")
        self.assertEqual(report.checks["registered source object hashes"], "MATCH")
        self.assertEqual(report.checks["normal-reader reopen"], "PASS")
        self.assertEqual(report.checks["source project changed by export"], "NO")
        self.assertEqual(report.checks["runtime/config/chat transported"], "NO")
        self.assertTrue(
            report.checks["post-restore candidate from exact restored base"].startswith(
                "SKIPPED"
            ),
            report.checks,
        )
        # This fixture has no design branch and no accepted Stage; an absent
        # category is said to be absent and is never answered MATCH.
        self.assertEqual(
            report.checks["branch/Stage identities"], "SKIPPED (absent in source)"
        )
        self.assertTrue(report.ok, report.lines())

        lines = report.lines()
        self.assertTrue(lines[0].startswith("source build:"), lines[0])
        self.assertIn(f"archive sha256: {report.archive_sha256}", lines)
        self.assertIn(f"archive size: {report.archive_bytes}", lines)
        self.assertIn("restore environment: isolated folder on this machine", lines)
        self.assertEqual(
            [line.split(":", 1)[0] for line in lines],
            [
                "source build",
                "archive sha256",
                "archive size",
                "restore environment",
                "project identity",
                "HEAD/state digest",
                "branch/Stage identities",
                "retained runs",
                "Board revision",
                "registered source object hashes",
                "drawing/model receipt refs",
                "normal-reader reopen",
                "post-restore candidate from exact restored base",
                "runtime/config/chat transported",
                "source project changed by export",
            ],
        )
        restored = FilesystemProjectRepository.open(
            self.restore_parent / self.source.name
        )
        self.assertEqual(restored.read_head(), self.head)

    def test_rehearse_reports_mismatch_when_restored_copy_is_tampered(self) -> None:
        original = driver._restore

        def tampering_restore(*args, **kwargs):
            restored = original(*args, **kwargs)
            registered = next(
                path for path in (restored / "objects").rglob("*") if path.is_file()
            )
            registered.chmod(0o600)
            registered.write_bytes(b"not the registered original bytes")
            return restored

        with patch.object(driver, "_restore", tampering_restore):
            report = self.rehearse(request=None)

        self.assertEqual(report.checks["registered source object hashes"], "MISMATCH")
        self.assertFalse(report.ok, report.lines())


class FakeRuntime:
    """The shapes ``test_candidate.py`` shows, with nothing behind them.

    ``drop`` removes one field from the reply that carries it, ``raise_on``
    makes the named path fail the way a runtime that went away does, and
    ``parameters`` is what the restored record declares.
    """

    def __init__(self, version: int, state_sha256: str) -> None:
        self.version = version
        self.state_sha256 = state_sha256
        self.calls: list[tuple[str, str]] = []
        self.drop: str | None = None
        self.raise_on: str | None = None
        self.parameters: list[dict] = [
            {
                "key": "module",
                "value": 1.2,
                "epistemicStatus": "declared",
                "lockAuthority": None,
            }
        ]

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if path == self.raise_on:
            raise OSError("the project runtime stopped answering")
        published = {"version": self.version, "stateSha256": self.state_sha256}
        if method == "GET" and path == "/api/state":
            return {
                "projectId": "demo-project",
                "published": published,
                "stateDigest": "a" * 64,
                "componentTree": [
                    {"componentId": "building", "parentComponentId": None},
                    {"componentId": "portico", "parentComponentId": "building"},
                ],
                "parameters": list(self.parameters),
                "elements": [],
            }
        if method == "POST" and path == "/api/proposals":
            assert body["stateDigest"] == "a" * 64, body
            return {"proposalId": "studio-proposal", "status": "proposed"}
        if method == "POST" and path.endswith("/candidate"):
            accepted = {"jobId": "job-1", "candidateId": "cand-1", "status": "queued"}
            accepted.pop(self.drop, None)
            return accepted
        if method == "GET" and path == "/api/jobs/job-1":
            return {"jobId": "job-1", "status": "succeeded", "candidateId": "cand-1"}
        if method == "GET" and path == "/api/candidates/cand-1":
            return {"candidateId": "cand-1", "status": "succeeded", "base": published}
        raise AssertionError(f"unexpected request: {method} {path}")


class RehearsalRuntimeFailureTests(RehearsalDriverTestCase):
    """A runtime that fails costs one row, never the ten already established."""

    def assert_only_the_candidate_row_failed(self, report) -> None:
        self.assertEqual(
            report.checks["post-restore candidate from exact restored base"], "FAIL"
        )
        self.assertFalse(report.ok, report.lines())
        self.assertEqual(len(report.lines()), 15)
        self.assertEqual(report.checks["project identity"], "MATCH")
        self.assertEqual(report.checks["HEAD/state digest"], "MATCH")
        self.assertEqual(report.checks["retained runs"], "MATCH")
        self.assertEqual(report.checks["normal-reader reopen"], "PASS")

    def test_a_reply_missing_the_candidate_id_fails_one_row_and_still_prints(self) -> None:
        runtime = FakeRuntime(self.head.version, self.head.state_sha256)
        runtime.drop = "candidateId"

        report = self.rehearse(request=runtime)

        self.assert_only_the_candidate_row_failed(report)

    def test_a_runtime_that_disappears_fails_one_row_and_still_prints(self) -> None:
        runtime = FakeRuntime(self.head.version, self.head.state_sha256)
        runtime.raise_on = "/api/jobs/job-1"

        report = self.rehearse(request=runtime)

        self.assert_only_the_candidate_row_failed(report)

    def test_a_record_with_no_restatable_number_says_which_side_is_empty(self) -> None:
        runtime = FakeRuntime(self.head.version, self.head.state_sha256)
        runtime.parameters = []

        report = self.rehearse(request=runtime)

        self.assertEqual(
            report.checks["post-restore candidate from exact restored base"],
            "SKIPPED (no restatable parameter)",
        )
        self.assertTrue(report.ok, report.lines())


class RehearsalRuntimePhaseTests(RehearsalDriverTestCase):
    def test_rehearse_runtime_phase_with_fake_client(self) -> None:
        runtime = FakeRuntime(self.head.version, self.head.state_sha256)

        report = self.rehearse(request=runtime)

        self.assertEqual(
            report.checks["post-restore candidate from exact restored base"], "PASS"
        )
        self.assertEqual(report.checks["HEAD/state digest"], "MATCH")
        self.assertTrue(report.ok, report.lines())
        self.assertIn(("GET", "/api/candidates/cand-1"), runtime.calls)

    def test_rehearse_runtime_phase_against_the_project_runtime(self) -> None:
        if str(STUDIO_API) not in sys.path:
            sys.path.insert(0, str(STUDIO_API))
        from fastapi.testclient import TestClient

        from archflow_studio_api.main import create_app
        from archflow_studio_api.settings import StudioSettings

        evidence = export_and_restore(
            self.source,
            self.archive,
            self.restore_parent,
            python=sys.executable,
            environment="isolated folder on this machine",
        )
        restored_dir = Path(evidence["restored_dir"])
        client = TestClient(
            create_app(StudioSettings(project_dir=restored_dir, cad_export="off"))
        )
        self.addCleanup(client.close)

        def request(method: str, path: str, body=None):
            response = client.request(method, path, json=body)
            if response.status_code >= 400:
                raise AssertionError(f"{method} {path}: {response.text}")
            return response.json()

        report = verify_restored(evidence, request=request)

        self.assertEqual(
            report.checks["post-restore candidate from exact restored base"],
            "PASS",
            report.lines(),
        )
        self.assertEqual(report.checks["HEAD/state digest"], "MATCH")
        self.assertTrue(report.ok, report.lines())
        # The continuation is a candidate, not an acceptance: HEAD stands.
        restored = FilesystemProjectRepository.open(restored_dir)
        self.assertEqual(restored.read_head(), self.head)


class RehearsalCommandLineTests(RehearsalDriverTestCase):
    def test_two_phases_hand_the_evidence_file_over_and_print_the_block(self) -> None:
        arguments = [
            "--source", str(self.source),
            "--archive", str(self.archive),
            "--restore-parent", str(self.restore_parent),
            "--python", sys.executable,
            "--environment-label", "isolated folder on this machine",
        ]
        first = io.StringIO()
        with contextlib.redirect_stdout(first), contextlib.redirect_stderr(io.StringIO()):
            code = main([*arguments, "--phase", "export-restore"])
        self.assertEqual(code, 0)
        # Only the summary block is ever printed to stdout, so what an
        # operator pastes back carries no local path.
        self.assertEqual(first.getvalue(), "")

        evidence_path = (
            self.restore_parent / f"{self.source.name}.rehearsal-evidence.json"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["project_id"], self.source.name)
        self.assertNotIn("contents", json.dumps(evidence))

        second = io.StringIO()
        with contextlib.redirect_stdout(second), contextlib.redirect_stderr(io.StringIO()):
            code = main([*arguments, "--phase", "verify"])
        self.assertEqual(code, 0)
        printed = second.getvalue().splitlines()
        self.assertTrue(printed[0].startswith("source build:"), printed)
        self.assertIn("project identity: MATCH", printed)
        self.assertIn("source project changed by export: NO", printed)
        self.assertEqual(len(printed), 15)

    def test_only_the_exporting_phases_ask_for_a_source(self) -> None:
        """Verify reads the archive and the evidence file, so it needs no source."""

        located = [
            "--archive", str(self.archive),
            "--restore-parent", str(self.restore_parent),
            "--python", sys.executable,
        ]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main([*located, "--source", str(self.source), "--phase", "export-restore"])
        self.assertEqual(code, 0)

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            code = main([*located, "--phase", "verify"])
        self.assertEqual(code, 0, printed.getvalue())
        self.assertIn("project identity: MATCH", printed.getvalue().splitlines())

        for phase in ("all", "export-restore"):
            refused = io.StringIO()
            with self.assertRaises(SystemExit) as raised,                     contextlib.redirect_stdout(io.StringIO()),                     contextlib.redirect_stderr(refused):
                main([*located, "--phase", phase])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--source is required", refused.getvalue())

    def test_the_verify_phase_is_named_by_the_archive_not_the_source_folder(self) -> None:
        """The project id comes from the archive; a folder is only a location."""

        arguments = [
            "--archive", str(self.archive),
            "--restore-parent", str(self.restore_parent),
            "--python", sys.executable,
        ]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main([*arguments, "--source", str(self.source), "--phase", "export-restore"])
        self.assertEqual(code, 0)
        self.assertTrue(
            (self.restore_parent / f"{self.source.name}.rehearsal-evidence.json").is_file()
        )

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            code = main([
                *arguments,
                "--source", str(self.root / "moved-somewhere-else"),
                "--phase", "verify",
            ])

        self.assertEqual(code, 0, printed.getvalue())
        self.assertIn("project identity: MATCH", printed.getvalue().splitlines())


if __name__ == "__main__":
    unittest.main()
