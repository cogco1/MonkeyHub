"""The daily benchmark harness's own logic, without starting a Hub (GH-547).

The Modeling opening is replayed against a small fake Hub that serves what a
project runtime answers, so the order of requests, the rounds, the browser's
revalidation and the choice of model are checked; the real Hub is measured by
the workflow itself. The local runner's checks are pure functions of what
Windows reports, tested here without a scheduled task or an installed app.
"""

from __future__ import annotations

from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
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

    def test_an_installed_version_publishes_under_windows_local_with_its_own_python(self):
        self.assertEqual(bench.LOCAL_RUNNER_KEY, "windows-local")
        self.assertEqual(bench.runner_key(None, installed=True), "windows-local")
        self.assertIsNone(bench.runner_key(None, installed=False))
        self.assertEqual(bench.runner_key("windows", installed=True), "windows")
        result = {"schema": bench.SCHEMA, "commit": COMMIT, "date": "2026-10-01T14:00:00Z",
                  "runner": bench.runner_description(bench.LOCAL_RUNNER_KEY, "3.13.15"), "metrics": {}}
        benchmark_data.check_result(result)
        self.assertEqual(result["runner"]["python"], "3.13.15")
        self.assertEqual(benchmark_data.result_path(result), "results/windows-local/2026-10-01-046fdbdafe70.json")


COMMIT = "046fdbdafe709a92338c3f58262c3070e93e5afd"


def installed_version(root: Path, commit: str = COMMIT, **build: object) -> Path:
    """A version directory with what the harness reads of one: no interpreter that runs."""

    directory = root / "versions" / f"{commit[:12]}-desktop"
    for relative in (bench.INSTALLED_PYTHON, bench.INSTALLED_ENTRY, Path("apps/monkeyhub/api/__pycache__/x.pyc")):
        (directory / relative).parent.mkdir(parents=True, exist_ok=True)
        (directory / relative).write_bytes(b"shipped")
    (directory / "source-version.txt").write_text(commit + "\n", encoding="utf-8")
    facts = {"sourceCommit": commit, "channel": "candidate", "pythonVersion": "3.13.15",
             "desktop": {"version": "0.1.980", "sourceCommit": commit},
             "pythonBytecode": {"cacheTag": "cpython-313", "invalidation": "unchecked-hash", "files": 2986}}
    (directory / "build-info.json").write_text(json.dumps({**facts, **build}), encoding="utf-8")
    return directory


class InstalledModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_an_installed_hub_runs_its_own_interpreter_and_run_py_as_the_desktop_host_does(self):
        copy = Path("hubs/first-1/046fdbdafe70-desktop")
        command = bench.hub_command(copy, installed=True)
        self.assertEqual(command[0], str(copy / "_runtime" / "python" / "python.exe"))
        self.assertEqual(command[1:3], ["-u", "-c"])
        self.assertIn("credentials.account_store = credentials.UnavailableSecretStore", command[3])
        self.assertEqual(command[4], str(copy / "apps" / "monkeyhub" / "run.py"))
        self.assertEqual(bench.hub_command(copy), [sys.executable, "-c", bench.LAUNCHER, str(copy)])

    def test_an_installed_hub_gets_its_own_app_data_and_no_python_or_monkeyhub_variable(self):
        base = {"PATH": "p", "PYTHONPATH": "x", "PYTHONPYCACHEPREFIX": "y", "PYTHONUTF8": "1", "PythonHome": "z",
                "PYTHONUSERBASE": "u", "MONKEYHUB_RENDER_API_KEY": "k", "ARCHFLOW_STUDIO_PROJECT_DIR": "d",
                "APPDATA": "C:/real", "LOCALAPPDATA": "C:/real-local"}
        self.assertEqual(bench.installed_environment(base, Path("/work/env")),
                         {"PATH": "p", "APPDATA": str(Path("/work/env/appdata")),
                          "LOCALAPPDATA": str(Path("/work/env/localappdata"))})

    def test_a_version_directory_names_its_commit_release_python_and_shipped_bytecode(self):
        version = bench.read_installed(installed_version(self.root))
        self.assertEqual(version.name, "046fdbdafe70-desktop")
        self.assertEqual(version.commit, COMMIT)
        self.assertEqual(version.describe(), {"version": "046fdbdafe70-desktop", "release": "0.1.980",
                                              "channel": "candidate", "python": "3.13.15", "bytecodeFiles": 2986})
        self.assertEqual(bench.installation_root(version.directory), self.root.resolve())
        self.assertEqual(bench.installation_root(self.root / "elsewhere"), self.root / "elsewhere")

    def test_what_is_not_an_installed_version_is_refused(self):
        other = installed_version(self.root / "other", sourceCommit="1" * 40)
        with self.assertRaisesRegex(bench.HarnessError, "names another"):
            bench.read_installed(other)
        missing = installed_version(self.root / "missing")
        (missing / bench.INSTALLED_PYTHON).rename(missing / "python.exe")
        with self.assertRaisesRegex(bench.HarnessError, "_runtime/python/python.exe is missing"):
            bench.read_installed(missing)

    def test_a_measured_copy_is_fresh_and_never_sits_right_under_a_versions_directory(self):
        version = bench.read_installed(installed_version(self.root))
        copy = bench.copy_version(version, self.root / "work" / "hubs" / "first-1")
        self.assertEqual(copy, self.root / "work" / "hubs" / "first-1" / "046fdbdafe70-desktop")
        self.assertEqual((copy / "apps/monkeyhub/api/__pycache__/x.pyc").read_bytes(), b"shipped")
        with self.assertRaises(FileExistsError):
            bench.copy_version(version, self.root / "work" / "hubs" / "first-1")
        with self.assertRaisesRegex(bench.HarnessError, "versions directory"):
            bench.copy_version(version, self.root / "work" / "Versions")
        self.assertTrue((version.directory / bench.INSTALLED_ENTRY).is_file())

    def first_launches(self, **options) -> tuple[dict, list]:
        """``run`` with no project, so only first launches start, each in a fake Hub that records how."""

        started = []

        class FakeHub:
            def __init__(self, code_root, runtime_root, environment, log, *, installed=False):
                self.code_root, self.runtime_root, self.environment, self.installed = (code_root, runtime_root,
                                                                                      environment, installed)

            def start(self):
                started.append(self)
                return 1500.0 + len(started)

            def stop(self):
                return None

            def tail(self):
                return ""

        with mock.patch.object(bench, "Hub", FakeHub), mock.patch.object(bench, "_revision", return_value="c" * 40), \
                mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": "elsewhere", "MONKEYHUB_X": "1"}):
            result = bench.run(bench.Options(bench.CODE_ROOT, {}, self.root / "work", samples=1, first_launch_samples=2,
                                             **options), say=lambda _: None)
        return result, started

    def test_a_checkout_first_launch_compiles_into_an_empty_prefix_of_its_own(self):
        result, started = self.first_launches()
        work = self.root / "work"
        self.assertEqual([hub.code_root for hub in started], [bench.CODE_ROOT.resolve()] * 2)
        self.assertEqual([hub.environment["PYTHONPYCACHEPREFIX"] for hub in started],
                         [str(work / "pycache" / "first-1"), str(work / "pycache" / "first-2")])
        self.assertFalse(any(hub.installed for hub in started))
        self.assertNotIn("installed", result["settings"])
        self.assertEqual(result["runner"]["key"], result["runner"]["os"])

    def test_each_first_launch_starts_a_fresh_copy_and_the_first_copy_serves_every_later_hub(self):
        version = installed_version(self.root)
        result, started = self.first_launches(runner_key=bench.LOCAL_RUNNER_KEY, installed=version)
        work = self.root / "work"
        self.assertEqual([hub.code_root for hub in started],
                         [work / "hubs" / "first-1" / version.name, work / "hubs" / "first-2" / version.name])
        self.assertTrue(all(hub.installed and hub.runtime_root == work / "runtime" / "first-launch" for hub in started))
        self.assertFalse(any(key.startswith(("PYTHON", "MONKEYHUB_")) for hub in started for key in hub.environment))
        self.assertEqual(result["metrics"]["hub_start.first_launch"]["samples"], [1501.0, 1502.0])
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["runner"]["key"], "windows-local")
        self.assertEqual(result["runner"]["python"], "3.13.15")
        self.assertEqual(result["settings"]["installed"]["release"], "0.1.980")
        self.assertEqual(result["settings"]["installed"]["copies"], "per-run")
        self.assertIn("harnessCommit", result["settings"])
        self.assertEqual(result["failures"], [])

    def test_a_kept_copy_is_launched_first_once_per_version_and_then_starts_every_hub(self):
        version = installed_version(self.root)
        keep = self.root / "benchmark-local" / bench.KEPT_DIRECTORY
        first, started = self.first_launches(runner_key=bench.LOCAL_RUNNER_KEY, installed=version, keep_in=keep)
        self.assertEqual([hub.code_root for hub in started], [keep / version.name])
        self.assertEqual(first["metrics"]["hub_start.first_launch"]["samples"], [1501.0])
        self.assertEqual((first["settings"]["firstLaunchSamples"], first["settings"]["installed"]["copies"]), (1, "new"))
        self.assertEqual((keep / version.name / "apps/monkeyhub/api/__pycache__/x.pyc").read_bytes(), b"shipped")
        self.assertFalse((self.root / "work" / "hubs").exists())
        # The next run starts from the kept copy: no first launch, so the metric is absent that day.
        later, started = self.first_launches(runner_key=bench.LOCAL_RUNNER_KEY, installed=version, keep_in=keep)
        self.assertEqual(started, [])
        self.assertNotIn("hub_start.first_launch", later["metrics"])
        self.assertEqual((later["settings"]["firstLaunchSamples"], later["settings"]["installed"]["copies"]), (0, "kept"))

    def test_run_and_local_take_their_options(self):
        parser = bench.build_parser()
        run = parser.parse_args(["run", "--project", "30=p", "--out", "r.json", "--installed", "v"])
        self.assertEqual(run.installed, Path("v"))
        local = parser.parse_args(["local"])
        self.assertEqual((local.version_dir, local.dev_root, local.sizes, local.samples), (None, None, (30, 150), 3))
        self.assertFalse(hasattr(local, "first_launch_samples"))
        self.assertEqual((local.min_idle_minutes, local.idle_wait_minutes, local.max_cpu, local.cpu_seconds),
                         (15.0, 30.0, 30.0, 60.0))
        self.assertFalse(local.force or local.no_publish)
        self.assertEqual(parser.parse_args(["local", "--sizes", "150,30,30"]).sizes, (30, 150))
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            parser.parse_args(["local", "--sizes", "30,x"])


