"""Measure MonkeyHub the same way every day (GH-547): a checkout in CI, the installed app on a person's machine.

    python tools/benchmarks/daily_benchmark.py run --project 30=<dir> --project 150=<dir> \\
        --out <result.json> [--summary <summary.md>] [--work <dir>] [--samples 5] \\
        [--first-launch-samples 5] [--idle-seconds 60] [--budget-override METRIC=VALUE] \\
        [--installed <version dir>] [--runner-key KEY]
    python tools/benchmarks/daily_benchmark.py local [--version-dir <version dir>] [--dev-root <dir>] \\
        [--sizes 30,150] [--samples 3] [--remote-url URL] [--no-publish] [--force]

Every Hub this starts runs this checkout's ``apps/monkeyhub/run.py`` on its own
port, with its own runtime root, ``APPDATA``, ``LOCALAPPDATA`` and bytecode
prefix under ``--work``, and without the account's credential store: nothing an
installed MonkeyHub reads or writes is touched. Each project is copied once
into ``--work`` and settled (``projection_check.settle``); the projects given
are never opened. A project comes from
``services/project-runtime/tests/synthetic_project.py``, built before the run
(``docs/development/benchmarks.md``): a repository tool takes nothing from a
test suite.

With ``--installed`` the Hubs run an installed version instead: its own
interpreter (``_runtime/python``) and ``run.py``, from copies of the version
directory, never from the directory given, with every ``PYTHON*`` variable
dropped as the package's updater drops them. A copy never sits directly under
a ``versions`` directory, so the Hub's update transaction stays off: it neither
checks for an update nor points the desktop shortcut anywhere. A first launch
starts a new copy, as shipped; the first copy, once launched, is the warm
version every later Hub starts from. ``run --installed`` makes its copies under
``--work``; a run given ``Options.keep_in`` keeps one copy per version there,
and launches a version first only when that copy does not exist yet.

``local`` is the scheduled entry on a person's Windows machine: it measures the
version the desktop shortcut opens in installed mode under
``<development root>/temp/benchmark-local``, from the copy it keeps of that
version, so ``hub_start.first_launch`` is measured once per version. It skips on
battery, when the processors are busy, when nobody has left the computer idle
long enough or when today's result is already published, and runs beside an
open MonkeyHub, recording that it was open. It builds the projects with this
checkout's generator in a process of their own, and pushes the result to
``results/windows-local/`` on the data branch. It never stops a process it did
not start and deletes nothing: earlier runs and the copies of earlier versions
are renamed into the development root's ``_TRASH_<YYYYMMDD>``.
``register_local_benchmark.ps1`` registers it.

Scenarios, every sample in a Hub of its own: ``hub_start.first_launch``
``--first-launch-samples`` times (locally once per version), idle once per
project size, and the rest ``--samples`` times at each size:

- ``hub_start``: process start to the first ``/api/health`` that answers, as a
  first launch and warm. From a checkout, a first launch has an empty
  ``PYTHONPYCACHEPREFIX``, so every module is compiled, and warm keeps a prefix
  from earlier launches. An installed first launch starts a new copy with the
  bytecode the package ships (#548); warm starts the copy launched before.
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
size one unmeasured Hub first opens everything once, so the warm bytecode (the
prefix, or the installed copy) and the project's index in its runtime root
exist; every measured Hub of that size reuses both, as a returning user does.

The result is one ``MonkeyHubBenchmark@1`` file; ``benchmark_data.py`` judges,
summarises and publishes it.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import http.client
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import shutil
import site
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlencode
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.benchmarks import benchmark_data
from tools.benchmarks.projection_check import ROUTES, settle
from tools.dev import source_roots, workspace

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
# An installed version: its own interpreter, whose python313._pth lists the
# source roots, runs its run.py the way the desktop host does (-u, from the
# version directory), with an account credential store that holds nothing. A
# version that opens the account's store some other way is never started.
INSTALLED_PYTHON = Path("_runtime") / "python" / "python.exe"
INSTALLED_ENTRY = Path("apps") / "monkeyhub" / "run.py"
INSTALLED_LAUNCHER = """\
import runpy, sys
from monkeyhub_api.settings import credentials
if not callable(getattr(credentials, "account_store", None)):
    raise SystemExit("this version opens the account's credential store another way; the benchmark does not start it")
