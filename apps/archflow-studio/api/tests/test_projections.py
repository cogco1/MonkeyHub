"""The projection cache: content keys, immutable blobs, leases, bounded retries and a real render process."""

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
from archflow_studio_api.application import projections
from archflow_studio_api.application.artifacts import ModelSource
from archflow_studio_api.application.projections import (
    DONE, ERROR, MODEL_LINES, PENDING, BlobStore, InMemoryStatusStore, ProjectionError, ProjectionQueue,
    RenderCancelled, RenderFailed, RenderResult, SubprocessRenderer, projection_key, projection_spec, recipe_of,
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
        raise RenderFailed(outcome)

    def cancel(self):
        self.cancelled.set()

    def close(self):
        pass


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

    def queue(self, renderer, store=None, **options):
        queue = ProjectionQueue(self.root / "projections", lambda: renderer, store=store or InMemoryStatusStore(),
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
        queue.store.enqueue(spec)
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
        store = InMemoryStatusStore()
        spec = projection_spec(SOURCE)
        store.enqueue(spec)
        store.claim(spec.key, now=self.clock())  # the process died while drawing it
        renderer = ScriptedRenderer()
        restarted = self.queue(renderer, store=store, timeout_s=60)
        restarted.start()
        self.settle(restarted)
        self.assertEqual((store.get(spec.key).status, renderer.calls), (DONE, [spec.key]))

    def test_a_source_that_cannot_be_drawn_leaves_no_row(self):
        renderer = ScriptedRenderer()
        queue = self.queue(renderer)
        stale = projection_spec(ModelSource("run-stale", "c" * 64, ASSET))

        def check(source):
            if source.run_id == "run-stale":
                raise StudioError(409, "MODEL_SOURCE_MISMATCH", "stale")

        with self.assertRaises(StudioError):
            queue.request(stale, check)
        self.assertIsNone(queue.store.get(stale.key), "a refused request records nothing")
        good = projection_spec(SOURCE)
        self.assertEqual(good.key, stale.key, "both runs hold the same model asset")
        queue.request(good, check)
        self.settle(queue)
        self.assertEqual(queue.request(stale, check).status, DONE, "a done key is served to every source")
        self.assertEqual(renderer.sources, [SOURCE])

    def test_a_retry_draws_from_the_newest_requester_not_the_first(self):
        renderer = ScriptedRenderer("MODEL_SOURCE_MISMATCH: first requester went stale", "ok")
        queue = self.queue(renderer)
        first = projection_spec(ModelSource("run-first", "c" * 64, ASSET))
        queue.request(first)
        self.settle(queue)
        self.assertEqual(queue.request(first).status, ERROR)
        self.clock.now += projections.BACKOFF_S[0]
        check = Mock()
        later = projection_spec(SOURCE)
        self.assertEqual(queue.request(later, check).status, PENDING)
        check.assert_called_once_with(SOURCE)
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
        store = InMemoryStatusStore()
        finish = store.finish
        broken = [True]

        def flaky_finish(key, **fields):
            if broken.pop() if broken else False:
                raise OSError("index unavailable")
            finish(key, **fields)

        store.finish = flaky_finish
        renderer = ScriptedRenderer()
        queue = self.queue(renderer, store=store)
        first, second = projection_spec(SOURCE), projection_spec(ModelSource("run", STATE, "e" * 64))
        with self.assertLogs(projections.__name__, "ERROR"):
            queue.request(first)
            self.settle(queue)
        queue.request(second)
        self.settle(queue)
        self.assertEqual(store.get(second.key).status, DONE, "the worker still runs")

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
                             "rows of the old renderer are dropped")

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
        self.assertIsNotNone(queue.blobs.read(kept))
        self.assertIsNotNone(queue.blobs.read(young))


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
        self.renderer = ScriptedRenderer()
        self.app.state.projections = ProjectionQueue(self.root / "cache" / "projections", lambda: self.renderer)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        # The synthetic source names no real run; the render process tests check real ones.
        checker = patch("archflow_studio_api.routes.projections.check_model_view_source")
        self.check = checker.start()
        self.addCleanup(checker.stop)

    def test_status_is_not_cached_and_the_blob_is_immutable(self):
        params = {**SOURCE.to_dict(), "size": 256}
        pending = self.client.get("/api/projections", params=params)
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.headers["cache-control"], "no-store")
        body = pending.json()
        self.assertEqual((body["status"], body["blobUrl"], body["recipe"]),
                         ("pending", None, {"view": "axon", "size": 256, "style": "lines"}))
        self.assertTrue(self.app.state.projections.wait_idle(10))
        done = self.client.get(f"/api/projections/{body['key']}")
        self.assertEqual(done.headers["cache-control"], "no-store")
        done = done.json()
        self.assertEqual((done["status"], done["inputSha256"], done["loadMs"]), ("done", ASSET, 250))
        blob = self.client.get(done["blobUrl"])
        self.assertEqual(blob.status_code, 200)
        self.assertEqual(blob.headers["content-type"], "image/png")
        self.assertEqual(blob.headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertEqual(blob.content, png_for(projection_spec(SOURCE, recipe={"size": 256})))
        self.assertTrue((self.root / "cache" / "projections" / "blobs" / f"{done['blobSha256']}.png").is_file())
        self.assertFalse(any("projections" in path.parts for path in (self.root / PROJECT_ID).rglob("*")),
                         "nothing is written into the project")

    def test_a_source_that_cannot_be_drawn_is_refused_before_anything_is_queued(self):
        self.check.side_effect = StudioError(409, "MODEL_SOURCE_MISMATCH", "The model source does not match.")
        refused = self.client.get("/api/projections", params=SOURCE.to_dict())
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "MODEL_SOURCE_MISMATCH"))
        self.assertEqual(self.app.state.projections.store.rows(), ())
        self.assertEqual(self.check.call_args.args[1:], (SOURCE, "axon"))

    def test_unknown_keys_blobs_and_recipes_are_refused(self):
        self.assertEqual(self.client.get(f"/api/projections/{'0' * 64}").json()["code"], "PROJECTION_UNKNOWN")
        missing = self.client.get(f"/api/projections/blobs/{'0' * 64}")
        self.assertEqual((missing.status_code, missing.json()["code"]), (404, "PROJECTION_BLOB_NOT_FOUND"))
        self.assertEqual(self.client.get("/api/projections/blobs/not-a-digest").status_code, 404)
        invalid = self.client.get("/api/projections", params={**SOURCE.to_dict(), "size": 333})
        self.assertEqual((invalid.status_code, invalid.json()["code"]), (422, "PROJECTION_RECIPE_INVALID"))

    def test_the_default_queue_lives_in_the_configured_cache(self):
        app = create_app(self.settings)
        with TestClient(app) as client:
            client.get(f"/api/projections/{'0' * 64}")
            self.assertEqual(app.state.projections.cache_root, self.root / "cache" / "projections")
        self.assertIsNone(app.state.projections, "shutdown closes the queue")
        default = StudioSettings(project_dir=self.root / PROJECT_ID)
        self.assertFalse(default.project_cache_dir.is_relative_to(self.root / PROJECT_ID))


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

    def test_an_unknown_model_is_a_failure_not_a_crash(self):
        missing = ModelSource(self.model.run_id, self.model.state_digest, "f" * 64)
        with self.assertRaises(RenderFailed):
            self.renderer().render(projection_spec(missing), timeout_s=120)

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
