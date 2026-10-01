"""Measure MonkeyHub from a checkout the same way every day (GH-547).

    python tools/benchmarks/daily_benchmark.py run --project 30=<dir> --project 150=<dir> \\
        --out <result.json> [--summary <summary.md>] [--work <dir>] [--samples 5] \\
        [--first-launch-samples 5] [--idle-seconds 60] [--budget-override METRIC=VALUE]

Every Hub this starts runs this checkout's ``apps/monkeyhub/run.py`` on its own
port, with its own runtime root, ``APPDATA``, ``LOCALAPPDATA`` and bytecode
prefix under ``--work``, and without the account's credential store: nothing an
installed MonkeyHub reads or writes is touched. Each project is copied once
into ``--work`` and settled (``projection_check.settle``); the projects given
are never opened. A project comes from
``services/project-runtime/tests/synthetic_project.py``, built before the run
(``docs/development/benchmarks.md``): a repository tool takes nothing from a
test suite.

Scenarios, every sample in a Hub of its own: ``hub_start.first_launch``
``--first-launch-samples`` times, idle once per project size, and the rest
``--samples`` times at each size:

- ``hub_start``: process start to the first ``/api/health`` that answers, as a
  first launch (an empty ``PYTHONPYCACHEPREFIX``: every module is compiled, as
  after an update, whose package ships no bytecode) and warm (a prefix kept
  from earlier launches).
- ``project_open``: the requests the web client's ``ensureProject`` sends, from
  opening the project's runtime until its worker is ready and answers the
  project binding. The Hub is asked every 25 ms, where the page waits 400 ms.
- ``open_modeling``: the requests the Modeling workspace sends when it opens
  (``ProjectWorkspace``, ``useSession``, ``App``, ``readDesignTreeSource``),
  in the same order and parallel rounds, until every one has answered, and
  ``open_modeling.model`` until the model's bytes have arrived; cold on a new
  worker, then warm. What the page asks for after the model is on screen, and
  its writes (client timings, the viewport capture), are not sent.
- ``route``: the six routes of the projection check through the Hub, each read
  once cold on a new worker and once warm.
- ``idle``: ``--idle-seconds`` (60 s) with the project open, Modeling opened
  once and the Hub's event stream held as a page holds it, from the moment
  the Hub and the worker have gone quiet after that opening; CPU seconds and
  bytes read of the Hub process and of the worker with its children.

Before they read anything, workers wait until the Hub has projected the project
and ``--settle-seconds`` more, the delay a person takes to click. Per project
size one unmeasured Hub first opens everything once, so the warm bytecode
prefix and the project's index in its runtime root exist; every measured Hub of
that size reuses both, as a returning user does.

The result is one ``MonkeyHubBenchmark@1`` file; ``benchmark_data.py`` judges,
summarises and publishes it.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import platform
import shutil
import site
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.benchmarks import benchmark_data
from tools.benchmarks.projection_check import ROUTES, settle

SCHEMA = benchmark_data.RESULT_SCHEMA
CODE_ROOT = Path(__file__).resolve().parents[2]
HEALTH_TIMEOUT_S = 180.0
WORKER_TIMEOUT_S = 180.0
PROJECTION_TIMEOUT_S = 180.0
INDEX_TIMEOUT_S = 600.0
STOP_TIMEOUT_S = 60.0
# How often a start is looked for: the Hub's health, a worker's readiness.
HEALTH_PROBE_S = 0.01
WORKER_PROBE_S = 0.025
# A refused connection to a closed port takes about 2 s on Windows; a probe
# gives up sooner and asks again.
CONNECT_PROBE_S = 0.05
IDLE_WINDOW_S = 10.0
# The idle window starts once what opening Modeling deferred has finished: when
# the Hub and the worker together spent less than IDLE_QUIET_CPU_S of CPU in
# IDLE_QUIET_STEP_S, or after IDLE_QUIET_LIMIT_S whatever they do.
IDLE_QUIET_STEP_S = 2.0
IDLE_QUIET_CPU_S = 0.1
IDLE_QUIET_LIMIT_S = 120.0
# The requests the model on screen waits for, one round after another.
MODEL_CHAINS = ("workspace", "session")
# Variables a developer's shell may carry that would change what is measured.
DROPPED_VARIABLES = ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")
DROPPED_PREFIXES = ("ARCHFLOW_", "MONKEYHUB_")
# Run in a new interpreter as the Hub: this checkout's source roots first, as
# run.py puts them; an account credential store that holds nothing; then run.py.
LAUNCHER = """\
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
roots = json.loads((root / "governance" / "architecture_policy.json").read_text(encoding="utf-8"))["python_source_roots"]
sys.path[:0] = [str(root / entry) for entry in roots]
from monkeyhub_api.settings import credentials
credentials.account_store = credentials.UnavailableSecretStore
sys.argv = [str(root / "apps" / "monkeyhub" / "run.py"), *sys.argv[2:]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


class HarnessError(RuntimeError):
    """A scenario that could not be measured, with what the Hub said."""


def _route_slug(route: str) -> str:
    return route.split("?", 1)[0].removeprefix("/api/").replace("-", "_").replace("/", "_")


# ---- HTTP ------------------------------------------------------------------------


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes
    started: float
    ended: float

    @property
    def ms(self) -> float:
        return (self.ended - self.started) * 1000.0

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def fetch(port: int, method: str, path: str, body: Any = None, headers: Mapping[str, str] | None = None,
          timeout: float = 120.0) -> Answer:
    """One request on a new connection, timed from before connecting until the body is read."""

    payload = None if body is None else json.dumps(body).encode("utf-8")
    sent = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        sent["Content-Type"] = "application/json"
    started = time.perf_counter()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        content = response.read()
        ended = time.perf_counter()
        return Answer(response.status, {key.lower(): value for key, value in response.getheaders()}, content,
                      started, ended)
    finally:
        connection.close()


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_PROBE_S):
            return True
    except OSError:
        return False