credentials.account_store = credentials.UnavailableSecretStore
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
# Where an installed version's results go on the data branch.
LOCAL_RUNNER_KEY = "windows-local"
# The local runner's directory under the development root (tools/dev/workspace.py),
# and where in it one copy of each installed version is kept: never named versions.
LOCAL_DIRECTORY = Path("temp") / "benchmark-local"
KEPT_DIRECTORY = "installed"
# The desktop shortcut the installer writes (apps/monkeyhub/installer/install.ps1).
SHORTCUT_NAME = "MonkeyHub.lnk"
# The desktop host's executable, now and before the rename.
DESKTOP_HOSTS = ("monkeyhub.exe", "monkeyarch.exe")
# A command line that runs a Hub, one of its services or a Project Runtime process, from any checkout or version.
HUB_COMMANDS = ("apps\\monkeyhub\\run.py", "apps/monkeyhub/run.py", "-m project_runtime")
# The workflow's generator step, run from services/project-runtime in a process of its own.
BUILD_PROJECT = """\
import pathlib, sys
from tests.synthetic_project import build_synthetic_project
print(build_synthetic_project(pathlib.Path(sys.argv[1]), project_id=sys.argv[2], runs=int(sys.argv[3])))
"""
# Input this recent while the runner waits for an idle computer means somebody is back.
IDLE_TOLERANCE_S = 2.0
IDLE_POLL_S = 30.0
# How often, between scenarios, a local run looks at what MonkeyHub runs beside it.
BESIDE_EVERY_S = 10.0


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
        self._image = kernel32.QueryFullProcessImageNameW
        self._image.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self._image.restype = wintypes.BOOL
        self._query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        self._query.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                ctypes.POINTER(ctypes.c_ulong)]
        self._query.restype = ctypes.c_long
        self._handles: dict[int, Any] = {}

    def _entries(self) -> list[tuple[int, int, str]]:
        """Every process in one snapshot: its id, its parent's id and its executable's name."""

        ctypes = self._ctypes
        snapshot = self._snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        try:
            entry = self._ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = []
            more = self._first(snapshot, ctypes.byref(entry))
            while more:
                found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
                more = self._next(snapshot, ctypes.byref(entry))
            return found
        finally:
            self._close(snapshot)

    def parents(self) -> dict[int, int]:
        return {pid: parent for pid, parent, _ in self._entries()}

    def images(self) -> list[dict[str, Any]]:
        """Every process with its executable's path and command line, where this account may read them."""

        rows = []
        for pid, _, name in self._entries():
            path = command = None
            handle = self._open(0x1000, False, pid) if pid else None  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    path, command = self._path_of(handle), self._command_of(handle)
                finally:
                    self._close(handle)
            rows.append({"pid": pid, "name": name, "path": path, "commandLine": command})
        return rows

    def _path_of(self, handle: Any) -> str | None:
        from ctypes import wintypes

        ctypes = self._ctypes
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if self._image(handle, 0, buffer, ctypes.byref(size)) else None

    def _command_of(self, handle: Any) -> str | None:
        """The command line through ProcessCommandLineInformation (60): a UNICODE_STRING and its text."""

        ctypes = self._ctypes
        size = ctypes.c_ulong(0)
        self._query(handle, 60, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if self._query(handle, 60, buffer, size.value, ctypes.byref(size)) != 0:
            return None

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

        text = UnicodeString.from_buffer(buffer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""

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


def installed_environment(base: Mapping[str, str], env_root: Path) -> dict[str, str]:
    """An installed Hub's variables: this run's app data, and nothing that steers Python or MonkeyHub.

    Every ``PYTHON*`` variable goes, as the package's updater drops them before
    it runs a version: the bundled interpreter still honours them, and a
    bytecode prefix would pass over the bytecode the version ships.
    """

    environment = {key: value for key, value in base.items()
                   if not key.upper().startswith(("PYTHON", *DROPPED_PREFIXES))}
    environment.update({"APPDATA": str(env_root / "appdata"), "LOCALAPPDATA": str(env_root / "localappdata")})
    return environment


def hub_command(code_root: Path, installed: bool = False) -> list[str]:
    """The interpreter and launcher a Hub starts with, before run.py's options.

    From a checkout this interpreter runs ``LAUNCHER``; an installed version's
    own interpreter runs its ``run.py`` through ``INSTALLED_LAUNCHER``.
    """

    if installed:
        return [str(code_root / INSTALLED_PYTHON), "-u", "-c", INSTALLED_LAUNCHER, str(code_root / INSTALLED_ENTRY)]
    return [sys.executable, "-c", LAUNCHER, str(code_root)]


class Hub:
    """One Hub process from the checkout or an installed copy, on its own ports, stopped as the desktop host stops it."""

    def __init__(self, code_root: Path, runtime_root: Path, environment: Mapping[str, str], log: Path, *,
                 installed: bool = False) -> None:
        self.code_root, self.runtime_root, self.environment, self.log = code_root, runtime_root, dict(environment), log
        self.installed = installed
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
        command = [*hub_command(self.code_root, self.installed), "--runtime-root", str(self.runtime_root),
                   "--port", str(self.port), "--no-browser", "--managed-stdin", "--managed-instance-id", instance]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        # Normal priority whatever starts the harness: a scheduled task starts it below normal.
        options: dict[str, Any] = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.NORMAL_PRIORITY_CLASS}
                                   if os.name == "nt" else {"start_new_session": True})
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
                # This Hub's own tree only: the process this harness started and its children.
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False,
                               **benchmark_data.NO_WINDOW)
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


@dataclass(frozen=True)
class InstalledVersion:
    """An installed version directory as its build describes it."""

    directory: Path
    commit: str
    release: str | None
    channel: str | None
    python: str | None
    bytecode_files: int | None

    @property
    def name(self) -> str:
        return self.directory.name

    def describe(self) -> dict[str, Any]:
        return {"version": self.name, "release": self.release, "channel": self.channel, "python": self.python,
                "bytecodeFiles": self.bytecode_files}


