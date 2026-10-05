"""Local device identity and team configuration, never retained project content.

Windows secrets use the account credential store. Unix development installs use
an account-private file under the explicit Hub runtime root. Public membership
configuration contains hashes only; neither status nor errors return secrets.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from uuid import uuid4

from .credentials import WindowsCredentialStore
from ..models import HubFailure


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DeviceSecrets:
    def __init__(self, root: Path):
        self.path = root / "config" / "team-secrets.json"
        self.target = "MonkeyHub/team/" + digest(str(root.resolve()))
        self.windows = WindowsCredentialStore() if sys.platform == "win32" else None

    def read(self) -> dict:
        try:
            if self.windows:
                return json.loads(self.windows.read(self.target) or "{}")
            if self.path.exists() and self.path.stat().st_mode & 0o077:
                raise HubFailure(409, "DEVICE_STORE_PERMISSIONS", "The device credential file must be private to this account.")
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def write(self, value: dict) -> None:
        if self.windows:
            self.windows.write(self.target, json.dumps(value, separators=(",", ":")))
        else:
            atomic_json(self.path, value)


class TeamSettings:
    def __init__(self, root: Path, *, secret_store=None):
        self.root = root
        self.path = root / "config" / "teams.json"
        self.secrets = secret_store or DeviceSecrets(root)
        self.lock = threading.RLock()

    def read(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"projects": {}}

    def save(self, value):
        atomic_json(self.path, value)

    def identity(self):
        with self.lock:
            private = self.secrets.read()
            if "device" not in private:
                private["device"] = {"secretKey": secrets.token_hex(32), "actorId": "device-" + uuid4().hex}
                self.secrets.write(private)
            return dict(private["device"])

    def transport_key(self, project_id: str) -> str:
        # Each project endpoint stays stable without competing for one relay identity.
        key = bytes.fromhex(self.identity()["secretKey"])
        return hmac.new(key, b"monkeyhub/transport/1/" + project_id.encode(), hashlib.sha256).hexdigest()

    def token(self, project_id: str, *, create=False):
        with self.lock:
            private = self.secrets.read()
            tokens = private.setdefault("tokens", {})
            if create and project_id not in tokens:
                tokens[project_id] = secrets.token_urlsafe(32)
                self.secrets.write(private)
            return tokens.get(project_id)

    def project(self, project_id):
        result = self.read()["projects"].get(project_id)
        if result is None:
            raise HubFailure(404, "TEAM_NOT_FOUND", "This project has no team connection.")
        return result

    def by_path(self, path):
        resolved = str(Path(path).resolve())
        return next((row for row in self.read()["projects"].values() if row["projectDir"] == resolved), None)

    def put_project(self, project_id, row):
        with self.lock:
            config = self.read()
            config["projects"][project_id] = row
            self.save(config)

    def invite(self, project_id, role, lifetime=1800):
        with self.lock:
            row = self.project(project_id)
            code = secrets.token_urlsafe(32)
            row.setdefault("invites", {})[digest(code)] = {"role": role, "expires": time.time() + lifetime, "usedBy": None}
            self.put_project(project_id, row)
            return code

    def redeem(self, project_id, code, peer, actor_id, name, token):
        with self.lock:
            row = self.project(project_id)
            invite = row.get("invites", {}).get(digest(code))
            if invite is None or invite["expires"] < time.time():
                raise HubFailure(403, "INVITE_INVALID", "This invitation is invalid or expired.")
            if invite["usedBy"] is not None:
                member = row["members"].get(actor_id)
                if invite["usedBy"] != peer or not member or member["nodeId"] != peer or member["tokenHash"] != digest(token) or member["role"] == "revoked":
                    raise HubFailure(409, "INVITE_USED", "This invitation has already been used.")
                return member
            if any(member["tokenHash"] == digest(token) for member in row["members"].values()):
                raise HubFailure(409, "MEMBER_CREDENTIAL_INVALID", "This credential is already assigned to a member.")
            if actor_id in row["members"] or any(member["nodeId"] == peer for member in row["members"].values()):
                raise HubFailure(409, "MEMBER_EXISTS", "This device is already a member.")
            member = {"actorId": actor_id, "name": name, "nodeId": peer, "role": invite["role"], "tokenHash": digest(token)}
            row["members"][actor_id] = member
            invite["usedBy"] = peer
            self.put_project(project_id, row)
            self.write_actors(project_id)
            return member

    def write_actors(self, project_id):
        row = self.project(project_id)
        actors = []
        for member in row["members"].values():
            if member["role"] == "revoked":
                continue
            actions = ["read"]
            if member["role"] in {"designer", "moderator"}:
                actions.append("propose")
            if member["role"] == "moderator":
                actions.append("accept")
            actors.append({"actor_id": member["actorId"], "display_name": member["name"],
                           "token_sha256": member["tokenHash"], "projects": {project_id: actions}})
        path = self.root / "config" / "teams" / (digest(project_id) + ".actors.json")
        atomic_json(path, {"format": "sha256", "actors": actors})
        return path