def free_ports(count: int) -> list[int]:
    """Distinct ports nothing listens on now: each is held until all are chosen."""

    held = []
    try:
        for _ in range(count):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            held.append(probe)
        return [probe.getsockname()[1] for probe in held]
    finally:
        for probe in held:
            probe.close()


# ---- process counters -------------------------------------------------------------


@dataclass(frozen=True)
class Counters:
    cpu_s: float
    read_bytes: int


class _WindowsProcesses:
    """CPU and I/O counters through the Win32 API; a handle keeps an exited process readable."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]

        self._IoCounters, self._ProcessEntry = IoCounters, ProcessEntry
        self._open = kernel32.OpenProcess
        self._open.argtypes, self._open.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
        self._times = kernel32.GetProcessTimes
        self._times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self._times.restype = wintypes.BOOL
        self._io = kernel32.GetProcessIoCounters
        self._io.argtypes, self._io.restype = [wintypes.HANDLE, ctypes.POINTER(IoCounters)], wintypes.BOOL
        self._close = kernel32.CloseHandle
        self._close.argtypes, self._close.restype = [wintypes.HANDLE], wintypes.BOOL
        self._snapshot = kernel32.CreateToolhelp32Snapshot
        self._snapshot.argtypes, self._snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
        self._first = kernel32.Process32FirstW
        self._next = kernel32.Process32NextW
        for function in (self._first, self._next):
            function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL
        self._handles: dict[int, Any] = {}

    def parents(self) -> dict[int, int]:
        ctypes = self._ctypes
        snapshot = self._snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        try:
            entry = self._ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = {}
            more = self._first(snapshot, ctypes.byref(entry))
            while more:
                found[entry.th32ProcessID] = entry.th32ParentProcessID
                more = self._next(snapshot, ctypes.byref(entry))
            return found
        finally:
            self._close(snapshot)

    def read(self, pid: int) -> Counters | None:
        ctypes = self._ctypes
        handle = self._handles.get(pid)
        if handle is None:
            handle = self._open(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            self._handles[pid] = handle
        from ctypes import wintypes

        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        io = self._IoCounters()
        if not self._times(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        if not self._io(handle, ctypes.byref(io)):
            return None
        # FILETIMEs count 100 ns.
        ticks = sum((spent.dwHighDateTime << 32) | spent.dwLowDateTime for spent in (kernel, user))
        return Counters(ticks / 1e7, int(io.ReadTransferCount))

    def close(self) -> None:
        for handle in self._handles.values():
            self._close(handle)
        self._handles.clear()


class _ProcProcesses:
    """CPU and I/O counters from /proc: ``rchar`` counts every byte read, page cache included."""

    def __init__(self) -> None:
        self._tick = os.sysconf("SC_CLK_TCK")

    @staticmethod
    def _stat(pid: int) -> list[str] | None:
        try:
            text = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        return text[text.rindex(")") + 2:].split()

    def parents(self) -> dict[int, int]:
        found = {}
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                fields = self._stat(int(entry.name))
                if fields:
                    found[int(entry.name)] = int(fields[1])
        return found

    def read(self, pid: int) -> Counters | None:
        fields = self._stat(pid)
        if fields is None:
            return None
        try:
            io = dict(line.split(": ", 1) for line in Path(f"/proc/{pid}/io").read_text().splitlines())
        except OSError:
            return None
        return Counters((int(fields[11]) + int(fields[12])) / self._tick, int(io["rchar"]))

    def close(self) -> None:
        return None


def process_counters():
    """This platform's counters, or None where neither Win32 nor /proc is there."""

    if os.name == "nt":
        return _WindowsProcesses()
    if Path("/proc/self/stat").is_file():
        return _ProcProcesses()
    return None


def descendants(parents: Mapping[int, int], root: int) -> list[int]:
    """``root`` and every process below it in a pid -> parent map."""

    tree, frontier = [root], [root]
    while frontier:
        parent = frontier.pop()
        children = [pid for pid, owner in parents.items() if owner == parent and pid not in tree]
        tree += children
        frontier += children
    return tree


# ---- one Hub ------------------------------------------------------------------------


def hub_environment(base: Mapping[str, str], env_root: Path, pycache: Path) -> dict[str, str]:
    """The Hub's variables: this run's settings and app data, the given bytecode prefix, nothing a shell set for MonkeyHub."""

    environment = {key: value for key, value in base.items()
                   if key not in DROPPED_VARIABLES and not key.startswith(DROPPED_PREFIXES)}
    # Python finds per-user packages through APPDATA on Windows; keep finding them where they are.
    environment.setdefault("PYTHONUSERBASE", site.getuserbase())
    environment.update({
        "APPDATA": str(env_root / "appdata"),
        "LOCALAPPDATA": str(env_root / "localappdata"),
        "PYTHONPYCACHEPREFIX": str(pycache),
        "PYTHONUTF8": "1",
    })
    return environment