def read_installed(directory: Path) -> InstalledVersion:
    """The version directory's interpreter, entry, commit and build facts; refuse anything else."""

    directory = Path(directory).resolve()
    for relative in (INSTALLED_PYTHON, INSTALLED_ENTRY):
        if not (directory / relative).is_file():
            raise HarnessError(f"{directory} is not an installed MonkeyHub version: {relative.as_posix()} is missing")
    try:
        commit = (directory / "source-version.txt").read_text(encoding="utf-8-sig").strip()
        build = json.loads((directory / "build-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HarnessError(f"{directory} has no readable source-version.txt and build-info.json: {error}") from error
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not isinstance(build, dict) or build.get("sourceCommit") != commit:
        raise HarnessError(f"{directory} names no source commit, or its build-info.json names another")
    desktop = build.get("desktop") if isinstance(build.get("desktop"), dict) else {}
    bytecode = build.get("pythonBytecode") if isinstance(build.get("pythonBytecode"), dict) else {}
    return InstalledVersion(directory, commit, build.get("releaseVersion") or desktop.get("version"),
                            build.get("channel"), build.get("pythonVersion"), bytecode.get("files"))


def copy_version(version: InstalledVersion, parent: Path) -> Path:
    """A fresh copy of the version as shipped, at ``parent/<version name>``; the directory given is only read.

    The Hub runs its update transaction only from a directory right under one
    named ``versions`` (``DesktopUpdates.supported``): a copy anywhere else
    never checks for an update, downloads one or points the desktop shortcut at
    itself.
    """

    if parent.name.casefold() == "versions":
        raise HarnessError("a measured copy may not sit right under a versions directory: its Hub would update")
    target = parent / version.name
    shutil.copytree(version.directory, target)
    return target


def installation_root(version: Path) -> Path:
    """The installation a version directory belongs to: the folder holding its versions directory."""

    return version.parent.parent if version.parent.name.casefold() == "versions" else version


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
                               check=False, stdin=subprocess.DEVNULL, **benchmark_data.NO_WINDOW)
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


def runner_description(key: str | None = None, python: str | None = None) -> dict[str, Any]:
    """The machine a result comes from; ``python`` is the Hub's interpreter when it is not this one."""

    system = platform.system().lower()
    return {"key": key or system, "os": system, "osVersion": platform.release(), "platform": platform.platform(),
            "python": python or platform.python_version(), "implementation": platform.python_implementation(),
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
    # An installed version directory to measure instead of the checkout; only ever copied.
    installed: Path | None = None
    # Where one copy per installed version is kept across runs: a version whose copy is
    # there is not launched first again, and first_launch_samples does not apply.
    keep_in: Path | None = None


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
    installed = read_installed(options.installed) if options.installed is not None else None
    # Measuring an installed version, the checkout is only the harness: its commit is a detail.
    harness = _checkout_value(code_root, "rev-parse", "HEAD") if installed is not None else None
    environment = os.environ.copy()
    collect = _Collector()
    sessions = iter(range(1, 10_000))

    def hub_for(runtime_root: Path, launch: Path, label: str) -> Hub:
        """``launch`` is a checkout Hub's bytecode prefix, or the copy an installed Hub runs from."""

        number = next(sessions)
        log = work / "logs" / f"{number:03d}-{label}.log"
        if installed is not None:
            return Hub(launch, runtime_root, installed_environment(environment, work / "env"), log, installed=True)
        return Hub(code_root, runtime_root, hub_environment(environment, work / "env", launch), log)

    def session(scenario: str, size: int | None, runtime_root: Path, launch: Path,
                body: Callable[[Hub], None]) -> bool:
        hub = hub_for(runtime_root, launch, f"{scenario}-{size or 'hub'}")
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

    def prime_all(warm: Path) -> None:
        """Each size once, unmeasured: the warm bytecode and every project's index."""

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
            if not session("prime", size, work / "runtime" / str(size), warm, prime):
                collect.details.setdefault("primeRetried", []).append(collect.failures.pop())
                session("prime", size, work / "runtime" / str(size), warm, prime)

    def first_launches(launch: Callable[[int], Path], samples: int) -> None:
        say(f"hub start, first launch x{samples}")
        for sample in range(1, samples + 1):
            def first_launch(hub: Hub) -> None:
                collect.add("hub_start.first_launch", "ms", hub.start())

            session("hub_start", None, work / "runtime" / "first-launch", launch(sample), first_launch)

    first_launch_samples = options.first_launch_samples
    copies = None
    if installed is None:
        warm = work / "pycache" / "warm"
        prime_all(warm)
        first_launches(lambda sample: work / "pycache" / f"first-{sample}", first_launch_samples)
    else:
        def fresh_copy(parent: Path, why: str) -> Path:
            say(f"copying {installed.name} {why}")
            return copy_version(installed, parent)

        # A first launch starts a copy as shipped. That copy, once launched, is warm, as an
        # installed version is after its first launch: every later Hub starts from it.
        if options.keep_in is not None:
            warm = options.keep_in / installed.name
            if warm.is_dir():
                copies, first_launch_samples = "kept", 0
                say(f"starting every Hub from the copy of {installed.name} kept since its first launch")
            else:
                copies, first_launch_samples = "new", 1
                first_launches(lambda sample: fresh_copy(options.keep_in, "to keep, for its first launch"), 1)
        else:
            copies = "per-run"
            first_launches(lambda sample: fresh_copy(work / "hubs" / f"first-{sample}", f"for first launch {sample}"),
                           first_launch_samples)
            warm = (work / "hubs" / "first-1" / installed.name if first_launch_samples > 0
                    else fresh_copy(work / "hubs" / "warm", "for the warm Hubs"))
        prime_all(warm)

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

            session("route", size, runtime_root, warm, routes)
            session("open_modeling", size, runtime_root, warm, modeling)

        say(f"{size} runs, idle {options.idle_seconds:.0f} s")

        def idle(hub: Hub) -> None:
            runtime_id = opened(hub, project)
            ModelingOpening(hub, runtime_id).run()
            measured = measure_idle(hub, runtime_id, options.idle_seconds)
            for name in ("hub", "worker"):
                collect.add(f"idle.{name}.cpu.{size}runs", "s", measured[name]["cpuSeconds"])
                collect.add(f"idle.{name}.read.{size}runs", "bytes", measured[name]["readBytes"])
            collect.details["idle"][f"{size}runs"] = measured

        session("idle", size, runtime_root, warm, idle)

    settings: dict[str, Any] = {"samples": options.samples, "firstLaunchSamples": first_launch_samples,
                                "idleSeconds": options.idle_seconds, "settleSeconds": options.settle_seconds,
                                "sizes": sorted(projects)}
    if installed is not None:
        # copies: "new" when this run made and first launched the kept copy, "kept" when it
        # started from one an earlier run made, "per-run" when every copy is the run's own.
        settings.update({"installed": {**installed.describe(), "copies": copies}, "harnessCommit": harness})
    return {
        "schema": SCHEMA,
        "commit": installed.commit if installed is not None else _revision(code_root),
        "date": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runner": runner_description(options.runner_key, installed.python if installed is not None else None),
        "settings": settings,
        "projects": {f"{size}runs": {"runs": sum(1 for path in (project.directory / "runs").iterdir() if path.is_dir()),
                                     "jsonFiles": sum(1 for _ in project.directory.rglob("*.json"))}
                     for size, project in projects.items()},
        "durationSeconds": round(time.perf_counter() - clock, 1),
        "metrics": collect.metrics(),
        "details": collect.details,
        "failures": collect.failures,
    }


# ---- the local runner ---------------------------------------------------------------
#
# Every check below is a pure function of what Windows reports, so it is tested
# without a scheduled task, an installed app or a busy machine; the readers
# beside them only ask Windows.


def _windows_path(value: str) -> str:
    """A Windows path as Windows compares it: no \\\\?\\ prefix, one case, backslashes."""

    text = value[4:] if value.startswith("\\\\?\\") else value
    return ntpath.normcase(ntpath.normpath(text))


def _under(root: Path | str) -> str:
    return _windows_path(str(root)).rstrip("\\") + "\\"


def monkeyhub_processes(processes: Iterable[Mapping[str, Any]], installations: Iterable[Path | str], *,
                        own_root: Path | str | None = None, own_pid: int | None = None) -> dict[str, list[str]]:
    """The MonkeyHub processes running beside a local run, as ``name pid`` by kind; the runner's own left out.

    ``desktop``: the desktop app, a desktop host (MonkeyHub.exe, or
    MonkeyArch.exe before the rename) or any process whose executable lies
    under one of ``installations``, the folders holding a ``versions``
    directory. ``elsewhere``: any Python whose command line runs a Hub, a Hub
    service or a Project Runtime process from anywhere else, such as a
    checkout. A process whose executable lies under ``own_root`` (the runner's
    copies) or whose id is ``own_pid`` is the runner's own.
    """

    installed = [_under(root) for root in installations]
    own = _under(own_root) if own_root is not None else None
    found: dict[str, list[str]] = {"desktop": [], "elsewhere": []}
    for row in processes:
        pid = row.get("pid")
        name, path = str(row.get("name") or ""), str(row.get("path") or "")
        where = _windows_path(path) if path else ""
        if (own_pid is not None and pid == own_pid) or (own and where.startswith(own)):
            continue
        command = str(row.get("commandLine") or "").casefold()
        if name.casefold() in DESKTOP_HOSTS or (where and any(where.startswith(root) for root in installed)):
            found["desktop"].append(f"{name} {pid}")
        elif name.casefold().startswith("python") and any(marker in command for marker in HUB_COMMANDS):
            found["elsewhere"].append(f"{name} {pid}")
    return found


def monkeyhub_beside(checks: Sequence[Mapping[str, Sequence[str]]]) -> dict[str, Any]:
    """What ``monkeyhub_processes`` saw over a run: whether the desktop app was open, at how many checks, how big."""

    return {"desktopOpen": any(check["desktop"] for check in checks), "checks": len(checks),
            "desktopSeen": sum(1 for check in checks if check["desktop"]),
            "desktopProcesses": max((len(check["desktop"]) for check in checks), default=0),
            "otherHubs": any(check["elsewhere"] for check in checks)}


def cpu_busy_percent(first: Sequence[int], second: Sequence[int]) -> float:
    """How busy the processors were between two ``system_times`` readings, in percent.

    Each reading is (idle, kernel, user) time of every processor; kernel time
    includes idle time.
    """

    idle, kernel, user = (after - before for before, after in zip(first, second))
    total = kernel + user
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (total - idle) / total))


