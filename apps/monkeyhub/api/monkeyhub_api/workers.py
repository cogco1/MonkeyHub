"""Supervise owned local service processes; never retry their domain operations."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Literal
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from .models import HubError, HubFailure


WorkerState = Literal["starting", "ready", "busy", "stopping", "stopped", "crashed", "recovering", "unavailable"]


def project_key(project_dir: str | None) -> str:
    return os.path.normcase(str(Path(project_dir).resolve())) if project_dir is not None else ""


@dataclass(frozen=True)
class WorkerSnapshot:
    worker_id: str
    service_id: str
    project_id: str | None
    project_dir: str | None
    instance_id: str
    process_id: int | None
    desired_state: Literal["running", "stopped"]
    state: WorkerState
    healthy: bool
    url: str | None
    error: HubError | None


@dataclass(frozen=True)
class WorkerLaunch:
    worker_id: str
    service_id: str
    project_id: str | None
    project_dir: str | None
    source_revision: str
    command: list[str]
    environment: dict[str, str]
    cwd: Path
    port: int
    logs_dir: Path
    health_fields: dict[str, object]


@dataclass
class _Child:
    launch: WorkerLaunch
    process: subprocess.Popen
    instance_id: str
    log_path: Path
    state: WorkerState = "starting"
    desired_state: Literal["running", "stopped"] = "running"
    healthy: bool = False
    error: HubError | None = None
    service_pid: int | None = None
    busy: bool = False

    @property
    def port(self) -> int:
        return self.launch.port


class WorkerSupervisor:
    def __init__(self):
        self._children: dict[str, _Child] = {}
        self._lock = threading.RLock()
        self._closing = False

    def snapshots(self, *, project_dir: str | None = None) -> tuple[WorkerSnapshot, ...]:
        with self._lock:
            return tuple(
                self._snapshot(child) for child in self._children.values()
                if project_dir is None or project_key(child.launch.project_dir) == project_key(project_dir)
            )

    def snapshot(self, worker_id: str) -> WorkerSnapshot | None:
        with self._lock:
            child = self._children.get(worker_id)
            return self._snapshot(child) if child else None

    def _snapshot(self, child: _Child) -> WorkerSnapshot:
        # Status reads cannot claim ready between an exit and the watcher's next poll.
        alive = self._observe_exit(child)
        return WorkerSnapshot(
            worker_id=child.launch.worker_id, service_id=child.launch.service_id,
            project_id=child.launch.project_id, project_dir=child.launch.project_dir,
            instance_id=child.instance_id,
            process_id=(child.service_pid or child.process.pid) if alive else None,
            desired_state=child.desired_state, state=child.state, healthy=child.healthy,
            url=f"http://127.0.0.1:{child.port}/", error=child.error.model_copy() if child.error else None,
        )

    def start(self, launch: WorkerLaunch, *, recovering: bool = False) -> WorkerSnapshot:
        with self._lock:
            if self._closing:
                raise HubFailure(409, "HUB_STOPPING", "The Hub is waiting for its applications to finish.")
            existing = self._children.get(launch.worker_id)
            if existing and existing.process.poll() is None:
                return self._snapshot(existing)
            with socket.socket() as probe:
                try:
                    if os.name != "nt":
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(("127.0.0.1", launch.port))
                except OSError as exc:
                    raise HubFailure(409, "PORT_IN_USE", f"Port {launch.port} is already in use. Choose another port; the existing process was left alone.") from exc
            instance_id = str(uuid4())
            command = launch.command + ["--port", str(launch.port), "--managed-stdin", "--managed-instance-id", instance_id]
            launch.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = launch.logs_dir / f"{launch.service_id}-{instance_id}.log"
            try:
                with log_path.open("ab") as output:
                    process = subprocess.Popen(
                        command, cwd=launch.cwd, env=launch.environment, stdin=subprocess.PIPE,
                        stdout=output, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except OSError as exc:
                raise HubFailure(503, "START_FAILED", f"The application could not start. See {log_path}.") from exc
            child = _Child(launch, process, instance_id, log_path, state="recovering" if recovering else "starting")
            self._children[launch.worker_id] = child
            threading.Thread(target=self._watch, args=(child,), daemon=True).start()
            return self._snapshot(child)

    def recover(self, worker_id: str) -> WorkerSnapshot:
        """Restart one crashed launch, preserving its binding and port, on explicit request."""
        with self._lock:
            child = self._children.get(worker_id)
            if child is None:
                raise HubFailure(409, "WORKER_NOT_STARTED", "This project has no owned worker to recover.")
            snapshot = self._snapshot(child)
            if snapshot.state == "recovering":
                return snapshot
            if snapshot.state != "crashed" or snapshot.desired_state != "running":
                raise HubFailure(409, "WORKER_NOT_CRASHED", "Only an unexpectedly exited owned worker can be recovered.")
            return self.start(child.launch, recovering=True)

    def set_busy(self, worker_id: str, busy: bool) -> None:
        """The runtime supplies activity; process liveness never proves operation success."""
        with self._lock:
            child = self._children.get(worker_id)
            if child is not None:
                self._observe_exit(child)
                child.busy = busy
                if child.healthy:
                    child.state = "busy" if busy else "ready"

    @staticmethod
    def _observe_exit(child: _Child) -> bool:
        code = child.process.poll()
        if code is None:
            return True
        if child.process.stdin is not None and not child.process.stdin.closed:
            try:
                child.process.stdin.close()
            except OSError:
                pass
        child.healthy = False
        if child.desired_state == "stopped":
            if child.state == "unavailable":
                return False
            if code == 0:
                child.state = "stopped"
                child.error = None
                return False
        if child.state != "crashed":
            child.error = HubError(code="PROCESS_EXITED", detail=f"The application exited with code {code}. See {child.log_path}.")
        child.state = "crashed"
        return False

    def _watch(self, child: _Child) -> None:
        deadline = time.monotonic() + 30
        opener = build_opener(ProxyHandler({}))
        while child.process.poll() is None:
            with self._lock:
                check = child.desired_state == "running"
            if check:
                try:
                    with opener.open(f"http://127.0.0.1:{child.port}/api/health", timeout=1) as response:
                        health = json.loads(response.read(65536))
                    if not isinstance(health, dict):
                        raise ValueError("Health must be an object")
                    matches = (
                        health.get("managedInstanceId") == child.instance_id
                        and isinstance(health.get("processId"), int)
                        and (health.get("processId") == child.process.pid or health.get("parentProcessId") == child.process.pid)
                        and health.get("sourceRevision") == child.launch.source_revision
                        and health.get("serverVersion") == "0.1.0"
                        and all(health.get(key) == value for key, value in child.launch.health_fields.items())
                    )
                    if matches and child.launch.project_dir is not None:
                        with opener.open(f"http://127.0.0.1:{child.port}/api/project", timeout=1) as response:
                            binding = json.loads(response.read(65536))
                        matches = (
                            isinstance(binding, dict)
                            and binding.get("projectId") == child.launch.project_id
                            and isinstance(binding.get("projectDir"), str)
                            and project_key(binding["projectDir"]) == project_key(child.launch.project_dir)
                        )
                    with self._lock:
                        if child.desired_state == "running" and child.process.poll() is None:
                            if matches:
                                child.service_pid = health["processId"]
                                child.healthy = True
                                child.error = None
                                child.state = "busy" if child.busy else "ready"
                            else:
                                detail = "The responding service does not match this launch, source version or selected project."
                                if health.get("sourceRevision") != child.launch.source_revision:
                                    detail += " Its source version differs from the running Hub. Restart MonkeyHub after changing the source version before opening its tools."
                                self._reject(child, "SERVICE_IDENTITY_MISMATCH", detail)
                except (OSError, URLError, ValueError):
                    with self._lock:
                        if child.desired_state == "running":
                            if child.service_pid is not None:
                                child.healthy = False
                                child.state = "unavailable"
                                child.error = HubError(code="SERVICE_UNAVAILABLE", detail="The owned service is not answering its health and project binding checks.")
                            elif time.monotonic() >= deadline:
                                self._reject(child, "START_TIMEOUT", f"The application did not become ready. See {child.log_path}.")
            time.sleep(0.1 if child.service_pid is None else 1)
        with self._lock:
            self._observe_exit(child)

    def _reject(self, child: _Child, code: str, detail: str) -> None:
        child.healthy = False
        child.state = "unavailable"
        child.desired_state = "stopped"
        child.error = HubError(code=code, detail=detail)
        self._send_stop(child)

    @staticmethod
    def _send_stop(child: _Child) -> None:
        if child.process.stdin is not None and not child.process.stdin.closed:
            try:
                child.process.stdin.write(b"stop\n")
                child.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    child.process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

    def stop(self, worker_id: str) -> None:
        with self._lock:
            child = self._children.get(worker_id)
            if child is not None:
                self._observe_exit(child)
                child.desired_state = "stopped"
                if child.process.poll() is None:
                    child.state = "stopping"
                    child.healthy = False
                    self._send_stop(child)

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
            for worker_id in self._children:
                self.stop(worker_id)

    def shutdown(self) -> None:
        self.begin_shutdown()
        for child in tuple(self._children.values()):
            child.process.wait()
