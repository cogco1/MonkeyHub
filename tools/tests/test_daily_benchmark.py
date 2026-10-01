"""The daily benchmark harness's own logic, without starting a Hub (GH-547).

The Modeling opening is replayed against a small fake Hub that serves what a
project runtime answers, so the order of requests, the rounds, the browser's
revalidation and the choice of model are checked; the real Hub is measured by
the workflow itself.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from tools.benchmarks import benchmark_data
from tools.benchmarks import daily_benchmark as bench

STAGE_MODEL = "a" * 64
CANDIDATE_MODEL = "b" * 64
CANDIDATE_STEP = "c" * 64
RUNTIME = "11111111-2222-3333-4444-555555555555"


def project_answers(*, current: dict | None) -> dict:
    """What a runtime answers for one small project: two lines, one Stage, one candidate."""

    history = {
        "main": {"projectId": "p", "branchId": "main",
                 "branches": [{"branchId": "main", "headStageRef": "stage-1"}, {"branchId": "alt", "headStageRef": "stage-2"}],
                 "stages": [{"stageRef": "stage-1", "modelSource": {"runId": "stage-run", "assetSha256": STAGE_MODEL,
                                                                     "stateDigest": "d1"}}]},
        "alt": {"projectId": "p", "branchId": "alt",
                "branches": [{"branchId": "main", "headStageRef": "stage-1"}, {"branchId": "alt", "headStageRef": "stage-2"}],
                "stages": []},
    }
    return {
        "/api/protocol": {"protocol": "archflow/2",
                          "capabilities": ["working-draft", "design-history", "working-copies", "working-source"]},
        "/api/project": {"projectId": "p"},
        "/api/working-draft": {"projectId": "p", "current": current, "localDraft": None, "revisionSha256": "r"},
        "/api/artifacts": {"projectId": "p", "artifacts": [
            {"runId": "stage-run", "sha256": STAGE_MODEL, "available": True, "format": "3dm", "fileName": "s.3dm"},
            {"runId": "cand-2", "sha256": CANDIDATE_MODEL, "available": True, "format": "3dm", "fileName": "c.3dm"},
            {"runId": "cand-2", "sha256": CANDIDATE_STEP, "available": True, "format": "step", "fileName": "c.step"},
        ]},
        "/api/working-source": {"projectId": "p", "head": {"branchId": "main"}},
        "/api/worktrees": {"projectId": "p"},
        "/api/working-copies": {"workingCopies": []},
        "history": history,
    }


class FakeRuntime:
    """Serves one project's routes under the Hub's forwarding prefix and records every request."""

    def __init__(self, answers: dict, delay: float = 0.02, delays: dict[str, float] | None = None) -> None:
        self.answers, self.delay, self.delays, self.requests = answers, delay, delays or {}, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - quiet
                return None

            def do_GET(self):  # noqa: N802 - the handler's name
                url = urlsplit(self.path)
                prefix = f"/api/runtime/projects/{RUNTIME}/studio"
                path, query = url.path.removeprefix(prefix), parse_qs(url.query)
                fake.requests.append((path, url.query, self.headers.get("If-None-Match")))
                time.sleep(fake.delays.get(path, fake.delay))
                status, body, headers = fake.answer(path, query, self.headers.get("If-None-Match"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def answer(self, path: str, query: dict, tag: str | None) -> tuple[int, bytes, dict]:
        if path == "/api/index":
            return 503, b'{"code": "INDEX_UNAVAILABLE"}', {}
        if path.startswith("/api/artifacts/") and path.endswith("/bytes"):
            return 200, b"model bytes of " + path.split("/")[3].encode(), {"Cache-Control": "immutable"}
        if path == "/api/design-history":
            line = query["branchId"][0]
            etag = f'"history-{line}"'
            if tag == etag:
                return 304, b"", {"ETag": etag}
            return 200, json.dumps(self.answers["history"][line]).encode(), {"ETag": etag}
        if path == "/api/state":
            return 200, json.dumps({"projectId": "p", "referenceRun": {"runId": query["run"][0]},
                                    "stateDigest": "d2"}).encode(), {}
        return 200, json.dumps(self.answers[path]).encode(), {"ETag": f'"{path}"'}

    def __enter__(self) -> "FakeRuntime":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def hub(self) -> bench.Hub:
        hub = bench.Hub(Path("."), Path("."), {}, Path("hub.log"))
        hub.port = self.server.server_address[1]
        return hub


class ModelingOpeningTests(unittest.TestCase):
    def open(self, current: dict | None) -> tuple[dict, list]:
        # A slow Worktree Graph holds the Design Tree back, as a cold worker's does,
        # so the tree reads each line after the session has: with its tag.
        with FakeRuntime(project_answers(current=current), delays={"/api/worktrees": 0.25}) as runtime:
            opening = bench.ModelingOpening(runtime.hub(), RUNTIME).run()
        return opening, runtime.requests

    def test_a_saved_stage_opens_in_four_rounds_with_its_model_beside_the_state(self):
        opening, requests = self.open({"runId": "cand-2", "sourceStageRef": "stage-1", "branchId": None})
        self.assertEqual(opening["rounds"], 4)
        self.assertEqual(opening["model"], {"files": [STAGE_MODEL], "runId": "stage-run", "bytes": len(b"model bytes of ") + 64,
                                            "prefetched": True})
        paths = [row["path"] for row in opening["requests"]]
        self.assertIn("/api/state?run=cand-2&sourceStageRef=stage-1", paths)
        self.assertIn(f"/api/artifacts/{STAGE_MODEL}/bytes", paths)
        bytes_read = next(row for row in opening["requests"] if row["path"].endswith("/bytes"))
        state_read = next(row for row in opening["requests"] if row["path"].startswith("/api/state"))
        self.assertLess(bytes_read["startMs"], state_read["startMs"] + state_read["ms"], "the bytes are asked for beside the state")
        # Both chains read both lines; the second read of a line revalidates with its tag.
        history = [(query, tag) for path, query, tag in requests if path == "/api/design-history"]
        self.assertEqual(sorted(query for query, _ in history), ["branchId=alt"] * 2 + ["branchId=main"] * 2)
        self.assertEqual(sorted(tag for _, tag in history if tag), ['"history-alt"', '"history-main"'])
        self.assertEqual({row["chain"] for row in opening["requests"]}, {"store", "workspace", "session", "tree"})
        self.assertEqual(next(row for row in opening["requests"] if row["chain"] == "store")["status"], 503)
        self.assertGreaterEqual(opening["totalMs"], opening["modelMs"])
        # The Design Tree's slow Worktree Graph is in the opening's total, not on the model's way.
        self.assertGreater(opening["totalMs"], 250)
        self.assertLess(opening["modelMs"], opening["totalMs"])

    def test_a_candidate_without_a_stage_shows_its_own_model_after_the_state(self):
        opening, _ = self.open({"runId": "cand-2", "sourceStageRef": None, "branchId": None})
        self.assertEqual(opening["rounds"], 5)
        self.assertEqual(opening["model"]["files"], [CANDIDATE_MODEL])
        self.assertFalse(opening["model"]["prefetched"])
        self.assertIn("/api/state?run=cand-2", [row["path"] for row in opening["requests"]])

    def test_without_a_saved_position_the_head_stage_opens(self):
        opening, _ = self.open(None)
        paths = [row["path"] for row in opening["requests"]]
        self.assertIn("/api/state?run=stage-run&sourceStageRef=stage-1", paths)
        self.assertEqual(opening["model"]["files"], [STAGE_MODEL])
        self.assertEqual(opening["rounds"], 4)


class ClientRuleTests(unittest.TestCase):
    def test_rounds_count_requests_made_one_after_another(self):
        self.assertEqual(bench.count_rounds([]), 0)
        self.assertEqual(bench.count_rounds([(0, 10), (1, 12), (12, 20), (12, 15), (21, 30)]), 3)
        # A request asked for before the one before it answered shares its round.
        self.assertEqual(bench.count_rounds([(0, 10), (9, 40), (35, 50)]), 1)

    def test_an_unsynced_local_draft_names_the_source_it_was_drawn_on(self):
        draft = {"current": {"runId": "saved", "sourceStageRef": "s0", "branchId": "alt"},
                 "localDraft": {"source": {"sourceRunId": "drawn-on", "sourceStageRef": "s1"}}}
        self.assertEqual(bench.saved_choice(draft), {"runId": "drawn-on", "sourceStageRef": "s1", "branchId": "alt"})
        self.assertEqual(bench.saved_choice({"current": draft["current"]}), draft["current"])
        self.assertIsNone(bench.saved_choice({"current": None, "localDraft": None}))
        self.assertIsNone(bench.saved_choice(None))

    def test_the_editing_base_is_the_saved_choice_or_the_head_stage(self):
        history = project_answers(current=None)["history"]["main"]
        self.assertEqual(bench.editing_base(None, history),
                         {"runId": "stage-run", "stageRef": "stage-1", "stageModel": history["stages"][0]["modelSource"]})
        self.assertEqual(bench.editing_base({"runId": "x", "sourceStageRef": None}, history),
                         {"runId": "x", "stageRef": None, "stageModel": None})
        self.assertEqual(bench.editing_base({"runId": "x"}, None), {"runId": "x", "stageRef": None, "stageModel": None})

    def test_the_home_model_prefers_the_composed_model_then_the_reference_run_then_the_last_listed(self):
        rows = [
            {"runId": "r", "sha256": "1", "available": True, "format": "3dm"},
            {"runId": "r", "sha256": "2", "available": True, "format": "3dm", "representation": "composed",
             "modelSource": {"runId": "r", "assetSha256": "2", "stateDigest": "d"}},
            {"runId": "r", "sha256": "3", "available": True, "format": "3dm", "sourceStepSha256": "s"},
            {"runId": "other", "sha256": "4", "available": True, "format": "3dm"},
            {"runId": "other", "sha256": "5", "available": False, "format": "3dm"},
        ]
        projection = {"referenceRun": {"runId": "r"}, "stateDigest": "d"}
        self.assertEqual([row["sha256"] for row in bench.home_models({"artifacts": rows}, projection, "r")], ["2"])
        stale = {**projection, "stateDigest": "older"}
        self.assertEqual([row["sha256"] for row in bench.home_models({"artifacts": rows}, stale, "r")], ["1", "2"])
        nothing = {"referenceRun": {"runId": "none"}}
        self.assertEqual([row["sha256"] for row in bench.home_models({"artifacts": rows}, nothing, None)], ["4"])
        self.assertEqual(bench.home_models({"artifacts": rows}, nothing, "none"), [])
        self.assertIsNone(bench.listed_model({"artifacts": rows}, {"runId": "other", "assetSha256": "5"}))

    def test_route_slugs_name_the_projection_checks_routes(self):
        self.assertEqual([bench._route_slug(route) for route in bench.ROUTES],
                         ["design_history", "worktrees", "artifacts", "documents", "working_source", "board"])


class ProcessTests(unittest.TestCase):
    def test_this_process_can_be_counted(self):
        counters = bench.process_counters()
        if counters is None:
            self.skipTest("neither Win32 nor /proc")
        try:
            sum(range(2_000_000))
            reading = counters.read(os.getpid())
            self.assertIsNotNone(reading)
            self.assertGreater(reading.cpu_s, 0)
            self.assertGreaterEqual(reading.read_bytes, 0)
            self.assertIn(os.getpid(), bench.descendants(counters.parents(), os.getppid()))
        finally:
            counters.close()

    def test_descendants_follow_the_tree_down_only(self):
        parents = {1: 0, 2: 1, 3: 2, 4: 1, 5: 9, 6: 5}
        self.assertEqual(sorted(bench.descendants(parents, 1)), [1, 2, 3, 4])
        self.assertEqual(bench.descendants(parents, 7), [7])

    def test_a_tally_keeps_what_an_exited_process_spent_and_counts_a_new_one_from_its_start(self):
        readings = {10: bench.Counters(1.0, 100), 11: bench.Counters(2.0, 200)}
        tally = bench.Tally(lambda pid: readings.get(pid))
        start = tally.update({"worker": [10, 11]}, starting=True)
        self.assertEqual(start["worker"], bench.Counters(0.0, 0))
        readings[10] = bench.Counters(1.5, 150)
        readings[11] = bench.Counters(2.25, 260)
        readings[12] = bench.Counters(0.25, 40)
        middle = tally.update({"worker": [10, 11, 12]})
        self.assertEqual(middle["worker"], bench.Counters(1.0, 150))
        del readings[11]  # exited
        readings[10] = bench.Counters(2.0, 150)
        end = tally.update({"worker": [10, 12]})
        self.assertEqual(end["worker"], bench.Counters(1.5, 150))
        self.assertEqual(tally.groups, {"worker": [10, 11, 12]})


class IsolationTests(unittest.TestCase):
    def test_the_hub_gets_its_own_settings_app_data_and_bytecode_and_nothing_a_shell_set(self):
        base = {"PATH": "p", "PYTHONPATH": "elsewhere", "PYTHONDONTWRITEBYTECODE": "1", "APPDATA": "C:/real",
                "ARCHFLOW_STUDIO_PROJECT_DIR": "x", "MONKEYHUB_RENDER_API_KEY": "k", "PYTHONUSERBASE": "C:/user"}
        environment = bench.hub_environment(base, Path("/work/env"), Path("/work/pycache/first-1"))
        self.assertEqual(environment["APPDATA"], str(Path("/work/env/appdata")))
        self.assertEqual(environment["LOCALAPPDATA"], str(Path("/work/env/localappdata")))
        self.assertEqual(environment["PYTHONPYCACHEPREFIX"], str(Path("/work/pycache/first-1")))
        self.assertEqual(environment["PYTHONUSERBASE"], "C:/user")
        self.assertEqual(environment["PATH"], "p")
        for dropped in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "ARCHFLOW_STUDIO_PROJECT_DIR", "MONKEYHUB_RENDER_API_KEY"):
            self.assertNotIn(dropped, environment)
        self.assertIn("PYTHONUSERBASE", bench.hub_environment({}, Path("/w"), Path("/p")))

    def test_ports_are_distinct_and_free(self):
        ports = bench.free_ports(3)
        self.assertEqual(len(set(ports)), 3)
        self.assertFalse(bench.listening(ports[0]))
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            self.assertTrue(bench.listening(server.getsockname()[1]))


class ResultTests(unittest.TestCase):
    def test_samples_become_schema_metrics_in_the_order_first_reported(self):
        collect = bench._Collector()
        collect.add("hub_start.warm", "ms", 100.0)
        collect.add("idle.hub.read.30runs", "bytes", 10.4)
        collect.add("hub_start.warm", "ms", 300.0)
        metrics = collect.metrics()
        self.assertEqual(list(metrics), ["hub_start.warm", "idle.hub.read.30runs"])
        self.assertEqual(metrics["hub_start.warm"], {"unit": "ms", "samples": [100.0, 300.0], "median": 200.0, "p90": 280.0})
        result = {"schema": bench.SCHEMA, "commit": "f" * 40, "date": "2026-10-01T20:30:00Z",
                  "runner": bench.runner_description(), "metrics": metrics}
        benchmark_data.check_result(result)
        self.assertEqual(result["runner"]["key"], result["runner"]["os"])
        self.assertTrue(result["runner"]["python"])


if __name__ == "__main__":
    unittest.main()
