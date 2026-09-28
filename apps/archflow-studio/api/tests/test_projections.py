"""The projection cache: content keys, immutable blobs, the status table in the project index, leases,
bounded retries, enqueue on commit, collection and a real render process (#367)."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from archflow.adapters import occt_backend
from archflow.project.index import (
    ArtifactRow, CandidateRow, IndexCommit, IndexStamp, ProjectIndex, RunRows, StageRow, TreeRows,
    add_commit_listener,
)
from archflow_studio_api.application import projections
from archflow_studio_api.application.artifacts import ModelSource
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.application.projections import (
    CURRENT, DONE, ERROR, MODEL_LINES, PENDING, REST, TREE_RECIPE, BlobStore, ProjectionError, ProjectionQueue,
    RenderCancelled, RenderFailed, RenderRefused, RenderResult, StatusTable, SubprocessRenderer, projection_key,
    projection_spec, recipe_of, tree_model_sources,
)
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow_studio_api.transport.errors import StudioError

from .support import PROJECT_ID, advance_head, make_empty_project

ASSET = "a" * 64
STATE = "b" * 64
SOURCE = ModelSource("run-synthetic", STATE, ASSET)


def png_for(spec, shade=0) -> bytes:
    info = PngImagePlugin.PngInfo()
    for key, value in sorted(spec.png_text().items()):
        info.add_text(key, value)
    buffer = BytesIO()
    Image.new("L", (8, 8), shade).save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


class ScriptedRenderer:
    """Each call takes the next outcome: "ok", "block" (until cancelled) or a failure message."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.sources = []
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.gate = threading.Event()

    def render(self, spec, *, timeout_s):
        self.calls.append(spec.key)
        self.sources.append(spec.source)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        self.started.set()
        if outcome == "ok":
            return RenderResult(png_for(spec), 0.25, 0.5)
        if outcome == "block":
            self.cancelled.wait(10)
            raise RenderCancelled("cancelled")
        if outcome == "wait":
            self.gate.wait(10)
            return RenderResult(png_for(spec), 0.25, 0.5)
        if outcome == "refuse":
            raise RenderRefused("MODEL_SOURCE_MISMATCH: gone")
        raise RenderFailed(outcome)

    def cancel(self):
        self.cancelled.set()

    def close(self):
        pass


class StubProjector:
    """The rows a test names: runs with 3dm model artifacts, stages, candidates and a working position."""

    version = "stub-projector@1"

    def __init__(self):
        self.models: dict[str, list[ModelSource]] = {}
        self.stages: list[tuple[str, str]] = []
        self.candidates: list[str] = []
        self.current: str | None = None

    def run_ids(self):
        return tuple(sorted(set(self.models) | set(self.candidates)))

    def project_run(self, run_id):
        artifacts = tuple(ArtifactRow(source.asset_sha256, "3dm", "composed", True, {
            "model_source": {"run_id": source.run_id, "state_digest": source.state_digest,
                             "asset_sha256": source.asset_sha256}}) for source in self.models.get(run_id, ()))
        candidate = CandidateRow(None, {}) if run_id in self.candidates else None
        return RunRows(run_id, {}, artifacts=artifacts, candidate=candidate)

    def project_tree(self):
        return TreeRows({}, tuple(StageRow("main", f"stage:{index}", run_id, {"model_sha256": sha})
                                  for index, (run_id, sha) in enumerate(self.stages)))

    def project_working(self):
        return {"current": self.current, "active": {}, "runsDigest": ""}


def open_index(directory: Path, projector: StubProjector | None = None) -> ProjectIndex:
    """A project index in ``directory``, loaded by this thread (no keeper)."""

    index = ProjectIndex(directory, projector=projector or StubProjector(),
                         stamp=lambda: IndexStamp("stub-projector@1", "project-a", "manifest"))
    index.load([], time.time_ns())
    return index