def power_reason(ac_line: int, battery_flag: int) -> str | None:
    """Why the power source stops a run (GetSystemPowerStatus's fields), or None on mains power."""

    if ac_line == 1:
        return None
    if ac_line == 0:
        return "the computer runs on battery"
    # 255: unknown. A machine with no system battery (128) is a desktop on mains power.
    return None if battery_flag & 128 else "the power source is unknown"


def idle_seconds(now_tick: int, last_input_tick: int) -> float:
    """Seconds since the last input, from two millisecond tick counts that wrap every 49.7 days."""

    return ((now_tick - last_input_tick) & 0xFFFFFFFF) / 1000.0


def idle_wait(idle_s: float, expected_s: float | None, waited_s: float, minimum_s: float,
              longest_s: float) -> tuple[bool, str | None]:
    """(ready, why not): ready once nobody has used the computer for ``minimum_s``.

    ``expected_s`` is the idle time the last reading and the pause since then
    add up to; less means somebody used the computer, which ends the wait, as
    does waiting ``longest_s``. (False, None) means wait on.
    """

    if idle_s >= minimum_s:
        return True, None
    if expected_s is not None and idle_s + IDLE_TOLERANCE_S < expected_s:
        return False, "somebody used the computer while the runner waited for it to stay idle"
    if waited_s >= longest_s:
        return False, f"the computer was not left idle for {minimum_s / 60:g} min within {longest_s / 60:g} min"
    return False, None


