"""Single durable filesystem owner for one ArchFlow project document."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised only on POSIX hosts
    import fcntl

from archflow.project.digests import project_state_sha256
from archflow.project.layout import ProjectLayout
from archflow.project.manifest import ProjectManifest, ProjectManifestError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import (
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)


class ProjectRepositoryError(RuntimeError):
    """Base error for durable project corruption or invalid transitions."""


class ProjectAlreadyExists(ProjectRepositoryError):
    pass


class ProjectIntegrityError(ProjectRepositoryError):
    pass


class StaleProjectHead(ProjectRepositoryError):
    pass


class PromotionAuthorityError(ProjectRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedTransition:
    expected: ProjectVersionRef
    event: ProjectRecordRef
    replacement: ProjectRecordRef


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    head: ProjectVersionRef
    reachable_paths: tuple[str, ...]
    orphan_paths: tuple[str, ...]


LEGACY_FORMAT_VERSION = 1
CURRENT_FORMAT_VERSION = 2


_LOCK_INDEX_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}

_HEAD_LOCK_TIMEOUT_SECONDS = 10.0
_HEAD_LOCK_RETRY_SECONDS = 0.05


def _project_lock(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root.resolve(strict=False)))
    with _LOCK_INDEX_GUARD:
        return _PROJECT_LOCKS.setdefault(key, threading.RLock())


class ProjectHeadLocked(ProjectRepositoryError):
    """Another process holds the project head lock."""


class _HeadFileLock:
    """OS-level advisory lock serializing HEAD mutation across processes.

    The per-project ``threading.RLock`` provides in-process ordering, so this
    lock must only be acquired while that lock is held; then at most one
    thread per process ever touches the underlying file handle.  The lock
    file lives beside ``HEAD`` and deliberately carries no ``.json`` suffix
    so integrity scans never treat it as a project record.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None
        self._depth = 0

    def _try_acquire(self, handle: BinaryIO) -> bool:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised only on POSIX hosts
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def __enter__(self) -> None:
        if self._depth:
            self._depth += 1
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        deadline = time.monotonic() + _HEAD_LOCK_TIMEOUT_SECONDS
        try:
            while not self._try_acquire(handle):
                if time.monotonic() >= deadline:
                    raise ProjectHeadLocked(
                        "another process holds the project head lock: "
                        f"{self._path}"
                    )
                time.sleep(_HEAD_LOCK_RETRY_SECONDS)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        self._depth = 1

    def __exit__(self, *exc_info: object) -> None:
        self._depth -= 1
        if self._depth:
            return
        handle = self._handle
        self._handle = None
        if handle is None:  # pragma: no cover - defensive
            return
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised only on POSIX hosts
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise TypeError("JSON record payload must be a mapping")
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be finite JSON data") from exc
    return (encoded + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ProjectIntegrityError(f"cannot read project record: {path.name}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_bytes(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectIntegrityError(f"invalid JSON record: {path.name}") from exc
    if not isinstance(value, dict):
        raise ProjectIntegrityError(f"JSON record is not an object: {path.name}")
    return value


def _write_immutable(path: Path, data: bytes) -> None:
    """Install immutable content without ever replacing an existing target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read_bytes(path) != data:
            raise ProjectIntegrityError(
                f"immutable project path already contains different bytes: {path.name}"
            )
        return

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_bytes(path) != data:
                raise ProjectIntegrityError(
                    f"concurrent immutable write disagreed: {path.name}"
                )
    finally:
        temporary.unlink(missing_ok=True)


def _replace_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # No post-replace fsync: raising after the swap is already visible
        # would break the caller's "exception means no promotion" contract,
        # which matters more than flushing the rename's directory metadata.
    finally:
        temporary.unlink(missing_ok=True)


def _version_dict(ref: ProjectVersionRef) -> dict[str, Any]:
    return {
        "project_id": ref.project_id,
        "version": ref.version,
        "state_sha256": ref.require_digest(),
    }


def _version_from_dict(value: object, *, field: str) -> ProjectVersionRef:
    if not isinstance(value, dict) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise ProjectIntegrityError(f"{field} is not an exact project-version ref")
    try:
        return ProjectVersionRef(
            project_id=value["project_id"],
            version=value["version"],
            state_sha256=value["state_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(f"{field} is invalid") from exc


def _semantic_state_sha256(
    state: object,
    *,
    field: str,
) -> str:
    if not isinstance(state, Mapping):
        raise ProjectIntegrityError(f"{field} must be an object")
    try:
        return project_state_sha256(state)
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(
            f"{field} semantic digest is invalid"
        ) from exc


def _require_semantic_state_identity(
    state: Mapping[str, Any],
    *,
    project_id: str,
    version: int,
    field: str,
) -> None:
    if state.get("schema") != "CanonicalState@1":
        return
    if (
        state.get("project_id") != project_id
        or state.get("version") != version
    ):
        raise ProjectIntegrityError(
            f"{field} canonical identity disagrees with its project version"
        )


def _record_dict(ref: ProjectRecordRef) -> dict[str, Any]:
    return {
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(
    value: object,
    *,
    project_id: str,
    field: str,
) -> ProjectRecordRef:
    if not isinstance(value, dict) or set(value) != {
        "relative_path",
        "sha256",
        "media_type",
    }:
        raise ProjectIntegrityError(f"{field} is not an exact record ref")
    try:
        return ProjectRecordRef(
            project_id=project_id,
            relative_path=value["relative_path"],
            sha256=value["sha256"],
            media_type=value["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise ProjectIntegrityError(f"{field} is invalid") from exc


class FilesystemProjectRepository:
    """Content-addressed records plus one atomic canonical ``HEAD``."""

    def __init__(self, layout: ProjectLayout, manifest: ProjectManifest) -> None:
        self.layout = layout
        self._manifest = manifest
        self._lock = _project_lock(layout.root)
        self._head_lock = _HeadFileLock(layout.root / "HEAD.lock")

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        project_id: str,
        initial_state: Mapping[str, Any],
    ) -> FilesystemProjectRepository:
        layout = ProjectLayout(Path(root), project_id)
        manifest = ProjectManifest(
            project_id=project_id,
            format_version=CURRENT_FORMAT_VERSION,
        )
        # The existence check and the HEAD write must sit inside the same
        # OS-level lock compare_and_swap uses, or a stalled duplicate
        # initialize from another process can reset a promoted HEAD to v0.
        head_lock = _HeadFileLock(layout.root / "HEAD.lock")
        with _project_lock(layout.root), head_lock:
            if layout.manifest.exists():
                raise ProjectAlreadyExists(f"project already exists: {project_id}")
            layout.root.mkdir(parents=True, exist_ok=True)
            for directory in (
                layout.inputs,
                layout.objects,
                layout.events,
                layout.canonical,
                layout.runs,
                layout.exports,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            _write_immutable(layout.manifest, _json_bytes(manifest.to_dict()))
            repository = cls(layout, manifest)
            state = dict(initial_state)
            _require_semantic_state_identity(
                state,
                project_id=project_id,
                version=0,
                field="initial state",
            )
            state_sha256 = _semantic_state_sha256(
                state,
                field="initial state",
            )
            snapshot = repository._put_internal_json(
                layout.canonical,
                "state-v000000",
                {
                    "schema": "CanonicalSnapshot@2",
                    "project_id": project_id,
                    "version": 0,
                    "state_sha256": state_sha256,
                    "parent": None,
                    "state": state,
                },
            )
            head_ref = ProjectVersionRef(project_id, 0, state_sha256)
            event = repository._put_internal_json(
                layout.events,
                "event-v000000",
                {
                    "schema": "ProjectEvent@2",
                    "project_id": project_id,
                    "event_type": "project.initialized",
                    "decision": "accepted",
                    "run_id": None,
                    "from": None,
                    "from_snapshot": None,
                    "to": _version_dict(head_ref),
                    "to_snapshot": _record_dict(snapshot),
                    "previous_event": None,
                    "decision_receipt": None,
                },
            )
            if layout.head.exists():
                raise ProjectAlreadyExists(
                    f"project head already exists: {project_id}"
                )
            _replace_atomic(
                layout.head,
                _json_bytes(repository._head_payload(head_ref, snapshot, event)),
            )
            repository.verify()
            return repository

    @classmethod
    def open(cls, root: Path) -> FilesystemProjectRepository:
        root = Path(root).resolve(strict=False)
        manifest_path = root / "project.json"
        try:
            manifest = ProjectManifest.from_dict(_read_json(manifest_path))
        except (ProjectManifestError, TypeError, ValueError) as exc:
            raise ProjectIntegrityError("project manifest is invalid") from exc
        if manifest.format_version not in {
            LEGACY_FORMAT_VERSION,
            CURRENT_FORMAT_VERSION,
        }:
            raise ProjectIntegrityError(
                f"unsupported project format version: "
                f"{manifest.format_version}"
            )
        repository = cls(ProjectLayout(root, manifest.project_id), manifest)
        repository.verify()
        return repository

    def load_manifest(self) -> ProjectManifest:
        current = ProjectManifest.from_dict(_read_json(self.layout.manifest))
        if current != self._manifest:
            raise ProjectIntegrityError("immutable project manifest changed")
        return current

    def read_head(self) -> ProjectVersionRef:
        return self._read_head_document()[0]

    def load_current_state(self) -> dict[str, Any]:
        _, snapshot_ref, _ = self._read_head_document()
        snapshot = self.load_json(snapshot_ref)
        state = snapshot.get("state")
        if not isinstance(state, dict):
            raise ProjectIntegrityError("canonical snapshot state is not an object")
        return state

    def create_run(
        self,
        run_id: str,
        *,
        base: ProjectVersionRef | None = None,
    ) -> RunRef:
        require_identifier(run_id, "run_id")
        chosen_base = base or self.read_head()
        self._require_project_version(chosen_base, durable=True)
        run = RunRef(self._manifest.project_id, run_id, chosen_base)
        run_layout = self.layout.run(run_id)
        payload = {
            "schema": "ProjectRun@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": _version_dict(run.base),
        }
        with self._lock:
            _write_immutable(run_layout.manifest, _json_bytes(payload))
            for directory in (
                run_layout.records,
                run_layout.branches,
                run_layout.candidates,
                run_layout.reviews,
                run_layout.workspaces,
                run_layout.recovery,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        return run

    def load_run(self, run_id: str) -> RunRef:
        require_identifier(run_id, "run_id")
        payload = _read_json(self.layout.run(run_id).manifest)
        if set(payload) != {"schema", "project_id", "run_id", "base"}:
            raise ProjectIntegrityError("run manifest schema drifted")
        if (
            payload.get("schema") != "ProjectRun@1"
            or payload.get("project_id") != self._manifest.project_id
            or payload.get("run_id") != run_id
        ):
            raise ProjectIntegrityError("run manifest identity changed")
        run = RunRef(
            project_id=self._manifest.project_id,
            run_id=run_id,
            base=_version_from_dict(payload["base"], field="run base"),
        )
        self._validate_run(run)
        return run

    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef:
        self._validate_run(run)
        require_identifier(record_kind, "record_kind")
        directory = self._destination_directory(run, destination)
        if destination.area in {
            PersistenceArea.EVENT,
            PersistenceArea.CANONICAL,
            PersistenceArea.OBJECT,
        }:
            raise PromotionAuthorityError(
                f"{destination.area.value} writes are repository-internal"
            )
        data = _json_bytes(payload)
        digest = _sha256(data)
        path = directory / f"{record_kind}-{digest}.json"
        with self._lock:
            _write_immutable(path, data)
        return self._record_ref(path, digest, "application/json")

    def ingest(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef:
        self._validate_run(run)
        if destination.area is not PersistenceArea.OBJECT:
            raise ValueError("binary artifacts require the object destination")
        require_identifier(artifact_id, "artifact_id")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type must be non-empty text")
        data = source.read()
        if not isinstance(data, bytes):
            raise TypeError("artifact source must be opened in binary mode")
        digest = _sha256(data)
        path = self.layout.objects / digest[:2] / digest
        with self._lock:
            _write_immutable(path, data)
        relative = path.relative_to(self.layout.root).as_posix()
        return ProjectArtifactRef(
            project_id=self._manifest.project_id,
            artifact_id=artifact_id,
            relative_path=relative,
            sha256=digest,
            media_type=media_type,
        )

    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]:
        self._require_record(ref)
        path = self.layout.resolve_record(ref)
        data = _read_bytes(path)
        if _sha256(data) != ref.sha256:
            raise ProjectIntegrityError(f"record digest mismatch: {ref.relative_path}")
        return _read_json(path)

    def list_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> tuple[ProjectRecordRef, ...]:
        """Discover verified JSON records in one assigned project area."""

        self._validate_run(run)
        directory = self._destination_directory(run, destination)
        if destination.area in {
            PersistenceArea.OBJECT,
            PersistenceArea.EVENT,
            PersistenceArea.CANONICAL,
        }:
            raise ValueError("use typed repository loaders for this internal area")
        if not directory.exists():
            return ()
        refs = []
        for path in sorted(directory.glob("*.json")):
            data = _read_bytes(path)
            ref = self._record_ref(path, _sha256(data), "application/json")
            self.load_json(ref)
            refs.append(ref)
        return tuple(refs)

    def prepare_transition(
        self,
        *,
        run: RunRef,
        expected: ProjectVersionRef,
        replacement_state: Mapping[str, Any],
        decision_receipt: ProjectRecordRef,
    ) -> PreparedTransition:
        self._validate_run(run)
        self._require_project_version(expected, durable=True)
        if run.base != expected:
            raise StaleProjectHead("run is not based on the proposed canonical version")
        receipt = self.load_json(decision_receipt)
        expected_receipt = {
            "schema",
            "status",
            "project_id",
            "run_id",
            "checked_state",
            "candidate_ref",
        }
        if set(receipt) != expected_receipt:
            raise PromotionAuthorityError("promotion decision receipt schema drifted")
        if (
            receipt["schema"] != "PromotionDecision@1"
            or receipt["status"] != "accepted"
            or receipt["project_id"] != run.project_id
            or receipt["run_id"] != run.run_id
            or _version_from_dict(
                receipt["checked_state"],
                field="decision checked_state",
            )
            != expected
        ):
            raise PromotionAuthorityError(
                "only an accepted exact-base decision may prepare canonical promotion"
            )

        current, current_snapshot, previous_event = (
            self._read_head_document()
        )
        if current != expected:
            raise StaleProjectHead(
                f"expected {expected!r}; canonical is {current!r}"
            )
        next_version = expected.version + 1
        replacement_payload = dict(replacement_state)
        if self._manifest.format_version == LEGACY_FORMAT_VERSION:
            snapshot = self._put_internal_json(
                self.layout.canonical,
                f"state-v{next_version:06d}",
                {
                    "schema": "CanonicalSnapshot@1",
                    "project_id": run.project_id,
                    "version": next_version,
                    "parent": _version_dict(expected),
                    "state": replacement_payload,
                },
            )
            replacement = ProjectVersionRef(
                run.project_id,
                next_version,
                snapshot.sha256,
            )
            event_payload = {
                "schema": "ProjectEvent@1",
                "project_id": run.project_id,
                "event_type": "candidate.promoted",
                "decision": "accepted",
                "run_id": run.run_id,
                "from": _version_dict(expected),
                "to": _version_dict(replacement),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": _record_dict(decision_receipt),
            }
        else:
            _require_semantic_state_identity(
                replacement_payload,
                project_id=run.project_id,
                version=next_version,
                field="replacement state",
            )
            state_sha256 = _semantic_state_sha256(
                replacement_payload,
                field="replacement state",
            )
            snapshot = self._put_internal_json(
                self.layout.canonical,
                f"state-v{next_version:06d}",
                {
                    "schema": "CanonicalSnapshot@2",
                    "project_id": run.project_id,
                    "version": next_version,
                    "state_sha256": state_sha256,
                    "parent": _version_dict(expected),
                    "state": replacement_payload,
                },
            )
            replacement = ProjectVersionRef(
                run.project_id,
                next_version,
                state_sha256,
            )
            event_payload = {
                "schema": "ProjectEvent@2",
                "project_id": run.project_id,
                "event_type": "candidate.promoted",
                "decision": "accepted",
                "run_id": run.run_id,
                "from": _version_dict(expected),
                "from_snapshot": _record_dict(current_snapshot),
                "to": _version_dict(replacement),
                "to_snapshot": _record_dict(snapshot),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": _record_dict(decision_receipt),
            }
        event = self._put_internal_json(
            self.layout.events,
            f"event-v{next_version:06d}",
            event_payload,
        )
        return PreparedTransition(expected, event, snapshot)

    def compare_and_swap(
        self,
        *,
        expected: ProjectVersionRef,
        event: ProjectRecordRef,
        replacement: ProjectRecordRef,
    ) -> ProjectVersionRef:
        self._require_project_version(expected, durable=True)
        # Thread lock first, then the OS file lock: the read-validate-replace
        # sequence must be exclusive across processes, not just threads.
        with self._lock, self._head_lock:
            current, current_snapshot, current_event = (
                self._read_head_document()
            )
            if current != expected:
                raise StaleProjectHead(
                    f"expected {expected!r}; canonical is {current!r}"
                )
            self._require_area(event, "events/")
            self._require_area(replacement, "canonical/")
            snapshot = self.load_json(replacement)
            event_payload = self.load_json(event)
            if self._manifest.format_version == LEGACY_FORMAT_VERSION:
                next_ref = ProjectVersionRef(
                    self._manifest.project_id,
                    expected.version + 1,
                    replacement.sha256,
                )
                snapshot_valid = (
                    snapshot.get("schema") == "CanonicalSnapshot@1"
                    and snapshot.get("project_id")
                    == self._manifest.project_id
                    and snapshot.get("version") == next_ref.version
                    and _version_from_dict(
                        snapshot.get("parent"),
                        field="snapshot parent",
                    )
                    == expected
                )
                event_valid = (
                    event_payload.get("schema") == "ProjectEvent@1"
                    and event_payload.get("decision") == "accepted"
                    and _version_from_dict(
                        event_payload.get("from"),
                        field="event from",
                    )
                    == expected
                    and _version_from_dict(
                        event_payload.get("to"),
                        field="event to",
                    )
                    == next_ref
                    and _record_from_dict(
                        event_payload.get("previous_event"),
                        project_id=self._manifest.project_id,
                        field="previous_event",
                    )
                    == current_event
                )
            else:
                state_sha256 = _semantic_state_sha256(
                    snapshot.get("state"),
                    field="replacement snapshot state",
                )
                next_ref = ProjectVersionRef(
                    self._manifest.project_id,
                    expected.version + 1,
                    state_sha256,
                )
                snapshot_valid = (
                    snapshot.get("schema") == "CanonicalSnapshot@2"
                    and snapshot.get("project_id")
                    == self._manifest.project_id
                    and snapshot.get("version") == next_ref.version
                    and snapshot.get("state_sha256") == state_sha256
                    and _version_from_dict(
                        snapshot.get("parent"),
                        field="snapshot parent",
                    )
                    == expected
                )
                event_valid = (
                    event_payload.get("schema") == "ProjectEvent@2"
                    and event_payload.get("decision") == "accepted"
                    and _version_from_dict(
                        event_payload.get("from"),
                        field="event from",
                    )
                    == expected
                    and _record_from_dict(
                        event_payload.get("from_snapshot"),
                        project_id=self._manifest.project_id,
                        field="event from_snapshot",
                    )
                    == current_snapshot
                    and _version_from_dict(
                        event_payload.get("to"),
                        field="event to",
                    )
                    == next_ref
                    and _record_from_dict(
                        event_payload.get("to_snapshot"),
                        project_id=self._manifest.project_id,
                        field="event to_snapshot",
                    )
                    == replacement
                    and _record_from_dict(
                        event_payload.get("previous_event"),
                        project_id=self._manifest.project_id,
                        field="previous_event",
                    )
                    == current_event
                )
            if not snapshot_valid:
                raise ProjectIntegrityError(
                    "replacement snapshot is not exact-base"
                )
            if not event_valid:
                raise PromotionAuthorityError(
                    "event does not prove an accepted exact-base transition"
                )
            _replace_atomic(
                self.layout.head,
                _json_bytes(self._head_payload(next_ref, replacement, event)),
            )
            return next_ref

    def verify(self) -> RecoveryReport:
        self.load_manifest()
        head, snapshot, event = self._read_head_document()
        reachable = {
            "project.json",
            "HEAD",
            snapshot.relative_path,
            event.relative_path,
        }
        expected = head
        expected_snapshot = snapshot
        current_event = event
        while True:
            snapshot_payload = self._verify_snapshot(
                expected_snapshot,
                expected,
            )
            event_payload = self.load_json(current_event)
            to_ref = _version_from_dict(event_payload.get("to"), field="event to")
            if to_ref != expected:
                raise ProjectIntegrityError("event chain does not reach HEAD")
            if self._manifest.format_version == CURRENT_FORMAT_VERSION:
                if event_payload.get("schema") != "ProjectEvent@2":
                    raise ProjectIntegrityError(
                        "format-version-2 event schema drifted"
                    )
                to_snapshot = _record_from_dict(
                    event_payload.get("to_snapshot"),
                    project_id=self._manifest.project_id,
                    field="event to_snapshot",
                )
                if to_snapshot != expected_snapshot:
                    raise ProjectIntegrityError(
                        "event does not name its resulting snapshot"
                    )
            elif event_payload.get("schema") != "ProjectEvent@1":
                raise ProjectIntegrityError(
                    "format-version-1 event schema drifted"
                )
            previous = event_payload.get("previous_event")
            parent = event_payload.get("from")
            if previous is None:
                from_snapshot = event_payload.get("from_snapshot")
                if (
                    parent is not None
                    or expected.version != 0
                    or (
                        self._manifest.format_version
                        == CURRENT_FORMAT_VERSION
                        and from_snapshot is not None
                    )
                    or snapshot_payload.get("parent") is not None
                ):
                    raise ProjectIntegrityError("event chain terminates incorrectly")
                break
            previous_ref = _record_from_dict(
                previous,
                project_id=self._manifest.project_id,
                field="previous_event",
            )
            parent_ref = _version_from_dict(parent, field="event from")
            reachable.add(previous_ref.relative_path)
            if self._manifest.format_version == CURRENT_FORMAT_VERSION:
                parent_snapshot = _record_from_dict(
                    event_payload.get("from_snapshot"),
                    project_id=self._manifest.project_id,
                    field="event from_snapshot",
                )
                if _version_from_dict(
                    snapshot_payload.get("parent"),
                    field="snapshot parent",
                ) != parent_ref:
                    raise ProjectIntegrityError(
                        "snapshot parent disagrees with event"
                    )
            else:
                parent_snapshot = self._canonical_ref_for_version(
                    parent_ref
                )
            self._verify_snapshot(parent_snapshot, parent_ref)
            reachable.add(parent_snapshot.relative_path)
            expected = parent_ref
            expected_snapshot = parent_snapshot
            current_event = previous_ref

        retained = {
            path.relative_to(self.layout.root).as_posix()
            for base in (self.layout.events, self.layout.canonical)
            if base.exists()
            for path in base.rglob("*.json")
            if path.is_file()
        }
        return RecoveryReport(
            head=head,
            reachable_paths=tuple(sorted(reachable)),
            orphan_paths=tuple(sorted(retained - reachable)),
        )

    def _read_head_document(
        self,
    ) -> tuple[ProjectVersionRef, ProjectRecordRef, ProjectRecordRef]:
        payload = _read_json(self.layout.head)
        if set(payload) != {
            "schema",
            "project_id",
            "current",
            "snapshot",
            "event",
        }:
            raise ProjectIntegrityError("HEAD schema drifted")
        expected_schema = (
            "ProjectHead@1"
            if self._manifest.format_version == LEGACY_FORMAT_VERSION
            else "ProjectHead@2"
        )
        if (
            payload.get("schema") != expected_schema
            or payload.get("project_id") != self._manifest.project_id
        ):
            raise ProjectIntegrityError("HEAD belongs to another project or schema")
        current = _version_from_dict(payload["current"], field="HEAD current")
        snapshot = _record_from_dict(
            payload["snapshot"],
            project_id=self._manifest.project_id,
            field="HEAD snapshot",
        )
        event = _record_from_dict(
            payload["event"],
            project_id=self._manifest.project_id,
            field="HEAD event",
        )
        self._require_area(snapshot, "canonical/")
        self._require_area(event, "events/")
        self._verify_snapshot(snapshot, current)
        self.load_json(event)
        return current, snapshot, event

    def _head_payload(
        self,
        current: ProjectVersionRef,
        snapshot: ProjectRecordRef,
        event: ProjectRecordRef,
    ) -> dict[str, Any]:
        return {
            "schema": (
                "ProjectHead@1"
                if self._manifest.format_version == LEGACY_FORMAT_VERSION
                else "ProjectHead@2"
            ),
            "project_id": self._manifest.project_id,
            "current": _version_dict(current),
            "snapshot": _record_dict(snapshot),
            "event": _record_dict(event),
        }

    def _verify_snapshot(
        self,
        snapshot: ProjectRecordRef,
        expected: ProjectVersionRef,
    ) -> dict[str, Any]:
        self._require_area(snapshot, "canonical/")
        payload = self.load_json(snapshot)
        if (
            payload.get("project_id") != self._manifest.project_id
            or payload.get("version") != expected.version
            or not isinstance(payload.get("state"), Mapping)
        ):
            raise ProjectIntegrityError("snapshot metadata disagrees")
        if self._manifest.format_version == LEGACY_FORMAT_VERSION:
            if (
                payload.get("schema") != "CanonicalSnapshot@1"
                or expected.require_digest() != snapshot.sha256
            ):
                raise ProjectIntegrityError(
                    "legacy snapshot identity disagrees"
                )
            return payload
        if payload.get("schema") != "CanonicalSnapshot@2":
            raise ProjectIntegrityError(
                "format-version-2 snapshot schema drifted"
            )
        state_sha256 = _semantic_state_sha256(
            payload["state"],
            field="snapshot state",
        )
        _require_semantic_state_identity(
            payload["state"],
            project_id=self._manifest.project_id,
            version=expected.version,
            field="snapshot state",
        )
        if (
            payload.get("state_sha256") != state_sha256
            or expected.require_digest() != state_sha256
        ):
            raise ProjectIntegrityError(
                "snapshot semantic state digest disagrees"
            )
        return payload

    def _destination_directory(
        self,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> Path:
        if not isinstance(destination, PersistenceDestination):
            raise TypeError("destination must be a PersistenceDestination")
        if destination.run_id is not None and destination.run_id != run.run_id:
            raise ValueError("destination belongs to another run")
        run_layout = self.layout.run(run.run_id)
        areas = {
            PersistenceArea.INPUT: self.layout.inputs,
            PersistenceArea.OBJECT: self.layout.objects,
            PersistenceArea.EVENT: self.layout.events,
            PersistenceArea.CANONICAL: self.layout.canonical,
            PersistenceArea.RUN_RECORD: run_layout.records,
            PersistenceArea.RUN_CANDIDATE: run_layout.candidates,
            PersistenceArea.RUN_REVIEW: run_layout.reviews,
            PersistenceArea.RUN_WORKSPACE: run_layout.workspaces,
            PersistenceArea.RUN_RECOVERY: run_layout.recovery,
            PersistenceArea.EXPORT: self.layout.exports,
        }
        if destination.area is PersistenceArea.RUN_BRANCH:
            if destination.branch_id is None:
                raise ValueError("run branch destination lacks branch_id")
            return run_layout.branches / destination.branch_id / "records"
        return areas[destination.area]

    def _put_internal_json(
        self,
        directory: Path,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef:
        require_identifier(record_kind, "record_kind")
        data = _json_bytes(payload)
        digest = _sha256(data)
        path = directory / f"{record_kind}-{digest}.json"
        _write_immutable(path, data)
        return self._record_ref(path, digest, "application/json")

    def _record_ref(
        self,
        path: Path,
        digest: str,
        media_type: str,
    ) -> ProjectRecordRef:
        relative = path.relative_to(self.layout.root).as_posix()
        return ProjectRecordRef(
            project_id=self._manifest.project_id,
            relative_path=relative,
            sha256=digest,
            media_type=media_type,
        )

    def _canonical_ref_for_version(
        self,
        ref: ProjectVersionRef,
    ) -> ProjectRecordRef:
        if self._manifest.format_version != LEGACY_FORMAT_VERSION:
            raise ProjectIntegrityError(
                "current snapshots require explicit record references"
            )
        digest = ref.require_digest()
        return ProjectRecordRef(
            project_id=self._manifest.project_id,
            relative_path=(
                f"canonical/state-v{ref.version:06d}-{digest}.json"
            ),
            sha256=digest,
        )

    def _require_record(self, ref: ProjectRecordRef) -> None:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be a ProjectRecordRef")
        if ref.project_id != self._manifest.project_id:
            raise ValueError("record belongs to another project")

    def _require_area(self, ref: ProjectRecordRef, prefix: str) -> None:
        self._require_record(ref)
        if not ref.relative_path.startswith(prefix):
            raise ProjectIntegrityError(
                f"record must belong to project area {prefix.rstrip('/')}"
            )

    def _require_project_version(
        self,
        ref: ProjectVersionRef,
        *,
        durable: bool,
    ) -> None:
        if not isinstance(ref, ProjectVersionRef):
            raise TypeError("ref must be a ProjectVersionRef")
        if ref.project_id != self._manifest.project_id:
            raise ValueError("version belongs to another project")
        if durable:
            ref.require_digest()

    def _validate_run(self, run: RunRef) -> None:
        if not isinstance(run, RunRef):
            raise TypeError("run must be a RunRef")
        if run.project_id != self._manifest.project_id:
            raise ValueError("run belongs to another project")
        payload = _read_json(self.layout.run(run.run_id).manifest)
        expected = {
            "schema": "ProjectRun@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": _version_dict(run.base),
        }
        if payload != expected:
            raise ProjectIntegrityError("run manifest changed or base drifted")
