"""Hub lifecycle for one stable device and its own project replicas."""
from __future__ import annotations
import base64
from functools import wraps
import json
import os
from pathlib import Path
import threading
import time

from monkeymesh.transport import IrohTransport, MeshUnavailable
from archflow.project.repository import FilesystemProjectRepository, ProjectRepositoryError
from ..models import HubFailure
from ..settings.team import TeamSettings, digest
from ..runtime.worker_http import request_http
from .gateway import MemberGateway


def serialized(action):
    @wraps(action)
    def run(self, *args, **kwargs):
        with self._actions:
            return action(self, *args, **kwargs)
    return run


class Teams:
    def __init__(self, applications, *, transport=IrohTransport, settings=None, test_loopback=False):
        self.applications = applications
        self.settings = settings or TeamSettings(applications.runtime_root)
        self.transport_type = transport
        self.test_loopback = test_loopback
        self.connections = {}
        self.gateways = {}
        self.lock = threading.RLock()
        self._actions = threading.RLock()
        self.closing = False

    @property
    def executable(self):
        name = "monkeymesh-tcp.exe" if os.name == "nt" else "monkeymesh-tcp"
        packaged = self.applications.source_root / "packages" / "monkeymesh" / "bin" / name
        development = self.applications.source_root / "packages" / "monkeymesh" / "native" / "target" / "debug" / name
        return packaged if packaged.exists() else development

    def connect(self, project_id):
        with self.lock:
            if self.closing:
                raise HubFailure(409, "HUB_STOPPING", "The Hub is shutting down.")
            previous = self.connections.get(project_id)
            if previous and previous.alive:
                return previous
            if previous:
                previous.close()
            row = self.settings.project(project_id)
            identity = self.settings.identity()
            try:
                if row["kind"] == "owner":
                    gateway = self.gateways.get(project_id)
                    if gateway is None:
                        gateway = self.gateways[project_id] = MemberGateway(self, project_id)
                    connection = self.transport_type(self.executable, secret_key=self.settings.transport_key(project_id), mode="listen",
                        target=gateway.port, enrollment_target=gateway.port, pairing=True,
                        allowed=tuple(m["nodeId"] for m in row["members"].values() if m["role"] != "revoked"),
                        peer_changed=gateway.peer_changed, test_loopback=self.test_loopback)
                    own = row["members"][identity["actorId"]]
                    own["nodeId"] = connection.node_id
                    self.settings.put_project(project_id, row)
                else:
                    connection = self.transport_type(self.executable, secret_key=self.settings.transport_key(project_id), mode="connect",
                        ticket=row["ticket"], test_loopback=self.test_loopback)
            except MeshUnavailable:
                raise HubFailure(503, "MESH_UNAVAILABLE", "The secure project connection is unavailable.") from None
            self.connections[project_id] = connection
            return connection

    def admission(self, project_id):
        connection = self.connections.get(project_id)
        if connection:
            row = self.settings.project(project_id)
            pairing = any(i["expires"] > time.time() for i in row.get("invites", {}).values())
            connection.admission([m["nodeId"] for m in row["members"].values() if m["role"] != "revoked"], pairing=pairing)

    def _stop_runtime(self, project_dir):
        self.applications.stop("monkeyarch", project_dir=project_dir)
        deadline = time.monotonic() + 120
        while any(worker.process_id for worker in self.applications.worker_snapshots(project_dir=project_dir)
                  if worker.service_id == "studio"):
            if time.monotonic() >= deadline:
                raise HubFailure(409, "TEAM_RUNTIME_DRAINING", "The Runtime is finishing accepted work. Retry when it has stopped; no work was killed.")
            time.sleep(.05)

    @serialized
    def share(self, project_dir, name, role):
        if not self.executable.is_file():
            raise HubFailure(503, "MESH_UNAVAILABLE", "Install or build the project transport before sharing a project.")
        root = Path(project_dir)
        if not root.is_absolute():
            raise HubFailure(422, "PROJECT_REQUIRED", "Choose an existing absolute project folder.")
        root = root.resolve()
        try:
            repository = FilesystemProjectRepository.open(root)
            project_id = repository.load_manifest().project_id
        except (OSError, ValueError, ProjectRepositoryError):
            raise HubFailure(422, "PROJECT_REQUIRED", "The selected project folder is missing or unreadable. Open an existing project before sharing it.") from None
        identity = self.settings.identity()
        row = self.settings.read()["projects"].get(project_id)
        if row and (row["kind"] != "owner" or row["projectDir"] != str(root)):
            raise HubFailure(409, "TEAM_EXISTS", "This project already has another team connection.")
        if not row:
            # Changing launch identity requires a graceful restart through the existing supervisor.
            self._stop_runtime(str(root))
            token = self.settings.token(project_id, create=True)
            row = {"kind": "owner", "projectId": project_id, "projectDir": str(root),
                "ownerActorId": identity["actorId"], "name": name, "members": {
                    identity["actorId"]: {"actorId": identity["actorId"], "name": name, "role": "moderator", "nodeId": "pending", "tokenHash": digest(token)}}, "invites": {}}
            self.settings.put_project(project_id, row)
            self.settings.write_actors(project_id)
        connection = self.connect(project_id)
        self.applications.start("monkeyarch", project_dir=str(root))
        code = self.settings.invite(project_id, role)
        self.admission(project_id)
        invitation = base64.urlsafe_b64encode(json.dumps({"version": 1, "ticket": connection.ticket,
            "projectId": project_id, "code": code}, separators=(",", ":")).encode()).decode()
        return {"projectId": project_id, "invitation": invitation, "expiresIn": 1800}

    @serialized
    def join(self, invitation, project_dir, name):
        try:
            if len(invitation) > 16384:
                raise ValueError
            invite = json.loads(base64.urlsafe_b64decode(invitation))
            if set(invite) != {"version", "ticket", "projectId", "code"} or invite["version"] != 1:
                raise ValueError
            from archflow.project.refs import require_identifier
            require_identifier(invite["projectId"], "project_id")
            if not isinstance(invite["ticket"], str) or not isinstance(invite["code"], str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise HubFailure(422, "INVITE_INVALID", "Paste a complete project invitation.") from None
        root = Path(project_dir)
        if not root.is_absolute():
            raise HubFailure(422, "REPLICA_PATH_INVALID", "Choose an absolute folder for this device's replica.")
        root = root.resolve()
        project_id = invite["projectId"]
        if root.name != project_id:
            root = root / project_id
        existing = self.settings.read()["projects"].get(project_id)
        if existing:
            if existing["kind"] != "member" or existing["projectDir"] != str(root):
                raise HubFailure(409, "TEAM_EXISTS", "This device already has a different connection for that project.")
            return self.resume(project_id)
        if root.exists() and any(root.iterdir()):
            raise HubFailure(409, "REPLICA_NOT_EMPTY", "Choose an empty folder; existing files were left untouched.")
        identity = self.settings.identity()
        token = self.settings.token(project_id, create=True)
        try:
            enrollment = self.transport_type(self.executable, secret_key=self.settings.transport_key(project_id), mode="connect",
                ticket=invite["ticket"], enrollment=True, test_loopback=self.test_loopback)
            try:
                result = request_http(enrollment.url, "/join", "POST", json.dumps({"code": invite["code"],
                    "actorId": identity["actorId"], "name": name, "token": token}).encode(), {"Content-Type": "application/json"}, timeout=45)
            finally:
                enrollment.close()
        except (MeshUnavailable, OSError):
            raise HubFailure(503, "JOIN_UNAVAILABLE", "Could not reach the project owner. You can retry this invitation.") from None
        if result.status != 200:
            failure = result.json()
            raise HubFailure(result.status, failure.get("code", "JOIN_REFUSED"), failure.get("detail", "The project owner refused this invitation."))
        answer = result.json()
        row = {"kind": "member", "projectId": project_id, "projectDir": str(root), "ticket": invite["ticket"],
               "ownerActorId": answer["ownerActorId"], "role": answer["role"], "name": name}
        self.settings.put_project(project_id, row)
        return self.resume(project_id)

    @serialized
    def resume(self, project_id):
        previous = self.connections.get(project_id)
        replaced = previous is not None and not previous.alive
        self.connect(project_id)
        row = self.settings.project(project_id)
        if replaced and row["kind"] == "member":
            self._stop_runtime(row["projectDir"])
        self.applications.start("monkeyarch", project_dir=row["projectDir"])
        return {"projectId": project_id, "projectDir": row["projectDir"], "status": "connecting"}

    def environment(self, project_dir):
        row = self.settings.by_path(project_dir)
        if row is None:
            return {}
        identity = self.settings.identity()
        values = {"ARCHFLOW_STUDIO_TEAM_ACTOR_ID": identity["actorId"], "ARCHFLOW_STUDIO_TEAM_ACTOR_NAME": row["name"],
                  "ARCHFLOW_STUDIO_PROJECT_OWNER_ACTOR_ID": row["ownerActorId"]}
        if row["kind"] == "owner":
            values.update({"ARCHFLOW_STUDIO_TEAM_OWNER": "1", "ARCHFLOW_STUDIO_TEAM_ROLE": "moderator",
                           "ARCHFLOW_STUDIO_ACTORS_FILE": str(self.settings.write_actors(row["projectId"]))})
        else:
            connection = self.connect(row["projectId"])
            values.update({"ARCHFLOW_STUDIO_TEAM_ROLE": row["role"], "ARCHFLOW_STUDIO_SYNC_AUTOMATIC": "1",
                "ARCHFLOW_STUDIO_SYNC_URL": connection.url, "ARCHFLOW_STUDIO_SYNC_TOKEN": self.settings.token(row["projectId"]),
                "ARCHFLOW_STUDIO_SYNC_PROJECT_ID": row["projectId"],
                "ARCHFLOW_STUDIO_TEAM_STATE_FILE": str(self.settings.root / "team-state" / (digest(row["projectId"]) + ".json"))})
        return values

    def members(self, project_id):
        row = self.settings.project(project_id)
        if row["kind"] != "owner":
            return []
        return [{key: member[key] for key in ("actorId", "name", "role", "nodeId")} for member in row["members"].values()]

    @serialized
    def change_role(self, project_id, actor_id, role):
        with self.settings.lock:
            row = self.settings.project(project_id)
            if row["kind"] != "owner" or actor_id == row["ownerActorId"]:
                raise HubFailure(403, "ROLE_CHANGE_FORBIDDEN", "Only the owner can change another member's role.")
            if actor_id not in row["members"]:
                raise HubFailure(404, "MEMBER_NOT_FOUND", "This member was not found.")
            row["members"][actor_id]["role"] = role
            self.settings.put_project(project_id, row)
            self.settings.write_actors(project_id)
            self.admission(project_id)
        return {"actorId": actor_id, "role": role}

    def status(self, project_id):
        row = self.settings.project(project_id)
        status = self.applications.status("monkeyarch", project_dir=row["projectDir"])
        answer = {"projectId": project_id, "projectDir": row["projectDir"], "kind": row["kind"],
                  "status": status.state, "ownerActorId": row["ownerActorId"], "members": self.members(project_id),
                  "error": status.error.detail if status.error else None}
        workers = self.applications.worker_snapshots(project_dir=row["projectDir"])
        url = status.apiUrl or next((worker.url for worker in workers if worker.service_id == "studio" and worker.process_id), None)
        if url:
            try:
                response = request_http(url, "/api/sync/status")
                if response.status == 200 and row["kind"] == "member":
                    answer.update(response.json())
            except OSError:
                answer["status"] = "offline"
        return answer

    def restore(self):
        for project_id in self.settings.read()["projects"]:
            try:
                self.resume(project_id)
            except (HubFailure, OSError, ValueError):
                # Saved membership survives an offline owner or missing native helper.
                continue

    def close(self):
        with self.lock:
            self.closing = True
            for connection in self.connections.values():
                connection.close()
            for gateway in self.gateways.values():
                gateway.close()