class Hub:
    """One Hub process from the checkout, on its own ports, stopped the way the desktop host stops it."""

    def __init__(self, code_root: Path, runtime_root: Path, environment: Mapping[str, str], log: Path) -> None:
        self.code_root, self.runtime_root, self.environment, self.log = code_root, runtime_root, dict(environment), log
        self.port = 0
        self.pid: int | None = None
        self.process: subprocess.Popen | None = None

    def start(self) -> float:
        """Start the Hub; answer the milliseconds from process start to its first health answer."""

        self.port, studio, monitor = free_ports(3)
        # A workspace folder of its own lists no project: a project is opened only when a scenario asks.
        benchmark_data.write_file(self.runtime_root / "config" / "applications.json", json.dumps(
            {"workspaceDir": str(self.runtime_root / "workspace"), "studioPort": studio, "monitorPort": monitor}))
        instance = str(uuid.uuid4())
        command = [sys.executable, "-c", LAUNCHER, str(self.code_root), "--runtime-root", str(self.runtime_root),
                   "--port", str(self.port), "--no-browser", "--managed-stdin", "--managed-instance-id", instance]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                                   else {"start_new_session": True})
        with open(self.log, "ab") as output:
            started = time.perf_counter()
            self.process = subprocess.Popen(command, cwd=str(self.code_root), env=self.environment,
                                            stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, **options)
        deadline = started + HEALTH_TIMEOUT_S
        while time.perf_counter() < deadline:
            if self.process.poll() is not None:
                raise HarnessError(f"the Hub exited with {self.process.returncode} before it answered health\n{self.tail()}")
            if listening(self.port):
                try:
                    answer = fetch(self.port, "GET", "/api/health", timeout=10)
                except OSError:
                    answer = None
                if answer is not None and answer.status == 200:
                    health = answer.json()
                    if health.get("managedInstanceId") == instance:
                        self.pid = int(health["processId"])
                        return (answer.ended - started) * 1000.0
            time.sleep(HEALTH_PROBE_S)
        raise HarnessError(f"the Hub did not answer health within {HEALTH_TIMEOUT_S:.0f} s\n{self.tail()}")

    def stop(self) -> None:
        """Ask the Hub to stop on its managed stdin; it stops its workers first. Kill the tree if it will not."""

        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.stdin.write(b"stop\n")
            process.stdin.flush()
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
            else:
                import signal

                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(30)

    def tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def get(self, path: str, **headers: str) -> Answer:
        return fetch(self.port, "GET", path, headers=headers)

    def post(self, path: str, body: Any) -> Answer:
        return fetch(self.port, "POST", path, body=body)

    def studio(self, runtime_id: str, path: str) -> str:
        return f"/api/runtime/projects/{runtime_id}/studio{path}"


def _expect(answer: Answer, what: str, *statuses: int) -> Answer:
    if answer.status not in (statuses or (200,)):
        raise HarnessError(f"{what} answered {answer.status}: {answer.body[:500].decode('utf-8', 'replace')}")
    return answer


# ---- scenarios ----------------------------------------------------------------------


@dataclass(frozen=True)
class Project:
    size: int
    directory: Path
    project_id: str


def copy_project(source: Path, destination: Path, size: int) -> Project:
    """One settled copy of a built project; the project given is never opened."""

    target = destination / source.name
    shutil.copytree(source, target)
    settle(target)
    project_id = json.loads((target / "project.json").read_text(encoding="utf-8"))["project_id"]
    return Project(size, target, project_id)


def _studio_worker(snapshot: Mapping[str, Any], runtime_id: str) -> Mapping[str, Any] | None:
    project = next((row for row in snapshot.get("projects", ()) if row.get("runtimeId") == runtime_id), {})
    return next((row for row in project.get("workers", ()) if row.get("serviceId") == "studio"), None)


def open_project(hub: Hub, project: Project) -> dict[str, Any]:
    """The web client's ``ensureProject``: attach, start the worker, wait until it runs, read its binding."""

    query = urlencode({"projectDir": str(project.directory)})
    started = time.perf_counter()
    opened = _expect(hub.post("/api/runtime/projects/open", {"projectDir": str(project.directory),
                                                             "projectId": project.project_id}), "open").json()
    runtime_id = opened["runtimeId"]
    worker = _studio_worker(_expect(hub.get("/api/runtime"), "GET /api/runtime").json(), runtime_id)
    if not (worker and worker.get("healthy")):
        _expect(hub.post(f"/api/apps/monkeyrender/start?{query}", {}), "start", 202)
        deadline = time.perf_counter() + WORKER_TIMEOUT_S
        while True:
            statuses = _expect(hub.get(f"/api/apps?{query}"), "GET /api/apps").json()
            status = next(row for row in statuses if row["appId"] == "monkeyrender")
            if status["state"] == "running" and status.get("url"):
                break
            if status["state"] in ("error", "unavailable"):
                raise HarnessError(f"the project's worker did not start: {status.get('error')}")
            if time.perf_counter() > deadline:
                raise HarnessError(f"the project's worker was not ready within {WORKER_TIMEOUT_S:.0f} s")
            time.sleep(WORKER_PROBE_S)
    statuses = _expect(hub.get(f"/api/apps?{query}"), "GET /api/apps").json()
    if not any(row["appId"] == "monkeyarch" and row["state"] == "running" and row.get("url") for row in statuses):
        raise HarnessError("the project service did not become ready")
    ready = time.perf_counter()
    binding = _expect(hub.get(hub.studio(runtime_id, "/api/project")), "the project binding")
    if binding.json().get("projectId") != project.project_id:
        raise HarnessError("the worker serves another project")
    return {"totalMs": (binding.ended - started) * 1000.0, "readyMs": (ready - started) * 1000.0,
            "bindingMs": binding.ms, "runtimeId": runtime_id}


