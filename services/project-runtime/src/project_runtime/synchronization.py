"""HTTP synchronization for the process's explicitly configured P036 project.

The application transports values. The repository owns validation, installation
and pointer changes; the shared service retains the existing acceptance path.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from starlette.datastructures import State

from archflow.project.repository import (
    FilesystemProjectRepository, ProjectRepositoryError,
    StaleDesignBranch, StaleProjectHead, StaleWorkingDraft,
)

from .binding import bound_project
from archflow.project.writer_lease import hold_writer_lease
from .sync_cache import SyncDownloadCache
from .sync_state import MemberRoleObservation
from .protocol import PROTOCOL_MAJOR
from .errors import StudioError
from .api.dto.synchronization import ProjectTransferDto, SynchronizationDto


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
        self._manifest = None
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
        if method not in {"GET", "HEAD"}:
            self._manifest = None
        if binary:
            return content
        try:
            return json.loads(content)
        except ValueError as exc:
            raise StudioError(502, "SYNC_RESPONSE_INVALID", "The shared project service returned an unreadable response.") from exc

    def manifest(self) -> dict:
        if self._manifest is not None:
            return self._manifest
        try:
            transfer = ProjectTransferDto.model_validate(self.request("GET", "/api/sync/manifest")).model_dump()
        except ValueError as exc:
            raise StudioError(502, "SYNC_RESPONSE_INVALID", "The shared project manifest is invalid.") from exc
        if transfer["project_id"] != self.project_id or transfer["mode"] != "snapshot":
            raise StudioError(409, "SYNC_PROJECT_MISMATCH", "The service does not hold the configured shared project.")
        self._manifest = transfer
        return transfer


def transfer_error(exc: ProjectRepositoryError) -> StudioError:
    if isinstance(exc, (StaleProjectHead, StaleDesignBranch, StaleWorkingDraft)):
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
    cache_root = settings.cache_dir
    cache = (getattr(state, "sync_cache", None) or SyncDownloadCache(cache_root / "sync")) if cache_root else None
    if repository is None and cache is not None:
        try:
            saved = cache.saved_initial_transfer(client.project_id, settings.project_dir)
            if saved is not None:
                transfer = ProjectTransferDto.model_validate(saved).model_dump()
        except (ValueError, KeyError, TypeError) as exc:
            raise StudioError(409, "SYNC_RESUME_STATE_INVALID", "The interrupted download journal is invalid; the project folder was not overwritten.") from exc
    # Cache lives outside P036 and survives an interrupted transfer. Only the
    # complete validated transfer crosses the repository's write boundary.
    contents = {}
    transferred_bytes = 0
    progress = getattr(state, "team_sync", None)
    pending = []
    for row in transfer["files"]:
        if repository:
            try:
                repository.read_transfer_file(row["path"], row["sha256"])
                continue
            except (ProjectRepositoryError, FileNotFoundError):
                pass
        pending.append(row)
    if progress:
        progress.total_bytes = sum(row["size"] for row in pending)
        progress.completed_bytes = 0
    if cache is None:  # Existing operator-configured clients remain supported.
        for row in pending:
            content = client.request("GET", "/api/sync/files?" + urlencode({"path": row["path"], "sha256": row["sha256"]}), binary=True)
            contents[row["path"]] = base64.b64encode(content).decode("ascii")
            transferred_bytes += len(content)
    else:
        offsets = {row["path"]: cache.offset(row["sha256"], row["size"]) for row in pending}
        remaining = list({row["sha256"]: row for row in pending}.values())
        if progress:
            progress.completed_bytes = sum(offsets.values())
        while remaining:
            chunks, capacity = [], 8 * 1024 * 1024
            for row in remaining[:128]:
                offset = offsets[row["path"]]
                length = min(capacity, row["size"] - offset)
                chunks.append({**row, "offset": offset, "length": length})
                capacity -= length
                if capacity == 0:
                    break
            # A full cached file needs no request, including after process restart.
            chunks = [chunk for chunk in chunks if chunk["length"] or not cache._path(chunk["sha256"]).exists()]
            if chunks:
                result = client.request("POST", "/api/sync/files/batch", {"files": chunks})
                try:
                    received = result["files"]
                    if len(received) != len(chunks):
                        raise ValueError("missing chunk")
                    for chunk, row in zip(chunks, received):
                        if any(row[key] != chunk[key] for key in chunk):
                            raise ValueError("chunk identity mismatch")
                        content = base64.b64decode(row["content"], validate=True)
                        if len(content) != chunk["length"]:
                            raise ValueError("truncated chunk")
                        cache.append(chunk["sha256"], chunk["offset"], content, chunk["size"])
                        offsets[chunk["path"]] += len(content)
                        transferred_bytes += len(content)
                        if progress:
                            progress.completed_bytes += len(content)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise StudioError(502, "SYNC_CHUNK_INVALID", "The shared project returned an invalid chunk.") from exc
            completed = [row for row in remaining if offsets[row["path"]] == row["size"] and cache._path(row["sha256"]).exists()]
            for row in completed:
                try:
                    contents[row["path"]] = cache.encoded(row["sha256"], row["size"])
                except ValueError as exc:
                    raise StudioError(502, "SYNC_DIGEST_INVALID", "The downloaded file failed its content check; retry to download it again.") from exc
                remaining.remove(row)
        for row in pending:
            if row["path"] not in contents:
                contents[row["path"]] = cache.encoded(row["sha256"], row["size"])
    transfer["contents"] = contents
    try:
        if repository:
            repository.pull_transfer(transfer, expected_head=expected_head, expected_branches=expected_branches)
        else:
            if cache is not None:
                selected = cache.initial_transfer(transfer, settings.project_dir)
                if selected != {**transfer, "contents": {}}:
                    raise StudioError(409, "SYNC_SNAPSHOT_CHANGED", "Another initial transfer is being installed; retry its retained snapshot.")
            if settings.sync_automatic and getattr(state, "replica_writer_lease", None) is None:
                state.replica_writer_lease = hold_writer_lease(settings.project_dir)
            FilesystemProjectRepository.bootstrap_transfer(settings.project_dir, transfer, expected_project_id=client.project_id)
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc
    if cache is not None:
        cache.finish_initial_transfer()
    # Keep the same live binding while candidate jobs may hold it. Its existing
    # explicit refresh reconciles imported content into the derived index.
    if repository:
        bound_project(state).refresh()
    return SynchronizationDto(projectId=client.project_id, filesTransferred=len(contents), bytesTransferred=transferred_bytes)


def push_shared_candidate(state: State, candidate_id: str, *, client: SharedProjectClient | None = None) -> SynchronizationDto:
    client = client or SharedProjectClient(state.settings)
    binding = bound_project(state)
    if binding.project_id != client.project_id:
        raise StudioError(409, "SYNC_PROJECT_MISMATCH", "The local project is not the configured shared project.")
    remote = client.manifest()
    known = {row["path"]: row["sha256"] for row in remote["files"]}
    try:
        transfer = binding.repository.export_transfer(run_id=candidate_id, known_files=known,
                                                      include_contents=state.settings.cache_dir is None)
    except ProjectRepositoryError as exc:
        raise transfer_error(exc) from exc
    if state.settings.cache_dir is not None:
        for start in range(0, len(transfer["files"]), 128):
            missing = client.request("POST", "/api/sync/files/missing", {"files": transfer["files"][start:start + 128]})["files"]
            for row in {row["sha256"]: row for row in missing}.values():
                content = binding.repository.read_transfer_file(row["path"], row["sha256"])
                offset = row["offset"]
                while offset < len(content) or len(content) == 0:
                    chunk = content[offset:offset + 1024 * 1024]
                    client.request("POST", "/api/sync/files/upload", {"files": [{**row, "offset": offset,
                        "length": len(chunk), "content": base64.b64encode(chunk).decode("ascii")}]})
                    offset += len(chunk)
                    if not content:
                        break
        transfer["contents"] = {}
    if state.settings.team_actor_id:
        working, _ = binding.repository.read_working_draft()
        if candidate_id in working["runs"]:
            transfer["retainedRow"] = working["runs"][candidate_id]
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


class TeamSynchronization:
    """Owned Runtime worker, polling only while its Hub-managed process lives.

    Push the actor's retained line before pulling others. Network failure never
    discards local work. Neither recovery records nor active jobs are uploaded.
    """
    def __init__(self, state):
        self.state = state
        self.observation = MemberRoleObservation(state.settings)
        self.role = self.observation.read()
        self.actor_names = {}
        self.status = "connecting"
        self.error = None
        self.total_bytes = self.completed_bytes = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_manifest = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="project-team-sync")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=95)

    def snapshot(self):
        return {"status": self.status, "role": self.role, "error": self.error,
                "totalBytes": self.total_bytes, "completedBytes": self.completed_bytes}

    def step(self):
        with self._lock:
            settings = self.state.settings
            client = SharedProjectClient(settings)
            identity = client.request("GET", "/api/sync/identity")
            if identity["actorId"] != settings.team_actor_id:
                raise StudioError(403, "SYNC_ACTOR_MISMATCH", "This connection belongs to another member.")
            try:
                self.observation.write(identity["role"])
            except (OSError, ValueError):
                self.role = "revoked"
                raise StudioError(503, "SYNC_ROLE_STATE_UNAVAILABLE", "The verified membership role could not be saved; editing stays read-only.") from None
            self.role = identity["role"]
            remote = client.request("GET", "/api/sync/lines")["lines"]
            self.actor_names = {row["actorId"]: row["name"] for row in remote}
            if (settings.project_dir / "HEAD").exists() and self.role != "viewer":
                repo = bound_project(self.state).repository
                # Retained offline alternatives belong to the project too, even
                # if the actor later continued from a different candidate.
                working, _ = repo.read_working_draft()
                retained = set(working["runs"]) - set(working["active"])
                missing_runs = retained - set(client.manifest()["run_ids"])
                for run_id in sorted(missing_runs):
                    self.status = "uploading"
                    push_shared_candidate(self.state, run_id, client=client)
                local = repo.sync_actor_line(settings.team_actor_id, owner_actor_id=settings.project_owner_actor_id)
                previous = next((row for row in remote if row["actorId"] == settings.team_actor_id), {"current": None, "row": None, "revision": None})
                if (local["current"], local["row"]) != (previous["current"], previous["row"]) and (local["current"] is not None or local["revision"] is not None):
                    self.status = "uploading"
                    if local["current"] is not None:
                        push_shared_candidate(self.state, local["current"], client=client)
                    client.request("PUT", "/api/sync/line", {"current": local["current"], "row": local["row"], "expectedRevision": previous["revision"]})
            self.status = "downloading"
            manifest = client.manifest()
            manifest_key = {key: value for key, value in manifest.items() if key != "contents"}
            initializing = not (settings.project_dir / "HEAD").exists()
            if manifest_key != self._last_manifest or initializing:
                pull_shared_project(self.state, client=client)
                # An interrupted first install may have completed its pinned
                # older snapshot; the next pass must read the latest one.
                self._last_manifest = None if initializing else manifest_key
            repo = bound_project(self.state).repository
            for line in remote:
                _, revision = repo.read_actor_working_draft(line["actorId"], owner_actor_id=settings.project_owner_actor_id)
                if line["actorId"] == settings.team_actor_id and revision is not None:
                    continue
                repo.receive_sync_actor_line(line["actorId"], {"current": line["current"], "row": line["row"]},
                    expected_revision=revision, owner_actor_id=settings.project_owner_actor_id)
            bound_project(self.state).refresh()
            self.status, self.error = "synced", None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except StudioError as exc:
                self.status, self.error = "offline", exc.code
                if exc.status in {401, 403}:
                    self.role, self.status = "revoked", "revoked"
                    try:
                        self.observation.write("revoked")
                    except (OSError, ValueError):
                        self.error = "SYNC_ROLE_STATE_UNAVAILABLE"
            except (OSError, ValueError, ProjectRepositoryError):
                self.status, self.error = "retry", "SYNC_RETRY_REQUIRED"
            if self._stop.wait(5):
                return
