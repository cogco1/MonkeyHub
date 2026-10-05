"""Durable observations of a member's last verified remote role.

This nonproject, noncache file records an upstream authorization observation,
not a preference or a new grant. Missing/corrupt observations fail read-only;
only another authenticated upstream identity response can restore write access.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROLES = frozenset({"viewer", "designer", "moderator", "revoked"})


class MemberRoleObservation:
    def __init__(self, settings):
        self.path = settings.team_state_file
        self.binding = {"projectId": settings.sync_project_id, "actorId": settings.team_actor_id,
                        "credentialSha256": hashlib.sha256((settings.sync_token or "").encode()).hexdigest()}

    def read(self) -> str:
        if self.path is None:
            return "viewer"
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "viewer"
        except (OSError, ValueError):
            return "revoked"
        if not isinstance(value, dict) or set(value) != {*self.binding, "role"}:
            return "revoked"
        if any(value[key] != expected for key, expected in self.binding.items()) or not isinstance(value["role"], str) or value["role"] not in ROLES:
            return "revoked"
        return value["role"]

    def write(self, role: str) -> None:
        if self.path is None or role not in ROLES:
            raise ValueError("A bounded membership observation is required")
        value = {**self.binding, "role": role}
        try:
            if json.loads(self.path.read_text(encoding="utf-8")) == value:
                return
        except (OSError, ValueError):
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                json.dump(value, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


def local_team_role(settings) -> str | None:
    return MemberRoleObservation(settings).read() if settings.sync_automatic else settings.team_role