def await_projection(hub: Hub, runtime_id: str) -> float:
    """Wait until the Hub has projected the project; answer how long that took."""

    started = time.perf_counter()
    deadline = started + PROJECTION_TIMEOUT_S
    while time.perf_counter() < deadline:
        snapshot = _expect(hub.get("/api/runtime"), "GET /api/runtime").json()
        project = next((row for row in snapshot["projects"] if row["runtimeId"] == runtime_id), {})
        if project.get("projection") == "ready":
            return (time.perf_counter() - started) * 1000.0
        time.sleep(0.1)
    raise HarnessError(f"the Hub did not project the project within {PROJECTION_TIMEOUT_S:.0f} s")


def await_index(hub: Hub, runtime_id: str) -> bool:
    """Wait until the worker's project index answers, so later workers load it instead of building it."""

    deadline = time.perf_counter() + INDEX_TIMEOUT_S
    while time.perf_counter() < deadline:
        answer = hub.get(hub.studio(runtime_id, "/api/index"))
        if answer.status == 200:
            return True
        if answer.status != 503:
            return False
        time.sleep(0.5)
    return False


def read_routes(hub: Hub, runtime_id: str) -> dict[str, dict[str, float]]:
    """The projection check's six routes through the Hub, each once cold and once warm."""

    timings: dict[str, dict[str, float]] = {}
    for phase in ("cold", "warm"):
        for route in ROUTES:
            answer = _expect(hub.get(hub.studio(runtime_id, route)), route)
            timings.setdefault(_route_slug(route), {})[phase] = answer.ms
    return timings


def count_rounds(spans: Sequence[tuple[float, float]]) -> int:
    """Requests made one after another: one asked for before the one before it answered shares its round.

    The same count as ``apps/monkeyhub/web/test/modelingOpen.browser.mjs``.
    """

    rounds, round_end = 0, float("-inf")
    for started, ended in sorted(spans):
        if started >= round_end:
            rounds += 1
            round_end = ended
        else:
            round_end = max(round_end, ended)
    return rounds


