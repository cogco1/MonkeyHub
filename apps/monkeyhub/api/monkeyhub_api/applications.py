"""Own only the Studio and Monitor children this Hub started."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading

from archflow.project.manifest import ProjectManifest
from archflow_studio_api.settings import read_application_settings, read_user_settings, save_application_settings
from archflow_studio_api.transport.settings import ApplicationSettingsDto

from .models import AppId, AppStatus, HubError, HubFailure
from . import fabrication
from .workers import WorkerLaunch, WorkerSnapshot, WorkerSupervisor, project_key

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


class Applications:
    def __init__(self, source_root: Path, runtime_root: Path, studio_web_dir: Path | None, hub_port: int):
        self.source_root = source_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.studio_web_dir = studio_web_dir.resolve() if studio_web_dir else None
        self.hub_port = hub_port
        self.source_revision = source_revision(self.source_root)
        self.supervisor = WorkerSupervisor()
        self._lock = threading.RLock()
        self._closing = False

    def statuses(self, *, project_dir: str | None = None) -> list[AppStatus]:
        return [self.status(app_id, project_dir=project_dir) for app_id in APPS]

    @staticmethod
    def _project_key(project_dir: str | None) -> str:
        return project_key(project_dir)

    def worker_snapshots(self, *, project_dir: str | None = None) -> tuple[WorkerSnapshot, ...]:
        return self.supervisor.snapshots(project_dir=project_dir)

    def set_busy(self, *, project_dir: str, busy: bool) -> None:
        self.supervisor.set_busy(self._child_key("studio", project_dir), busy)

    def recover(self, *, project_dir: str) -> AppStatus:
        with self._lock:
            if self._closing:
                raise HubFailure(409, "HUB_STOPPING", "The Hub is waiting for its applications to finish.")
            self.supervisor.recover(self._child_key("studio", project_dir))
            return self.status("monkeyarch", project_dir=project_dir)

    def _child_key(self, service: str, project_dir: str | None) -> str:
        if service != "studio":
            return service
        if project_dir is None:
            project_dir = read_application_settings(self.runtime_root).project_dir
        return f"studio:{self._project_key(project_dir)}"

    def configure(self, settings: ApplicationSettingsDto) -> ApplicationSettingsDto:
        with self._lock:
            previous = read_application_settings(self.runtime_root)
            changed_services = set()
            if (settings.project_dir, settings.reference_run, settings.cad_export, settings.studio_port) != (previous.project_dir, previous.reference_run, previous.cad_export, previous.studio_port):
                changed_services.add("studio")
            if settings.monitor_port != previous.monitor_port:
                changed_services.add("monitor")
            if self._closing or any(worker.service_id in changed_services and worker.process_id is not None for worker in self.worker_snapshots()):
                raise HubFailure(409, "APPS_RUNNING", "Stop the applications before changing their launch configuration.")
            if settings.studio_port == settings.monitor_port or self.hub_port in {settings.studio_port, settings.monitor_port}:
                raise HubFailure(409, "PORT_CONFLICT", "Hub, Studio and Monitor must use different ports.")
            return save_application_settings(self.runtime_root, settings)

    def status(self, app_id: AppId, *, project_dir: str | None = None) -> AppStatus:
        title, service = APPS[app_id]
        with self._lock:
            if service == "hub":
                available = fabrication.available(self.source_root)
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
            child = self.supervisor.snapshot(self._child_key(service, project_dir))
            if child is None:
                return AppStatus(appId=app_id, title=title, serviceId=service, state="stopped")
            state = {"ready": "running", "busy": "running", "recovering": "starting", "crashed": "error", "unavailable": "error"}.get(child.state, child.state)
            url = None
            if state == "running":
                url = child.url
                if app_id == "monkeydiagram":
                    url += "?view=documents"
                elif app_id == "monkeyboard":
                    url += "?view=board"
            return AppStatus(
                appId=app_id, title=title, serviceId=service, state=state,
                url=url, processId=child.process_id,
                error=child.error,
            )

    def start(self, app_id: AppId, *, project_dir: str | None = None) -> AppStatus:
        _, service = APPS[app_id]
        with self._lock:
            if self._closing:
                raise HubFailure(409, "HUB_STOPPING", "The Hub is waiting for its applications to finish.")
            if service == "hub":
                return self.status(app_id, project_dir=project_dir)
            key = self._child_key(service, project_dir)
            existing = self.supervisor.snapshot(key)
            if existing is not None and existing.process_id is not None:
                return self.status(app_id, project_dir=project_dir)
            if self.source_revision is None:
                raise HubFailure(503, "SOURCE_VERSION_UNKNOWN", "This source folder has no verifiable version. Use a complete version package or checkout.")
            settings = read_application_settings(self.runtime_root)
            port = settings.studio_port if service == "studio" else settings.monitor_port
            if port == self.hub_port or settings.studio_port == settings.monitor_port:
                raise HubFailure(409, "PORT_CONFLICT", "Hub, Studio and Monitor must use different ports.")
            if service == "studio" and project_dir is not None:
                same_project = self._project_key(project_dir) == self._project_key(settings.project_dir)
                settings = settings.model_copy(update={
                    "project_dir": str(Path(project_dir).resolve()),
                    "reference_run": settings.reference_run if same_project else None,
                })
                if not same_project:
                    reserved = {self.hub_port, settings.studio_port, settings.monitor_port}
                    reserved.update(int(worker.url.split(":")[-1].rstrip("/")) for worker in self.worker_snapshots() if worker.process_id is not None and worker.url)
                    while True:
                        with socket.socket() as probe:
                            probe.bind(("127.0.0.1", 0))
                            port = probe.getsockname()[1]
                        if port not in reserved:
                            break
            args, environ = self._command(service, settings)
            project_id = None
            selected_project = str(Path(settings.project_dir).resolve()) if service == "studio" else None
            if selected_project is not None:
                try:
                    project_id = ProjectManifest.from_dict(json.loads((Path(selected_project) / "project.json").read_text(encoding="utf-8-sig"))).project_id
                except (OSError, ValueError, TypeError) as exc:
                    raise HubFailure(409, "PROJECT_REQUIRED", "The selected folder does not contain a valid project manifest.") from exc
            self.supervisor.start(WorkerLaunch(
                worker_id=key, service_id=service, project_id=project_id, project_dir=selected_project,
                source_revision=self.source_revision, command=args, environment=environ,
                cwd=self.source_root, port=port, logs_dir=self.runtime_root / "logs",
                health_fields={"name": "MonkeyMonitor"} if service == "monitor" else {"projectBound": True},
            ))
            return self.status(app_id, project_dir=project_dir)

    def _command(self, service: str, settings: ApplicationSettingsDto) -> tuple[list[str], dict[str, str]]:
        args = [sys.executable, str(self.source_root / "apps/monkeyhub/run.py"), "--service", service]
        environ = os.environ.copy()
        diagnostics = self.runtime_root / "diagnostics" / "monkeymonitor"
        if service == "monitor":
            return args + ["serve", "--data-dir", str(diagnostics), "--codex-bindings-url",
                           f"http://127.0.0.1:{self.hub_port}/api/chat/usage-sources"], environ
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

    def stop(self, app_id: AppId, *, project_dir: str | None = None) -> AppStatus:
        _, service = APPS[app_id]
        with self._lock:
            if service == "hub":
                raise HubFailure(409, "APP_HOSTED_BY_HUB", "MonkeyFab is an operation page in this Hub and has no separate process to stop.")
            self.supervisor.stop(self._child_key(service, project_dir))
            return self.status(app_id, project_dir=project_dir)

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
            self.supervisor.begin_shutdown()

    def shutdown(self) -> None:
        self.begin_shutdown()
        self.supervisor.shutdown()