def reproject(index: ProjectIndex) -> None:
    """Apply what the stub projector now says, as the keeper would after a change."""

    index.apply(None, 0, areas={"branches", "working", "runs", *(f"run:{run}" for run in index._projector.run_ids())})


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class KeyTests(unittest.TestCase):
    RECIPE = {"view": "axon", "size": 512, "style": "lines"}

    def key(self, **changes):
        fields = {"input_sha256": ASSET, "kind": MODEL_LINES, "recipe": self.RECIPE, "renderer": "mesh-lines-1",
                  **changes}
        return projection_key(fields.pop("input_sha256"), fields.pop("kind"), fields.pop("recipe"), **fields)

    def test_the_key_is_stable_and_names_no_run_or_time(self):
        self.assertEqual(self.key(), self.key())
        self.assertRegex(self.key(), r"^[0-9a-f]{64}$")
        other_run = ModelSource("run-other", "c" * 64, ASSET)
        self.assertEqual(projection_spec(SOURCE).key, projection_spec(other_run).key)
        self.assertEqual(projection_spec(SOURCE, recipe={}).key,
                         projection_spec(SOURCE, recipe={"view": "axon", "size": 512, "style": "lines"}).key)

    def test_every_output_affecting_field_changes_the_key(self):
        base = self.key()
        changed = {
            "input": self.key(input_sha256="d" * 64),
            "kind": self.key(kind="other-kind"),
            "view": self.key(recipe={**self.RECIPE, "view": "top"}),
            "size": self.key(recipe={**self.RECIPE, "size": 256}),
            "style": self.key(recipe={**self.RECIPE, "style": "shaded"}),
            "renderer": self.key(renderer="mesh-lines-2"),
            "salt": self.key(salt="archflow-projection-2"),
        }
        for field, key in changed.items():
            with self.subTest(field=field):
                self.assertNotEqual(key, base)
        self.assertEqual(len(set(changed.values())), len(changed))

    def test_the_renderer_version_and_salt_reach_the_key_of_a_spec(self):
        before = projection_spec(SOURCE).key
        with patch("monkeydiagram.mesh_views.RENDERER_VERSION", "mesh-lines-test"):
            self.assertNotEqual(projection_spec(SOURCE).key, before)
        with patch.object(projections, "SALT", "archflow-projection-test"):
            self.assertNotEqual(projection_spec(SOURCE).key, before)

    def test_every_pipeline_input_reaches_the_key(self):
        from archflow_studio_api.application import drawings
        from monkeydiagram import mesh_views

        before = projection_spec(SOURCE)
        right, up, look = drawings._VIEW_FRAMES["axon"]
        real_version = mesh_views.metadata.version
        changes = {
            "frame": patch.dict(drawings._VIEW_FRAMES, {"axon": (right, up, tuple(-x for x in look))}),
            "margin": patch.object(drawings, "VIEW_MARGIN", 0.1),
            "tessellation": patch.object(drawings, "AXON_CHORD_PX", 0.25),
            "angular deflection": patch.object(mesh_views, "ANGULAR_DEFLECTION", 0.25),
            "crease": patch.object(mesh_views, "CREASE_DEGREES", 20.0),
            "supersample": patch.object(mesh_views, "_SUPERSAMPLE", 3),
            "depth tolerance": patch.object(mesh_views, "_DEPTH_TOLERANCE", 2.0),
            "png": patch.object(mesh_views, "_PNG_COMPRESS_LEVEL", 9),
            "OCP": patch.object(mesh_views.metadata, "version",
                                lambda name: "7.9.9" if name == "cadquery-ocp" else real_version(name)),
        }
        keys = set()
        for name, change in changes.items():
            with self.subTest(input=name), change:
                spec = projection_spec(SOURCE)
                self.assertNotEqual(spec.key, before.key)
                self.assertNotEqual(spec.renderer, before.renderer)
                keys.add(spec.key)
        self.assertEqual(len(keys), len(changes))
        self.assertEqual(projection_spec(SOURCE).key, before.key)
        pipeline = json.loads(before.png_text()["archflow:pipeline"])
        self.assertEqual((pipeline["margin"], pipeline["chordPx"], pipeline["mesh"]["renderer"]),
                         (drawings.VIEW_MARGIN, drawings.AXON_CHORD_PX, mesh_views.RENDERER_VERSION))
        self.assertIn("cadquery-ocp", pipeline["mesh"]["libraries"])

    def test_recipes_are_complete_and_refuse_unknown_fields_and_values(self):
        self.assertEqual(recipe_of(MODEL_LINES, {"size": 256}), {"view": "axon", "size": 256, "style": "lines"})
        for kind, fields in ((MODEL_LINES, {"colour": "red"}), (MODEL_LINES, {"size": 333}),
                             (MODEL_LINES, {"view": "top"}), ("screenshot", {})):
            with self.subTest(kind=kind, fields=fields), self.assertRaises(ProjectionError):
                recipe_of(kind, fields)


class BlobTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.blobs = BlobStore(self.root / "projections")

    def test_a_blob_is_named_by_its_digest_and_written_then_renamed(self):
        data = png_for(projection_spec(SOURCE))
        with patch.object(projections.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.blobs.put(data)
        self.assertEqual(list((self.root / "projections" / "blobs").iterdir()), [], "nothing half-written in place")
        self.assertEqual(list((self.root / "projections" / "tmp").iterdir()), [], "the temp file is removed")
        sha = self.blobs.put(data)
        self.assertEqual(self.blobs.path(sha).name, f"{sha}.png")
        self.assertEqual(self.blobs.read(sha), data)
        self.assertEqual(self.blobs.put(data), sha)

    def test_a_missing_or_corrupt_blob_is_a_miss(self):
        sha = self.blobs.put(b"one projection")
        self.blobs.path(sha).write_bytes(b"tampered")
        self.assertIsNone(self.blobs.read(sha))
        self.assertIsNone(self.blobs.read("f" * 64))
        with self.assertRaises(ProjectionError):
            self.blobs.path("../escape")


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.clock = Clock()
        self.projector = StubProjector()
        self.index = open_index(self.root / "index", self.projector)
        self.addCleanup(self.index.close)

    def queue(self, renderer, index=None, **options):
        queue = ProjectionQueue(self.root / "projections", lambda: renderer, StatusTable(index or self.index),
                                clock=self.clock, **options)
        self.addCleanup(queue.shutdown)
        return queue

    def settle(self, queue):
        self.assertTrue(queue.wait_idle(10))

    def test_a_miss_is_queued_drawn_once_and_then_served_from_the_blob(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer)
        spec = projection_spec(SOURCE)
        first = queue.request(spec)
        self.assertEqual(first.status, PENDING)
        self.settle(queue)
        done = queue.request(spec)
        self.assertEqual((done.status, done.attempts, done.load_ms, done.render_ms), (DONE, 1, 250, 500))
        with Image.open(BytesIO(queue.blobs.read(done.blob_sha256))) as image:
            self.assertEqual(image.text["archflow:projection-key"], spec.key)
            self.assertEqual(image.text["archflow:input-sha256"], ASSET)
            self.assertEqual(image.text["archflow:renderer"], spec.renderer)
        self.assertEqual(renderer.calls, [spec.key])

    def test_the_status_survives_a_restart_and_a_done_key_is_not_drawn_again(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        queue.shutdown()
        self.index.close()
        reopened = open_index(self.root / "index", self.projector)
        self.addCleanup(reopened.close)
        again = ScriptedRenderer()
        restarted = self.queue(again, index=reopened)
        row = restarted.request(spec)
        self.settle(restarted)
        self.assertEqual((row.status, row.attempts), (DONE, 1), "the row and its blob were found again")
        self.assertEqual(again.calls, [], "nothing is drawn twice")

    def test_a_rebuild_of_the_index_keeps_the_status_rows(self):
        queue = self.queue(ScriptedRenderer())
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        blob = queue.store.get(spec.key).blob_sha256
        epoch = self.index.token.epoch
        self.index.rebuild([], time.time_ns())
        self.assertNotEqual(self.index.token.epoch, epoch)
        row = queue.store.get(spec.key)
        self.assertEqual((row.status, row.blob_sha256), (DONE, blob))
        with self.index.snapshot() as snapshot:
            self.assertIn(f"projections:{spec.key}", {entity["id"] for entity in snapshot.entities()})

    def test_a_done_projection_is_an_index_entity_and_moves_the_revision(self):
        announced = []
        self.index.projection_listener = lambda: announced.append(self.index.token.revision)
        queue = self.queue(ScriptedRenderer())
        spec = projection_spec(SOURCE)
        before = self.index.token.revision
        queue.request(spec)
        self.assertEqual(self.index.token.revision, before, "a queued row moves nothing a client shows")
        self.settle(queue)
        self.assertEqual(self.index.token.revision, before + 1)
        self.assertEqual(announced, [before + 1])
        self.assertEqual(self.index.last_commit.domains, frozenset({"projections"}))
        with self.index.snapshot() as snapshot:
            upserts, deletes = snapshot.changes(before)
        self.assertEqual(deletes, [])
        (entity,) = upserts
        blob = queue.store.get(spec.key).blob_sha256
        self.assertEqual((entity["id"], entity["domain"], entity["rev"]), (f"projections:{spec.key}", "projections", before + 1))
        self.assertEqual(entity["body"], {"key": spec.key, "inputSha256": ASSET, "kind": MODEL_LINES,
                                          "recipe": dict(spec.recipe), "renderer": spec.renderer, "blobSha256": blob})
        self.assertNotIn("source", entity["body"], "no requester's source reaches other clients")

    def test_deleting_the_cache_directory_only_draws_again(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        blob = queue.request(spec).blob_sha256
        shutil.rmtree(self.root / "projections")
        self.assertEqual(queue.request(spec).status, PENDING)
        self.settle(queue)
        again = queue.request(spec)
        self.assertEqual((again.status, again.blob_sha256), (DONE, blob))
        self.assertEqual(len(renderer.calls), 2)

    def test_a_lost_lease_is_reclaimed_after_its_timeout_and_a_live_one_is_left_alone(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer, timeout_s=60)
        queue.start()  # a running worker, with nothing to reclaim yet
        spec = projection_spec(SOURCE)
        queue.store.enqueue(spec, now=self.clock())
        claimed_at = self.clock()
        queue.store.claim(spec.key, now=claimed_at)  # another worker took it and was lost
        self.clock.now += queue.lease_s - 1
        row = queue.request(spec)
        self.settle(queue)
        self.assertEqual((row.status, queue.store.get(spec.key).claimed_at), (PENDING, claimed_at),
                         "a live lease is left alone")
        self.assertEqual(renderer.calls, [], "the running worker was not handed a leased job")
        self.clock.now += 1
        self.assertEqual(queue.request(spec).status, PENDING)
        self.settle(queue)
        row = queue.store.get(spec.key)
        self.assertEqual((row.status, row.attempts), (DONE, 2), "the lost attempt still counts")
        self.assertEqual(renderer.calls, [spec.key])

    def test_a_restart_reclaims_every_lease(self):
        store = StatusTable(self.index)
        spec = projection_spec(SOURCE)
        store.enqueue(spec, now=self.clock())
        store.claim(spec.key, now=self.clock())  # the process died while drawing it
        self.index.close()
        reopened = open_index(self.root / "index", self.projector)
        self.addCleanup(reopened.close)
        self.assertIsNotNone(StatusTable(reopened).get(spec.key).claimed_at, "the lease was kept")
        renderer = ScriptedRenderer()
        restarted = self.queue(renderer, index=reopened, timeout_s=60)
        restarted.start()
        self.settle(restarted)
        self.assertEqual((restarted.store.get(spec.key).status, renderer.calls), (DONE, [spec.key]))

    def refusing(self, *refused_runs):
        def check(spec):
            if spec.source.run_id in refused_runs:
                raise StudioError(409, "MODEL_SOURCE_MISMATCH", "stale")
        return check

    def test_every_request_checks_its_source_even_for_a_done_key(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer, check=self.refusing("run-stale"))
        stale = projection_spec(ModelSource("run-stale", "c" * 64, ASSET))
        with self.assertRaises(StudioError):
            queue.request(stale)
        self.assertIsNone(queue.store.get(stale.key), "a refused request records nothing")
        good = projection_spec(SOURCE)
        self.assertEqual(good.key, stale.key, "both runs hold the same model asset")
        queue.request(good)
        self.settle(queue)
        self.assertEqual(queue.request(good).status, DONE)
        with self.assertRaises(StudioError, msg="a fabricated source is refused though the key is done"):
            queue.request(stale)
        self.assertEqual(renderer.sources, [SOURCE])

    def test_reading_a_key_checks_the_source_before_it_is_queued_again(self):
        queue = self.queue(ScriptedRenderer(), check=self.refusing())
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        shutil.rmtree(self.root / "projections")
        queue.check = self.refusing(SOURCE.run_id)
        with self.assertRaises(StudioError):
            queue.read(spec.key)
        self.assertEqual(queue.store.get(spec.key).status, DONE, "nothing was queued for a source that went stale")
        self.assertIsNone(queue.read("0" * 64))

    def test_an_idle_retry_checks_its_source_first(self):
        renderer = ScriptedRenderer("broken")
        queue = self.queue(renderer, check=self.refusing())
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        self.clock.now += projections.BACKOFF_S[0]
        queue.check = self.refusing(SOURCE.run_id)
        queue._idle()
        self.settle(queue)
        self.assertEqual((queue.store.get(spec.key).status, len(renderer.calls)), (ERROR, 1),
                         "a source that no longer checks is not retried")
        queue.check = self.refusing()
        queue._idle()
        self.settle(queue)
        self.assertEqual((queue.store.get(spec.key).status, len(renderer.calls)), (DONE, 2))

    def test_a_source_the_render_process_refuses_uses_no_attempt(self):
        renderer = ScriptedRenderer("refuse", "refuse", "refuse", "refuse", "ok")
        queue = self.queue(renderer, max_attempts=3)
        spec = projection_spec(SOURCE)
        for _ in range(4):
            queue.request(spec)
            self.settle(queue)
            self.assertIsNone(queue.store.get(spec.key), "a refusal leaves no row, and no error")
        queue.request(spec)
        self.settle(queue)
        row = queue.store.get(spec.key)
        self.assertEqual((row.status, row.attempts), (DONE, 1))

    def test_a_retry_draws_from_the_newest_requester_not_the_first(self):
        renderer = ScriptedRenderer("broken", "ok")
        queue = self.queue(renderer)
        first = projection_spec(ModelSource("run-first", "c" * 64, ASSET))
        queue.request(first)
        self.settle(queue)
        self.assertEqual(queue.request(first).status, ERROR)
        self.clock.now += projections.BACKOFF_S[0]
        queue.check = Mock()
        later = projection_spec(SOURCE)
        self.assertEqual(queue.request(later).status, PENDING)
        queue.check.assert_called_once_with(later)
        self.settle(queue)
        row = queue.request(later)
        self.assertEqual((row.status, row.spec.source), (DONE, SOURCE))
        self.assertEqual(renderer.sources, [first.source, SOURCE])

    def test_two_requests_retrying_one_key_at_once_do_not_fail(self):
        queue = self.queue(ScriptedRenderer("broken"))
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        self.clock.now += projections.BACKOFF_S[0]
        errored = queue.store.get(spec.key)
        # The other request's retry lands between this request's read and its own retry.
        with patch.object(queue.store, "retry", return_value=None):
            self.assertEqual(queue.request(spec), errored)

    def test_a_store_error_does_not_stop_the_worker(self):
        store = StatusTable(self.index)
        finish = store.finish
        broken = [True]

        def flaky_finish(key, **fields):
            if broken.pop() if broken else False:
                raise OSError("index unavailable")
            finish(key, **fields)

        store.finish = flaky_finish
        renderer = ScriptedRenderer()
        queue = ProjectionQueue(self.root / "projections", lambda: renderer, store, clock=self.clock)
        self.addCleanup(queue.shutdown)
        first, second = projection_spec(SOURCE), projection_spec(ModelSource("run", STATE, "e" * 64))
        with self.assertLogs(projections.__name__, "ERROR"):
            queue.request(first)
            self.settle(queue)
        queue.request(second)
        self.settle(queue)
        self.assertEqual(store.get(second.key).status, DONE, "the worker still runs")

    def test_an_idle_pass_that_fails_does_not_stop_the_worker(self):
        queue = self.queue(ScriptedRenderer())
        queue.start()
        with patch.object(queue.store, "reclaim", side_effect=OSError("index unavailable")), \
                self.assertLogs(projections.__name__, "ERROR"):
            with queue._wake:
                queue._wake.notify_all()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and queue._running != projections._IDLE:
                time.sleep(0.01)
            self.settle(queue)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        self.assertEqual(queue.store.get(spec.key).status, DONE)

    def test_failures_retry_with_backoff_a_bounded_number_of_times(self):
        renderer = ScriptedRenderer("broken", "broken", "broken", "ok")
        queue = self.queue(renderer, max_attempts=3)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        row = queue.request(spec)
        self.assertEqual((row.status, row.attempts, row.error), (ERROR, 1, "broken"))
        self.assertEqual(row.next_attempt_at, self.clock.now + projections.BACKOFF_S[0])
        self.clock.now += projections.BACKOFF_S[0] - 1
        self.assertEqual(queue.request(spec).status, ERROR, "not before the backoff")
        self.clock.now += 1
        self.assertEqual(queue.request(spec).status, PENDING)
        self.settle(queue)
        self.clock.now += projections.BACKOFF_S[1]
        queue.request(spec)
        self.settle(queue)
        row = queue.request(spec)
        self.assertEqual((row.status, row.attempts, row.next_attempt_at), (ERROR, 3, None))
        self.clock.now += 10 * projections.BACKOFF_S[-1]
        self.assertEqual(queue.request(spec).status, ERROR)
        self.settle(queue)
        self.assertEqual(len(renderer.calls), 3, "three attempts, then it stays an error")

    def test_a_new_renderer_version_retries_old_failures_one_request_at_a_time(self):
        renderer = ScriptedRenderer(*["broken"] * 3)
        queue = self.queue(renderer, max_attempts=1)
        specs = [projection_spec(ModelSource("run", STATE, f"{index:064x}")) for index in range(3)]
        for spec in specs:
            queue.request(spec)
        self.settle(queue)
        self.assertEqual({queue.request(spec).status for spec in specs}, {ERROR})
        with patch("monkeydiagram.mesh_views.RENDERER_VERSION", "mesh-lines-next"):
            renewed = projection_spec(specs[0].source)
            self.assertNotEqual(renewed.key, specs[0].key)
            self.assertEqual(queue.request(renewed).status, PENDING)
            self.settle(queue)
            self.assertEqual(queue.request(renewed).status, DONE)
            self.assertEqual(len(renderer.calls), 4, "only the requested projection was drawn again")
            queue.collect()
            self.assertEqual([row.key for row in queue.store.rows()], [renewed.key],
                             "failures of the old renderer are dropped")

    def test_a_new_renderer_replaces_done_pictures_one_at_a_time(self):
        queue = self.queue(ScriptedRenderer())
        specs = [projection_spec(ModelSource("run", STATE, f"{index:064x}")) for index in range(2)]
        for spec in specs:
            queue.request(spec)
        self.settle(queue)
        with patch("monkeydiagram.mesh_views.RENDERER_VERSION", "mesh-lines-next"):
            renewed = projection_spec(specs[0].source)
            queue.request(renewed)
            self.settle(queue)
            queue.collect()
            kept = {row.key for row in queue.store.rows()}
        self.assertEqual(kept, {renewed.key, specs[1].key},
                         "an old picture goes once its replacement is drawn, and not before")
        with self.index.snapshot() as snapshot:
            self.assertEqual({entity["id"] for entity in snapshot.entities() if entity["domain"] == "projections"},
                             {f"projections:{renewed.key}", f"projections:{specs[1].key}"})

    def test_a_row_the_old_renderer_never_drew_is_not_drawn_by_the_new_one(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer)
        spec = projection_spec(SOURCE)
        queue.store.enqueue(spec, now=self.clock())
        with patch("monkeydiagram.mesh_views.RENDERER_VERSION", "mesh-lines-next"):
            queue.start()
            self.settle(queue)
            queue._run(spec.key)
            self.assertEqual(renderer.calls, [])
            self.assertIsNone(queue.store.get(spec.key))

    def test_a_cancel_is_not_a_failure(self):
        renderer = ScriptedRenderer("block", "ok")
        queue = self.queue(renderer)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.assertTrue(renderer.started.wait(10))
        queue.cancel(spec.key)
        self.settle(queue)
        self.assertIsNone(queue.store.get(spec.key), "nothing recorded, no attempt kept")
        queue.request(spec)
        self.settle(queue)
        row = queue.request(spec)
        self.assertEqual((row.status, row.attempts, row.error), (DONE, 1, None))

    def test_the_collector_keeps_what_rows_reach_and_waits_out_the_grace_window(self):
        queue = self.queue(ScriptedRenderer(), grace_s=100)
        spec = projection_spec(SOURCE)
        queue.request(spec)
        self.settle(queue)
        kept = queue.request(spec).blob_sha256
        orphan, young = queue.blobs.put(b"orphan"), queue.blobs.put(b"young")
        old = self.clock.now - 101
        for sha in (kept, orphan):
            os.utime(queue.blobs.path(sha), (old, old))
        os.utime(queue.blobs.path(young), (self.clock.now, self.clock.now))
        self.assertEqual(queue.collect(), (f"{orphan}.png",))
        self.assertIsNotNone(queue.blobs.read(kept), "a live row's blob is never removed, however old")
        self.assertIsNotNone(queue.blobs.read(young))

    def test_the_collector_drops_rows_no_artifact_reaches_after_the_grace_window(self):
        self.projector.models = {"run": [SOURCE]}
        reproject(self.index)
        queue = self.queue(ScriptedRenderer(), grace_s=100)
        reached, gone = projection_spec(SOURCE), projection_spec(ModelSource("run-gone", STATE, "e" * 64))
        for spec in (reached, gone):
            queue.request(spec)
        self.settle(queue)
        gone_blob = queue.store.get(gone.key).blob_sha256
        self.clock.now += 99
        queue.collect()
        self.assertEqual({row.key for row in queue.store.rows()}, {reached.key, gone.key}, "within the grace window")
        self.clock.now += 1
        queue.collect()
        self.assertEqual({row.key for row in queue.store.rows()}, {reached.key},
                         "an input no artifact names is unreachable")
        old = self.clock.now - 101
        os.utime(queue.blobs.path(gone_blob), (old, old))
        queue.collect()
        self.assertIsNone(queue.blobs.read(gone_blob))
        self.assertIsNotNone(queue.blobs.read(queue.store.get(reached.key).blob_sha256))

    def test_a_commit_queues_the_tree_current_first_then_what_readers_show_then_the_rest(self):
        sources = {name: ModelSource(f"run-{name}", STATE, sha * 64)
                   for name, sha in (("old", "1"), ("stage", "2"), ("current", "3"), ("option", "4"), ("shown", "5"))}
        self.projector.models = {source.run_id: [source] for source in sources.values()}
        self.projector.stages = [("run-old", "1" * 64), ("run-stage", "2" * 64)]
        self.projector.candidates = ["run-option", "run-shown"]
        self.projector.current = "run-current"
        reproject(self.index)
        self.assertEqual(tree_model_sources(self.index), [
            (CURRENT, sources["current"]), (REST, sources["stage"]), (REST, sources["old"]),
            (REST, sources["option"]), (REST, sources["shown"])], "newest Stage first")
        renderer = ScriptedRenderer("wait")
        queue = self.queue(renderer, tree_sources=lambda: tree_model_sources(self.index))
        queue.start()  # its first pass is the commits it missed; the current model is drawn first
        self.assertTrue(renderer.started.wait(10))
        queue.request(projection_spec(sources["shown"], recipe=TREE_RECIPE))  # a reader shows it meanwhile
        renderer.gate.set()
        self.settle(queue)
        order = {projection_spec(source, recipe=TREE_RECIPE).key: name for name, source in sources.items()}
        self.assertEqual([order[key] for key in renderer.calls], ["current", "shown", "stage", "old", "option"])
        self.assertEqual({row.status for row in queue.store.rows()}, {DONE})
        new = ModelSource("run-new", STATE, "6" * 64)
        self.projector.models[new.run_id] = [new]
        self.projector.candidates.append(new.run_id)
        reproject(self.index)
        queue.committed()
        self.settle(queue)
        self.assertEqual([order.get(key, key) for key in renderer.calls[5:]],
                         [projection_spec(new, recipe=TREE_RECIPE).key], "a commit draws only what is not drawn")

    def test_the_same_model_is_drawn_at_its_next_size_first(self):
        renderer = ScriptedRenderer("block")
        queue = self.queue(renderer)
        queue.request(projection_spec(SOURCE, recipe={"size": 1024}))
        self.assertTrue(renderer.started.wait(10))
        other = projection_spec(ModelSource("run", STATE, "e" * 64))
        queue.request(other)
        small = projection_spec(SOURCE, recipe={"size": 512})
        queue.request(small, priority=REST)
        renderer.cancel()
        self.settle(queue)
        self.assertEqual(renderer.calls[1:], [small.key, other.key])


class RenderProcessFailureTests(unittest.TestCase):
    def test_a_process_that_dies_reports_the_end_of_its_stderr(self):
        real_popen = projections.subprocess.Popen

        def broken(arguments, **options):
            script = "import sys; sys.stderr.write('render boot failed\\n'); sys.exit(3)"
            return real_popen([arguments[0], "-c", script], **options)

        renderer = SubprocessRenderer(Path(tempfile.gettempdir()))
        self.addCleanup(renderer.close)
        with patch.object(projections.subprocess, "Popen", side_effect=broken):
            with self.assertRaisesRegex(RenderFailed, "exited with 3: render boot failed"):
                renderer.render(projection_spec(SOURCE), timeout_s=60)
        self.assertFalse(renderer.ready.is_set())


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        make_empty_project(self.root)
        self.settings = StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off", cache_dir=self.root / "cache")
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.stop_index_events)
        binding = bound_project(self.app.state)
        self.addCleanup(binding.close)
        keeper = binding.await_index(30)
        self.assertIsNotNone(keeper, "the index loads")
        self.index = keeper.index
        self.renderer = ScriptedRenderer()
        # The synthetic source names no real run; the render process tests check real ones.
        self.check = Mock()
        self.app.state.projections = ProjectionQueue(self.root / "cache" / "projections", lambda: self.renderer,
                                                     StatusTable(self.index), check=self.check)
        self.addCleanup(self.app.state.projections.shutdown)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_status_is_not_cached_and_the_blob_is_immutable(self):
        params = {**SOURCE.to_dict(), "size": 256}
        pending = self.client.get("/api/projections", params=params)
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.headers["cache-control"], "no-store")
        body = pending.json()
        self.assertEqual((body["status"], body["blobUrl"], body["recipe"], body["source"]),
                         ("pending", None, {"view": "axon", "size": 256, "style": "lines"}, SOURCE.to_dict()))
        self.assertTrue(self.app.state.projections.wait_idle(10))
        done = self.client.get(f"/api/projections/{body['key']}")
        self.assertEqual(done.headers["cache-control"], "no-store")
        done = done.json()
        self.assertEqual((done["status"], done["inputSha256"], done["loadMs"], done["source"]),
                         ("done", ASSET, 250, None), "a key alone is not answered with anyone's source")
        blob = self.client.get(done["blobUrl"])
        self.assertEqual(blob.status_code, 200)
        self.assertEqual(blob.headers["content-type"], "image/png")
        self.assertEqual(blob.headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertEqual(blob.content, png_for(projection_spec(SOURCE, recipe={"size": 256})))
        self.assertTrue((self.root / "cache" / "projections" / "blobs" / f"{done['blobSha256']}.png").is_file())
        self.assertFalse(any("projections" in path.parts for path in (self.root / PROJECT_ID).rglob("*")),
                         "nothing is written into the project")

    def test_a_done_projection_reaches_clients_as_an_index_commit_and_an_entity(self):
        events = self.app.state.events
        before = self.client.get("/api/index").json()
        pending = self.client.get("/api/projections", params=SOURCE.to_dict()).json()
        self.assertTrue(self.app.state.projections.wait_idle(10))
        committed = [event for event in events.replay() if event["type"] == "index.committed"
                     and event["revision"] > before["revision"]]
        self.assertEqual([event["domains"] for event in committed], [["projections"]])
        changes = self.client.get("/api/index", params={"since": before["revision"], "epoch": before["epoch"]}).json()
        (entity,) = changes["upserts"]
        self.assertEqual((entity["id"], entity["body"]["blobSha256"]),
                         (f"projections:{pending['key']}", self.app.state.projections.store.get(pending["key"]).blob_sha256))

    def test_a_source_that_cannot_be_drawn_is_refused_on_every_request(self):
        self.check.side_effect = StudioError(409, "MODEL_SOURCE_MISMATCH", "The model source does not match.")
        refused = self.client.get("/api/projections", params=SOURCE.to_dict())
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "MODEL_SOURCE_MISMATCH"))
        self.assertEqual(self.app.state.projections.store.rows(), ())
        (spec,), _ = self.check.call_args
        self.assertEqual((spec.source, spec.recipe["view"]), (SOURCE, "axon"))
        self.check.side_effect = None
        key = self.client.get("/api/projections", params=SOURCE.to_dict()).json()["key"]
        self.assertTrue(self.app.state.projections.wait_idle(10))
        fabricated = {**SOURCE.to_dict(), "stateDigest": "c" * 64}
        self.check.side_effect = lambda spec: (_ for _ in ()).throw(
            StudioError(409, "MODEL_SOURCE_MISMATCH", "no")) if spec.source.state_digest == "c" * 64 else None
        refused = self.client.get("/api/projections", params=fabricated)
        self.assertEqual(refused.status_code, 409, "a done key is no answer to a fabricated source")
        self.assertEqual(self.client.get(f"/api/projections/{key}").json()["status"], "done")

    def test_unknown_keys_blobs_and_recipes_are_refused(self):
        self.assertEqual(self.client.get(f"/api/projections/{'0' * 64}").json()["code"], "PROJECTION_UNKNOWN")
        missing = self.client.get(f"/api/projections/blobs/{'0' * 64}")
        self.assertEqual((missing.status_code, missing.json()["code"]), (404, "PROJECTION_BLOB_NOT_FOUND"))
        self.assertEqual(self.client.get("/api/projections/blobs/not-a-digest").status_code, 404)
        invalid = self.client.get("/api/projections", params={**SOURCE.to_dict(), "size": 333})
        self.assertEqual((invalid.status_code, invalid.json()["code"]), (422, "PROJECTION_RECIPE_INVALID"))

    def test_the_default_queue_keeps_its_rows_in_the_index_and_its_blobs_in_the_cache(self):
        # A cache of its own: this test's other runtime holds the first one's index.
        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off",
                                        cache_dir=self.root / "cache-2"))
        self.addCleanup(app.state.stop_index_events)
        with TestClient(app) as client:
            client.get(f"/api/projections/{'0' * 64}")
            self.assertEqual(app.state.projections.cache_root, self.root / "cache-2" / "projections")
            self.assertIs(app.state.projections.store.index, bound_project(app.state).await_index(30).index)
        self.assertIsNone(app.state.projections, "shutdown closes the queue")
        default = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.assertFalse(default.project_cache_dir.is_relative_to(self.root / PROJECT_ID))

    def test_a_runtime_without_an_index_answers_that_it_keeps_no_projections(self):
        app = create_app(StudioSettings(project_dir=self.root / PROJECT_ID, cad_export="off"))
        with TestClient(app) as client:
            refused = client.get("/api/projections", params=SOURCE.to_dict())
        self.assertEqual((refused.status_code, refused.json()["code"]), (503, "PROJECTION_INDEX_UNAVAILABLE"))


