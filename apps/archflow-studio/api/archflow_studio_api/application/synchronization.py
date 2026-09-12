"""HTTP synchronization for the process's explicitly configured P036 project.

The application transports values. The repository owns validation, installation
and pointer changes; the shared service retains the existing acceptance path.
"""

from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from starlette.datastructures import State

from archflow.project.repository import (
    FilesystemProjectRepository, ProjectRepositoryError,
    StaleDesignBranch, StaleProjectHead,
)

from .binding import bound_project
from ..protocol import PROTOCOL_MAJOR
from ..transport.errors import StudioError
from ..transport.synchronization import ProjectTransferDto, SynchronizationDto


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SharedProjectClient:
    """One trusted operator-configured endpoint; tokens never travel in URLs."""

    def __init__(self, settings):
        if not settings.sync_url:
            raise StudioError(409, "SYNC_NOT_CONFIGURED", "Configure this Runtime's shared project connection first.")
        self._url = settings.sync_url.rstrip("/")
        self._token = settings.sync_token
        self.project_id = settings.sync_project_id
        self._opener = build_opener(_NoRedirect())
        identity = self.request("GET", "/api/protocol")
        if identity.get("protocol") != f"archflow/{PROTOCOL_MAJOR}" or "shared-project" not in identity.get("capabilities", []):
            raise StudioError(409, "SYNC_PROTOCOL_MISMATCH", "The configured endpoint is not a compatible shared project service.")

    def request(self, method: str, path: str, payload=None, *, binary=False):
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(self._url + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=90) as response:
                content = response.read()
        except HTTPError as exc:
            try:
                failure = json.loads(exc.read())
                code, detail = failure["code"], failure["detail"]
                if not isinstance(code, str) or not isinstance(detail, str):
                    raise ValueError("invalid error")
            except (ValueError, KeyError, TypeError):
                code, detail = "SYNC_REMOTE_REFUSED", "The shared project service refused the request."
            raise StudioError(exc.code, code, detail) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise StudioError(503, "SYNC_UNAVAILABLE", "The shared project service is unavailable. Retained local work is available for retry.") from exc
        if binary:
            return content
        try:
            return json.loads(content)
        except ValueError as exc:
            raise StudioError(502, "SYNC_RESPONSE_INVALID", "The shared project service returned an unreadable response.") from exc

    def manifest(self) -> dict:
        try:
            transfer = ProjectTransferDto.model_validate(self.request("GET", "/api/sync/manifest")).model_dump()
        except ValueError as exc:
            raise StudioError(502, "SYNC_RESPONSE_INVALID", "The shared project manifest is invalid.") from exc
        if transfer["project_id"] != self.project_id or transfer["mode"] != "snapshot":
            raise StudioError(409, "SYNC_PROJECT_MISMATCH", "The service does not hold the configured shared project.")
        return transfer


def transfer_error(exc: ProjectRepositoryError) -> StudioError:
    if isinstance(exc, (StaleProjectHead, StaleDesignBranch)):
        return StudioError(409, "SYNC_BASE_CHANGED", "The project or local branch changed during synchronization. Retained work is available; review the current version before retrying.")
    detail = "Project transfer integrity or references are invalid; no shared version was advanced."
    # P036's transfer refusals contain project-relative evidence, not machine
    # paths or credentials. Preserve that actionable reason on the wire.
    if str(exc).startswith("TRANSFER_"):
        detail += " " + str(exc)
    return StudioError(422, "SYNC_TRANSFER_INVALID", detail)


def pull_shared_project(state: State, *, client: SharedProjectClient | None = None) -> SynchronizationDto:
    settings = state.settings
    client = client or SharedProjectClient(settings)
    repository = None
    if (settings.project_dir / "project.json").exists() and (settings.project_dir / "HEAD").exists():
        repository = bound_project(state).repository
        if repository.load_manifest().project_id != client.project_id:
            raise StudioError(409, "SYNC_PROJECT_MISMATCH", "The local project is not the configured shared project.")
    expected_head = repository.read_head() if repository else None
    expected_branches = repository.read_design_branches() if repository else None
    transfer = client.manifest()
    # The manifest is metadata only. Matching immutable bytes stay local.
    contents = {}
    transferred_bytes = 0
    for row in transfer["files"]:
        if repository:
            try:
                repository.read_transfer_file(row["path"], row["sha256"])
                continue
            except (ProjectRepositoryError, FileNotFoundError):
                pass
        content = client.request("GET", "/api/sync/files?" + urlencode({"path": row["path"], "sha256": row["sha256"]}), binary=True)
        contents[row["path"]] = base64.b64encode(content).decode("ascii")
        transferred_bytes += len(content)
    transfer["contents"] = contents
    try:
        if repository:
            repository.pull_transfer(transfer, expected_head=expected_head, expected_branches=expected_branches)
        else:
            FilesystemProjectRepository.bootstrap_transfer(settings.project_dir, transfer, expected_project_id=client.project_id)
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc
    state.binding = None
    return SynchronizationDto(projectId=client.project_id, filesTransferred=len(contents), bytesTransferred=transferred_bytes)


def push_shared_candidate(state: State, candidate_id: str, *, client: SharedProjectClient | None = None) -> SynchronizationDto:
    client = client or SharedProjectClient(state.settings)
    binding = bound_project(state)
    if binding.project_id != client.project_id:
        raise StudioError(409, "SYNC_PROJECT_MISMATCH", "The local project is not the configured shared project.")
    remote = client.manifest()
    known = {row["path"]: row["sha256"] for row in remote["files"]}
    try:
        transfer = binding.repository.export_transfer(run_id=candidate_id, known_files=known)
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc
    return SynchronizationDto.model_validate(client.request("POST", "/api/sync/candidates", transfer))


def accept_shared_candidate(state: State, candidate_id: str, payload: dict) -> dict:
    client = SharedProjectClient(state.settings)
    candidate_path = "/api/candidates/" + quote(candidate_id, safe="")
    try:
        client.request("GET", candidate_path)
    except StudioError as exc:
        if exc.status != 404:
            raise
        push_shared_candidate(state, candidate_id, client=client)
    result = client.request("POST", candidate_path + "/accept", payload)
    # Acceptance retries are idempotent on the service. A lost response or a
    # failed pull can be retried without creating another Stage.
    pull_shared_project(state, client=client)
    return result


def fork_shared_branch(state: State, payload: dict) -> dict:
    client = SharedProjectClient(state.settings)
    result = client.request("POST", "/api/design-branches", payload)
    pull_shared_project(state, client=client)
    return result
