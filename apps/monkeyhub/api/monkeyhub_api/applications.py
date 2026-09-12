"""Own only the Studio and Monitor children this Hub started."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from archflow_studio_api.settings import read_application_settings, read_user_settings, save_application_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto

from .models import AppId, AppStatus, HubError, HubFailure
from . import fabrication

APPS = {
    "monkeyarch": ("MonkeyArch", "studio"),
    "monkeydiagram": ("MonkeyDiagram", "studio"),
    "monkeymonitor": ("MonkeyMonitor", "monitor"),
    "monkeyboard": ("MonkeyBoard", "studio"),
    "monkeyfab": ("MonkeyFab", "hub"),
}


def source_revision(root: Path) -> str | None:
    """A packaged source marker or the actual checkout, never a caller's claim."""
    marker = root / "source-version.txt"
    try:
        if marker.exists():
            value = marker.read_text(encoding="utf-8-sig").strip()
        else:
            value = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", value) else None


@dataclass
class _Child:
    process: subprocess.Popen
    instance_id: str
    port: int
    log_path: Path
    state: str = "starting"
    error: HubError | None = None


class Applications:
    def __init__(self, source_root: Path, runtime_root: Path, studio_web_dir: Path | None, hub_port: int):
        self.source_root = source_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.studio_web_dir = studio_web_dir.resolve() if studio_web_dir else None
        self.hub_port = hub_port
        self.source_revision = source_revision(self.source_root)
        self._children: dict[str, _Child] = {}
        self._lock = threading.RLock()
        self._closing = False

    def statuses(self) -> list[AppStatus]:
        return [self.status(app_id) for app_id in APPS]

    def configure(self, settings: ApplicationSettingsDto) -> ApplicationSettingsDto:
        with self._lock:
            previous = read_application_settings(self.runtime_root)
            changed_services = set()
            if (settings.project_dir, settings.reference_run, settings.cad_export, settings.studio_port) != (previous.project_dir, previous.reference_run, previous.cad_export, previous.studio_port):
                changed_services.add("studio")
            if settings.monitor_port != previous.monitor_port:
                changed_services.add("monitor")
            if self._closing or any(service in changed_services and child.process.poll() is None for service, child in self._children.items()):
                raise HubFailure(409, "APPS_RUNNING", "Stop the applications before changing their launch configuration.")
            if settings.studio_port == settings.monitor_port or self.hub_port in {settings.studio_port, settings.monitor_port}:
                raise HubFailure(409, "PORT_CONFLICT", "Hub, Studio and Monitor must use different ports.")
            return save_application_settings(self.runtime_root, settings)

    def status(self, app_id: AppId) -> AppStatus:
        title, service = APPS[app_id]
        with self._lock:
            if service == "hub":
                available = fabrication.available()
                return AppStatus(
                    appId=app_id, title=title, serviceId="hub", available=available,
                    state="running" if available else "unavailable",
                    url=f"http://127.0.0.1:{self.hub_port}/?view=fab" if available else None,
                    processId=os.getpid() if available else None,
                    error=None if available else HubError(
                        code="FAB_UNAVAILABLE",
                        detail="MonkeyFab is not included in this Python environment. Use the integrated application package.",
                    ),
                )
            child = self._children.get(service)
            if child is None:
                return AppStatus(appId=app_id, title=title, serviceId=service, state="stopped")
            state = child.state
            url = None
            if state == "running":
                url = f"http://127.0.0.1:{child.port}/"
                if app_id == "monkeydiagram":
                    url += "?view=documents"
                elif app_id == "monkeyboard":
                    url += "?view=board"
            return AppStatus(
                appId=app_id, title=title, serviceId=service, state=state,
                url=url, processId=child.process.pid if child.process.poll() is None else None,
                error=child.error,
            )

    def start(self, app_id: AppId) -> AppStatus:
        _, service = APPS[app_id]
        with self._lock:
            if self._closing:
                raise HubFailure(409, "HUB_STOPPING", "The Hub is waiting for its applications to finish.")
            if service == "hub":
                return self.status(app_id)
            existing = self._children.get(service)
            if existing is not None and existing.process.poll() is None:
                return self.status(app_id)
            if self.source_revision is None:
                raise HubFailure(503, "SOURCE_VERSION_UNKNOWN", "This source folder has no verifiable version. Use a complete version package or checkout.")
            settings = read_application_settings(self.runtime_root)
            port = settings.studio_port if service == "studio" else settings.monitor_port
            if port == self.hub_port or settings.studio_port == settings.monitor_port:
                raise HubFailure(409, "PORT_CONFLICT", "Hub, Studio and Monitor must use different ports.")
            args, environ = self._command(service, settings)
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise HubFailure(409, "PORT_IN_USE", f"Port {port} is already in use. Choose another port; the existing process was left alone.") from exc
            instance_id = str(uuid4())
            args += ["--port", str(port), "--managed-stdin", "--managed-instance-id", instance_id]
            logs = self.runtime_root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            log_path = logs / f"{service}-{instance_id}.log"
            try:
                with log_path.open("ab") as output:
                    process = subprocess.Popen(
                        args, cwd=self.source_root, env=environ, stdin=subprocess.PIPE,
                        stdout=output, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except OSError as exc:
                raise HubFailure(503, "START_FAILED", f"The application could not start. See {log_path}.") from exc
            child = _Child(process, instance_id, port, log_path)
            self._children[service] = child
            threading.Thread(target=self._watch, args=(service, child), daemon=True).start()
            return self.status(app_id)

    def _command(self, service: str, settings: ApplicationSettingsDto) -> tuple[list[str], dict[str, str]]:
        args = [sys.executable, str(self.source_root / "apps/monkeyhub/run.py"), "--service", service]
        environ = os.environ.copy()
        diagnostics = self.runtime_root / "diagnostics" / "monkeymonitor"
        if service == "monitor":
            return args + ["serve", "--data-dir", str(diagnostics)], environ
        if settings.project_dir is None:
            raise HubFailure(409, "PROJECT_REQUIRED", "Choose a complete project folder before opening MonkeyArch or MonkeyDiagram.")
        project = Path(settings.project_dir)
        if not (project / "project.json").is_file():
            raise HubFailure(409, "PROJECT_REQUIRED", "The selected folder does not contain project.json. Choose a complete project folder.")
        if self.studio_web_dir is None or not (self.studio_web_dir / "index.html").is_file():
            raise HubFailure(503, "STUDIO_WEB_MISSING", "The Studio web build is missing. Prepare the selected version package or build its web client.")
        preferences = read_user_settings()
        # The same preference owner supplies defaults; unrelated shell environment
        # must not silently select a different project, remote listener or compiler.
        for key in tuple(environ):
            if key.startswith("ARCHFLOW_STUDIO_"):
                environ.pop(key)
        environ.update({
            "ARCHFLOW_STUDIO_MODE": "local",
            "ARCHFLOW_STUDIO_PROJECT_DIR": str(project),
            "ARCHFLOW_STUDIO_CAD_EXPORT": settings.cad_export,
            "ARCHFLOW_STUDIO_INTENT_PROVIDER": preferences.intent_provider or "deterministic",
            "MONKEYMONITOR_DATA_DIR": str(diagnostics),
        })
        if settings.reference_run:
            environ["ARCHFLOW_STUDIO_REFERENCE_RUN"] = settings.reference_run
        if preferences.intent_model:
            environ["ARCHFLOW_STUDIO_INTENT_MODEL"] = preferences.intent_model
        if preferences.intent_timeout_s is not None:
            environ["ARCHFLOW_STUDIO_INTENT_TIMEOUT_S"] = str(preferences.intent_timeout_s)
        return args + ["--host", "127.0.0.1", "--web-dir", str(self.studio_web_dir)], environ

    def _watch(self, service: str, child: _Child) -> None:
        deadline = time.monotonic() + 30
        opener = build_opener(ProxyHandler({}))
        while child.process.poll() is None:
            with self._lock:
                starting = child.state == "starting"
            if starting:
                try:
                    with opener.open(f"http://127.0.0.1:{child.port}/api/health", timeout=1) as response:
                        health = json.loads(response.read(65536))
                    if not isinstance(health, dict):
                        raise ValueError("Health must be an object")
                    identity_matches = (
                        health.get("managedInstanceId") == child.instance_id
                        and (health.get("processId") == child.process.pid or health.get("parentProcessId") == child.process.pid)
                        and health.get("sourceRevision") == self.source_revision
                        and health.get("serverVersion") == "0.1.0"
                    )
                    expected_name = health.get("name") == "MonkeyMonitor" if service == "monitor" else health.get("projectBound") is True
                    with self._lock:
                        if child.state == "starting":
                            if identity_matches and expected_name:
                                child.state = "running"
                            else:
                                child.error = HubError(code="SERVICE_IDENTITY_MISMATCH", detail="The responding service does not match this launch, source version or selected project.")
                                child.state = "error"
                                self._send_stop(child)
                except (OSError, URLError, HTTPError, ValueError):
                    pass
                with self._lock:
                    if child.state == "starting" and time.monotonic() >= deadline:
                        child.error = HubError(code="START_TIMEOUT", detail=f"The application did not become ready. See {child.log_path}.")
                        child.state = "error"
                        self._send_stop(child)
            time.sleep(0.1)
        with self._lock:
            if child.error is None:
                if child.state == "stopping" and child.process.returncode == 0:
                    child.state = "stopped"
                else:
                    child.state = "error"
                    child.error = HubError(code="PROCESS_EXITED", detail=f"The application exited with code {child.process.returncode}. See {child.log_path}.")

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

    def stop(self, app_id: AppId) -> AppStatus:
        _, service = APPS[app_id]
        with self._lock:
            if service == "hub":
                raise HubFailure(409, "APP_HOSTED_BY_HUB", "MonkeyFab is an operation page in this Hub and has no separate process to stop.")
            child = self._children.get(service)
            if child is not None and child.process.poll() is None:
                if child.state != "error":
                    child.state = "stopping"
                self._send_stop(child)
            return self.status(app_id)

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
            for app_id in ("monkeyarch", "monkeymonitor"):
                self.stop(app_id)

    def shutdown(self) -> None:
        self.begin_shutdown()
        for child in tuple(self._children.values()):
            child.process.wait()