def published_today(runs: Path, today: date) -> str | None:
    """Today's run that published its result, if any: one a day is enough.

    A run that failed, stopped early, was not published (``--no-publish``) or
    could not push leaves no ``published.json``, so a later idle period tries again.
    """

    for directory in sorted(runs.glob(f"{today:%Y%m%d}-*"), reverse=True):
        if (directory / "published.json").is_file():
            return directory.name
    return None


def shortcut_target(data: bytes) -> tuple[str | None, str | None]:
    """A Windows shell link's target path and working directory ([MS-SHLLINK]); None for what it lacks."""

    if len(data) < 0x4C or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        raise ValueError("not a Windows shell link")
    flags = struct.unpack_from("<I", data, 0x14)[0]
    offset = 0x4C
    if flags & 0x1:  # HasLinkTargetIDList
        offset += 2 + struct.unpack_from("<H", data, offset)[0]
    target = None
    if flags & 0x2:  # HasLinkInfo
        size, header, info_flags, _, base, _, _ = struct.unpack_from("<7I", data, offset)
        if info_flags & 0x1:  # VolumeIDAndLocalBasePath
            if header >= 0x24:
                start = offset + struct.unpack_from("<I", data, offset + 28)[0]
                end = start
                while data[end:end + 2] != b"\0\0":
                    if end + 2 > len(data):
                        raise ValueError("the link's base path has no end")
                    end += 2
                target = data[start:end].decode("utf-16-le")
            else:
                start = offset + base
                target = data[start:data.index(b"\0", start)].decode("mbcs" if os.name == "nt" else "latin-1")
        offset += size
    unicode = bool(flags & 0x80)
    directory = None
    # NAME, RELATIVE_PATH, WORKING_DIR, COMMAND_LINE_ARGUMENTS and ICON_LOCATION, in that order.
    for bit in (0x4, 0x8, 0x10, 0x20, 0x40):
        if flags & bit:
            count = struct.unpack_from("<H", data, offset)[0]
            width = 2 if unicode else 1
            text = data[offset + 2:offset + 2 + count * width]
            offset += 2 + count * width
            if bit == 0x10:
                directory = text.decode("utf-16-le" if unicode else ("mbcs" if os.name == "nt" else "latin-1"))
    return target, directory


def version_of_shortcut(target: str | None, directory: str | None) -> Path | None:
    """The version directory a MonkeyHub shortcut opens: its MonkeyHub.exe's folder, else its working directory."""

    if target and ntpath.basename(target).casefold() == "monkeyhub.exe":
        return Path(ntpath.dirname(target))
    return Path(directory) if directory else None


def desktop_directory() -> Path:
    """The account's Desktop folder, where the installer puts the shortcut (FOLDERID_Desktop)."""

    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    folder = Guid(0xB4BFCC3A, 0xDB2C, 0x424C, (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
    found = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(Guid), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(found)) != 0:
            raise OSError("SHGetKnownFolderPath(FOLDERID_Desktop) failed")
        return Path(found.value)
    finally:
        ole32.CoTaskMemFree(found)


def shortcut_version(desktop: Path | None = None) -> Path | None:
    """The version directory the desktop shortcut opens, or None without a readable MonkeyHub shortcut."""

    try:
        target, directory = shortcut_target(((desktop or desktop_directory()) / SHORTCUT_NAME).read_bytes())
    except (OSError, ValueError, struct.error):
        return None
    return version_of_shortcut(target, directory)


def running_processes() -> list[dict[str, Any]]:
    counters = _WindowsProcesses()
    try:
        return counters.images()
    finally:
        counters.close()


def system_times() -> tuple[int, int, int]:
    """Idle, kernel and user time of every processor so far, in 100 ns."""

    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(3)]
    if not ctypes.WinDLL("kernel32").GetSystemTimes(*(ctypes.byref(value) for value in times)):
        raise OSError("GetSystemTimes failed")
    idle, kernel, user = ((value.dwHighDateTime << 32) | value.dwLowDateTime for value in times)
    return idle, kernel, user


def power_status() -> tuple[int, int]:
    """GetSystemPowerStatus's ACLineStatus and BatteryFlag."""

    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    status = Status()
    if not ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(status)):
        raise OSError("GetSystemPowerStatus failed")
    return status.ACLineStatus, status.BatteryFlag


def user_idle_seconds() -> float:
    """Seconds since anybody used this session's keyboard or mouse."""

    import ctypes
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetTickCount.restype = wintypes.DWORD
    if not ctypes.WinDLL("user32").GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo failed")
    return idle_seconds(kernel32.GetTickCount(), info.dwTime)


def wait_until_idle(minimum_s: float, longest_s: float, *, read: Callable[[], float] = user_idle_seconds,
                    sleep: Callable[[float], None] = time.sleep, step: float = IDLE_POLL_S) -> str | None:
    """Wait until nobody has used the computer for ``minimum_s``; answer why that did not happen, or None."""

    waited, expected = 0.0, None
    while True:
        idle = read()
        ready, reason = idle_wait(idle, expected, waited, minimum_s, longest_s)
        if ready:
            return None
        if reason:
            return reason
        pause = min(step, max(1.0, minimum_s - idle))
        sleep(pause)
        waited += pause
        expected = idle + pause


