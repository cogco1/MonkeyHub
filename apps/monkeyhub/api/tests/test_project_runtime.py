"""Hub HTTP admission and recovery against real isolated Studio/P036 workers."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_monkeyhub_lifecycle import LocalHubCase, ROOT, project_fixture, wait_for
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.binding import ProjectBinding
from project_runtime.settings import StudioSettings
from monkeyhub_api.runtime import manager as runtime_module, worker_http
from monkeyhub_api.models import ChatSummary, HubError, HubFailure
from monkeyhub_api.runtime.manager import ProjectRuntime, ProjectRuntimeManager, _WorkCopyObservation
from monkeyhub_api.runtime.operations import OperationManager
from monkeyhub_api.runtime.worker_http import HttpResult
from monkeyhub_api.runtime.workers import WorkerSnapshot


class ProjectRuntimeHttpTests(LocalHubCase):
    def setUp(self):
        super().setUp()
        self.fixture = project_fixture()
        self.repository, _ = self.fixture.make_project(self.root / "projects")
        self.project_id = self.fixture.PROJECT_ID
        self.project = self.root / "projects" / self.project_id

    def make_parallel_project(self):
        project_id = "parallel-project"
        root = self.root / "projects" / project_id
        payload = deepcopy(self.fixture.RECORD_PAYLOAD)
        payload["project_id"] = project_id
        # Reuse the real fixture's authored inputs and retained receipt writer,
        # selecting only this disposable project's independent identity.
        with patch.object(self.fixture, "PROJECT_ID", project_id):
            repository = FilesystemProjectRepository.initialize(root, project_id=project_id,
                initial_state={"project_id": project_id, "version": 0})
            run = repository.create_run(self.fixture.REFERENCE_RUN_ID)
            self.fixture.write_runner_record(repository, payload)
            self.fixture.write_runner_seats(repository)
            digest = self.fixture.runner_state_digest(repository, run.run_id, payload)
            self.fixture.retain_runner_receipt(repository, run, design_state_digest=digest, record_payload=payload)
        return project_id, root

    def open_project(self, client, project_id=None, project=None):
        project_id, project = project_id or self.project_id, project or self.project
        response = client.post("/api/runtime/projects/open", json={"projectId": project_id, "projectDir": str(project)})
        self.assertEqual(response.status_code, 200, response.text)
        runtime = response.json()
        started = client.post("/api/apps/monkeyarch/start", params={"projectDir": str(project)})
        self.assertEqual(started.status_code, 202, started.text)
        self.wait_state(client, "monkeyarch", "running", project_dir=project)
        self.wait_runtime(client, runtime["runtimeId"], lambda row: row["projection"] == "ready")
        return runtime["runtimeId"]

    def read_runtime(self, client, runtime_id):
        response = client.get(f"/api/runtime/projects/{runtime_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_runtime(self, client, runtime_id, predicate):
        def check():
            row = self.read_runtime(client, runtime_id)
            return row if predicate(row) else None
        return wait_for(check, "Project runtime did not reach the expected state", timeout=40)

    def proxy(self, client, runtime_id, path, method="GET", *, operation_id=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if operation_id:
            headers["Idempotency-Key"] = operation_id
        return client.request(method, f"/api/runtime/projects/{runtime_id}/studio{path}", headers=headers, **kwargs)

    def propose(self, client, runtime_id, *, project_id=None, stage=None):
        source = stage["candidateId"] if stage else self.fixture.REFERENCE_RUN_ID
        params = {"run": source}
        if stage:
            params["sourceStageRef"] = stage["stageRef"]
        state = self.proxy(client, runtime_id, "/api/state", params=params)
        self.assertEqual(state.status_code, 200, state.text)
        body = {"projectId": project_id or self.project_id, "stateDigest": state.json()["stateDigest"],
                "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2",
                "sourceRunId": source}
        if stage:
            body["sourceStageRef"] = stage["stageRef"]
        response = self.proxy(client, runtime_id, "/api/proposals", "POST", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait_job(self, client, runtime_id, job_id):
        def check():
            response = self.proxy(client, runtime_id, f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()
            return row if row["status"] in {"succeeded", "failed"} else None
        row = wait_for(check, "Candidate job did not finish", timeout=120)
        self.assertEqual(row["status"], "succeeded", row)
        return row

    @staticmethod
    def project_bytes(project):
        return {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}

    @staticmethod
    def png_bytes(color, size=(40, 20), *, compress_level=6):
        from io import BytesIO
        from PIL import Image

        output = BytesIO()
        Image.new("RGB", size, color=color).save(output, format="PNG", compress_level=compress_level)
        return output.getvalue()

    def upload_document(self, client, runtime_id, data, file_name, mime_type, *, replaces=None):
        body = {"projectId": self.project_id, "fileName": file_name, "mimeType": mime_type,
                "contentBase64": base64.b64encode(data).decode("ascii")}
        if replaces is not None:
            body["runId"] = None
            body["replacesPages"] = replaces
        uploaded = self.proxy(client, runtime_id, "/api/documents", "POST", json=body)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        return uploaded.json()

    def upload_image(self, client, runtime_id, data, *, replaces=None):
        pages = None if replaces is None else [{"runId": replaces["runId"], "assetSha256": replaces["assetSha256"],
                                                "revisionRef": replaces["revisionRef"], "pageIndex": 0, "newPageIndex": 0}]
        return self.upload_document(client, runtime_id, data, "live-plan.png", "image/png", replaces=pages)

    @staticmethod
    def pdf_bytes(*sizes, title="plan"):
        """A PDF with the given page sizes; ``title`` changes the bytes, not the pages."""

        from io import BytesIO
        from pypdf import PdfWriter

        writer = PdfWriter()
        for width, height in sizes:
            writer.add_blank_page(width=width, height=height)
        writer.add_metadata({"/Title": title})
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    def open_work_copy(self, client, runtime_id, document):
        """Ask the Studio for this page's editable file, the way the Board does."""

        response = self.proxy(client, runtime_id, f"/api/documents/{document['assetSha256']}/work-copy", "POST",
                              json={"projectId": self.project_id, "runId": document["runId"],
                                    "revisionRef": document["revisionRef"]})
        self.assertEqual(response.status_code, 201, response.text)
        copy = response.json()
        return copy, self.project / Path(*copy["relativePath"].split("/"))

    def wait_document_replacement(self, client, runtime_id, old_asset):
        def read():
            response = self.proxy(client, runtime_id, "/api/documents")
            self.assertEqual(response.status_code, 200, response.text)
            for document in response.json()["documents"]:
                if any(page["assetSha256"] == old_asset for page in document["replacesPages"]):
                    return document
            return None
        return wait_for(read, "The edited work copy did not become a registered replacement", timeout=20)

    def wait_runtime_error(self, client, runtime_id, code):
        return self.wait_runtime(client, runtime_id,
                                 lambda row: (row["error"] or {}).get("code") == code)

    def test_work_copy_edit_registers_a_stable_replacement_without_moving_head(self):
        first = self.png_bytes("white")
        second = self.png_bytes("black")
        third = self.png_bytes("gray")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            # Registering a document materialises nothing. The copy exists only
            # because it was explicitly asked for.
            self.assertEqual(manager.bind_work_copies(runtime), {})
            copy, work = self.open_work_copy(client, runtime_id, original)
            self.assertEqual(work.read_bytes(), first)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            head = FilesystemProjectRepository.open(self.project).read_head()

            # A producer may save through a partial write. The observer waits
            # for the same size/mtime twice and re-stats after reading, so only
            # complete bytes are ever offered to the document owner.
            split = len(second) // 2
            with work.open("wb") as stream:
                stream.write(second[:split])
                stream.flush(); os.fsync(stream.fileno())
                time.sleep(0.1)
                stream.write(second[split:])
                stream.flush(); os.fsync(stream.fileno())
            replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            self.assertNotEqual(replacement["assetSha256"], original["assetSha256"])
            self.assertIsNone(replacement["revisionRef"])
            self.assertIsNone(replacement["modelSource"])
            self.assertIsNone(replacement["sourceStageRef"])
            self.assertEqual(FilesystemProjectRepository.open(self.project).read_head(), head)
            old_bytes = self.proxy(client, runtime_id, f"/api/documents/{original['assetSha256']}/bytes",
                                   params={"runId": original["runId"]})
            new_bytes = self.proxy(client, runtime_id, f"/api/documents/{replacement['assetSha256']}/bytes",
                                   params={"runId": replacement["runId"]})
            self.assertEqual(old_bytes.status_code, 200, old_bytes.text)
            self.assertEqual(new_bytes.status_code, 200, new_bytes.text)
            self.assertEqual(old_bytes.content, first)
            self.assertEqual(new_bytes.content, second)
            self.assertTrue(any(row["kind"] == "artifact/updated" and row["runtimeId"] == runtime_id
                                for row in manager.events.replay()))
            self.assertIsNone(self.read_runtime(client, runtime_id)["error"])

            # Bytes the document owner refuses are reported, never registered,
            # and never delay the project's own retained projection.
            count = len(self.proxy(client, runtime_id, "/api/documents").json()["documents"])
            work.write_bytes(b"not a png")
            row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_DOCUMENT_INVALID")
            self.assertEqual(row["projection"], "ready")
            self.assertEqual(len(self.proxy(client, runtime_id, "/api/documents").json()["documents"]), count)

            # A later real edit still lands, and clears the refusal with it.
            work.write_bytes(third)
            latest = self.wait_document_replacement(client, runtime_id, replacement["assetSha256"])
            self.assertNotEqual(latest["assetSha256"], replacement["assetSha256"])
            self.wait_runtime(client, runtime_id, lambda row: row["error"] is None)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            self.assertEqual(FilesystemProjectRepository.open(self.project).read_head(), head)

    def test_work_copy_edit_made_while_hub_is_down_is_registered_after_restart(self):
        first = self.png_bytes("white")
        second = self.png_bytes("navy")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            self.assertEqual(work.read_bytes(), first)
            head = FilesystemProjectRepository.open(self.project).read_head()
        # The work copy is a project file and outlives the Hub process. Editing
        # it before the next runtime attaches must not be mistaken for a baseline.
        work.write_bytes(second)
        with self.hub() as client:
            reopened = self.open_project(client)
            self.assertEqual(reopened, runtime_id)
            replacement = self.wait_document_replacement(client, reopened, original["assetSha256"])
            manager = client.app.state.runtimes
            self.assertEqual(list(manager.bind_work_copies(manager.get(reopened)).values()), [str(work)])
            self.assertEqual(FilesystemProjectRepository.open(self.project).read_head(), head)
            served = self.proxy(client, reopened, f"/api/documents/{replacement['assetSha256']}/bytes",
                                params={"runId": replacement["runId"]})
            self.assertEqual(served.status_code, 200, served.text)
            self.assertEqual(served.content, second)

    def test_atomic_script_save_coalesces_and_ignores_timestamp_only_save(self):
        first = self.png_bytes("white", compress_level=0)
        second = self.png_bytes("black", compress_level=0)
        self.assertEqual(len(first), len(second))
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            wait_for(lambda: any(row.observed_sha256 == original["assetSha256"]
                                 for row in runtime.work_copies.values()),
                     "The original work copy was not observed", timeout=20)
            updates = lambda: [event for event in manager.events.replay()
                               if event["kind"] == "artifact/updated" and event["runtimeId"] == runtime_id]
            before = len(updates())
            # A real external producer emits five atomic saves with the same
            # final content, preserving both the original size and timestamp.
            script = """import base64, os, sys
from pathlib import Path
target = Path(sys.argv[1])
data = base64.b64decode(sys.argv[2])
stamp = target.stat()
for _ in range(5):
    temporary = target.with_suffix('.saving')
    temporary.write_bytes(data)
    os.utime(temporary, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    os.replace(temporary, target)
"""
            subprocess.run([sys.executable, "-c", script, str(work), base64.b64encode(second).decode()],
                           check=True, capture_output=True, timeout=15)
            replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            wait_for(lambda: len(updates()) == before + 1, "The registered edit did not emit one update", timeout=5)
            observed = next(iter(runtime.work_copies.values()))
            hashed_at = observed.hashed_at_ns
            stamp = work.stat()
            os.utime(work, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
            # Arbitrary adjacent files never acquire a document binding.
            (work.parent / "unbound.png").write_bytes(self.png_bytes("red"))
            wait_for(lambda: observed.hashed_at_ns is not None and observed.hashed_at_ns != hashed_at,
                     "The timestamp-only save was not inspected", timeout=10)
            self.assertEqual(len(updates()), before + 1)
            documents = self.proxy(client, runtime_id, "/api/documents").json()["documents"]
            self.assertEqual(len(documents), 2)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            served = self.proxy(client, runtime_id, f"/api/documents/{replacement['assetSha256']}/bytes",
                                params={"runId": replacement["runId"]})
            self.assertEqual(served.content, second)

    def test_lost_work_copy_reply_reconciles_exact_retained_revision_without_replay(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, self.png_bytes("white"))
            _, work = self.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            forward = manager.forward
            writes = []
            def lose_reply(runtime, path, method, body, headers):
                result = forward(runtime, path, method, body, headers)
                if path == "/api/documents" and method == "POST":
                    writes.append(body)
                    raise HubFailure(503, "OPERATION_INTERRUPTED", "The registration response was lost.")
                return result
            before = sum(event["kind"] == "artifact/updated" and event["runtimeId"] == runtime_id
                         for event in manager.events.replay())
            with patch.object(manager, "forward", lose_reply):
                work.write_bytes(self.png_bytes("black"))
                replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])
                wait_for(lambda: any(row.copy.head_asset_sha256 == replacement["assetSha256"]
                                     and row.failure is None for row in runtime.work_copies.values()),
                         "The retained registration did not clear its lost-response error", timeout=10)
                self.assertEqual(len(writes), 1)
                self.assertIsNone(self.read_runtime(client, runtime_id)["error"])
                updates = sum(event["kind"] == "artifact/updated" and event["runtimeId"] == runtime_id
                              for event in manager.events.replay())
                self.assertEqual(updates, before + 1)
                manager.bind_work_copies(runtime)
                self.assertEqual(sum(event["kind"] == "artifact/updated" and event["runtimeId"] == runtime_id
                                     for event in manager.events.replay()), updates)

    def test_unregistered_work_copy_lost_reply_keeps_its_error_without_replay(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, self.png_bytes("white"))
            _, work = self.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            forward = manager.forward
            writes = []
            def lose_request(runtime, path, method, body, headers):
                if path == "/api/documents" and method == "POST":
                    writes.append(body)
                    raise HubFailure(503, "OPERATION_INTERRUPTED", "No registered result is known.")
                return forward(runtime, path, method, body, headers)
            with patch.object(manager, "forward", lose_request):
                work.write_bytes(self.png_bytes("black"))
                self.wait_runtime_error(client, runtime_id, "WORK_COPY_OPERATION_INTERRUPTED")
                manager.bind_work_copies(runtime)
                manager._observe_work_copies(runtime)
                self.assertEqual(len(writes), 1)
                self.assertEqual(self.read_runtime(client, runtime_id)["error"]["code"],
                                 "WORK_COPY_OPERATION_INTERRUPTED")
                self.assertEqual(len(self.proxy(client, runtime_id, "/api/documents").json()["documents"]), 1)

    def test_untouched_work_copy_never_rolls_back_a_page_replaced_elsewhere(self):
        first = self.png_bytes("white")
        second = self.png_bytes("black")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            self.assertEqual(work.read_bytes(), first)
            # The architect updates the page through the Board's own upload
            # instead. The copy still holds the superseded bytes and nobody
            # touched it, so it is not evidence that the page moved back.
            manager = client.app.state.runtimes
            wait_for(lambda: any(row.observed_sha256 == original["assetSha256"]
                                 for row in manager.get(runtime_id).work_copies.values()),
                     "The initial work copy was not observed", timeout=20)
            replacement = self.upload_image(client, runtime_id, second, replaces=original)
            documents = lambda: self.proxy(client, runtime_id, "/api/documents").json()["documents"]
            before = len(documents())
            time.sleep(4)
            self.assertEqual(len(documents()), before)
            self.assertFalse(any(page["assetSha256"] == replacement["assetSha256"]
                                 for row in documents() for page in row["replacesPages"]))
            self.assertIsNone(self.read_runtime(client, runtime_id)["error"])
            self.assertEqual(work.read_bytes(), first)

    def test_offline_undo_keeps_current_page_and_reports_restart_conflict(self):
        first = self.png_bytes("white")
        second = self.png_bytes("black")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            work.write_bytes(second)
            replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            head = FilesystemProjectRepository.open(self.project).read_head()
            count = len(self.proxy(client, runtime_id, "/api/documents").json()["documents"])

        # An offline undo and a stale, untouched copy have the same bytes.
        # Neither proves a replacement, but the disagreement must be visible.
        work.write_bytes(first)
        with self.hub() as client:
            reopened = self.open_project(client)
            row = self.wait_runtime_error(client, reopened, "WORK_COPY_RESTART_CONFLICT")
            self.assertEqual(row["projection"], "ready")
            self.assertIn("live-plan.png", row["error"]["detail"])
            self.assertEqual(len(self.proxy(client, reopened, "/api/documents").json()["documents"]), count)
            self.assertEqual(FilesystemProjectRepository.open(self.project).read_head(), head)
            served = self.proxy(client, reopened, f"/api/documents/{replacement['assetSha256']}/bytes",
                                params={"runId": replacement["runId"]})
            self.assertEqual(served.content, second)
            # Matching the current page clears the notice without a new record.
            work.write_bytes(second)
            self.wait_runtime(client, reopened, lambda value: value["error"] is None)
            self.assertEqual(len(self.proxy(client, reopened, "/api/documents").json()["documents"]), count)

    def test_work_copy_permission_failure_is_visible_and_recovers_without_resave(self):
        first = self.png_bytes("white")
        second = self.png_bytes("black")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            read_bytes = Path.read_bytes
            def locked_read(path):
                if path == work:
                    raise PermissionError("File is in use")
                return read_bytes(path)
            with patch.object(Path, "read_bytes", locked_read):
                work.write_bytes(second)
                row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_READ_FAILED")
                self.assertEqual(row["projection"], "ready")
                self.assertIn("live-plan.png", row["error"]["detail"])
                self.assertEqual(len(self.proxy(client, runtime_id, "/api/documents").json()["documents"]), 1)
            self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            self.wait_runtime(client, runtime_id, lambda row: row["error"] is None)

    def test_work_copy_retries_failed_source_resolution_without_resave(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, self.png_bytes("white"))
            _, work = self.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            wait_for(lambda: any(row.observed_sha256 == original["assetSha256"]
                                 for row in manager.get(runtime_id).work_copies.values()),
                     "The initial work copy was not observed", timeout=20)
            with patch("monkeyhub_api.runtime.manager.list_document_work_copies", side_effect=OSError("Temporarily unreadable")):
                work.write_bytes(self.png_bytes("black"))
                row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_READ_FAILED")
                self.assertEqual(row["projection"], "ready")
            self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            self.wait_runtime(client, runtime_id, lambda row: row["error"] is None)

    def test_work_copy_and_board_upload_share_one_document_writer(self):
        import json
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, self.png_bytes("white"))
            _, work = self.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            forwarded = manager.forward
            arriving = threading.Barrier(2)
            attempts = []
            def simultaneous_forward(runtime, path, method, body, headers):
                if method == "POST" and path == "/api/documents":
                    attempts.append(json.loads(body))
                    arriving.wait(timeout=20)
                return forwarded(runtime, path, method, body, headers)
            with patch.object(manager, "forward", simultaneous_forward):
                work.write_bytes(self.png_bytes("black"))
                payload = {"projectId": self.project_id, "runId": None, "fileName": "other-client.png",
                    "mimeType": "image/png", "contentBase64": base64.b64encode(self.png_bytes("navy")).decode(),
                    "replacesPages": [{"runId": original["runId"], "assetSha256": original["assetSha256"],
                        "revisionRef": original["revisionRef"], "pageIndex": 0, "newPageIndex": 0}]}
                response = self.proxy(client, runtime_id, "/api/documents", "POST", json=payload)
                self.assertIn(response.status_code, (201, 409))
                wait_for(lambda: len(attempts) == 2, "The observed save bypassed Studio", timeout=20)
                self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            self.assertEqual(len(attempts), 2)
            documents = self.proxy(client, runtime_id, "/api/documents").json()["documents"]
            successors = [document for document in documents if any(
                page["assetSha256"] == original["assetSha256"] for page in document["replacesPages"])]
            self.assertEqual(len(successors), 1)

    def test_an_edit_back_to_earlier_bytes_is_reported_rather_than_dropped(self):
        first = self.png_bytes("white")
        second = self.png_bytes("black")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            work.write_bytes(second)
            replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])

            # The architect undoes the edit. These bytes are already registered
            # with a different replacement mapping, so the immutable document
            # contract refuses them. That refusal is the user's to see: the
            # Board keeps showing the page the project actually answers for.
            work.write_bytes(first)
            row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_DOCUMENT_REVISION_ALREADY_REGISTERED")
            self.assertIn("live-plan.png", row["error"]["detail"])
            self.assertEqual(row["projection"], "ready")
            self.assertFalse(any(page["assetSha256"] == replacement["assetSha256"]
                                 for document in self.proxy(client, runtime_id, "/api/documents").json()["documents"]
                                 for page in document["replacesPages"]))
            # The refusal stands until the copy registers something else; it is
            # not re-reported on every heartbeat and never becomes a stale project.
            time.sleep(3)
            later = self.read_runtime(client, runtime_id)
            self.assertEqual(later["error"]["code"], "WORK_COPY_DOCUMENT_REVISION_ALREADY_REGISTERED")
            self.assertEqual(later["projection"], "ready")

    def test_a_reshaped_work_copy_surfaces_the_aspect_ratio_refusal(self):
        first = self.png_bytes("white")
        reshaped = self.png_bytes("black", size=(40, 40))
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_image(client, runtime_id, first)
            _, work = self.open_work_copy(client, runtime_id, original)
            work.write_bytes(reshaped)
            row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_DOCUMENT_REPLACEMENT_INVALID")
            self.assertEqual(row["projection"], "ready")
            self.assertEqual(len(self.proxy(client, runtime_id, "/api/documents").json()["documents"]), 1)

    def test_pdf_work_copy_edit_registers_every_page_without_moving_head(self):
        first = self.pdf_bytes((400, 300), (300, 400), title="first")
        second = self.pdf_bytes((400, 300), (300, 400), title="second")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_document(client, runtime_id, first, "plan.pdf", "application/pdf")
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            copy, work = self.open_work_copy(client, runtime_id, original)
            self.assertIsNone(copy["refusal"])
            self.assertEqual(work.read_bytes(), first)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            head = FilesystemProjectRepository.open(self.project).read_head()
            work.write_bytes(second)
            replacement = self.wait_document_replacement(client, runtime_id, original["assetSha256"])
            self.assertEqual(replacement["mimeType"], "application/pdf")
            self.assertEqual(sorted((page["pageIndex"], page["newPageIndex"])
                                    for page in replacement["replacesPages"]), [(0, 0), (1, 1)])
            self.assertEqual(FilesystemProjectRepository.open(self.project).read_head(), head)
            self.wait_runtime(client, runtime_id, lambda row: row["error"] is None)

    def test_pdf_work_copy_with_a_changed_page_count_is_refused_visibly(self):
        # One page in, one page listed either way: only the declared whole
        # document lets the owner see that the file itself grew.
        first = self.pdf_bytes((400, 300), title="first")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_document(client, runtime_id, first, "plan.pdf", "application/pdf")
            _, work = self.open_work_copy(client, runtime_id, original)
            work.write_bytes(self.pdf_bytes((400, 300), (300, 400), (400, 300), title="three"))
            self.wait_runtime_error(client, runtime_id, "WORK_COPY_DOCUMENT_REPLACEMENT_INVALID")
            documents = self.proxy(client, runtime_id, "/api/documents").json()["documents"]
            self.assertEqual([document["assetSha256"] for document in documents], [original["assetSha256"]])

    def replaces_page(self, document, page_index=0, new_page_index=0):
        return [{"runId": document["runId"], "assetSha256": document["assetSha256"],
                 "revisionRef": document["revisionRef"],
                 "pageIndex": page_index, "newPageIndex": new_page_index}]

    def looked_again(self, observed):
        """Wait for one observation pass that actually read the copy's bytes."""

        observed.hashed_at_ns = None
        wait_for(lambda: True if observed.hashed_at_ns is not None else None,
                 "The work copy was not looked at again", timeout=20)

    def test_a_page_replaced_elsewhere_reports_the_copy_only_once_it_is_edited(self):
        first = self.pdf_bytes((400, 300), (300, 400), title="first")
        second = self.pdf_bytes((400, 300), (300, 400), title="second")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_document(client, runtime_id, first, "plan.pdf", "application/pdf")
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            copy, work = self.open_work_copy(client, runtime_id, original)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            # The Board replaces page 1 on its own, the way its dialog does.
            self.upload_document(client, runtime_id, self.png_bytes("red", size=(300, 400)),
                                 "page-two.png", "image/png",
                                 replaces=self.replaces_page(original, 1, 0))
            count = len(self.proxy(client, runtime_id, "/api/documents").json()["documents"])
            observed = next(iter(runtime.work_copies.values()))
            # Nobody edited anything. A document that moved on without this copy
            # is the row's news, not the project's error, so the error surface
            # stays free for something a person can actually act on.
            self.looked_again(observed)
            self.assertIsNone(observed.failure)
            self.assertIsNone(self.read_runtime(client, runtime_id)["error"])
            self.assertIn("different documents", observed.copy.refusal)
            # Saving is news. It is reported, the file is left exactly as it
            # was saved, and nothing is registered behind the architect's back.
            work.write_bytes(second)
            row = self.wait_runtime_error(client, runtime_id, "WORK_COPY_NOT_EDITABLE")
            self.assertEqual(row["projection"], "ready")
            self.assertEqual(work.read_bytes(), second)
            self.assertEqual(list(manager.bind_work_copies(runtime).values()), [str(work)])
            self.assertEqual(len(self.proxy(client, runtime_id, "/api/documents").json()["documents"]), count)

    def test_an_edit_refused_while_the_document_moved_registers_once_it_can(self):
        first = self.pdf_bytes((400, 300), title="first")
        edited = self.pdf_bytes((400, 300), title="edited")
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.upload_document(client, runtime_id, first, "plan.pdf", "application/pdf")
            _, work = self.open_work_copy(client, runtime_id, original)
            # Another client answers for this page with a three-page file, so
            # no single file stands for the document any more.
            three = self.upload_document(client, runtime_id,
                                         self.pdf_bytes((400, 300), (300, 400), (400, 300), title="three"),
                                         "spread.pdf", "application/pdf",
                                         replaces=self.replaces_page(original))
            # The architect saves inside that window.
            work.write_bytes(edited)
            self.wait_runtime_error(client, runtime_id, "WORK_COPY_NOT_EDITABLE")
            self.assertEqual(work.read_bytes(), edited)
            # The page becomes a one-page document again and the refusal goes.
            again = self.upload_document(client, runtime_id, self.pdf_bytes((400, 300), title="again"),
                                         "plan.pdf", "application/pdf",
                                         replaces=self.replaces_page(three))
            # The edit that was waiting is registered, not swallowed.
            replacement = self.wait_document_replacement(client, runtime_id, again["assetSha256"])
            self.assertEqual(replacement["mimeType"], "application/pdf")
            self.assertEqual([(page["pageIndex"], page["newPageIndex"])
                              for page in replacement["replacesPages"]], [(0, 0)])
            bytes_back = self.proxy(client, runtime_id, f"/api/documents/{replacement['assetSha256']}/bytes",
                                    params={"runId": replacement["runId"]})
            self.assertEqual(bytes_back.content, edited)
            self.wait_runtime(client, runtime_id, lambda row: row["error"] is None)

    def test_cold_hub_preserves_admission_identity_for_two_projects_without_retained_runs(self):
        other_id, other_project = self.make_parallel_project()
        operation_id = str(uuid4())
        requests = []
        with self.hub() as client:
            for project_id, project in ((self.project_id, self.project), (other_id, other_project)):
                runtime_id = self.open_project(client, project_id, project)
                state = self.proxy(client, runtime_id, "/api/state").json()
                body = {"projectId": project_id, "stateDigest": state["stateDigest"],
                        "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2"}
                response = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=operation_id, json=body)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(sorted(path.name for path in (project / "runs").iterdir()), [self.fixture.REFERENCE_RUN_ID])
                requests.append((project_id, project, runtime_id, body, self.project_bytes(project)))
        # New Hub app/managers and new real workers, sharing only their explicit
        # runtime directory and the original P036 projects.
        with self.hub() as client:
            for project_id, project, prior_runtime, body, before in requests:
                runtime_id = self.open_project(client, project_id, project)
                self.assertEqual(runtime_id, prior_runtime)
                repeated = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=operation_id, json=body)
                self.assertEqual(repeated.status_code, 409, repeated.text)
                self.assertEqual(repeated.json()["code"], "OPERATION_NEEDS_RECOVERY")
                changed = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=operation_id,
                                     json={**body, "utterance": "set height to 9"})
                self.assertEqual(changed.status_code, 409, changed.text)
                self.assertEqual(changed.json()["code"], "OPERATION_ID_CONFLICT")
                self.assertEqual(self.project_bytes(project), before)
                rows = self.read_runtime(client, runtime_id)["operations"]
                self.assertEqual([(row["operationId"], row["projectId"]) for row in rows if row["operationId"] == operation_id],
                                 [(operation_id, project_id)])

    def test_concurrent_duplicate_submission_parallel_projects_and_page_reopen(self):
        other_id, other_project = self.make_parallel_project()
        with self.hub() as client:
            a = self.open_project(client)
            b = self.open_project(client, other_id, other_project)
            workers_before = {row["runtimeId"]: row["workers"][0] for row in client.get("/api/runtime").json()["projects"]}
            self.assertNotEqual(workers_before[a]["processId"], workers_before[b]["processId"])
            self.assertNotEqual(workers_before[a]["url"], workers_before[b]["url"])
            proposal_a = self.propose(client, a)
            proposal_b = self.propose(client, b, project_id=other_id)
            operation_id = str(uuid4())
            candidate_id = "hub-cand-" + operation_id.replace("-", "")
            barrier = threading.Barrier(3)

            def submit(runtime_id, proposal):
                barrier.wait(timeout=10)
                return self.proxy(client, runtime_id, f"/api/proposals/{proposal['proposalId']}/candidate", "POST", operation_id=operation_id)

            with ThreadPoolExecutor(max_workers=3) as pool:
                responses = [future.result(timeout=40) for future in [
                    pool.submit(submit, a, proposal_a), pool.submit(submit, a, proposal_a), pool.submit(submit, b, proposal_b),
                ]]
            for response in responses:
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(response.json()["candidateId"], candidate_id)
            self.assertEqual(responses[0].json(), responses[1].json())
            self.assertNotEqual(responses[0].json()["jobId"], responses[2].json()["jobId"])
            self.wait_job(client, a, responses[0].json()["jobId"])
            self.wait_job(client, b, responses[2].json()["jobId"])
            for runtime_id, project_id, project in ((a, self.project_id, self.project), (b, other_id, other_project)):
                snapshot = self.wait_runtime(client, runtime_id, lambda row: any(
                    operation["operationId"] == operation_id and operation["status"] == "completed" for operation in row["operations"]))
                operation = next(row for row in snapshot["operations"] if row["operationId"] == operation_id)
                self.assertEqual(operation["projectId"], project_id)
                self.assertEqual(operation["candidateId"], candidate_id)
                self.assertEqual(len([row for row in snapshot["retained"]["jobs"] if row["candidateId"] == candidate_id]), 1)
                self.assertEqual(sorted(path.name for path in (project / "runs").iterdir()), sorted([self.fixture.REFERENCE_RUN_ID, candidate_id]))
                reopened = client.post("/api/runtime/projects/open", json={"projectId": project_id, "projectDir": str(project)})
                self.assertEqual(reopened.status_code, 200, reopened.text)
                self.assertEqual(reopened.json()["runtimeId"], runtime_id)
                self.assertEqual(reopened.json()["workers"][0]["instanceId"], workers_before[runtime_id]["instanceId"])
            refreshed = client.get("/api/runtime").json()
            self.assertEqual({row["runtimeId"] for row in refreshed["projects"]}, {a, b})
            wrong = client.post(f"/api/runtime/projects/{a}/close", json={"projectId": other_id})
            self.assertEqual(wrong.status_code, 409, wrong.text)
            closed = client.post(f"/api/runtime/projects/{a}/close", json={"projectId": self.project_id})
            self.assertEqual(closed.status_code, 202, closed.text)
            self.assertEqual(closed.json()["state"], "closed")
            self.wait_state(client, "monkeyarch", "stopped", project_dir=self.project)
            other = self.read_runtime(client, b)
            self.assertEqual(other["workers"][0]["instanceId"], workers_before[b]["instanceId"])
            self.assertIn(other["workers"][0]["state"], {"ready", "busy"})

    def register_model(self, client, runtime_id, run_id, state_digest):
        model = (ROOT / "services/project-runtime/tests/fixtures/model-source-a.3dm").read_bytes()
        response = self.proxy(client, runtime_id, "/api/model-assets", "POST", json={
            "projectId": self.project_id, "runId": run_id, "stateDigest": state_digest,
            "fileName": "complete.3dm", "contentBase64": base64.b64encode(model).decode(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["modelSource"]

    def test_committed_candidate_survives_worker_crash_without_duplicate_modification(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            state = self.proxy(client, runtime_id, "/api/state").json()
            model = self.register_model(client, runtime_id, self.fixture.REFERENCE_RUN_ID, state["stateDigest"])
            initial = self.proxy(client, runtime_id, "/api/design-stages/initialize", "POST",
                json={"projectId": self.project_id, "modelSource": model})
            self.assertEqual(initial.status_code, 201, initial.text)
            initial = initial.json()
            proposal = self.propose(client, runtime_id, stage=initial)
            candidate_operation = str(uuid4())
            candidate_path = f"/api/proposals/{proposal['proposalId']}/candidate"
            submitted = self.proxy(client, runtime_id, candidate_path, "POST", operation_id=candidate_operation)
            self.assertEqual(submitted.status_code, 202, submitted.text)
            self.wait_job(client, runtime_id, submitted.json()["jobId"])
            candidate_id = submitted.json()["candidateId"]
            candidate_state = self.proxy(client, runtime_id, "/api/state",
                params={"run": candidate_id, "sourceStageRef": initial["stageRef"]})
            self.assertEqual(candidate_state.status_code, 200, candidate_state.text)
            self.register_model(client, runtime_id, candidate_id, candidate_state.json()["stateDigest"])
            accept_operation = str(uuid4())
            accept_path = f"/api/candidates/{candidate_id}/accept"
            accept_body = {"projectId": self.project_id, "expectedHeadStageRef": initial["stageRef"]}
            accepted = self.proxy(client, runtime_id, accept_path, "POST", operation_id=accept_operation, json=accept_body)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            completed = self.wait_runtime(client, runtime_id, lambda row: any(
                operation["operationId"] == accept_operation and operation["committed"] and operation["status"] == "completed" for operation in row["operations"]))
            original_worker = completed["workers"][0]
            before = self.project_bytes(self.project)
            owned = client.app.state.applications.supervisor._children[original_worker["workerId"]].process
            os.kill(original_worker["processId"], signal.SIGTERM)  # Only this test's verified managed worker.
            owned.wait(timeout=10)
            crashed = self.read_runtime(client, runtime_id)
            self.assertEqual(crashed["workers"][0]["state"], "crashed")
            self.assertIsNone(crashed["workers"][0]["processId"])
            recovered = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(recovered.status_code, 202, recovered.text)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            self.assertEqual(ready["workers"][0]["url"], original_worker["url"])
            self.assertNotEqual(ready["workers"][0]["instanceId"], original_worker["instanceId"])
            self.assertEqual(ready["retained"]["jobs"], [])
            retained = next(row for row in ready["operations"] if row["operationId"] == accept_operation)
            self.assertTrue(retained["committed"])
            self.assertEqual(retained["status"], "completed")
            repeated_candidate = self.proxy(client, runtime_id, candidate_path, "POST", operation_id=candidate_operation)
            self.assertEqual(repeated_candidate.status_code, 202, repeated_candidate.text)
            self.assertEqual(repeated_candidate.json(), submitted.json())
            repeated_accept = self.proxy(client, runtime_id, accept_path, "POST", operation_id=accept_operation, json=accept_body)
            self.assertEqual(repeated_accept.status_code, 200, repeated_accept.text)
            self.assertEqual(repeated_accept.json(), accepted.json())
            history = self.proxy(client, runtime_id, "/api/design-history")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()["stages"], [initial, accepted.json()])
            self.assertEqual(self.project_bytes(self.project), before)

    def test_wrong_project_and_stale_base_are_refused_without_runs(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            before = self.project_bytes(self.project)
            state = self.proxy(client, runtime_id, "/api/state").json()
            body = {"projectId": self.project_id, "stateDigest": state["stateDigest"],
                    "targetComponentId": "portico", "elementId": "portico-base", "utterance": "set height to 2.2"}
            wrong_open = client.post("/api/runtime/projects/open", json={"projectId": "other-project", "projectDir": str(self.project)})
            self.assertEqual(wrong_open.status_code, 409, wrong_open.text)
            wrong_body = self.proxy(client, runtime_id, "/api/proposals", "POST", json={**body, "projectId": "other-project"})
            self.assertEqual(wrong_body.status_code, 409, wrong_body.text)
            self.assertEqual(wrong_body.json()["code"], "PROJECT_MISMATCH")
            wrong_query = self.proxy(client, runtime_id, "/api/state", params={"projectId": "other-project"})
            self.assertEqual(wrong_query.status_code, 409, wrong_query.text)
            stale_id = str(uuid4())
            stale = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=stale_id,
                json={**body, "stateDigest": "0" * 64})
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertIn("STALE", stale.json()["code"])
            snapshot = self.wait_runtime(client, runtime_id, lambda row: any(
                operation["operationId"] == stale_id and operation["status"] == "refused" for operation in row["operations"]))
            self.assertFalse(any(row["committed"] for row in snapshot["operations"]))
            self.assertEqual(self.project_bytes(self.project), before)

    def test_worker_exit_before_candidate_write_is_not_completed_or_replayed(self):
        # Patch only the disposable child interpreter. It retains the normal
        # Studio HTTP routes and managed identity, then exits at the real job's
        # execution boundary before execute_candidate writes its first run.
        fault_script = self.root / "exit-before-candidate.py"
        launcher = ROOT / "apps/monkeyhub/run.py"
        fault_script.write_text(
            "import os, runpy, sys\n"
            f"sys.path[:0] = {[str(ROOT / 'services/project-runtime/src'), str(ROOT)]!r}\n"
            "from project_runtime.api.routes import candidates\n"
            "def exit_before_candidate(*args, **kwargs):\n    os._exit(73)\n"
            "candidates.execute_candidate = exit_before_candidate\n"
            f"sys.argv[0] = {str(launcher)!r}\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n",
            encoding="utf-8",
        )
        with self.hub() as client:
            applications = client.app.state.applications
            original_command = applications._command

            def fault_command(service, settings):
                command, environment = original_command(service, settings)
                if service == "studio":
                    command[1] = str(fault_script)
                return command, environment

            with patch.object(applications, "_command", side_effect=fault_command):
                runtime_id = self.open_project(client)
            proposal = self.propose(client, runtime_id)
            operation_id = str(uuid4())
            path = f"/api/proposals/{proposal['proposalId']}/candidate"
            before = self.project_bytes(self.project)
            response = self.proxy(client, runtime_id, path, "POST", operation_id=operation_id)
            self.assertIn(response.status_code, {202, 503}, response.text)
            crashed = self.wait_runtime(client, runtime_id, lambda row:
                row["workers"][0]["state"] == "crashed" and any(
                    operation["operationId"] == operation_id and operation["status"] == "needs_recovery"
                    for operation in row["operations"]))
            original_instance = crashed["workers"][0]["instanceId"]
            recovered = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(recovered.status_code, 202, recovered.text)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            worker = ready["workers"][0]
            self.assertNotEqual(worker["instanceId"], original_instance)
            operation = next(row for row in ready["operations"] if row["operationId"] == operation_id)
            self.assertEqual(operation["status"], "needs_recovery")
            self.assertFalse(operation["committed"])
            self.assertEqual(ready["retained"]["jobs"], [])
            repeated = self.proxy(client, runtime_id, path, "POST", operation_id=operation_id)
            self.assertIn(repeated.status_code, {202, 409}, repeated.text)
            self.assertEqual(self.read_runtime(client, runtime_id)["workers"][0]["instanceId"], worker["instanceId"])
            self.assertEqual(self.project_bytes(self.project), before)

    def test_crashed_worker_can_be_explicitly_stopped_and_started_again(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            original = self.read_runtime(client, runtime_id)["workers"][0]
            before = self.project_bytes(self.project)
            owned = client.app.state.applications.supervisor._children[original["workerId"]].process
            os.kill(original["processId"], signal.SIGTERM)
            owned.wait(timeout=10)
            crashed = self.read_runtime(client, runtime_id)["workers"][0]
            self.assertEqual(crashed["state"], "crashed")
            self.assertEqual(crashed["desiredState"], "running")
            for _ in range(2):
                stopped = client.post("/api/apps/monkeyboard/stop", params={"projectDir": str(self.project)})
                self.assertEqual(stopped.status_code, 202, stopped.text)
                self.assertEqual(stopped.json()["state"], "stopped")
                snapshot = self.read_runtime(client, runtime_id)["workers"][0]
                self.assertEqual(snapshot["state"], "stopped")
                self.assertEqual(snapshot["desiredState"], "stopped")
                self.assertIsNone(snapshot["processId"])
                self.assertIsNone(snapshot["error"])
            refused = client.post(f"/api/runtime/projects/{runtime_id}/recover", json={"projectId": self.project_id})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["code"], "WORKER_NOT_CRASHED")
            restarted = client.post("/api/apps/monkeyboard/start", params={"projectDir": str(self.project)})
            self.assertEqual(restarted.status_code, 202, restarted.text)
            self.wait_state(client, "monkeyarch", "running", project_dir=self.project)
            ready = self.wait_runtime(client, runtime_id, lambda row: row["projection"] == "ready" and row["workers"][0]["healthy"])
            self.assertNotEqual(ready["workers"][0]["instanceId"], original["instanceId"])
            self.assertEqual(ready["workers"][0]["desiredState"], "running")
            self.assertEqual(self.project_bytes(self.project), before)

    def test_same_worker_recovers_readiness_without_rebuilding_unchanged_projection(self):
        from monkeyhub_api.runtime.worker_http import request_http

        with self.hub() as client:
            runtime_id = self.open_project(client)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            applications = client.app.state.applications
            before = self.project_bytes(self.project)
            # Serialize against the real observer while simulating one transport
            # outage. The worker remains alive and its retained base is unchanged.
            with runtime.refresh_lock:
                worker = applications.worker_snapshots(project_dir=str(self.project))[0]
                unavailable = replace(worker, state="unavailable", healthy=False)
                with patch.object(applications, "worker_snapshots", return_value=(unavailable,)):
                    manager._refresh(runtime)
                self.assertEqual(runtime.projection, "stale")
                with patch("monkeyhub_api.runtime.manager.request_http", wraps=request_http) as reads:
                    manager._refresh(runtime)
                self.assertEqual(runtime.projection, "ready")
                self.assertFalse(any(call.args[1] == "/api/state" for call in reads.call_args_list))
            self.assertEqual(self.project_bytes(self.project), before)

    def test_idle_watcher_rederives_work_copies_only_when_their_inputs_move(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            wait_for(lambda: runtime.work_copy_key is not None, "The opening pass did not bind work copies", timeout=10)
            with patch("monkeyhub_api.runtime.manager._WORK_COPY_CHECK_S", 0.2), \
                    patch("monkeyhub_api.runtime.manager._IDLE_HEARTBEAT_S", 0.2),                     patch.object(manager, "_work_copy_inputs", wraps=manager._work_copy_inputs) as checks,                     patch.object(manager, "bind_work_copies", wraps=manager.bind_work_copies) as binds:
                # Deriving the list reads every record of every run (#314), so
                # an idle watcher compares what decides it and derives nothing.
                runtime.wake.set()
                wait_for(lambda: checks.call_count >= 3, "The idle watcher stopped comparing its inputs", timeout=10)
                self.assertEqual(binds.call_count, 0, "An idle pass re-derived unchanged work copies")

                with patch.object(runtime.wake, "set", wraps=runtime.wake.set) as wakes:
                    # A read changes nothing retained and wakes nobody.
                    listed = self.proxy(client, runtime_id, "/api/documents")
                    self.assertEqual(listed.status_code, 200, listed.text)
                    self.assertEqual(wakes.call_count, 0, "A forwarded read woke the project watcher")
                    # A registered document is a new input, taken on the wake.
                    original = self.upload_image(client, runtime_id, self.png_bytes("white"))
                    self.assertGreater(wakes.call_count, 0)
                wait_for(lambda: binds.call_count >= 1, "A registered document was not re-derived", timeout=10)
                self.assertEqual(manager.bind_work_copies(runtime), {})
                derived = binds.call_count

                # So is the file the Studio writes when a copy is asked for.
                _, work = self.open_work_copy(client, runtime_id, original)
                wait_for(lambda: str(work) in {str(row.copy.path) for row in runtime.work_copies.values()},
                         "An opened work copy was not bound", timeout=10)
                self.assertGreater(binds.call_count, derived)

    def test_idle_runtime_skips_history_scans_but_refreshes_on_request_and_crash(self):
        with self.hub() as client:
            runtime_id = self.open_project(client)
            manager = client.app.state.runtimes
            original = self.read_runtime(client, runtime_id)["workers"][0]
            before = self.project_bytes(self.project)
            state_digest = self.fixture.runner_state_digest(self.repository, self.fixture.REFERENCE_RUN_ID)
            with patch.object(manager, "_read_retained", wraps=manager._read_retained) as scans:
                # Let the opening wake and ready transition finish, then cover
                # several normal status ticks with a healthy, idle real worker.
                time.sleep(1.2)
                idle_scans = scans.call_count
                runtime = manager.get(runtime_id)
                with patch.object(manager, "project_snapshot", wraps=manager.project_snapshot) as snapshots, \
                     patch.object(runtime.operations, "records", wraps=runtime.operations.records) as records:
                    time.sleep(3.2)
                    snapshots.assert_not_called()
                    records.assert_not_called()
                self.assertEqual(scans.call_count, idle_scans, "Idle status polling rescanned retained history")
                operation_id = str(uuid4())
                proposed = self.proxy(client, runtime_id, "/api/proposals", "POST", operation_id=operation_id, json={
                    "projectId": self.project_id, "stateDigest": state_digest,
                    "sourceRunId": self.fixture.REFERENCE_RUN_ID, "targetComponentId": "portico",
                    "elementId": "portico-base", "utterance": "set height to 2.2",
                })
                self.assertEqual(proposed.status_code, 201, proposed.text)
                wait_for(lambda: scans.call_count > idle_scans,
                         "A forwarded mutation did not promptly refresh retained state", timeout=5)
                operation = next(row for row in self.read_runtime(client, runtime_id)["operations"]
                                 if row["operationId"] == operation_id)
                self.assertEqual(operation["status"], "completed")
                cold_scans = sum(call.kwargs.get("worker") is None for call in scans.call_args_list)
                owned = client.app.state.applications.supervisor._children[original["workerId"]].process
                os.kill(original["processId"], signal.SIGTERM)
                owned.wait(timeout=10)

                def crash_observed():
                    snapshot = self.read_runtime(client, runtime_id)
                    cold_read = sum(call.kwargs.get("worker") is None for call in scans.call_args_list) > cold_scans
                    return snapshot if snapshot["workers"][0]["state"] == "crashed" and snapshot["projection"] == "stale" and cold_read else None

                crashed = wait_for(crash_observed, "Idle runtime did not promptly observe and cold-read the crashed worker", timeout=5)
                self.assertIsNone(crashed["workers"][0]["processId"])
                self.assertEqual(crashed["workers"][0]["instanceId"], original["instanceId"])
                self.assertEqual(self.project_bytes(self.project), before)

    def test_idle_snapshot_gate_keeps_operation_chat_and_status_updates_visible(self):
        # Drive the real watcher one heartbeat at a time over an isolated
        # project. No server, provider or owned process is started here.
        workers, sessions, ticks, copy_errors = [], [], [], []
        now = [0.0]
        applications = SimpleNamespace(worker_snapshots=lambda **_: tuple(workers), set_busy=lambda **_: None)
        chats = SimpleNamespace(list=lambda **kw: [row for row in sessions if row.archived == kw.get("archived", False)])
        manager = ProjectRuntimeManager(applications, chats)
        runtime = ProjectRuntime("idle", self.project_id, str(self.project), OperationManager(self.project_id),
                                 ProjectBinding.open(StudioSettings(project_dir=self.project, cad_export="off")))
        chat = ChatSummary(id="chat", projectId=self.project_id, projectDir=str(self.project), title="status",
                           provider="codex", createdAt="2026-09-26", updatedAt="2026-09-26")
        admitted = []

        def observe(_runtime):
            if copy_errors:
                raise OSError("editable copy unavailable")

        def heartbeat(_timeout):
            current = runtime.last_snapshot
            kinds = [row["kind"] for row in manager.events.replay()]
            tick = len(ticks)
            ticks.append(current)
            if tick == 0:
                self.assertIsNotNone(current)
            elif tick == 1:
                self.assertIs(current, ticks[0], "An unchanged heartbeat rebuilt the snapshot")
                admission, _ = runtime.operations.admit(str(uuid4()), "POST", "/api/proposals", b"{}",
                    retained=runtime.retained, source="studio", session_id=None)
                admitted.append(admission)  # Still in flight, before the forwarder's completion wake.
            elif tick == 2:
                self.assertEqual(current["operations"][0]["status"], "executing")
                runtime.operations.replied(admitted[0], HttpResult(200, b"{}", {}))
                runtime.wake.set()
            elif tick == 3:
                self.assertEqual(current["operations"][0]["status"], "completed")
                sessions.append(chat)
                manager.chat_changed(chat)
            elif tick == 4:
                self.assertEqual(current["sessions"][0]["id"], chat.id)
                self.assertEqual(kinds[-1], "agent/progress")
                manager._clients = 1
            elif tick == 5:
                self.assertEqual(current["clients"], 1)
                workers.append(WorkerSnapshot("worker", "studio", self.project_id, str(self.project),
                    "instance", None, "stopped", "stopped", False, None, None))
            elif tick == 6:
                self.assertEqual(current["projection"], "stale")
                self.assertEqual(kinds[-1], "projection/invalidated")
                workers[0] = replace(workers[0], error=HubError(code="WORKER_DETAIL", detail="changed detail"))
            elif tick == 7:
                self.assertEqual(current["workers"][0]["error"]["code"], "WORKER_DETAIL")
                copy_errors.append(True)
            elif tick == 8:
                self.assertEqual(current["error"]["code"], "WORK_COPY_READ_FAILED")
                copy_errors.clear()
            elif tick == 9:
                self.assertIsNone(current["error"])
                runtime.work_copies[("run", "asset", None)] = SimpleNamespace(
                    failure=HubError(code="COPY_REFUSED", detail="not registered"))
            elif tick == 10:
                self.assertEqual(current["error"]["code"], "COPY_REFUSED")
                runtime.work_copies.clear()
                runtime.projection = "ready"
            elif tick == 11:
                self.assertIsNone(current["error"])
                self.assertEqual(kinds[-1], "projection/updated")
                # A separate read may update retained results between ticks.
                runtime.retained = {**runtime.retained, "runsScanned": 99}
            elif tick == 12:
                self.assertEqual(current["retained"]["runs_scanned"], 99)
                self.repository.create_run("external-new-run")
                self.assertFalse(runtime.wake.is_set())
                now[0] = 30.0  # A separate client has no Hub wake; the due pass must see it.
            elif tick == 13:
                self.assertIn("external-new-run", [row[0] for row in runtime.work_copy_key])
                manager._closing.set()

        with patch.object(runtime.wake, "wait", side_effect=heartbeat), \
             patch.object(manager, "_observe_work_copies", side_effect=observe), \
             patch("monkeyhub_api.runtime.manager.time.monotonic", side_effect=lambda: now[0]):
            manager._watch(runtime)
        self.assertEqual(len(ticks), 14)


class WorkCopyObservationTests(unittest.TestCase):
    """Real files, with only the observer clock advanced to bound idle costs."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-work-copy-observer-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name) / "plan.png"
        self.first = ProjectRuntimeHttpTests.png_bytes("white", compress_level=0)
        self.second = ProjectRuntimeHttpTests.png_bytes("black", compress_level=0)
        self.assertEqual(len(self.first), len(self.second))
        self.work.write_bytes(self.first)
        self.observed = _WorkCopyObservation(SimpleNamespace(path=self.work))
        self.manager = object.__new__(ProjectRuntimeManager)

    def read_at(self, seconds):
        with patch("monkeyhub_api.runtime.manager.time.monotonic_ns", return_value=int(seconds * 1_000_000_000)):
            return self.manager._stable_work_copy_bytes(self.observed)

    def replace_preserving_timestamp(self):
        stamp = self.work.stat()
        temporary = self.work.with_suffix(".saving")
        temporary.write_bytes(self.second)
        os.utime(temporary, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        os.replace(temporary, self.work)

    def test_waits_for_elapsed_stability_even_when_file_timestamp_is_old(self):
        old = time.time_ns() - 10_000_000_000
        os.utime(self.work, ns=(old, old))
        with patch.object(Path, "read_bytes", autospec=True, return_value=self.first) as read:
            for instant in (0, 0.01, 1, 1.999):
                self.assertIsNone(self.read_at(instant))
            read.assert_not_called()
            self.assertEqual(self.read_at(2), self.first)
            self.assertEqual(read.call_count, 1)

    def test_atomic_replacement_with_same_size_and_timestamp_waits_then_reads(self):
        self.assertIsNone(self.read_at(0))
        self.assertEqual(self.read_at(2), self.first)
        self.replace_preserving_timestamp()
        self.assertIsNone(self.read_at(3))
        self.assertIsNone(self.read_at(4.999))
        self.assertEqual(self.read_at(5), self.second)

    def test_same_metadata_in_place_save_is_detected_by_bounded_content_refresh(self):
        read_bytes = Path.read_bytes
        with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes) as read:
            self.assertIsNone(self.read_at(0))
            self.assertEqual(self.read_at(2), self.first)
            stamp = self.work.stat()
            self.work.write_bytes(self.second)
            os.utime(self.work, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            for instant in range(3, 32):
                self.assertIsNone(self.read_at(instant))
            self.assertEqual(read.call_count, 1)
            self.assertEqual(self.read_at(32), self.second)
            self.assertEqual(read.call_count, 2)

    def test_atomic_replacement_during_read_discards_the_old_sample(self):
        self.assertIsNone(self.read_at(0))
        read_bytes = Path.read_bytes
        def replace_during_read(path):
            data = read_bytes(path)
            self.replace_preserving_timestamp()
            return data
        with patch.object(Path, "read_bytes", replace_during_read):
            self.assertIsNone(self.read_at(2))
        self.assertIsNone(self.read_at(3.999))
        self.assertEqual(self.read_at(4), self.second)


def _settle(root: Path) -> None:
    """Age every time in a project, as if its last write were long ago."""

    old = time.time_ns() - 60_000_000_000
    for folder, _, names in os.walk(root):
        for name in names:
            os.utime(os.path.join(folder, name), ns=(old, old))
    for folder, _, _ in os.walk(root, topdown=False):
        os.utime(folder, ns=(old, old))


class RuntimeCostTests(unittest.TestCase):
    """What an unchanged project costs the Hub (#363): no reopen per request,
    no idle history read, and a runtime snapshot measured in milliseconds."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-runtime-cost-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = project_fixture()

    def project(self, name="p0"):
        repository, _ = self.fixture.make_project(self.root / name)
        return repository

    def manager(self, workers=(), sessions=()):
        from monkeyhub_api.runtime.workers import project_key

        def worker_snapshots(*, project_dir=None):
            # The supervisor's selection: launches naming this project directory.
            return tuple(row for row in workers if project_dir is None
                         or (row.project_dir is not None and project_key(row.project_dir) == project_key(project_dir)))

        applications = SimpleNamespace(worker_snapshots=worker_snapshots, set_busy=lambda **_: None,
                                       runtime_root=self.root / "hub")
        chats = SimpleNamespace(list=lambda **kw: [row for row in sessions if row.archived == kw.get("archived", False)])
        return ProjectRuntimeManager(applications, chats)

    def runtime(self, manager, repository, name="runtime"):
        path = repository.layout.root
        binding = ProjectBinding.open(StudioSettings(project_dir=path, cad_export="off"))
        runtime = ProjectRuntime(name, self.fixture.PROJECT_ID, str(path), OperationManager(self.fixture.PROJECT_ID), binding)
        manager._projects[runtime.runtime_id] = runtime
        return runtime

    def test_get_verifies_the_project_again_only_when_its_binding_moves(self):
        repository = self.project()
        manager = self.manager()
        self.addCleanup(manager.shutdown)
        with patch("monkeyhub_api.runtime.manager._project", wraps=runtime_module._project) as checks:
            opened = manager.open(self.fixture.PROJECT_ID, str(repository.layout.root))
            self.assertEqual(checks.call_count, 1)
            for _ in range(5):
                self.assertIs(manager.get(opened.runtime_id), opened)
            self.assertEqual(checks.call_count, 1, "An unchanged project was opened again per request")

            manifest = repository.layout.manifest
            stamp = manifest.stat()
            os.utime(manifest, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
            self.assertIs(manager.get(opened.runtime_id), opened)
            self.assertEqual(checks.call_count, 2)
            self.assertIs(manager.get(opened.runtime_id), opened)
            self.assertEqual(checks.call_count, 2)

            # A folder that now holds another project is refused as before.
            other = json.loads(manifest.read_text(encoding="utf-8"))
            other["project_id"] = "another-project"
            manifest.write_text(json.dumps(other), encoding="utf-8")
            with self.assertRaises(HubFailure) as refused:
                manager.get(opened.runtime_id)
            self.assertEqual(refused.exception.status, 422)
            self.assertEqual(checks.call_count, 3)

    def test_opening_an_observed_project_again_neither_verifies_nor_rereads_an_unchanged_project(self):
        # #449: a page reload opens the project's runtime again while its workspace loads.
        repository = self.project()
        manager = self.manager()
        self.addCleanup(manager.shutdown)
        root = str(repository.layout.root)
        with patch("monkeyhub_api.runtime.manager._project", wraps=runtime_module._project) as checks:
            opened = manager.open(self.fixture.PROJECT_ID, root)
            self.assertEqual(checks.call_count, 1)
            with patch.object(opened.wake, "set", wraps=opened.wake.set) as full_reads, \
                    patch.object(opened.wake, "check", wraps=opened.wake.check) as moved_checks:
                for _ in range(3):
                    self.assertIs(manager.open(self.fixture.PROJECT_ID, root), opened)
                self.assertEqual(checks.call_count, 1, "an unchanged project was verified again on each open")
                full_reads.assert_not_called()
                self.assertEqual(moved_checks.call_count, 3, "each open still asks whether the project moved")
            with self.assertRaises(HubFailure) as refused:
                manager.open("another-project", root)
            self.assertEqual(refused.exception.error.code, "PROJECT_MISMATCH")
            manifest = repository.layout.manifest
            stamp = manifest.stat()
            os.utime(manifest, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
            self.assertIs(manager.open(self.fixture.PROJECT_ID, root), opened)
            self.assertEqual(checks.call_count, 3, "a moved manifest is verified again")

    def test_get_refuses_a_changed_binding_with_the_same_conflict(self):
        repository = self.project()
        manager = self.manager()
        runtime = self.runtime(manager, repository)
        runtime.binding_signature = runtime_module.binding_signature(runtime.project_dir)
        with patch("monkeyhub_api.runtime.manager._project", return_value=("another-project", runtime.project_dir)) as checks:
            self.assertIs(manager.get(runtime.runtime_id), runtime)
            checks.assert_not_called()
            os.utime(repository.layout.manifest, ns=(1, 1))
            with self.assertRaises(HubFailure) as refused:
                manager.get(runtime.runtime_id)
        self.assertEqual((refused.exception.status, refused.exception.error.code), (409, "PROJECT_MISMATCH"))

    def test_idle_watcher_skips_the_history_read_of_an_unchanged_project(self):
        repository = self.project()
        manager = self.manager()
        runtime = self.runtime(manager, repository)
        _settle(repository.layout.root)
        # The binding's layout watch sees the aged times itself within a
        # moment; the test waits for it rather than for the moment.
        runtime.binding.layout_watch().sync()
        watched = runtime.binding.layout_watch().watch
        tree = watched._tree
        walked_by = []
        visit_one = tree._visit_one

        def visit(relative, force_list):
            walked_by.append(threading.current_thread().name)
            return visit_one(relative, force_list)

        tree._visit_one = visit
        now = [0.0]
        refreshes = []
        script = []

        def heartbeat(_timeout):
            refreshes.append(refresh.call_count)
            step = len(refreshes)
            if step == 1:
                now[0] = 30.0  # idle fallback due; nothing on disk moved
            elif step == 2:
                now[0] = 60.0
                repository.create_run("external-run")  # a separate client writes
                _settle(repository.layout.root)
                runtime.binding.layout_watch().sync()
            elif step == 3:
                now[0] = 90.0  # unchanged since that read
            elif step == 4:
                runtime.wake.set()  # a Hub mutation still refreshes at once
            elif step == 5:
                now[0] = 120.0  # that read recorded its token too: nothing to read (#435)
            elif step == 6:
                now[0] = 150.0
            else:
                manager._closing.set()
            script.append(now[0])

        with patch.object(manager, "refresh", wraps=manager.refresh) as refresh, \
             patch.object(runtime.wake, "wait", side_effect=heartbeat), \
             patch.object(manager, "_observe_work_copies", return_value=0), \
             patch("monkeyhub_api.runtime.manager.time.monotonic", side_effect=lambda: now[0]):
            manager._watch(runtime)

        # Refresh counts after each pass: first idle read, skipped, changed,
        # skipped, woken, skipped, skipped.
        self.assertEqual(refreshes, [1, 1, 2, 2, 3, 3, 3])
        # The idle check read the watch's token; only the watch walked.
        self.assertTrue(walked_by)
        self.assertEqual({name for name in walked_by if not name.startswith("layout-watch:")}, set())
        # The observer let go of the project folder when it stopped.
        self.assertFalse(watched.running)

    def test_first_idle_fallback_after_an_open_reuses_the_opening_read(self):
        # After an open, the first idle fallback read all retained history
        # again although nothing had moved (#435). The opening read's token
        # answers for it; a separate client's write is still read.
        repository = self.project()
        manager = self.manager()
        runtime = self.runtime(manager, repository)
        _settle(repository.layout.root)
        runtime.binding.layout_watch().sync()
        now = [0.0]
        refreshes = []

        def heartbeat(_timeout):
            refreshes.append(refresh.call_count)
            step = len(refreshes)
            if step in (1, 2):
                now[0] += 30.0  # the first idle fallbacks after the open
            elif step == 3:
                repository.create_run("external-run")  # a separate client writes
                _settle(repository.layout.root)
                runtime.binding.layout_watch().sync()
                now[0] += 30.0
            else:
                manager._closing.set()

        runtime.wake.set()  # what open() asks of its observer
        with patch.object(manager, "refresh", wraps=manager.refresh) as refresh, \
             patch.object(runtime.wake, "wait", side_effect=heartbeat), \
             patch.object(manager, "_observe_work_copies", return_value=0), \
             patch("monkeyhub_api.runtime.manager.time.monotonic", side_effect=lambda: now[0]):
            manager._watch(runtime)
        # Opening read, two skipped fallbacks, then the external write read.
        self.assertEqual(refreshes, [1, 1, 1, 2])
        self.assertIn("external-run", runtime.binding.run_ids())

    def test_a_simulated_idle_minute_costs_the_observer_one_pass_per_idle_interval(self):
        repository = self.project()
        path = str(repository.layout.root)
        worker = WorkerSnapshot("studio:p0", "studio", self.fixture.PROJECT_ID, path, "instance", 4000,
                                "running", "ready", True, "http://127.0.0.1:9/", None)
        manager = self.manager([worker])
        runtime = self.runtime(manager, repository)
        runtime.last_workers = (("instance", "ready", True),)
        _settle(repository.layout.root)
        runtime.binding.layout_watch().sync()
        now, passes = [0.0], []

        def heartbeat(timeout):
            passes.append(timeout)
            now[0] += timeout
            if now[0] >= 60:
                manager._closing.set()
            return False

        with patch.object(manager, "refresh") as refresh, \
             patch.object(manager, "_follow_worker"), \
             patch.object(manager, "_work_copy_inputs", wraps=manager._work_copy_inputs) as inputs, \
             patch.object(runtime.wake, "wait", side_effect=heartbeat), \
             patch("monkeyhub_api.runtime.manager.time.monotonic", side_effect=lambda: now[0]):
            manager._watch(runtime)
        # One pass every idle interval; the old observer passed every second.
        self.assertLessEqual(len(passes), 60 / runtime_module._IDLE_HEARTBEAT_S)
        self.assertEqual(set(passes), {runtime_module._IDLE_HEARTBEAT_S})
        # The retained history is read once, not on every idle fallback, and
        # the work-copy inputs are compared on their own cadence only.
        self.assertEqual(refresh.call_count, 1)
        self.assertLessEqual(inputs.call_count, 1 + 60 / runtime_module._WORK_COPY_CHECK_S)

    def test_a_status_change_ends_the_idle_wait_without_asking_for_a_read(self):
        manager = self.manager()
        runtime = self.runtime(manager, self.project())
        supervisor = SimpleNamespace(listeners=[], add_listener=lambda listener: supervisor.listeners.append(listener))
        watched = ProjectRuntimeManager(SimpleNamespace(supervisor=supervisor), None)
        watched._projects[runtime.runtime_id] = runtime
        for change in (lambda: supervisor.listeners[0](), lambda: setattr(watched, "_clients", 1),
                       lambda: watched.chat_changed(SimpleNamespace(projectDir=runtime.project_dir))):
            change()
            self.assertTrue(runtime.wake.is_set())
            self.assertFalse(runtime.wake.take(), "A status change asked for a retained read")
            self.assertFalse(runtime.wake.is_set())
        runtime.wake.set()  # a Hub mutation still asks for one
        self.assertTrue(runtime.wake.take())

    def test_runtime_snapshot_of_five_projects_stays_in_milliseconds(self):
        workers, sessions, repositories = [], [], []
        for index in range(5):
            repository = self.project(f"p{index}")
            path = str(repository.layout.root)
            workers.append(WorkerSnapshot(f"studio:{index}", "studio", self.fixture.PROJECT_ID, path,
                                          f"instance-{index}", 4000 + index, "running", "ready", True,
                                          f"http://127.0.0.1:{9100 + index}/", None))
            for number in range(4):
                sessions.append(ChatSummary(id=f"chat-{index}-{number}", projectId=self.fixture.PROJECT_ID,
                                            projectDir=path, title=f"chat {number}", provider="codex",
                                            createdAt="2026-09-26T00:00:00+00:00", updatedAt="2026-09-26T00:00:00+00:00",
                                            archived=number == 3))
            repositories.append(repository)
        manager = self.manager(workers, sessions)
        for index, repository in enumerate(repositories):
            runtime = self.runtime(manager, repository, f"runtime-{index}")
            runtime.retained = manager._read_retained(runtime)
            for number in range(60):
                admission, _ = runtime.operations.admit(str(uuid4()), "POST", "/api/proposals", str(number).encode(),
                                                        retained=runtime.retained, source="studio", session_id=None)
                runtime.operations.replied(admission, HttpResult(200 if number % 5 else 409, b"{}", {}))

        snapshot = manager.snapshot()
        self.assertEqual(len(snapshot.projects), 5)
        self.assertEqual(sum(len(project.sessions) for project in snapshot.projects), 20)
        self.assertTrue(all(len(project.workers) == 1 for project in snapshot.projects))
        self.assertEqual([project.model_dump() for project in snapshot.projects],
                         [manager.project_snapshot(runtime).model_dump() for runtime in manager._projects.values()])
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            manager.snapshot()
            timings.append(time.perf_counter() - started)
        self.assertLess(min(timings), 0.2, f"GET /api/runtime took {min(timings) * 1000:.0f} ms at this size")

    def test_a_worker_request_never_loads_the_certificate_store(self):
        # A new opener per request made a new HTTPS context, which reads the system
        # certificate store: about 20 ms of every forwarded request on Windows.
        with _serving() as worker, patch.object(ssl.SSLContext, "load_default_certs",
                                                side_effect=AssertionError("certificate store loaded")):
            for _ in range(2):
                self.assertEqual(worker_http.request_http(worker, "/api/protocol").status, 200)

    def test_a_worker_request_connects_at_once_and_its_timeout_bounds_the_exchange(self):
        # A timed connect waits in select(), which Windows wakes a timer tick (about 15 ms)
        # late even when the local worker accepted at once.
        with _serving() as worker, patch("socket.create_connection", wraps=socket.create_connection) as connect:
            self.assertEqual(worker_http.request_http(worker, "/api/protocol", timeout=7).status, 200)
        connect.assert_called_once()
        self.assertIsNone(connect.call_args.args[1])

    def test_a_worker_that_accepts_and_never_answers_still_times_out(self):
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        outcome = []

        def read():
            try:
                worker_http.request_http(f"http://127.0.0.1:{listener.getsockname()[1]}", "/api/protocol", timeout=0.5)
            except OSError as error:
                outcome.append(error)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(10)
        self.assertFalse(reader.is_alive(), "A silent worker held the Hub's request past its timeout")
        self.assertIsInstance(outcome[0], TimeoutError)


@contextmanager
def _serving():
    """A worker stand-in on a loopback port: every GET answers 200 with a small JSON body."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    unittest.main()