@unittest.skipUnless(occt_backend.occt_available(), "cadquery-ocp is not installed")
class RenderProcessTests(unittest.TestCase):
    """The real renderer: a low-priority process drawing one exact committed model."""

    @classmethod
    def setUpClass(cls):
        from .test_candidate import CandidateTestCase

        class Maker(CandidateTestCase):
            def runTest(self):
                pass

        maker = Maker()
        maker.setUp()
        cls.maker = maker
        maker.client.close()
        maker.app = create_app(StudioSettings(project_dir=maker.root / PROJECT_ID, cad_export="occt"))
        maker.client = TestClient(maker.app)
        accepted, job = maker.run_candidate("set height to 2.2", elementId="portico-base")
        assert job["status"] == "succeeded", job
        candidate = maker.client.get(f"/api/candidates/{accepted['candidateId']}").json()
        cls.model = ModelSource.from_dict(next(row for row in candidate["artifacts"] if row["format"] == "3dm")["modelSource"])
        maker.client.close()
        cls.project_dir = maker.root / PROJECT_ID

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.maker.root, True)

    def renderer(self, **options):
        renderer = SubprocessRenderer(self.project_dir, **options)
        self.addCleanup(renderer.close)
        return renderer

    def test_the_process_draws_a_self_describing_deterministic_png(self):
        spec = projection_spec(self.model, recipe={"size": 256})
        renderer = self.renderer()
        first = renderer.render(spec, timeout_s=120)
        second = renderer.render(spec, timeout_s=120)
        self.assertEqual(first.png, second.png, "no time or random id in the bytes")
        self.assertGreater(first.load_s, 0)
        self.assertGreater(first.render_s, 0)
        with Image.open(BytesIO(first.png)) as image:
            self.assertEqual((image.format, image.mode, max(image.size)), ("PNG", "L", 256))
            self.assertLess(image.getextrema()[0], 128, "the model leaves lines")
            self.assertEqual(image.text, {key: value for key, value in spec.png_text().items()})

    def test_a_timeout_kills_the_process_and_the_next_job_starts_a_new_one(self):
        renderer = self.renderer()
        spec = projection_spec(self.model, recipe={"size": 128})
        with self.assertRaisesRegex(RenderFailed, "timed out"):
            renderer.render(spec, timeout_s=0.01)
        self.assertEqual(max(Image.open(BytesIO(renderer.render(spec, timeout_s=120).png)).size), 128)

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads the render process's memory from /proc")
    def test_the_memory_cap_stops_the_process_during_a_render(self):
        def memory_mb(process, field):
            for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
                if line.startswith(field + ":"):
                    return int(line.split()[1]) / 1024
            raise AssertionError(field)

        spec = projection_spec(self.model, recipe={"size": 1024})
        probe = self.renderer()
        probe._process = probe._start()
        self.assertTrue(probe.ready.wait(60))
        started = memory_mb(probe._process, "VmRSS")
        probe.render(spec, timeout_s=120)
        peak = memory_mb(probe._process, "VmHWM")
        if peak - started < 40:
            self.skipTest(f"a render grows the process by only {peak - started:.0f} MB here")
        cap = round(started + (peak - started) / 4)
        capped = self.renderer(memory_cap_mb=cap)
        with self.assertRaisesRegex(RenderFailed, f"memory cap of {cap} MB exceeded"):
            capped.render(spec, timeout_s=120)
        self.assertTrue(capped.ready.is_set(), "the process started and was stopped while rendering")

    def test_a_stale_source_is_refused_and_does_not_poison_the_key(self):
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        app = create_app(StudioSettings(project_dir=self.project_dir, cad_export="off", cache_dir=cache))
        stale = ModelSource(self.model.run_id, "0" * 64, self.model.asset_sha256)
        self.assertEqual(projection_spec(stale).key, projection_spec(self.model).key)
        with TestClient(app) as client:
            refused = client.get("/api/projections", params=stale.to_dict())
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(app.state.projections.store.rows(), (), "nothing queued for the stale source")
            self.assertEqual(client.get("/api/projections", params=self.model.to_dict()).json()["status"], "pending")
            self.assertTrue(app.state.projections.wait_idle(120))
            self.assertEqual(client.get("/api/projections", params=self.model.to_dict()).json()["status"], "done")

    def test_a_run_on_an_older_canonical_base_is_drawn_but_not_acted_on(self):
        from archflow.project.repository import FilesystemProjectRepository
        from archflow_studio_api.application.binding import ProjectBinding
        from archflow_studio_api.application.drawings import draw_model_view
        from archflow_studio_api.application.projection import project_state, require_actionable

        copy = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, copy, True)
        project = copy / PROJECT_ID
        shutil.copytree(self.project_dir, project)
        advance_head(FilesystemProjectRepository.open(project), run_id="promotion-after-model")
        binding = ProjectBinding.open(StudioSettings(project_dir=project, cad_export="off"))
        self.addCleanup(binding.close)
        with self.assertRaises(StudioError) as refused:
            require_actionable(project_state(binding, self.model.run_id))
        self.assertEqual(refused.exception.code, "REFERENCE_BASE_STALE", "actions still need current HEAD")
        drawn = draw_model_view(binding, model_source=self.model, view="axon", size_px=256)
        self.assertEqual(max(drawn.width, drawn.height), 256)
        app = create_app(StudioSettings(project_dir=project, cad_export="off", cache_dir=copy / "cache"))
        with TestClient(app) as client:
            params = {**self.model.to_dict(), "size": 256}
            self.assertEqual(client.get("/api/projections", params=params).json()["status"], "pending")
            self.assertTrue(app.state.projections.wait_idle(120))
            done = client.get("/api/projections", params=params).json()
            self.assertEqual(done["status"], "done", done)
            with Image.open(BytesIO(client.get(done["blobUrl"]).content)) as image:
                self.assertEqual(image.size, (drawn.width, drawn.height))

    def test_an_unknown_model_is_refused_not_a_crash(self):
        missing = ModelSource(self.model.run_id, self.model.state_digest, "f" * 64)
        with self.assertRaisesRegex(RenderRefused, "MODEL_SOURCE_UNREGISTERED"):
            self.renderer().render(projection_spec(missing), timeout_s=120)

    def test_one_read_of_the_model_draws_every_size_and_each_as_a_fresh_read_would(self):
        renderer = self.renderer()
        large = renderer.render(projection_spec(self.model, recipe={"size": 1024}), timeout_s=120)
        small = renderer.render(projection_spec(self.model, recipe={"size": 512}), timeout_s=120)
        self.assertGreater(large.load_s, 0)
        self.assertEqual(small.load_s, 0, "the second size did not read the model again")
        fresh = self.renderer().render(projection_spec(self.model, recipe={"size": 512}), timeout_s=120)
        self.assertEqual(small.png, fresh.png, "the finer mesh of the first size does not reach the second")

    def test_a_committed_state_is_drawn_for_the_tree_with_nobody_asking(self):
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        app = create_app(StudioSettings(project_dir=self.project_dir, cad_export="off", cache_dir=cache))
        self.addCleanup(app.state.stop_index_events)
        spec = projection_spec(self.model, recipe=TREE_RECIPE)
        with TestClient(app) as client:
            started = time.monotonic()
            index = client.get("/api/index")  # the Hub opens the project; nothing asks for a picture
            while index.status_code == 503 and time.monotonic() < started + 30:
                time.sleep(0.05)
                index = client.get("/api/index")
            index = index.json()
            self.assertIn(f"run:{self.model.run_id}", {entity["id"] for entity in index["upserts"]})
            deadline = started + 60
            while time.monotonic() < deadline:
                queue = app.state.projections
                row = queue.store.get(spec.key) if queue is not None else None
                if row is not None and row.status == DONE:
                    break
                time.sleep(0.1)
            self.assertEqual(getattr(row, "status", None), DONE, "drawn within a minute of the commit")
            changes = client.get("/api/index", params={"since": index["revision"], "epoch": index["epoch"]}).json()
            self.assertIn(f"projections:{spec.key}", {entity["id"] for entity in changes["upserts"]})
            self.assertTrue(client.get(f"/api/projections/blobs/{row.blob_sha256}").content.startswith(b"\x89PNG"))

    def test_the_route_draws_through_the_process_and_a_cleared_cache_draws_again(self):
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cache, True)
        app = create_app(StudioSettings(project_dir=self.project_dir, cad_export="off", cache_dir=cache))
        with TestClient(app) as client:
            params = {**self.model.to_dict(), "size": 256}
            self.assertEqual(client.get("/api/projections", params=params).json()["status"], "pending")
            self.assertTrue(app.state.projections.wait_idle(120))
            done = client.get("/api/projections", params=params).json()
            self.assertEqual(done["status"], "done", done)
            self.assertGreater(done["renderMs"], 0)
            shutil.rmtree(cache / "projections")
            self.assertEqual(client.get("/api/projections", params=params).json()["status"], "pending")
            self.assertTrue(app.state.projections.wait_idle(120))
            again = client.get("/api/projections", params=params).json()
            self.assertEqual((again["status"], again["blobSha256"]), ("done", done["blobSha256"]))
            data = client.get(again["blobUrl"]).content
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.text["archflow:input-sha256"], self.model.asset_sha256)


if __name__ == "__main__":
    unittest.main()