def log_event(path: Path, line: str) -> None:
    """Append one event to the local log, each line with the local time; echo it to a console if there is one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        for text in line.splitlines() or [""]:
            stream.write(f"{stamp} {text}\n")
    # A scheduled run under pythonw has no console.
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass


@contextmanager
def hold_lock(path: Path) -> Iterator[bool]:
    """Hold the local runner's lock while one run lasts; yield False when another run holds it.

    The lock is one byte range of a file that stays where it is; it is let go
    when this process ends, however it ends.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def move_to_trash(paths: Iterable[Path], trash: Path, say: Callable[[str], None]) -> list[Path]:
    """Rename each path into ``trash``; nothing is deleted. Answer where each went.

    The development root's ``_TRASH_<YYYYMMDD>`` is where a person empties
    things from; a rename keeps them on the same volume and never copies. A
    name already there gets ``-2``, ``-3``; a path that cannot move stays.
    """

    moved = []
    for path in paths:
        target, number = trash / path.name, 2
        while os.path.lexists(target):
            target, number = trash / f"{path.name}-{number}", number + 1
        try:
            trash.mkdir(parents=True, exist_ok=True)
            path.rename(target)
        except OSError as error:
            say(f"could not move {path} to {target}: {error}")
            continue
        moved.append(target)
    return moved


def stale_kept(kept: Path, name: str) -> list[Path]:
    """What in the kept directory no run will start from: every entry but this version's copy and its record.

    A copy counts once its record (``<name>.json``) says a published run
    measured its first launch; a copy without one, left by a run that stopped
    or did not publish, is stale with the copies and records of other versions.
    """

    if not kept.is_dir():
        return []
    ready = (kept / name).is_dir() and (kept / f"{name}.json").is_file()
    keep = {name, f"{name}.json"} if ready else set()
    return [entry for entry in sorted(kept.iterdir()) if entry.name not in keep]


def console_python() -> str:
    """This interpreter, as the console python.exe beside it when a scheduled run uses pythonw.exe."""

    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console) if executable.name.casefold() == "pythonw.exe" and console.is_file() else str(executable)


def build_projects(code_root: Path, parent: Path, sizes: Sequence[int], say: Callable[[str], None]) -> dict[int, Path]:
    """Build each synthetic project with this checkout's generator, as the workflow does.

    The generator runs in a process of its own, from the Runtime's directory
    and with this checkout's source roots: nothing here imports a test suite.
    """

    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in DROPPED_VARIABLES and not key.upper().startswith(DROPPED_PREFIXES)}
    environment.update({"PYTHONPATH": os.pathsep.join(source_roots.roots(code_root)), "PYTHONUTF8": "1"})
    built = {}
    for size in sizes:
        project_id, started = f"synthetic-bench-{size}", time.perf_counter()
        completed = subprocess.run([console_python(), "-c", BUILD_PROJECT, str(parent / str(size)), project_id, str(size)],
                                   cwd=code_root / "services" / "project-runtime", env=environment,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   stdin=subprocess.DEVNULL, **benchmark_data.NO_WINDOW)
        if completed.returncode != 0:
            raise HarnessError(f"building {project_id} failed:\n{(completed.stderr or completed.stdout)[-3000:]}")
        built[size] = parent / str(size) / project_id
        say(f"built {project_id} in {time.perf_counter() - started:.0f} s")
    return built


def _checkout_value(code_root: Path, *args: str) -> str | None:
    """One value Git reports for the checkout, or None."""

    completed = subprocess.run(["git", "-C", str(code_root), *args], capture_output=True, text=True, check=False,
                               encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, **benchmark_data.NO_WINDOW)
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def publishing_identity(code_root: Path, url: str | None = None) -> tuple[str, tuple[str, str]]:
    """Where and as whom the checkout publishes: ``url`` or its origin's, and the author its Git configuration names.

    A new publisher repository has no configuration of its own; pushing uses
    the account's existing credentials for that URL.
    """

    url = url or _checkout_value(code_root, "remote", "get-url", "origin")
    name, email = (_checkout_value(code_root, "config", "--get", key) for key in ("user.name", "user.email"))
    if not name or not email:
        raise HarnessError(f"{code_root} names no Git author (user.name and user.email) to publish as")
    if not url:
        raise HarnessError(f"{code_root} has no origin remote to publish to; pass --remote-url")
    return url, (name, email)


def _monkeyhub_now(installations: Sequence[Path], root: Path) -> dict[str, list[str]] | None:
    """The MonkeyHub processes running now beside this runner, or None when Windows will not list them."""

    try:
        return monkeyhub_processes(running_processes(), installations, own_root=root, own_pid=os.getpid())
    except OSError:
        return None


def run_local(arguments: argparse.Namespace) -> int:
    """The local runner: check, measure, write, publish and log one run; answer the exit status."""

    if os.name != "nt":
        raise SystemExit("the local runner measures the installed MonkeyHub on Windows")
    dev_root = arguments.dev_root or workspace.configured_root(CODE_ROOT)
    if dev_root is None:
        raise SystemExit("no development root: pass --dev-root, or configure one with tools/dev/workspace.py")
    dev_root = Path(dev_root).resolve()
    root = dev_root / LOCAL_DIRECTORY

    def log(line: str) -> None:
        log_event(root / "logs" / "local.log", line)

    try:
        with hold_lock(root / "local.lock") as held:
            if not held:
                log("skipped: another local run is in progress")
                return 0
            return _measure_locally(arguments, dev_root, root, log)
    except Exception:  # noqa: BLE001 - a scheduled run has no console: its log is where a failure is read
        log("failed:\n" + traceback.format_exc().rstrip())
        return 1