def shell_link(target: str, directory: str, *, unicode_base: bool = False) -> bytes:
    """A shell link as the installer's IShellLinkW writes one: an ID list, link info and Unicode string data."""

    flags = 0x1 | 0x2 | 0x4 | 0x10 | 0x80
    header = struct.pack("<II", 0x4C, 0x00021401) + bytes(12) + struct.pack("<I", flags) + bytes(0x4C - 24)
    id_list = struct.pack("<H", 4) + b"\x02\x00\0\0"
    volume = struct.pack("<IIII", 17, 3, 0x1234, 16) + b"\0"
    size = 0x24 if unicode_base else 0x1C
    base = target.encode("ascii") + b"\0"
    ansi_at = size + len(volume)
    suffix_at = ansi_at + len(base)
    fields = [size, 1, size, ansi_at, 0, suffix_at]
    tail = volume + base + b"\0"
    if unicode_base:
        fields += [size + len(tail), size + len(tail) + len(target) * 2 + 2]
        tail += target.encode("utf-16-le") + b"\0\0" + b"\0\0"
    info = struct.pack(f"<{len(fields) + 1}I", size + len(tail), *fields) + tail

    def string(text: str) -> bytes:
        return struct.pack("<H", len(text)) + text.encode("utf-16-le")

    return header + id_list + info + string("Open MonkeyHub") + string(directory)