def saved_choice(working_draft: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The saved editing position as ``useSession`` reads it: an unsynced local draft's source first."""

    if not working_draft:
        return None
    current = working_draft.get("current") or {}
    local = working_draft.get("localDraft")
    if local:
        source = local.get("source") or {}
        return {"runId": source.get("sourceRunId"), "sourceStageRef": source.get("sourceStageRef"),
                "branchId": current.get("branchId")}
    return dict(current) if working_draft.get("current") else None


def editing_base(choice: Mapping[str, Any] | None, history: Mapping[str, Any] | None) -> dict[str, Any]:
    """The run, Stage and Stage model ``useSession`` opens: the saved choice, or the head Stage without one."""

    stages = (history or {}).get("stages") or []
    if history is not None and choice is None:
        branch = next((row for row in history.get("branches", ()) if row.get("branchId") == history.get("branchId")), {})
        head = next((stage for stage in stages if stage.get("stageRef") == branch.get("headStageRef")), None)
        source = (head or {}).get("modelSource")
        return {"runId": (source or {}).get("runId"), "stageRef": (head or {}).get("stageRef"), "stageModel": source}
    choice = choice or {}
    stage_ref = choice.get("sourceStageRef")
    stage = next((row for row in stages if row.get("stageRef") == stage_ref), None) if stage_ref else None
    return {"runId": choice.get("runId"), "stageRef": stage_ref, "stageModel": (stage or {}).get("modelSource")}


def _viewable(row: Mapping[str, Any]) -> bool:
    return bool(row.get("available") and row.get("sha256") and row.get("format") == "3dm"
                and not row.get("sourceStepSha256"))


def listed_model(listing: Mapping[str, Any], source: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The listed, viewable file of a model source: what ``App`` prefetches beside the state."""

    if not source:
        return None
    return next((row for row in listing.get("artifacts", ()) if row.get("runId") == source.get("runId")
                 and row.get("sha256") == source.get("assetSha256") and _viewable(row)), None)


def home_models(listing: Mapping[str, Any], projection: Mapping[str, Any], source_run: str | None) -> list[Mapping[str, Any]]:
    """The files ``App`` shows when nothing was prefetched: the reference run's model, else the last listed one."""

    rows = listing.get("artifacts", ())
    run = (projection.get("referenceRun") or {}).get("runId")
    composed = next((row.get("modelSource") for row in rows if row.get("runId") == run
                     and row.get("representation") == "composed"
                     and (row.get("modelSource") or {}).get("stateDigest") == projection.get("stateDigest")), None)
    if composed:
        chosen = [row for row in rows if row.get("runId") == composed.get("runId")
                  and row.get("sha256") == composed.get("assetSha256") and _viewable(row)]
    else:
        chosen = [row for row in rows if row.get("runId") == run and _viewable(row)]
    if chosen or source_run is not None:
        return chosen
    viewable = [row for row in rows if _viewable(row)]
    return viewable[-1:]


@dataclass
class _Request:
    chain: str
    path: str
    status: int
    started: float
    ended: float
    size: int


@dataclass
class ModelingOpening:
    """One opening of the Modeling workspace, replayed through the Hub as the web client sends it.

    Two chains run side by side once the handshake has answered: the session
    (binding, saved position and model list; design history; state beside the
    model's bytes) and the Design Tree (working source and Worktree Graph; the
    head's line; the other lines). A URL already answered in this opening is
    asked again with its ETag, as the browser revalidates it.
    """

    hub: Hub
    runtime_id: str
    log: list[_Request] = field(default_factory=list)
    _etags: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _model_done: float | None = None
    _model: dict[str, Any] | None = None

    def _get(self, chain: str, path: str, *accepted: int) -> Answer:
        with self._lock:
            cached = self._etags.get(path)
        headers = {"If-None-Match": cached[0]} if cached else {}
        answer = self.hub.get(self.hub.studio(self.runtime_id, path), **headers)
        if answer.status == 304 and cached:
            answer = Answer(304, answer.headers, cached[1], answer.started, answer.ended)
        elif answer.headers.get("etag") and answer.status == 200:
            with self._lock:
                self._etags[path] = (answer.headers["etag"], answer.body)
        with self._lock:
            self.log.append(_Request(chain, path, answer.status, answer.started, answer.ended, len(answer.body)))
        if answer.status not in (accepted or (200, 304)):
            raise HarnessError(f"{path} answered {answer.status}: {answer.body[:300].decode('utf-8', 'replace')}")
        return answer

    def _round(self, pool: ThreadPoolExecutor, chain: str, paths: Sequence[str]) -> list[Answer]:
        return list(pool.map(lambda path: self._get(chain, path), paths))

    def _bytes(self, pool: ThreadPoolExecutor, rows: Sequence[Mapping[str, Any]], prefetched: bool) -> None:
        answers = self._round(pool, "session", [f"/api/artifacts/{row['sha256']}/bytes" for row in rows])
        self._model_done = max(answer.ended for answer in answers)
        self._model = {"files": [row["sha256"] for row in rows], "runId": rows[0].get("runId"),
                       "bytes": sum(len(answer.body) for answer in answers), "prefetched": prefetched}

    def _session(self, pool: ThreadPoolExecutor, capabilities: Sequence[str]) -> None:
        reads = ["/api/project", "/api/artifacts"]
        if "working-draft" in capabilities:
            reads.insert(0, "/api/working-draft")
        answers = dict(zip(reads, self._round(pool, "session", reads)))
        listing = answers["/api/artifacts"].json()
        draft = answers["/api/working-draft"].json() if "/api/working-draft" in answers else None
        choice = saved_choice(draft)
        branch = (choice or {}).get("branchId") or "main"
        reads = []
        if "design-history" in capabilities:
            reads.append(f"/api/design-history?{urlencode({'branchId': branch})}")
        if "working-copies" in capabilities:
            reads.append("/api/working-copies")
        answers = dict(zip(reads, self._round(pool, "session", reads)))
        history = answers[reads[0]].json() if reads and reads[0].startswith("/api/design-history") else None
        base = editing_base(choice, history)
        query = {key: value for key, value in (("run", base["runId"]), ("sourceStageRef", base["stageRef"])) if value}
        state_path = "/api/state" + (f"?{urlencode(query)}" if query else "")
        prefetch = listed_model(listing, base["stageModel"])
        if prefetch is not None:
            state_read = pool.submit(self._get, "session", state_path)
            self._bytes(pool, [prefetch], prefetched=True)
            projection = state_read.result().json()
        else:
            projection = self._get("session", state_path).json()
        # App reads every other line's history once the session has one, beside the model it shows.
        others = [f"/api/design-history?{urlencode({'branchId': row['branchId']})}"
                  for row in (history or {}).get("branches", ()) if row.get("branchId") != (history or {}).get("branchId")]
        other_reads = [pool.submit(self._get, "session", path) for path in others]
        if prefetch is None:
            rows = home_models(listing, projection, base["runId"])
            if rows:
                self._bytes(pool, rows, prefetched=False)
        for read in other_reads:
            read.result()

    def _tree(self, pool: ThreadPoolExecutor) -> None:
        source, _ = self._round(pool, "tree", ["/api/working-source?workspace=modeling", "/api/worktrees"])
        line = ((source.json().get("head") or {}).get("branchId")) or "main"
        history = self._get("tree", f"/api/design-history?{urlencode({'branchId': line})}").json()
        self._round(pool, "tree", [f"/api/design-history?{urlencode({'branchId': row['branchId']})}"
                                   for row in history.get("branches", ()) if row.get("branchId") != history.get("branchId")])

    def run(self) -> dict[str, Any]:
        started = time.perf_counter()
        # The pool runs single requests only, never a task that waits on another;
        # the Design Tree's chain has a thread of its own.
        with ThreadPoolExecutor(max_workers=12) as pool:
            # The project store's index read, the handshake and the binding go first, side by side.
            store = pool.submit(self._get, "store", "/api/index", 200, 503)
            protocol, _ = self._round(pool, "workspace", ["/api/protocol", "/api/project"])
            capabilities = protocol.json().get("capabilities") or []
            failure: list[BaseException] = []

            def tree() -> None:
                try:
                    self._tree(pool)
                except BaseException as error:  # noqa: BLE001 - raised again below, in this thread
                    failure.append(error)

            follower = threading.Thread(target=tree, daemon=True) if "design-history" in capabilities else None
            if follower is not None:
                follower.start()
            try:
                self._session(pool, capabilities)
            finally:
                if follower is not None:
                    follower.join()
            store.result()
            if failure:
                raise failure[0]
        finished = max(row.ended for row in self.log)
        end = self._model_done if self._model_done is not None else finished
        # Rounds are counted on the chain the model waits for; the index read and
        # the Design Tree run beside it and would merge rounds that are not shared.
        opening = [row for row in self.log if row.started <= end and row.chain in MODEL_CHAINS]
        return {
            "totalMs": (finished - started) * 1000.0,
            "modelMs": (end - started) * 1000.0,
            "rounds": count_rounds([(row.started, row.ended) for row in opening]),
            "model": self._model,
            "requests": [{"chain": row.chain, "path": row.path, "status": row.status,
                          "startMs": round((row.started - started) * 1000.0, 1),
                          "ms": round((row.ended - row.started) * 1000.0, 1), "bytes": row.size}
                         for row in sorted(self.log, key=lambda row: row.started)],
        }


class EventStream:
    """The Hub's runtime event stream, held open and read the way a page holds it."""

    def __init__(self, port: int) -> None:
        self._connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._stop = threading.Event()

    def __enter__(self) -> "EventStream":
        self._connection.request("GET", "/api/runtime/events", headers={"Accept": "text/event-stream"})
        self._response = self._connection.getresponse()
        if self._response.status != 200:
            raise HarnessError(f"/api/runtime/events answered {self._response.status}")
        self._thread.start()
        return self

    def _read(self) -> None:
        try:
            while not self._stop.is_set() and self._response.readline():
                pass
        except (OSError, ValueError, http.client.HTTPException):
            pass

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        try:
            self._connection.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        self._connection.close()
        self._thread.join(5)


class Tally:
    """What groups of processes spent since the tally began, process by process.

    A process present at the start counts from its reading then; one that
    appears later counts from its own start. A process that can no longer be
    read keeps its last reading, so an exit never takes back what it spent.
    """

    def __init__(self, read: Callable[[int], Counters | None]) -> None:
        self._read = read
        self._first: dict[int, Counters] = {}
        self._last: dict[int, Counters] = {}
        self._groups: dict[str, list[int]] = {}

    def update(self, groups: Mapping[str, Sequence[int]], *, starting: bool = False) -> dict[str, Counters]:
        for name, pids in groups.items():
            members = self._groups.setdefault(name, [])
            for pid in pids:
                if pid in members:
                    continue
                members.append(pid)
                if not starting:
                    self._first[pid] = Counters(0.0, 0)
        for pid in {pid for members in self._groups.values() for pid in members}:
            reading = self._read(pid)
            if reading is not None:
                self._first.setdefault(pid, reading)
                self._last[pid] = reading
        return {name: Counters(sum(self._spent(pid).cpu_s for pid in members),
                               sum(self._spent(pid).read_bytes for pid in members))
                for name, members in self._groups.items()}

    def _spent(self, pid: int) -> Counters:
        first, last = self._first.get(pid), self._last.get(pid)
        if first is None or last is None:
            return Counters(0.0, 0)
        return Counters(last.cpu_s - first.cpu_s, last.read_bytes - first.read_bytes)

    @property
    def groups(self) -> dict[str, list[int]]:
        return {name: list(members) for name, members in self._groups.items()}


def measure_idle(hub: Hub, runtime_id: str, seconds: float) -> dict[str, Any]:
    """CPU seconds and bytes read over ``seconds`` with the project open and the event stream held.

    The window starts once the Hub and the worker have gone quiet after what
    opened the project; how long that took is reported beside it.
    """

    counters = process_counters()
    if counters is None:
        raise HarnessError("this platform has neither Win32 process counters nor /proc")
    try:
        with EventStream(hub.port):
            worker = _studio_worker(_expect(hub.get("/api/runtime"), "GET /api/runtime").json(), runtime_id)
            if not worker or not worker.get("processId"):
                raise HarnessError("the project's worker has no process to measure")
            worker_pid = int(worker["processId"])

            def groups() -> dict[str, list[int]]:
                return {"hub": [hub.pid], "worker": descendants(counters.parents(), worker_pid)}

            quiet_since = time.perf_counter()
            watch = Tally(counters.read)
            spent = watch.update(groups(), starting=True)
            while time.perf_counter() - quiet_since < IDLE_QUIET_LIMIT_S:
                time.sleep(IDLE_QUIET_STEP_S)
                now = watch.update(groups())
                busy = sum(now[name].cpu_s - spent[name].cpu_s for name in now)
                spent = now
                if busy < IDLE_QUIET_CPU_S:
                    break
            quiet_after = time.perf_counter() - quiet_since
            tally = Tally(counters.read)
            previous = tally.update(groups(), starting=True)
            windows = []
            started = time.perf_counter()
            while (remaining := seconds - (time.perf_counter() - started)) > 0:
                time.sleep(min(IDLE_WINDOW_S, remaining))
                now = tally.update(groups())
                windows.append({name: {"cpuSeconds": round(now[name].cpu_s - previous[name].cpu_s, 3),
                                       "readBytes": now[name].read_bytes - previous[name].read_bytes}
                                for name in now})
                previous = now
            elapsed = time.perf_counter() - started
    finally:
        counters.close()
    return {"seconds": round(elapsed, 2), "quietAfterSeconds": round(quiet_after, 1), "pids": tally.groups,
            "hub": {"cpuSeconds": previous["hub"].cpu_s, "readBytes": previous["hub"].read_bytes},
            "worker": {"cpuSeconds": previous["worker"].cpu_s, "readBytes": previous["worker"].read_bytes},
            "windows": windows}


# ---- the run ----------------------------------------------------------------------


def _revision(root: Path) -> str:
    completed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                               check=False, stdin=subprocess.DEVNULL)
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        raise HarnessError(f"{root} is not a Git checkout with a commit")
    return value


def _cpu_name() -> str | None:
    if sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return None
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            return None
    return platform.processor() or None


def runner_description(key: str | None = None) -> dict[str, Any]:
    system = platform.system().lower()
    return {"key": key or system, "os": system, "osVersion": platform.release(), "platform": platform.platform(),
            "python": platform.python_version(), "implementation": platform.python_implementation(),
            "cpu": _cpu_name(), "cpuCount": os.cpu_count(), "machine": platform.machine()}


@dataclass
class Options:
    code_root: Path
    projects: dict[int, Path]
    work: Path
    samples: int = 5
    first_launch_samples: int = 5
    idle_seconds: float = 60.0
    settle_seconds: float = 2.0
    runner_key: str | None = None


class _Collector:
    """Samples by metric, in the order the scenarios first report them, and what went wrong."""

    def __init__(self) -> None:
        self.samples: dict[str, tuple[str, list[float]]] = {}
        self.failures: list[dict[str, Any]] = []
        self.details: dict[str, Any] = {"projectOpen": {}, "openModeling": {}, "idle": {}, "projection": {}}

    def add(self, metric: str, unit: str, value: float) -> None:
        self.samples.setdefault(metric, (unit, []))[1].append(value)

    def fail(self, scenario: str, size: int | None, error: BaseException, hub: Hub | None) -> None:
        self.failures.append({"scenario": scenario, "size": size, "error": f"{type(error).__name__}: {error}"[:2000],
                              "log": hub.tail() if hub else ""})

    def metrics(self) -> dict[str, Any]:
        return {metric: benchmark_data.metric_entry(values, unit) for metric, (unit, values) in self.samples.items()}


def run(options: Options, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Every scenario at every project size; one ``MonkeyHubBenchmark@1`` document."""

    started_at = datetime.now(timezone.utc)
    clock = time.perf_counter()
    work = options.work
    code_root = options.code_root.resolve()
    environment = os.environ.copy()
    warm_prefix = work / "pycache" / "warm"
    collect = _Collector()
    sessions = iter(range(1, 10_000))

    def hub_for(runtime_root: Path, prefix: Path, label: str) -> Hub:
        number = next(sessions)
        return Hub(code_root, runtime_root, hub_environment(environment, work / "env", prefix),
                   work / "logs" / f"{number:03d}-{label}.log")

    def session(scenario: str, size: int | None, runtime_root: Path, prefix: Path,
                body: Callable[[Hub], None]) -> bool:
        hub = hub_for(runtime_root, prefix, f"{scenario}-{size or 'hub'}")
        try:
            body(hub)
            return True
        except Exception as error:  # noqa: BLE001 - one failed sample never stops the run
            collect.fail(scenario, size, error, hub)
            say(f"  {scenario} {size or ''} failed: {error}")
            return False
        finally:
            hub.stop()

    projects = {size: copy_project(source.resolve(), work / "projects" / str(size), size)
                for size, source in sorted(options.projects.items())}

    def opened(hub: Hub, project: Project, *, measured: bool = True) -> str:
        start_ms = hub.start()
        opening = open_project(hub, project)
        projection_ms = await_projection(hub, opening["runtimeId"])
        if measured:
            collect.add("hub_start.warm", "ms", start_ms)
            collect.add(f"project_open.{project.size}runs", "ms", opening["totalMs"])
            collect.details["projectOpen"].setdefault(f"{project.size}runs", []).append(
                {key: round(value, 1) for key, value in opening.items() if key.endswith("Ms")})
            collect.details["projection"].setdefault(f"{project.size}runs", []).append(round(projection_ms, 1))
        time.sleep(options.settle_seconds)
        return opening["runtimeId"]

    # Each size once, unmeasured: the warm bytecode prefix and every project's index.
    for size, project in projects.items():
        say(f"priming {size} runs")

        def prime(hub: Hub, project: Project = project) -> None:
            runtime_id = opened(hub, project, measured=False)
            if not await_index(hub, runtime_id):
                say("  the project index did not answer; workers will build it again")
            ModelingOpening(hub, runtime_id).run()
            read_routes(hub, runtime_id)

        # Compiling everything can outlast a slow runner's worker start; the second try starts
        # warmer, and only its failure is the run's.
        if not session("prime", size, work / "runtime" / str(size), warm_prefix, prime):
            collect.details.setdefault("primeRetried", []).append(collect.failures.pop())
            session("prime", size, work / "runtime" / str(size), warm_prefix, prime)

    say(f"hub start, first launch x{options.first_launch_samples}")
    for sample in range(options.first_launch_samples):
        def first_launch(hub: Hub) -> None:
            collect.add("hub_start.first_launch", "ms", hub.start())

        session("hub_start", None, work / "runtime" / "first-launch", work / "pycache" / f"first-{sample + 1}",
                first_launch)

    for size, project in projects.items():
        runtime_root = work / "runtime" / str(size)
        for sample in range(options.samples):
            say(f"{size} runs, sample {sample + 1}/{options.samples}")

            def routes(hub: Hub) -> None:
                runtime_id = opened(hub, project)
                for slug, phases in read_routes(hub, runtime_id).items():
                    for phase, value in phases.items():
                        collect.add(f"route.{slug}.{phase}.{size}runs", "ms", value)

            def modeling(hub: Hub) -> None:
                runtime_id = opened(hub, project)
                for phase in ("cold", "warm"):
                    opening = ModelingOpening(hub, runtime_id).run()
                    collect.add(f"open_modeling.{phase}.{size}runs", "ms", opening["totalMs"])
                    collect.add(f"open_modeling.model.{phase}.{size}runs", "ms", opening["modelMs"])
                    collect.details["openModeling"].setdefault(f"{phase}, {size} runs", opening)

            session("route", size, runtime_root, warm_prefix, routes)
            session("open_modeling", size, runtime_root, warm_prefix, modeling)

        say(f"{size} runs, idle {options.idle_seconds:.0f} s")

        def idle(hub: Hub) -> None:
            runtime_id = opened(hub, project)
            ModelingOpening(hub, runtime_id).run()
            measured = measure_idle(hub, runtime_id, options.idle_seconds)
            for name in ("hub", "worker"):
                collect.add(f"idle.{name}.cpu.{size}runs", "s", measured[name]["cpuSeconds"])
                collect.add(f"idle.{name}.read.{size}runs", "bytes", measured[name]["readBytes"])
            collect.details["idle"][f"{size}runs"] = measured

        session("idle", size, runtime_root, warm_prefix, idle)

    return {
        "schema": SCHEMA,
        "commit": _revision(code_root),
        "date": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runner": runner_description(options.runner_key),
        "settings": {"samples": options.samples, "firstLaunchSamples": options.first_launch_samples,
                     "idleSeconds": options.idle_seconds, "settleSeconds": options.settle_seconds,
                     "sizes": sorted(projects)},
        "projects": {f"{size}runs": {"runs": sum(1 for path in (project.directory / "runs").iterdir() if path.is_dir()),
                                     "jsonFiles": sum(1 for _ in project.directory.rglob("*.json"))}
                     for size, project in projects.items()},
        "durationSeconds": round(time.perf_counter() - clock, 1),
        "metrics": collect.metrics(),
        "details": collect.details,
        "failures": collect.failures,
    }


def _project_option(value: str) -> tuple[int, Path]:
    size, separator, path = value.partition("=")
    if not separator or not size.isdigit():
        raise argparse.ArgumentTypeError("--project takes SIZE=DIR, such as 30=../projects/synthetic-bench")
    return int(size), Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="measure every scenario and write one result")
    command.add_argument("--project", type=_project_option, action="append", required=True, metavar="SIZE=DIR",
                         help="a built synthetic project of SIZE runs (copied, never opened)")
    command.add_argument("--out", type=Path, required=True, help="where to write the MonkeyHubBenchmark@1 result")
    command.add_argument("--summary", type=Path, help="where to write the Markdown summary")
    command.add_argument("--work", type=Path, help="scratch directory (default: a new temporary one)")
    command.add_argument("--samples", type=int, default=5)
    command.add_argument("--first-launch-samples", type=int)
    command.add_argument("--idle-seconds", type=float, default=60.0)
    command.add_argument("--settle-seconds", type=float, default=2.0)
    command.add_argument("--runner-key", help="the results directory on the data branch (default: the OS)")
    command.add_argument("--budgets", type=Path, default=benchmark_data.DEFAULT_BUDGETS)
    command.add_argument("--budget-override", action="append", default=[], metavar="METRIC=VALUE")
    options = parser.parse_args(argv)
    # A console that cannot show a sign gets a placeholder, not a traceback at the end of a run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    work = (options.work or Path(tempfile.mkdtemp(prefix="monkeyhub-benchmark-"))).resolve()
    if work.exists() and any(work.iterdir()):
        parser.error(f"--work {work} is not empty")
    budgets = benchmark_data.override_budgets(benchmark_data.load_budgets(options.budgets), options.budget_override)
    projects = dict(options.project)
    result = run(Options(CODE_ROOT, projects, work, samples=options.samples,
                         first_launch_samples=options.samples if options.first_launch_samples is None
                         else options.first_launch_samples,
                         idle_seconds=options.idle_seconds, settle_seconds=options.settle_seconds,
                         runner_key=options.runner_key),
                 say=lambda line: print(line, flush=True))
    benchmark_data.write_file(options.out, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    text = benchmark_data.summary_markdown(result, budgets)
    if options.summary is not None:
        benchmark_data.write_file(options.summary, text)
    sys.stdout.write(text)
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