def _measure_locally(arguments: argparse.Namespace, dev_root: Path, root: Path, log: Callable[[str], None]) -> int:
    runs, kept = root / "runs", root / KEPT_DIRECTORY
    if not arguments.force:
        done = published_today(runs, date.today())
        if done:
            log(f"skipped: today's result is published ({done})")
            return 0
    shortcut = shortcut_version()
    version = arguments.version_dir or shortcut
    if version is None:
        log("failed: no desktop shortcut opens an installed MonkeyHub; pass --version-dir")
        return 1
    installed = read_installed(version)
    installations = sorted({installation_root(installed.directory),
                            *([installation_root(shortcut)] if shortcut else [])}, key=str)
    budgets = benchmark_data.load_budgets(arguments.budgets)
    # Known before anything is measured: a run that could not be published would be lost.
    url, author = (None, None) if arguments.no_publish else publishing_identity(CODE_ROOT, arguments.remote_url)

    def skip(reasons: list[str]) -> int:
        log("skipped: " + "; ".join(reasons))
        return 0

    # An open MonkeyHub does not stop a run: what was open beside it is kept in the result.
    power = power_reason(*power_status())
    if power and not arguments.force:
        return skip([power])
    if arguments.min_idle_minutes > 0 and not arguments.force:
        reason = wait_until_idle(arguments.min_idle_minutes * 60, arguments.idle_wait_minutes * 60)
        if reason:
            return skip([reason])
    first = system_times()
    time.sleep(arguments.cpu_seconds)
    busy = cpu_busy_percent(first, system_times())
    reasons = [power] if power else []
    if busy > arguments.max_cpu:
        reasons.append(f"the processors were {busy:.0f}% busy over {arguments.cpu_seconds:g} s "
                       f"(the limit is {arguments.max_cpu:g}%)")
    power = power_reason(*power_status())
    if power and power not in reasons:
        reasons.append(power)
    if reasons and not arguments.force:
        return skip(reasons)
    if reasons:
        log("forced past: " + "; ".join(reasons))

    checks: list[dict[str, list[str]]] = []
    looked = [0.0]

    def look() -> None:
        seen = _monkeyhub_now(installations, root)
        looked[0] = time.monotonic()
        if seen is not None:
            checks.append(seen)

    look()
    if checks and checks[0]["desktop"]:
        log(f"MonkeyHub is open beside this run: {', '.join(checks[0]['desktop'])}")
    trash = dev_root / f"_TRASH_{date.today():%Y%m%d}" / "benchmark-local"
    for moved in move_to_trash(sorted(path for path in runs.iterdir() if path.is_dir()) if runs.is_dir() else [],
                               trash, log):
        # A run Task Scheduler stopped, or one that failed, leaves no result.
        ended = "" if (moved / "result.json").is_file() else " (it ended without a result)"
        log(f"moved an earlier run to {moved}{ended}")
    for moved in move_to_trash(stale_kept(kept, installed.name), trash, log):
        log(f"moved {moved.name}, no longer a kept copy, to {moved}")
    new = not (kept / installed.name).is_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = runs / stamp
    log(f"started: {installed.name} ({installed.release or 'no release'}) at {run_dir}; sizes "
        f"{', '.join(map(str, arguments.sizes))}; {arguments.samples} samples; "
        f"{'a new version: its first launch is measured' if new else 'from its kept copy'}; "
        f"processors {busy:.0f}% busy")
    projects = build_projects(CODE_ROOT, run_dir / "projects", arguments.sizes, log)

    def say(line: str) -> None:
        log(line)
        # Between scenarios, never during one: whether the desktop app was open beside the run.
        if time.monotonic() - looked[0] >= BESIDE_EVERY_S:
            look()

    result = run(Options(CODE_ROOT, projects, run_dir / "work", samples=arguments.samples, first_launch_samples=1,
                         idle_seconds=arguments.idle_seconds, settle_seconds=arguments.settle_seconds,
                         runner_key=LOCAL_RUNNER_KEY, installed=installed.directory, keep_in=kept),
                 say=say)
    look()
    result["settings"]["local"] = {"cpuPercent": round(busy, 1), "cpuSeconds": arguments.cpu_seconds,
                                   "forcedPast": reasons, "monkeyhub": monkeyhub_beside(checks)}
    benchmark_data.write_file(run_dir / "result.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    benchmark_data.write_file(run_dir / "summary.md", benchmark_data.summary_markdown(result, budgets))
    failures = result["failures"]
    log(f"measured in {result['durationSeconds']:.0f} s: {len(result['metrics'])} metrics, {len(failures)} failures; "
        f"{run_dir / 'result.json'}")
    for failure in failures:
        log(f"  {failure['scenario']} {failure.get('size') or ''}: {failure['error']}")
    if url is None or author is None:
        log("not published (--no-publish)")
        return 1 if failures else 0
    # Unattended: a credential prompt would wait for nobody until the task's time limit.
    os.environ.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    repository = benchmark_data.publisher_repository(run_dir / "data-repo", url)
    outcome = benchmark_data.publish(repository, run_dir / "data", [result], budgets, push=True, author=author)
    benchmark_data.write_file(run_dir / "published.json", json.dumps(
        {"url": url, "branch": benchmark_data.DATA_BRANCH, "commit": outcome["commit"], "file": outcome["written"][0],
         "flags": [flag["metric"] for flag in outcome["flags"]]}, indent=2) + "\n")
    log(f"published {outcome['written'][0]} to {benchmark_data.DATA_BRANCH} at {outcome['commit'][:12]} ({url})")
    first_launch = result["metrics"].get("hub_start.first_launch") or {}
    if result["settings"]["installed"].get("copies") == "new" and first_launch.get("samples"):
        # Its first launch is published: later runs start from this copy and launch it first no more.
        benchmark_data.write_file(kept / f"{installed.name}.json", json.dumps(
            {"version": installed.name, "commit": installed.commit, "firstLaunchMs": first_launch.get("median"),
             "run": stamp, "file": outcome["written"][0]}, indent=2) + "\n")
        log(f"kept {kept / installed.name} for later runs")
    for flag in outcome["flags"]:
        log(f"  flagged {flag['metric']}: {', '.join(flag['reasons'])} (median {flag['median']} {flag['unit']})")
    return 1 if failures else 0


# ---- command line -------------------------------------------------------------------


def _project_option(value: str) -> tuple[int, Path]:
    size, separator, path = value.partition("=")
    if not separator or not size.isdigit():
        raise argparse.ArgumentTypeError("--project takes SIZE=DIR, such as 30=../projects/synthetic-bench")
    return int(size), Path(path)


def runner_key(given: str | None, installed: bool) -> str | None:
    """The results directory a run publishes to: as given, else the installed app's, else the OS (None)."""

    return given or (LOCAL_RUNNER_KEY if installed else None)


def _sizes_option(value: str) -> tuple[int, ...]:
    parts = [part.strip() for part in value.split(",")]
    if not all(part.isdigit() and int(part) > 0 for part in parts):
        raise argparse.ArgumentTypeError("--sizes takes run counts separated by commas, such as 30,150")
    return tuple(sorted({int(part) for part in parts}))


def build_parser() -> argparse.ArgumentParser:
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
    command.add_argument("--installed", type=Path, metavar="VERSION_DIR",
                         help="measure fresh copies of this installed version directory instead of the checkout")
    command.add_argument("--runner-key", help=f"the results directory on the data branch (default: the OS, "
                                              f"{LOCAL_RUNNER_KEY} with --installed)")
    command.add_argument("--budgets", type=Path, default=benchmark_data.DEFAULT_BUDGETS)
    command.add_argument("--budget-override", action="append", default=[], metavar="METRIC=VALUE")

    local = commands.add_parser("local", help="the scheduled run on a person's machine: check, measure the installed "
                                              "MonkeyHub from its kept copy, publish to results/windows-local/")
    local.add_argument("--version-dir", type=Path, help="the installed version to measure (default: the version the "
                                                       "desktop shortcut opens); only ever copied, once per version")
    local.add_argument("--dev-root", type=Path, help="the development root (default: the one tools/dev/workspace.py "
                                                    "configured); the runner works in <dev root>/temp/benchmark-local")
    local.add_argument("--sizes", type=_sizes_option, default=(30, 150), help="project sizes in runs (default: 30,150)")
    local.add_argument("--samples", type=int, default=3)
    local.add_argument("--idle-seconds", type=float, default=60.0)
    local.add_argument("--settle-seconds", type=float, default=2.0)
    local.add_argument("--min-idle-minutes", type=float, default=15.0,
                       help="how long nobody may have used the computer before measuring; 0 does not wait")
    local.add_argument("--idle-wait-minutes", type=float, default=30.0,
                       help="how long to wait for that before skipping")
    local.add_argument("--max-cpu", type=float, default=30.0, help="skip above this processor use, in percent")
    local.add_argument("--cpu-seconds", type=float, default=60.0, help="how long processor use is averaged over")
    local.add_argument("--remote-url", help="where results are pushed (default: the checkout's origin)")
    local.add_argument("--no-publish", action="store_true", help="measure and write the result, push nothing")
    local.add_argument("--force", action="store_true",
                       help="measure even on battery, with busy processors or today already published, without "
                            "waiting for an idle computer; the reasons are logged and kept in the result")
    local.add_argument("--budgets", type=Path, default=benchmark_data.DEFAULT_BUDGETS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)
    # A console that cannot show a sign gets a placeholder, not a traceback at the end of a run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if options.command == "local":
        return run_local(options)

    work = (options.work or Path(tempfile.mkdtemp(prefix="monkeyhub-benchmark-"))).resolve()
    if work.exists() and any(work.iterdir()):
        parser.error(f"--work {work} is not empty")
    budgets = benchmark_data.override_budgets(benchmark_data.load_budgets(options.budgets), options.budget_override)
    projects = dict(options.project)
    result = run(Options(CODE_ROOT, projects, work, samples=options.samples,
                         first_launch_samples=options.samples if options.first_launch_samples is None
                         else options.first_launch_samples,
                         idle_seconds=options.idle_seconds, settle_seconds=options.settle_seconds,
                         runner_key=runner_key(options.runner_key, options.installed is not None),
                         installed=options.installed),
                 say=lambda line: print(line, flush=True))
    benchmark_data.write_file(options.out, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    text = benchmark_data.summary_markdown(result, budgets)
    if options.summary is not None:
        benchmark_data.write_file(options.summary, text)
    sys.stdout.write(text)
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