class LocalRunnerTests(unittest.TestCase):
    TARGET = "D:\\MonkeyHub\\versions\\046fdbdafe70-desktop\\MonkeyHub.exe"
    DIRECTORY = "D:\\MonkeyHub\\versions\\046fdbdafe70-desktop"

    def test_the_desktop_shortcut_names_the_version_it_opens(self):
        for unicode_base in (False, True):
            target, directory = bench.shortcut_target(shell_link(self.TARGET, self.DIRECTORY, unicode_base=unicode_base))
            self.assertEqual((target, directory), (self.TARGET, self.DIRECTORY))
        self.assertEqual(bench.version_of_shortcut(self.TARGET, None), Path(self.DIRECTORY))
        self.assertEqual(bench.version_of_shortcut("D:\\Other\\tool.exe", self.DIRECTORY), Path(self.DIRECTORY))
        self.assertIsNone(bench.version_of_shortcut(None, None))
        with self.assertRaises(ValueError):
            bench.shortcut_target(b"not a link")
        with tempfile.TemporaryDirectory() as desktop:
            self.assertIsNone(bench.shortcut_version(Path(desktop)))
            (Path(desktop) / bench.SHORTCUT_NAME).write_bytes(shell_link(self.TARGET, self.DIRECTORY))
            self.assertEqual(bench.shortcut_version(Path(desktop)), Path(self.DIRECTORY))

    def test_the_monkeyhub_beside_a_run_is_told_apart_from_the_runners_own_hubs(self):
        installed = "D:\\MonkeyHub\\versions\\v"
        rows = [
            {"pid": 1, "name": "MonkeyHub.exe", "path": installed + "\\MonkeyHub.exe", "commandLine": "MonkeyHub.exe"},
            {"pid": 2, "name": "python.exe", "path": "\\\\?\\" + installed + "\\_runtime\\python\\python.exe",
             "commandLine": "python.exe run.py --service studio"},
            {"pid": 3, "name": "node.exe", "path": "d:\\monkeyhub\\versions\\v\\_runtime\\node\\node.exe",
             "commandLine": "node acp"},
            {"pid": 4, "name": "python.exe", "path": "C:\\Python312\\python.exe",
             "commandLine": "python D:\\work\\checkout\\apps\\monkeyhub\\run.py --service monitor"},
            {"pid": 5, "name": "python.exe", "path": "C:\\Python312\\python.exe",
             "commandLine": "python -m project_runtime.application.projections --project-dir x"},
            # The runner's own Hub, from its kept copy, and the runner itself.
            {"pid": 6, "name": "python.exe", "path": "D:\\DEV\\temp\\benchmark-local\\installed\\v\\_runtime\\python"
                                                     "\\python.exe", "commandLine": "python.exe -u -c x apps\\monkeyhub\\run.py"},
            {"pid": 7, "name": "python.exe", "path": "C:\\Python312\\python.exe",
             "commandLine": "python -m pytest services/project-runtime/tests"},
            {"pid": 8, "name": "pythonw.exe", "path": "C:\\Python312\\pythonw.exe",
             "commandLine": "pythonw.exe daily_benchmark.py local"},
            {"pid": 9, "name": "MonkeyArch.exe", "path": None, "commandLine": None},
            {"pid": 10, "name": "System", "path": None, "commandLine": None},
            {"pid": 11, "name": "git.exe", "path": "C:\\Program Files\\Git\\bin\\git.exe",
             "commandLine": "git log -- apps/monkeyhub/run.py"},
        ]
        seen = bench.monkeyhub_processes(rows, ["D:\\MonkeyHub"], own_root="D:\\DEV\\temp\\benchmark-local", own_pid=8)
        self.assertEqual(seen, {"desktop": ["MonkeyHub.exe 1", "python.exe 2", "node.exe 3", "MonkeyArch.exe 9"],
                                "elsewhere": ["python.exe 4", "python.exe 5"]})
        self.assertEqual(bench.monkeyhub_processes([rows[5], rows[6], rows[9], rows[10]], ["D:\\MonkeyHub"],
                                                   own_root="D:\\DEV\\temp\\benchmark-local"),
                         {"desktop": [], "elsewhere": []})

    def test_a_result_says_whether_the_desktop_app_was_open_beside_the_run(self):
        open_app = {"desktop": ["MonkeyHub.exe 1", "python.exe 2"], "elsewhere": []}
        closed = {"desktop": [], "elsewhere": ["python.exe 4"]}
        self.assertEqual(bench.monkeyhub_beside([open_app, closed, open_app]),
                         {"desktopOpen": True, "checks": 3, "desktopSeen": 2, "desktopProcesses": 2, "otherHubs": True})
        self.assertEqual(bench.monkeyhub_beside([]),
                         {"desktopOpen": False, "checks": 0, "desktopSeen": 0, "desktopProcesses": 0,
                          "otherHubs": False})

    def test_processor_use_is_the_busy_share_of_every_processor(self):
        self.assertEqual(bench.cpu_busy_percent((100, 300, 100), (160, 400, 200)), 70.0)
        self.assertEqual(bench.cpu_busy_percent((5, 5, 5), (5, 5, 5)), 0.0)

    def test_battery_or_an_unknown_source_on_a_machine_with_a_battery_stops_a_run(self):
        self.assertIsNone(bench.power_reason(1, 0))
        self.assertEqual(bench.power_reason(0, 1), "the computer runs on battery")
        self.assertIsNone(bench.power_reason(255, 128))
        self.assertEqual(bench.power_reason(255, 1), "the power source is unknown")

    def test_idle_time_counts_across_the_tick_counters_wrap(self):
        self.assertEqual(bench.idle_seconds(5_000, 2_000), 3.0)
        self.assertEqual(bench.idle_seconds(1_000, 0xFFFFFFFF - 999), 2.0)

    def test_the_runner_waits_for_an_idle_computer_and_gives_up_when_somebody_uses_it(self):
        self.assertEqual(bench.idle_wait(900, None, 0, 900, 1800), (True, None))
        self.assertEqual(bench.idle_wait(300, None, 0, 900, 1800), (False, None))
        self.assertEqual(bench.idle_wait(330, 330, 30, 900, 1800), (False, None))
        self.assertIn("used the computer", bench.idle_wait(5, 330, 60, 900, 1800)[1])
        self.assertIn("not left idle for 15 min within 30 min", bench.idle_wait(600, 600, 1800, 900, 1800)[1])
        pauses = []
        readings = iter([600.0, 700.0, 800.0, 900.0])
        self.assertIsNone(bench.wait_until_idle(900, 1800, read=lambda: next(readings), sleep=pauses.append, step=100))
        self.assertEqual(pauses, [100, 100, 100])
        readings = iter([600.0, 3.0])
        self.assertIn("used the computer", bench.wait_until_idle(900, 1800, read=lambda: next(readings),
                                                                 sleep=lambda _: None, step=100))

    def test_one_published_run_a_day_is_enough(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            self.assertIsNone(bench.published_today(runs, date(2026, 10, 1)))
            # Yesterday's published run, and today's that measured but did not publish, leave today open.
            (runs / "20260930-230000").mkdir(parents=True)
            (runs / "20260930-230000" / "published.json").write_text("{}", encoding="utf-8")
            (runs / "20261001-020000").mkdir()
            (runs / "20261001-020000" / "result.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(bench.published_today(runs, date(2026, 10, 1)))
            (runs / "20261001-120000").mkdir()
            (runs / "20261001-120000" / "published.json").write_text("{}", encoding="utf-8")
            self.assertEqual(bench.published_today(runs, date(2026, 10, 1)), "20261001-120000")

    def test_one_run_at_a_time_and_the_lock_file_stays(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "local" / "local.lock"
            with bench.hold_lock(lock) as first:
                self.assertTrue(first)
                with bench.hold_lock(lock) as second:
                    self.assertFalse(second)
            with bench.hold_lock(lock) as again:
                self.assertTrue(again)
            self.assertTrue(lock.is_file())

    def test_earlier_runs_are_renamed_into_the_trash_and_nothing_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs, trash = Path(temporary) / "runs", Path(temporary) / "_TRASH_20261001" / "benchmark-local"
            for name in ("20260930-120000", "20261001-090000"):
                (runs / name / "work").mkdir(parents=True)
                (runs / name / "result.json").write_text(name, encoding="utf-8")
            (trash / "20260930-120000").mkdir(parents=True)
            lines = []
            moved = bench.move_to_trash(sorted(runs.iterdir()), trash, lines.append)
            self.assertEqual([path.name for path in moved], ["20260930-120000-2", "20261001-090000"])
            self.assertEqual((trash / "20260930-120000-2" / "result.json").read_text(encoding="utf-8"), "20260930-120000")
            self.assertTrue((trash / "20260930-120000").is_dir())
            self.assertEqual(list(runs.iterdir()), [])
            self.assertEqual(lines, [])
            self.assertEqual(bench.move_to_trash([], trash, lines.append), [])
            # What cannot move stays where it is, and says so.
            missing = Path(temporary) / "gone"
            self.assertEqual(bench.move_to_trash([missing], trash, lines.append), [])
            self.assertEqual(len(lines), 1)

    def test_only_this_versions_copy_with_its_published_first_launch_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            kept = Path(temporary) / "installed"
            self.assertEqual(bench.stale_kept(kept, "new-desktop"), [])
            for name in ("old-desktop", "new-desktop"):
                (kept / name).mkdir(parents=True)
            (kept / "old-desktop.json").write_text("{}", encoding="utf-8")
            # This version's copy has no record of a published first launch yet: nothing is kept.
            self.assertEqual([path.name for path in bench.stale_kept(kept, "new-desktop")],
                             ["new-desktop", "old-desktop", "old-desktop.json"])
            (kept / "new-desktop.json").write_text("{}", encoding="utf-8")
            self.assertEqual([path.name for path in bench.stale_kept(kept, "new-desktop")],
                             ["old-desktop", "old-desktop.json"])
            self.assertEqual(bench.stale_kept(kept, "old-desktop"), [kept / "new-desktop", kept / "new-desktop.json"])

    def test_the_local_log_keeps_every_line_with_its_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "logs" / "local.log"
            with mock.patch("sys.stdout", None):
                bench.log_event(log, "skipped: the computer runs on battery")
                bench.log_event(log, "failed:\nTraceback\n  line")
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual([line.split(" ", 1)[1] for line in lines],
                             ["skipped: the computer runs on battery", "failed:", "Traceback", "  line"])
            self.assertTrue(all(line[:4].isdigit() and line[10] == "T" for line in lines))


if __name__ == "__main__":
    unittest.main()
