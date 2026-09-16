"""Single durable filesystem owner for one ArchFlow project document."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from pathlib import PurePath
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import uuid4
from urllib.parse import unquote, urlsplit

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised only on POSIX hosts
    import fcntl

from archflow.project.digests import project_state_sha256
from archflow.project.layout import AUTHORED_RECORD_PATH, ProjectLayout
from archflow.project.manifest import ProjectManifest, ProjectManifestError
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    DESIGN_STAGE,
    PROJECT_FORMAT_MIGRATION,
    is_registered,
    require_registered,
)
from archflow.project.version_refs import (
    content_digest_of as _content_digest_of,
    locations as _declared_locations,
    covered_pointers as _covered_pointers,
    derived_fields as _derived_fields,
    closure_check as _closure_check,
    read_back as _read_back,
    recompute as _recompute_derived,
    register as _register_version_refs,
    register_derived as _register_derived_fields,
    restate as _restate_declared,
)
from archflow.project.refs import (
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    record_file_name,
    require_identifier,
    require_project_relative_path,
    parse_record_file_name,
)


class ProjectRepositoryError(RuntimeError):
    """Base error for durable project corruption or invalid transitions."""


class ProjectAlreadyExists(ProjectRepositoryError):
    pass


class ProjectIntegrityError(ProjectRepositoryError):
    pass


class StaleProjectHead(ProjectRepositoryError):
    pass


class StaleDesignBranch(ProjectRepositoryError):
    """The design branch advanced since this change was prepared."""


class PromotionAuthorityError(ProjectRepositoryError):
    pass


def _transfer_path(value: str) -> str:
    """Retained native export names may contain @; never accept host paths.

    ``exports`` is a project-owned root a retained reference may name, so the
    closure can follow a typed edge into it. It is never walked: only a file
    some retained record actually references travels.
    """
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: expected a project-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in ("", ".", "..") or part.endswith((".", " "))
        or any(ord(char) < 32 for char in part) for part in path.parts
    ):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: unsafe project path")
    allowed = value in ("project.json", "HEAD", "design/branches.json",
                        "input/runner/state-record.json", "input/runner/seats.json",
                        "input/runner/program-sheet.json")
    if not allowed and path.parts[0] not in (
        "canonical", "events", "exports", "objects", "runs",
    ):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: unassigned project area")
    if any(part.endswith(".lock") for part in path.parts):
        raise ProjectIntegrityError("TRANSFER_PATH_INVALID: locks are not project content")
    return value


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
SUPPORTED_FORMAT_VERSIONS: tuple[int, ...] = (
    LEGACY_FORMAT_VERSION,
    CURRENT_FORMAT_VERSION,
)

# What ``project.json`` says this build can do with a directory. There is no
# ``upgradeable`` status: a supported legacy format 1 is readable in place and
# is upgraded only through ``migrate_project_format``, which writes a new
# format-2 directory and never rewrites the source, so upgradeability is a
# property of that operation rather than a fourth thing a directory declares.
PROJECT_FORMAT_CURRENT = "current"
PROJECT_FORMAT_SUPPORTED_LEGACY = "supported_legacy"
PROJECT_FORMAT_TOO_NEW = "too_new"
PROJECT_FORMAT_INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ProjectFormatInspection:
    """What one directory's ``project.json`` declares, read and nothing more.

    ``project.json`` is the only authority here. This reads no HEAD, opens no
    project and writes nothing, so a too-new or malformed project can be named
    instead of only raising on open.
    """

    root: Path
    status: str
    format_version: int | None
    project_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class RetainedFormatEntry:
    """One row of the retained closure grouped by what governs its format.

    ``category`` says which authority owns the file: ``envelope`` for the
    project-format structures ``project.json`` versions, ``record`` for the
    content-addressed run records the record-kind table names, ``run_manifest``
    and ``design`` for the stable project structures, ``authored_input`` for
    work in progress, and ``artifact`` for digest-identified bytes. ``schema``
    is the literal the payload declares, and ``registered`` says whether the
    record-kind table still holds this kind, or is ``None`` where record-kind
    registration does not apply. Nothing here interprets a payload's contents.
    """

    category: str
    kind: str
    schema: str | None
    registered: bool | None
    count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class ProjectMigrationPlan:
    """An ephemeral dry run: what a migration would have to touch, and why not.

    This is a plain value, not a second retained manifest. ``planned`` says the
    retained closure was read and inventoried; when it is false the plan is a
    refusal and ``blockers`` says why. A caller may act only when
    ``migration_required`` is true and ``blockers`` is empty.
    """

    inspection: ProjectFormatInspection
    target_format_version: int
    planned: bool
    migration_required: bool
    project_id: str | None
    head: ProjectVersionRef | None
    run_ids: tuple[str, ...]
    inventory: tuple[RetainedFormatEntry, ...]
    retained_files: int
    retained_bytes: int
    orphan_paths: tuple[str, ...]
    required_transformations: tuple[str, ...]
    preserved: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectFormatMigration:
    """What one completed forward migration wrote, and what it did not restate.

    ``versions`` is the exact identity map the migration used: one row per
    published version, its legacy snapshot-file digest and the semantic digest
    that names the same state in the target. ``rewritten`` names the few
    project-relative files whose owner restated a base; every other retained
    file was copied byte for byte. ``embedded_legacy_references`` is always
    empty in a receipt that was returned: the migration refuses rather than
    hand back a project whose retained records still name a legacy version, so
    the field is the proof that the scan ran and found nothing rather than a
    list of what was left behind.
    ``unscanned_binaries`` names the opaque files this did not open and
    ``orphan_legacy_references`` the identities inside records the published
    chain does not reach, which are carried over exactly as they are: the
    receipt states the limits of its own account rather than reading its own
    silence as absence.
    """

    source_root: Path
    target_root: Path
    project_id: str
    source_format_version: int
    target_format_version: int
    source_head_sha256: str
    versions: tuple[tuple[int, str, str], ...]
    rewritten: tuple[str, ...]
    preserved_files: int
    orphans: tuple[str, ...]
    orphan_legacy_references: tuple[tuple[str, str, str, int, str], ...]
    unscanned_binaries: tuple[str, ...]
    embedded_legacy_references: tuple[tuple[str, str, str, int, str], ...]
    receipt: ProjectRecordRef | None

    def to_dict(self) -> dict[str, Any]:
        """The retained ``ProjectFormatMigration@1`` payload this becomes."""

        semantic = {version: new for version, _, new in self.versions}
        return {
            "schema": "ProjectFormatMigration@1",
            "project_id": self.project_id,
            "source_format_version": self.source_format_version,
            "target_format_version": self.target_format_version,
            "source_head_sha256": self.source_head_sha256,
            "versions": [
                {"version": version, "legacy_state_sha256": legacy, "state_sha256": new}
                for version, legacy, new in self.versions
            ],
            "rewritten": list(self.rewritten),
            "preserved_files": self.preserved_files,
            "orphans": list(self.orphans),
            "orphan_legacy_references": [
                {"path": path, "pointer": pointer, "shape": shape,
                 "version": version, "legacy_state_sha256": legacy}
                for path, pointer, shape, version, legacy in self.orphan_legacy_references
            ],
            "unscanned_binaries": list(self.unscanned_binaries),
            "embedded_legacy_references": [
                {
                    "path": path,
                    "pointer": pointer,
                    "shape": shape,
                    "version": version,
                    "legacy_state_sha256": legacy,
                    "state_sha256": semantic.get(version),
                }
                for path, pointer, shape, version, legacy in self.embedded_legacy_references
            ],
        }


@dataclass(slots=True)
class _CopiedClosure:
    """What the byte-for-byte pass did with everything the envelope does not own."""

    rewritten: list[str]
    preserved_files: int
    orphans: list[str]
    orphan_legacy: list[tuple[str, str, str, int, str]]
    unscanned_binaries: list[str]
    embedded: list[tuple[str, str, str, int, str]]

_LOCK_INDEX_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}

_HEAD_LOCK_TIMEOUT_SECONDS = 10.0
_HEAD_LOCK_RETRY_SECONDS = 0.05

_HEAD_SHARE_RETRY_ATTEMPTS = 200
_HEAD_SHARE_RETRY_SECONDS = 0.005


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

    @property
    def path(self) -> Path:
        """Where this lock lives. ``__enter__`` creates it if it is absent."""

        return self._path

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
    return _parse_json_document(_read_bytes(path), path.name)


def _parse_json_document(data: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectIntegrityError(f"invalid JSON record: {name}") from exc
    if not isinstance(value, dict):
        raise ProjectIntegrityError(f"JSON record is not an object: {name}")
    return value


def _retry_windows_sharing(operation, *, subject: str):
    """Absorb transient Windows sharing violations around the HEAD swap.

    On Windows, ``os.replace`` onto a file a concurrent reader holds open and
    opening the file while a replace is in flight both surface as
    ``PermissionError``.  A brief bounded retry keeps readers from
    misreporting a healthy project as corrupt and keeps the compare-and-swap
    writer from leaking a bare ``PermissionError``; exhaustion fails with the
    typed ``ProjectHeadLocked``.  POSIX behavior is unchanged: the retry only
    engages on Windows.
    """

    last_error: PermissionError | None = None
    for _ in range(_HEAD_SHARE_RETRY_ATTEMPTS):
        try:
            return operation()
        except PermissionError as exc:
            if os.name != "nt":
                raise
            last_error = exc
            time.sleep(_HEAD_SHARE_RETRY_SECONDS)
    raise ProjectHeadLocked(
        f"concurrent HEAD access kept the project head busy: {subject}"
    ) from last_error


def _read_shared_json(path: Path) -> dict[str, Any]:
    """Read a JSON document that a concurrent ``os.replace`` may be swapping."""

    def read() -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            if os.name == "nt" and isinstance(exc, PermissionError):
                raise
            raise ProjectIntegrityError(
                f"cannot read project record: {path.name}"
            ) from exc

    return _parse_json_document(
        _retry_windows_sharing(read, subject=path.name),
        path.name,
    )


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
        # On Windows a concurrent HEAD reader briefly blocks the replace
        # with a sharing violation; retry within a bound instead of leaking
        # a bare PermissionError from a healthy race.
        _retry_windows_sharing(
            lambda: os.replace(temporary, path),
            subject=path.name,
        )
        # No post-replace fsync: raising after the swap is already visible
        # would break the caller's "exception means no promotion" contract,
        # which matters more than flushing the rename's directory metadata.
    finally:
        temporary.unlink(missing_ok=True)


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


_VERSION_REF_KEYS = frozenset({"project_id", "version", "state_sha256"})
_VERSION_DIGEST_KEYS = frozenset({"version", "state_sha256"})
_HEX_DIGITS = frozenset("0123456789abcdef")

# The three shapes a retained payload uses to name a published project version.
# ``version_ref`` is the exact ``ProjectVersionRef`` mapping; ``version_digest``
# is the two-field form a design branch retains (state.spatial); ``digest_field``
# is a flat string field such as a CAD receipt's ``base_state_sha256``. Every
# reader of retained payloads uses this one walker, so the dry run and the
# migration receipt cannot disagree about what a project still embeds.
VERSION_REF = "version_ref"
VERSION_DIGEST = "version_digest"
DIGEST_FIELD = "digest_field"


@dataclass(frozen=True, slots=True)
class EmbeddedVersionIdentity:
    """One location in a retained payload that names a project version.

    ``detail`` is ``None`` when every field read; otherwise it says what did
    not, and the readable parts are still carried. A location is reported, not
    interpreted: whether it must be restated is its owner's question.
    """

    json_pointer: str
    shape: str
    project_id: str | None
    version: int | None
    state_sha256: str | None
    detail: str | None


def _digest_or_none(value: object) -> str | None:
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value.lower())
    ):
        return value
    return None


def _version_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strings_in(payload: object, pointer: str = "") -> list[tuple[str, str]]:
    """(pointer, value) for every string anywhere in a payload."""

    found: list[tuple[str, str]] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(_strings_in(value, f"{pointer}/{escaped}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_strings_in(value, f"{pointer}/{index}"))
    elif isinstance(payload, str):
        found.append((pointer or "/", payload))
    return found


def _identity_row(
    payload: Mapping[str, Any], pointer: str, shape: str,
) -> EmbeddedVersionIdentity:
    raw_project = payload.get("project_id") if shape == VERSION_REF else None
    project_id = raw_project if isinstance(raw_project, str) and raw_project else None
    version = _version_or_none(payload.get("version"))
    digest = _digest_or_none(payload.get("state_sha256"))
    problems: list[str] = []
    if shape == VERSION_REF and project_id is None:
        problems.append("project_id is not a non-empty string")
    if version is None:
        problems.append("version is not a non-negative integer")
    if digest is None:
        problems.append("state_sha256 is not a 64-character lowercase sha-256")
    return EmbeddedVersionIdentity(
        json_pointer=pointer or "/",
        shape=shape,
        project_id=project_id,
        version=version,
        state_sha256=digest,
        detail="; ".join(problems) or None,
    )


def embedded_version_identities(
    payload: object, pointer: str = "",
) -> list[EmbeddedVersionIdentity]:
    """Every place ``payload`` names a published project version, in order.

    Matched mappings are not descended, so a reference's own fields are never
    reported twice. A string field whose name contains ``state_sha256`` is
    reported as a flat digest: CAD receipts and stage envelopes retain a base
    that way, and a scan that only knew the mapping shape would call a project
    fully surveyed while those digests sat unlisted.
    """

    found: list[EmbeddedVersionIdentity] = []
    if isinstance(payload, Mapping):
        keys = set(payload)
        if keys == _VERSION_REF_KEYS:
            return [_identity_row(payload, pointer, VERSION_REF)]
        if keys == _VERSION_DIGEST_KEYS:
            return [_identity_row(payload, pointer, VERSION_DIGEST)]
        for key, value in payload.items():
            name = str(key)
            escaped = name.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            if "state_sha256" in name and isinstance(value, str):
                found.append(EmbeddedVersionIdentity(
                    json_pointer=child,
                    shape=DIGEST_FIELD,
                    project_id=None,
                    version=_version_or_none(payload.get("base_version"))
                    if name == "base_state_sha256" else None,
                    state_sha256=_digest_or_none(value),
                    detail=None if _digest_or_none(value)
                    else "state_sha256 is not a 64-character lowercase sha-256",
                ))
                continue
            found.extend(embedded_version_identities(value, child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(embedded_version_identities(value, f"{pointer}/{index}"))
    return found


def legacy_version_identities(
    payload: object, legacy_digests: Mapping[str, int],
) -> list[EmbeddedVersionIdentity]:
    """Every place ``payload`` names a known legacy version, in any spelling at all.

    The typed shapes come first, and then every remaining string in the payload
    that *contains* one of these digests, case-insensitively, wherever it sits
    and whatever else that string holds. A CAD document's user
    strings keep the canonical base as the value of a ``{key, value}`` row - no
    field is named ``state_sha256`` anywhere near it - and a migration that
    only knew the named shapes would copy that row forward unchanged and call
    the project migrated. ``legacy_digests`` maps a legacy snapshot-file digest
    to its version; a row is selected by its digest alone, so a reference whose
    declared version disagrees with its digest is still reported.
    """

    found = [
        row for row in embedded_version_identities(payload)
        if row.state_sha256 is not None and row.state_sha256 in legacy_digests
    ]
    named = {row.json_pointer for row in found}

    def inside_a_named_location(pointer: str) -> bool:
        # The digest inside a reference this already named is that reference,
        # not a second location: reporting both would refuse every project.
        return any(
            pointer == other or pointer.startswith(f"{other}/") for other in named
        )

    for pointer, value in _strings_in(payload):
        if inside_a_named_location(pointer) or len(value) < 64:
            continue
        lowered = value.lower()
        for digest, version in legacy_digests.items():
            # Not only a field that *is* the digest: a retained string may wrap
            # it - ``record:<sha>``, ``artifact:sha256:<sha>``, a snapshot file
            # name, a ``project://`` URI - and a CAD document may have
            # upper-cased it. Each of those still names that version.
            if digest.lower() in lowered:
                found.append(EmbeddedVersionIdentity(
                    json_pointer=pointer, shape=DIGEST_FIELD, project_id=None,
                    version=version, state_sha256=digest, detail=None,
                ))
                break
    return found


# The run the migration files its own receipt in, and the event keys a
# format-2 event states for itself: everything else an older event carried is
# carried through rather than dropped.
MIGRATION_RUN_ID = "format-migration"
# The planner groups undeclared identities by kind so a project carrying many
# reads as a list of contracts to declare; the migration repeats the same
# detection per record, because the person adding a declaration needs the
# record and the pointer. This sentence is how one recognises the other's.
UNDECLARED_IDENTITY_BLOCKER = "migration needs a handler"
# The run manifest is this module's own retained record, and it names the
# canonical version the run is based on in one place.
VERSION_REF_POINTERS = {"ProjectRun@1": ("/base",)}
_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("ProjectRun@1", ())
_MIGRATED_EVENT_KEYS = frozenset({
    "schema", "project_id", "event_type", "decision", "run_id",
    "from", "from_snapshot", "to", "to_snapshot", "previous_event",
    "decision_receipt",
})
# ``_write_immutable``/``_replace_atomic`` leave these behind if a writer dies.
_TEMPORARY_NAME = re.compile(r"^\..+\.[0-9a-f]{32}\.tmp$")


def _migration_source(root: Path) -> Path:
    """The source project directory, or a typed refusal naming it."""

    try:
        return Path(root).resolve(strict=True)
    except OSError as exc:
        raise ProjectIntegrityError(
            f"MIGRATION_NOT_APPLICABLE: the source directory cannot be read: {exc}"
        ) from exc


def _contains_path(outer: Path, inner: Path) -> bool:
    """Whether ``inner`` is ``outer`` or lies inside it, comparably on Windows."""

    def parts(path: Path) -> list[str]:
        text = str(path)
        for prefix in ("\\\\?\\UNC\\", "\\\\?\\"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return [os.path.normcase(part) for part in PurePath(text).parts]

    outer_parts, inner_parts = parts(outer), parts(inner)
    return inner_parts[: len(outer_parts)] == outer_parts

_RECORD_REF_KEYS = frozenset({"relative_path", "sha256", "media_type"})
_RECORD_AREAS = ("records", "reviews", "candidates", "branches")
_CASCADE_PASSES = 64


@dataclass(slots=True)
class _RecordCascade:
    """Retained records restated to a fixed point, and what moved with them."""

    payloads: dict[str, dict[str, Any]]
    renames: dict[str, str]
    moved_digests: dict[str, str]
    restated: list[str]
    blockers: list[str]


def _load_version_ref_owners() -> None:
    """Load every owner's declaration explicitly.

    A declaration registers when its owner module is imported, so a reader that
    trusted an incidental import would see a partial table and refuse a project
    for a contract this build does state. Imported here rather than at module
    scope because one of the owners is this module's own caller.
    """

    import archflow.project.version_ref_owners  # noqa: F401


def undeclared_version_identities(
    payload: object, legacy_digests: Mapping[str, int],
) -> list[EmbeddedVersionIdentity]:
    """Every legacy identity in ``payload`` that no owner declares how to restate.

    The one rule. ``plan_project_migration`` and the migration both ask this,
    so the dry run can never refuse what the migration would accept, nor the
    other way round.
    """

    covered = _covered_pointers(payload)
    return [
        row for row in legacy_version_identities(payload, legacy_digests)
        if row.json_pointer not in covered
    ]


def _is_record_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if len(parts) < 4 or parts[0] != "runs" or parts[2] not in _RECORD_AREAS:
        return False
    try:
        parse_record_file_name(parts[-1])
    except ValueError:
        return False
    return True


def _rewrite_references(
    payload: Any,
    project_id: str,
    renames: Mapping[str, str],
    digests: Mapping[str, str],
) -> Any:
    """Move every reference that names a record which moved.

    A record is named three ways: as the exact ``ProjectRecordRef`` mapping, as
    a ``project://`` URI, and - where its owner declares one - as a content
    digest. All three move together or the migrated project names a file that
    is not there.
    """

    if isinstance(payload, Mapping):
        if _RECORD_REF_KEYS <= set(payload) <= (_RECORD_REF_KEYS | {"project_id"}):
            relative = payload.get("relative_path")
            if isinstance(relative, str) and relative in renames:
                moved = renames[relative]
                return {
                    **{
                        key: _rewrite_references(value, project_id, renames, digests)
                        for key, value in payload.items()
                    },
                    "relative_path": moved,
                    "sha256": parse_record_file_name(PurePosixPath(moved).name)[1],
                }
        return {
            key: _rewrite_references(value, project_id, renames, digests)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_rewrite_references(item, project_id, renames, digests) for item in payload]
    if isinstance(payload, str):
        if payload in digests:
            return digests[payload]
        prefix = f"project://{project_id}/"
        if payload.startswith(prefix):
            relative = payload[len(prefix):]
            if relative in renames:
                return prefix + renames[relative]
    return payload


def _canonical_snapshot_moves(
    lineage: list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]],
    semantic: list[str],
    project_id: str,
) -> dict[str, str]:
    """Where each published snapshot file is going, before any of it is written.

    A retained record may cite the canonical snapshot by path - as a record
    reference or a ``project://`` URI - and that file does not survive a format
    change: its schema, its contents and therefore its name all move. The new
    names follow from the states, which are already validated by this point, so
    a record citing one moves in the same pass as everything else. If this ever
    disagreed with what the writers produce, the migration would name a file
    that is not there and refuse.
    """

    moves: dict[str, str] = {}
    previous: ProjectVersionRef | None = None
    for (legacy_ref, _, state), digest in zip(lineage, semantic):
        version = legacy_ref.version
        payload = {
            "schema": "CanonicalSnapshot@2",
            "project_id": project_id,
            "version": version,
            "state_sha256": digest,
            "parent": None if previous is None else previous.to_dict(),
            "state": state,
        }
        kind = f"state-v{version:06d}"
        moves[f"canonical/{record_file_name(kind, legacy_ref.require_digest())}"] = (
            f"canonical/{record_file_name(kind, _sha256(_json_bytes(payload)))}"
        )
        previous = ProjectVersionRef(project_id, version, digest)
    return moves


def _cascade_records(
    payloads: Mapping[str, dict[str, Any]],
    *,
    project_id: str,
    mapping: Mapping[str, str],
    legacy_digests: Mapping[str, int],
    moved: Mapping[str, str] | None = None,
) -> _RecordCascade:
    """Restate every declared version identity and follow the rename to a fixed point.

    Restating a content-addressed record changes its bytes, so its file name
    changes and everything naming it has to move too; moving those changes
    them in turn. The record graph is acyclic because a record can only name
    one that already existed, so repeating the pass converges - but it is
    capped, and a project that does not settle is refused rather than written.

    A record carrying a known legacy identity at a location no owner declares
    is a blocker: this build cannot say what restating it would mean.
    """

    current = {path: dict(payload) for path, payload in payloads.items()}
    blockers: list[str] = []
    for path in sorted(current):
        payload = current[path]
        schema = payload.get("schema")
        for row in undeclared_version_identities(payload, legacy_digests):
            blockers.append(
                f"record {path} ({schema if isinstance(schema, str) else 'undeclared schema'}) "
                f"carries a project-version identity at {row.json_pointer} that no owner "
                f"in this build restates; {UNDECLARED_IDENTITY_BLOCKER}"
            )
    if blockers:
        return _RecordCascade(current, {}, {}, [], blockers)

    # The envelope's own files are not records, but a record may cite one and
    # they move too: seeding the rename map with them lets a single pass move
    # every citation of anything at all.
    renames: dict[str, str] = dict(moved or {})
    # A record whose owner states a content digest is cited by that digest
    # elsewhere; restating the record moves the digest, and every citation of
    # it has to move too or the migrated project cites a value nothing has.
    digests: dict[str, str] = {}
    original_digests = {
        path: _content_digest_of(payload) for path, payload in current.items()
    }
    restated: set[str] = set()
    for _ in range(_CASCADE_PASSES):
        changed = False
        for path in sorted(current):
            # Always transform the payload as it arrived. The rename map is
            # keyed by the name a record came in under, so a record that moves
            # twice must be resolved from that name both times; rewriting the
            # already-rewritten copy would leave every citation one step behind.
            # Rewriting the references changes the record's contents too, so
            # the owners get a second pass to rebuild whatever they derive from
            # those contents; an empty mapping restates nothing and rebuilds
            # everything.
            updated = _restate_declared(
                _rewrite_references(
                    _restate_declared(payloads[path], mapping),
                    project_id, renames, digests,
                ),
                {},
            )
            if updated == current[path]:
                continue
            # ``current`` stays keyed by the path the record came from, so
            # one entry per record records where it has moved to so far.
            changed = True
            current[path] = updated
            restated.add(path)
            previous_digest = original_digests.get(path)
            if previous_digest is not None:
                moved_digest = _content_digest_of(updated)
                if moved_digest is not None and moved_digest != previous_digest:
                    digests[previous_digest] = moved_digest
            if _is_record_path(path):
                kind, previous_sha = parse_record_file_name(PurePosixPath(path).name)
                moved_sha = _sha256(_json_bytes(updated))
                renames[path] = (
                    PurePosixPath(path).parent / record_file_name(kind, moved_sha)
                ).as_posix()
                # A record is also cited by its file digest alone - a stage
                # envelope states the digest of the record it executes. That
                # digest moves with the record like its name does.
                if moved_sha != previous_sha:
                    digests[previous_sha] = moved_sha
        if not changed:
            return _RecordCascade(
                {renames.get(path, path): payload for path, payload in current.items()},
                # The seeded envelope moves are not this pass's to report.
                {path: name for path, name in renames.items() if path in current},
                dict(digests),
                sorted(restated),
                [],
            )
    return _RecordCascade(
        current, renames, dict(digests), sorted(restated),
        [
            f"restating this project's records did not settle within "
            f"{_CASCADE_PASSES} passes; its retained references may name each other "
            f"in a cycle this build cannot resolve"
        ],
    )


def _copy_retained_closure(
    staging: Path,
    target_root: Path,
    legacy: FilesystemProjectRepository,
    mapping: Mapping[str, str],
    legacy_digests: Mapping[str, int],
    cascade: _RecordCascade,
) -> _CopiedClosure:
    """Write everything the envelope does not own into the target.

    The cascade has already restated every declared version identity and moved
    every reference that names a record which changed; this writes the result,
    copies the opaque bytes unchanged, and records what it could not survey.
    Retained ``canonical``/``events`` records the published chain does not
    reach - what a promotion interrupted before its HEAD swap leaves behind -
    are content-addressed, so they are carried over and named in the receipt
    rather than silently dropped.
    """

    report = _CopiedClosure([], 0, [], [], [], [])
    orphans = set(legacy.verify().orphan_paths)
    locks = {path.relative_to(staging).as_posix() for path in legacy.lock_paths()}
    written = {
        path: _json_bytes(payload) for path, payload in cascade.payloads.items()
    }
    moved = set(cascade.renames)
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        if relative in ("project.json", "HEAD") or relative in locks:
            continue
        if _TEMPORARY_NAME.match(PurePosixPath(relative).name):
            continue
        data = _read_bytes(path)
        category, _, _ = _retained_category(relative)
        if relative.startswith(("canonical/", "events/")):
            if relative in orphans:
                # Content-addressed and unreachable, so it is carried over as
                # it is - but it is still read, because the snapshot an
                # interrupted promotion left names the version it was reaching.
                for row in legacy_version_identities(
                    _parse_json_document(data, relative), legacy_digests,
                ):
                    report.orphan_legacy.append((
                        relative, row.json_pointer, row.shape,
                        legacy_digests[row.state_sha256], row.state_sha256,
                    ))
                _write_immutable(target_root / relative, data)
                report.orphans.append(relative)
            continue
        if category == "artifact":
            # Digest-identified bytes are opaque. A legacy digest written
            # inside one is neither read nor rewritten; the file is named so
            # the receipt does not read its silence as absence.
            if _legacy_digest_in_bytes(data, legacy_digests):
                report.unscanned_binaries.append(relative)
            _write_immutable(target_root / relative, data)
            report.preserved_files += 1
            continue
        destination = cascade.renames.get(relative, relative)
        payload = cascade.payloads.get(destination)
        if payload is None:
            # Not a document the cascade read: its decodability was already
            # settled by the plan's own read of this closure.
            _write_immutable(target_root / relative, data)
            report.preserved_files += 1
            continue
        for row in legacy_version_identities(payload, legacy_digests):
            report.embedded.append((
                destination, row.json_pointer, row.shape,
                legacy_digests[row.state_sha256], row.state_sha256,
            ))
        _write_immutable(target_root / destination, written[destination])
        if written[destination] == data and relative == destination:
            report.preserved_files += 1
        else:
            report.rewritten.append(
                destination if relative not in moved else f"{relative} -> {destination}"
            )
    return report


def _require_consistent_migration(
    migrated: FilesystemProjectRepository,
    closure: Mapping[str, Any],
    mapping: Mapping[str, str],
    moved_digests: Mapping[str, str],
) -> None:
    """Every retained record reads, resolves and still digests to what it says.

    ``verify()`` proves the published chain and the design history; this proves
    the rest of the closure: that no record names a legacy version, that every
    record reference resolves to a file whose bytes match, and that a record
    whose owner derives a digest from its own contents still agrees with that
    owner after the restatement.
    """

    # Every version the migration replaced. A migrated record naming one of
    # these anywhere - in a typed reference or as the value of a CAD user
    # string - is a location the cascade did not reach.
    legacy = {digest: 0 for digest in mapping}
    # What each record says its own content digest is, and every 64-character
    # value the closure holds: a citation of a record that moved has to have
    # moved with it, or the next stage to check its binding is where it shows.
    owned: dict[str, str] = {}
    cited: dict[str, set[str]] = {}
    documents: dict[str, Mapping[str, Any]] = {}
    for entry in closure["files"]:
        path = entry["path"]
        if _retained_category(path)[0] == "artifact":
            continue
        payload = _parse_json_document(
            migrated.read_transfer_file(path, entry["sha256"]), path,
        )
        for row in legacy_version_identities(payload, legacy):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path}{row.json_pointer} still names a legacy version"
            )
        for reference in _record_references_in(payload, migrated.layout.project_id):
            resolved = migrated.layout.root / reference
            if not resolved.is_file():
                raise ProjectIntegrityError(
                    f"MIGRATION_VERIFY: {path} names {reference}, which is not there"
                )
            try:
                _, named = parse_record_file_name(PurePosixPath(reference).name)
            except ValueError:
                continue
            if _sha256(_read_bytes(resolved)) != named:
                raise ProjectIntegrityError(
                    f"MIGRATION_VERIFY: {reference} does not hold the bytes its name claims"
                )
        if _retained_category(path)[0] != "envelope":
            # The envelope was written by this build's own writers rather than
            # cascaded, so there is no earlier derivation of it to go stale.
            _require_self_derived_fields_agree(path, payload)
            declared = _content_digest_of(payload)
            if declared is not None:
                owned[path] = declared
        for _, value in _strings_in(payload):
            if len(value) == 64:
                cited.setdefault(value.lower(), set()).add(path)
        documents[path] = payload
    _require_citations_resolve(owned, cited, moved_digests)
    _require_cross_record_rules(migrated.layout.project_id, documents)


def _require_cross_record_rules(
    project_id: str, documents: Mapping[str, Mapping[str, Any]],
) -> None:
    """Let each owner check what it says about the records it names.

    Nothing inside one record can tell whether the digest it cites for another
    is still right. The owner is handed a resolver over the migrated closure
    and applies its own rule - the same rule the next stage will apply, and
    computed independently of whatever this record's digest registration says.
    """

    prefix = f"project://{project_id}/"

    def resolve(reference: object) -> Mapping[str, Any] | None:
        if isinstance(reference, Mapping):
            reference = reference.get("relative_path")
        if not isinstance(reference, str):
            return None
        if reference.startswith(prefix):
            reference = reference[len(prefix):]
        return documents.get(reference)

    for path, payload in sorted(documents.items()):
        try:
            _closure_check(payload, resolve)
        except ProjectRepositoryError:
            raise
        except Exception as exc:
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} breaks a rule its own owner states "
                f"about the records it names: {exc}"
            ) from exc


def _require_citations_resolve(
    owned: Mapping[str, str],
    cited: Mapping[str, set[str]],
    moved_digests: Mapping[str, str],
) -> None:
    """No citation still names a record by a digest that record no longer has.

    A record restated by the cascade digests differently afterwards, and the
    cascade moves every citation of the old value with it. Anything that kept
    the old value is pointing at a version of that record which does not
    exist - ``require_stage_exit_binding`` would call it stale or
    cross-scoped, long after the migration reported success - and anything a
    record claims about itself has to be what the closure cites.
    """

    for old, new in sorted(moved_digests.items()):
        for path in sorted(cited.get(old.lower(), ())):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} still cites {old[:12]}, the digest a "
                f"record had before this migration; it is now {new[:12]}"
            )
    current = {digest.lower() for digest in owned.values()}
    for path, digest in sorted(owned.items()):
        if digest.lower() not in current:  # pragma: no cover - defensive
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} does not digest to what it claims"
            )


def _require_self_derived_fields_agree(path: str, payload: Mapping[str, Any]) -> None:
    """A record still says about itself what its owner says about it.

    A record that serialises a digest of its own contents is invalidated by
    any restatement inside it. Its owner declares those fields and how to
    rebuild them; here the rebuild is run and compared. A kind whose schema
    never declared what it derives is refused rather than trusted: that is the
    state in which a migration writes a record its own reader will reject.
    """

    schema = payload.get("schema")
    if not isinstance(schema, str):
        return
    fields = _derived_fields(schema)
    if fields is None:
        # A record that only names files by their digest derives nothing from a
        # canonical base; the question is whether it carries a version identity.
        if _declared_locations(payload):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} is a {schema} carrying a project-version "
                f"identity whose owner never said what it derives from one"
            )
        return
    # The owner's reader first, and for every record whose owner lends one: a
    # record with nothing derived can still be left inconsistent by a moved
    # reference, and the reader is what every later stage will use.
    try:
        _read_back(payload)
    except ProjectRepositoryError:
        raise
    except Exception as exc:
        raise ProjectIntegrityError(
            f"MIGRATION_VERIFY: {path} is a {schema} its own reader rejects "
            f"after migration: {exc}"
        ) from exc
    if not fields:
        return
    # The rebuild cannot vouch for itself - comparing it with itself proves
    # only that it is a function - but it does say which fields to look at.
    rebuilt = _recompute_derived(payload)
    for field in fields:
        if payload.get(field) != rebuilt.get(field):
            raise ProjectIntegrityError(
                f"MIGRATION_VERIFY: {path} keeps a {field!r} its own owner no longer "
                f"derives from its contents"
            )




def _record_references_in(payload: object, project_id: str) -> list[str]:
    """Every project-relative path this payload names as a retained record."""

    found: list[str] = []
    if isinstance(payload, Mapping):
        if _RECORD_REF_KEYS <= set(payload) <= (_RECORD_REF_KEYS | {"project_id"}):
            relative = payload.get("relative_path")
            if isinstance(relative, str) and relative.startswith("runs/"):
                found.append(relative)
        for value in payload.values():
            found.extend(_record_references_in(value, project_id))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_record_references_in(value, project_id))
    elif isinstance(payload, str):
        prefix = f"project://{project_id}/"
        if payload.startswith(prefix) and payload[len(prefix):].startswith("runs/"):
            found.append(payload[len(prefix):])
    return found


def _legacy_digest_in_bytes(data: bytes, legacy_digests: Mapping[str, int]) -> bool:
    """Whether opaque bytes contain a known legacy digest as plain ASCII."""

    return any(digest.encode("ascii") in data for digest in legacy_digests)


class FilesystemProjectRepository:
    """Content-addressed records, issued HEAD and atomic design positions."""

    def __init__(self, layout: ProjectLayout, manifest: ProjectManifest) -> None:
        self.layout = layout
        self._manifest = manifest
        self._lock = _project_lock(layout.root)
        self._head_lock = _HeadFileLock(layout.root / "HEAD.lock")
        self._design_lock = _HeadFileLock(layout.design_branches.with_suffix(".lock"))

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        project_id: str,
        initial_state: Mapping[str, Any],
        authored_record: Mapping[str, Any] | None = None,
        seat_pack: Mapping[str, Any] | None = None,
    ) -> FilesystemProjectRepository:
        """Create a project, optionally installing its caller-authored WIP inputs.

        Input meaning belongs to the caller. These files are installed only at
        creation; they create no run and are not published design content.
        """

        layout = ProjectLayout(Path(root), project_id)
        manifest = ProjectManifest(
            project_id=project_id,
            format_version=CURRENT_FORMAT_VERSION,
        )
        state = dict(initial_state)
        _require_semantic_state_identity(
            state, project_id=project_id, version=0, field="initial state"
        )
        state_sha256 = _semantic_state_sha256(state, field="initial state")
        authored_files = tuple(
            (path, _json_bytes(dict(payload)))
            for path, payload in (
                (layout.authored_record, authored_record),
                (layout.seat_pack, seat_pack),
            )
            if payload is not None
        )
        # The existence check and the HEAD write must sit inside the same
        # OS-level lock compare_and_swap uses, or a stalled duplicate
        # initialize from another process can reset a promoted HEAD to v0.
        head_lock = _HeadFileLock(layout.root / "HEAD.lock")
        with _project_lock(layout.root), head_lock:
            if layout.manifest.exists() or layout.head.exists():
                raise ProjectAlreadyExists(f"project already exists: {project_id}")
            for path, _ in authored_files:
                if path.exists():
                    raise ProjectAlreadyExists(f"authored input already exists: {path}")
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
                    "to": head_ref.to_dict(),
                    "to_snapshot": _record_dict(snapshot),
                    "previous_event": None,
                    "decision_receipt": None,
                },
            )
            for path, data in authored_files:
                _write_immutable(path, data)
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

    def initialize_authored_inputs(
        self,
        *,
        expected_head: ProjectVersionRef,
        expected_record: Mapping[str, Any] | None,
        authored_record: Mapping[str, Any],
        seat_pack: Mapping[str, Any],
    ) -> bool:
        """Install caller-prepared initial WIP without replacing other inputs.

        The caller decides whether the record is empty and what modeling means.
        Compare the exact previous mapping and HEAD under the project lock. Seats
        are installed first; an interrupted call can retry with the same payload
        before the actionable authored record becomes visible. No run or issue.
        """

        with self._lock, self._head_lock:
            if self.read_head() != expected_head or expected_head.version != 0:
                raise StaleProjectHead("initial authored inputs require the unchanged initial HEAD")
            path = self.layout.authored_record
            current = _read_json(path) if path.exists() else None
            desired = dict(authored_record)
            if current != expected_record and current != desired:
                raise ProjectAlreadyExists("authored input changed before initialization")
            seats = self.layout.seat_pack
            if seats.exists() and _read_json(seats) != dict(seat_pack):
                raise ProjectAlreadyExists("project already declares different modeling seats")
            if current == desired and seats.exists():
                return False
            if not seats.exists():
                _write_immutable(seats, _json_bytes(dict(seat_pack)))
            _replace_atomic(path, _json_bytes(desired))
            return True

    def load_manifest(self) -> ProjectManifest:
        current = ProjectManifest.from_dict(_read_json(self.layout.manifest))
        if current != self._manifest:
            raise ProjectIntegrityError("immutable project manifest changed")
        return current

    def read_head(self) -> ProjectVersionRef:
        # The version this project publishes right now. ``read_head`` keeps
        # its name after ADR-007 because it reads the file called ``HEAD``
        # and the format owns that name; every caller that shows the answer
        # to a person calls it the published design, or issue N.
        return self._read_head_document()[0]

    def load_current_state(self) -> dict[str, Any]:
        _, snapshot_ref, _ = self._read_head_document()
        snapshot = self.load_json(snapshot_ref)
        state = snapshot.get("state")
        if not isinstance(state, dict):
            raise ProjectIntegrityError("canonical snapshot state is not an object")
        return state

    def load_version_state(self, version: ProjectVersionRef) -> dict[str, Any]:
        """Read an exact published ancestor, following retained event references."""
        self._require_project_version(version, durable=True)
        current, snapshot_ref, event_ref = self._read_head_document()
        while current != version:
            if current.version <= version.version:
                raise ProjectIntegrityError("requested version is not in published history")
            event = self.load_json(event_ref)
            parent = _version_from_dict(event.get("from"), field="event from")
            snapshot_ref = (
                _record_from_dict(event.get("from_snapshot"), project_id=self._manifest.project_id,
                                  field="event from_snapshot")
                if self._manifest.format_version == CURRENT_FORMAT_VERSION
                else self._canonical_ref_for_version(parent)
            )
            event_ref = _record_from_dict(event.get("previous_event"), project_id=self._manifest.project_id,
                                          field="previous_event")
            self._verify_snapshot(snapshot_ref, parent)
            current = parent
        snapshot = self._verify_snapshot(snapshot_ref, version)
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
            "base": run.base.to_dict(),
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

    def create_run_batch(
        self,
        run_ids: tuple[str, ...],
        *,
        base: ProjectVersionRef | None = None,
        require_current_base: bool = False,
    ) -> tuple[RunRef, ...]:
        """Create an exact run set or leave no member of the set behind.

        This is intentionally narrower than a general record transaction: it
        atomically guards fixed sibling-run bootstrap under the repository's
        process and HEAD locks, and rolls back only roots proven absent before
        this call if filesystem creation fails.
        """

        if not isinstance(run_ids, tuple) or not run_ids:
            raise TypeError("run_ids must be a non-empty tuple")
        for run_id in run_ids:
            require_identifier(run_id, "run_id")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_ids must be unique")
        if not isinstance(require_current_base, bool):
            raise TypeError("require_current_base must be bool")
        chosen_base = base or self.read_head()
        self._require_project_version(chosen_base, durable=True)
        runs = tuple(
            RunRef(self._manifest.project_id, run_id, chosen_base)
            for run_id in run_ids
        )
        layouts = tuple(self.layout.run(run.run_id) for run in runs)
        created_roots: list[Path] = []
        with self._lock, self._head_lock:
            if require_current_base:
                current, _, _ = self._read_head_document()
                if current != chosen_base:
                    raise StaleProjectHead(
                        "fixed run batch no longer shares the current canonical base"
                    )
            existing = [
                layout.root
                for layout in layouts
                if layout.root.exists() or layout.manifest.exists()
            ]
            if existing:
                raise ProjectAlreadyExists(
                    "run batch target already exists: "
                    + ", ".join(str(path) for path in existing)
                )
            try:
                for run, run_layout in zip(runs, layouts, strict=True):
                    resolved_root = run_layout.root.resolve(strict=False)
                    resolved_runs = self.layout.runs.resolve(strict=False)
                    if (
                        not resolved_root.is_relative_to(resolved_runs)
                        or resolved_root.parent != resolved_runs
                    ):
                        raise ProjectIntegrityError(
                            "run batch target escaped the assigned runs root"
                        )
                    created_roots.append(resolved_root)
                    payload = {
                        "schema": "ProjectRun@1",
                        "project_id": run.project_id,
                        "run_id": run.run_id,
                        "base": run.base.to_dict(),
                    }
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
            except BaseException as exc:
                cleanup_failures: list[str] = []
                for created_root in reversed(created_roots):
                    try:
                        if created_root.exists():
                            shutil.rmtree(created_root)
                    except OSError as cleanup_exc:  # pragma: no cover - OS fault
                        cleanup_failures.append(
                            f"{created_root}: {cleanup_exc}"
                        )
                if cleanup_failures:
                    raise ProjectIntegrityError(
                        "run batch failed and rollback was incomplete: "
                        + "; ".join(cleanup_failures)
                    ) from exc
                raise
        return runs

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
        # The kind is the only word a reader has for what a retained file is,
        # so it comes from one table. Reads stay unrestricted: retained runs
        # from the archived lanes carry kinds the spine never writes.
        try:
            require_registered(record_kind)
        except ValueError as exc:
            raise ProjectRepositoryError(str(exc)) from exc
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
        path = directory / record_file_name(record_kind, digest)
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

    def put_workspace_file(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        workspace_relative_path: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef:
        """Install one immutable binary below an assigned run workspace."""

        self._validate_run(run)
        if destination.area is not PersistenceArea.RUN_WORKSPACE:
            raise ValueError(
                "workspace files require the run-workspace destination"
            )
        require_identifier(artifact_id, "artifact_id")
        relative_in_workspace = require_project_relative_path(
            workspace_relative_path
        )
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("media_type must be non-empty text")
        data = source.read()
        if not isinstance(data, bytes):
            raise TypeError("workspace source must be opened in binary mode")

        workspace = self._destination_directory(run, destination).resolve(
            strict=False
        )
        portable = PurePosixPath(relative_in_workspace)
        path = (workspace / Path(*portable.parts)).resolve(strict=False)
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("workspace path escapes the assigned run") from exc
        digest = _sha256(data)
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
        return _parse_json_document(data, path.name)

    def _require_design_stage(self, ref: ProjectRecordRef) -> dict[str, Any]:
        self._require_record(ref)
        parts = PurePosixPath(ref.relative_path).parts
        if len(parts) != 4 or parts[0] != "runs" or parts[2] != "reviews" or ref.record_kind != DESIGN_STAGE:
            raise ProjectIntegrityError("design branch must reference a retained design stage review")
        self.load_run(parts[1])
        return self.load_json(ref)

    def _design_branch_payload(self, branch_id: str, value: object) -> dict[str, Any]:
        require_identifier(branch_id, "branch_id")
        if not isinstance(value, Mapping) or set(value) != {"branch_id", "parent_branch", "fork_stage", "head_stage"}:
            raise ProjectIntegrityError("design branch reference schema drifted")
        if value["branch_id"] != branch_id:
            raise ProjectIntegrityError("design branch id disagrees with its key")
        parent = value["parent_branch"]
        if parent is not None:
            require_identifier(parent, "parent_branch")
            if parent == branch_id:
                raise ProjectIntegrityError("design branch cannot fork from itself")
        result: dict[str, Any] = {"branch_id": branch_id, "parent_branch": parent}
        for field in ("fork_stage", "head_stage"):
            ref = ProjectRecordRef.from_dict(value[field], field)
            self._require_design_stage(ref)
            result[field] = ref.to_dict()
        return result

    def read_design_branches(self) -> dict[str, dict[str, Any]]:
        """Read verified design references, including projects predating them."""
        if not self.layout.design_branches.exists():
            return {}
        payload = _read_shared_json(self.layout.design_branches)
        if set(payload) != {"schema", "project_id", "branches"} or payload["schema"] != "DesignBranches@1" or payload["project_id"] != self._manifest.project_id:
            raise ProjectIntegrityError("design branches belong to another project or schema")
        if not isinstance(payload["branches"], Mapping):
            raise ProjectIntegrityError("design branches must be a mapping")
        return {branch_id: self._design_branch_payload(branch_id, value) for branch_id, value in payload["branches"].items()}

    def compare_and_swap_design_branch(
        self,
        *,
        branch_id: str,
        expected_head: ProjectRecordRef | None,
        branch: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish a retained design position without moving canonical HEAD.

        The application owns acceptance and lineage rules. The repository
        verifies durable references and serializes creation or head movement.
        """
        replacement = self._design_branch_payload(branch_id, branch)
        if expected_head is not None:
            self._require_design_stage(expected_head)
        with self._lock, self._design_lock:
            branches = self.read_design_branches()
            previous = branches.get(branch_id)
            actual = None if previous is None else ProjectRecordRef.from_dict(previous["head_stage"])
            if actual != expected_head:
                raise StaleDesignBranch(f"design branch {branch_id!r} changed; reload its current stage")
            if previous is not None and any(previous[key] != replacement[key] for key in ("parent_branch", "fork_stage")):
                raise ProjectIntegrityError("an existing design branch's fork identity is immutable")
            branches[branch_id] = replacement
            _replace_atomic(self.layout.design_branches, _json_bytes({
                "schema": "DesignBranches@1", "project_id": self._manifest.project_id,
                "branches": branches,
            }))
        return replacement

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
        # ``prepare_transition``, ``compare_and_swap`` and
        # ``PromotionDecision@1`` keep their names: the decision receipt's key
        # set is compared literally below and retained receipts were written
        # against it (ADR-004). The act these two perform is called an
        # *issue* (ADR-007), and ``archflow.project.issue`` is its one caller.
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
                    "parent": expected.to_dict(),
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
                "from": expected.to_dict(),
                "to": replacement.to_dict(),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": _record_dict(decision_receipt),
            }
        else:
            return self._write_transition(
                previous=expected,
                previous_snapshot=current_snapshot,
                previous_event=previous_event,
                state=replacement_payload,
                event_type="candidate.promoted",
                decision="accepted",
                run_id=run.run_id,
                decision_receipt=_record_dict(decision_receipt),
            )
        event = self._put_internal_json(
            self.layout.events,
            f"event-v{next_version:06d}",
            event_payload,
        )
        return PreparedTransition(expected, event, snapshot)

    def _write_transition(
        self,
        *,
        previous: ProjectVersionRef,
        previous_snapshot: ProjectRecordRef,
        previous_event: ProjectRecordRef,
        state: Mapping[str, Any],
        event_type: Any,
        decision: Any,
        run_id: Any,
        decision_receipt: Any,
        carried: Mapping[str, Any] | None = None,
    ) -> PreparedTransition:
        """Write one format-2 snapshot and the event that reaches it. HEAD is untouched.

        The only place a ``CanonicalSnapshot@2``/``ProjectEvent@2`` pair is
        composed, so a promotion and a format migration cannot drift into two
        spellings of the same transition. ``carried`` seeds the event with keys
        an older event already held; the keys this format owns are written over
        them, so a foreign field survives a migration instead of being dropped.
        """

        version = previous.version + 1
        payload = dict(state)
        _require_semantic_state_identity(
            payload,
            project_id=self._manifest.project_id,
            version=version,
            field=f"state v{version}",
        )
        state_sha256 = _semantic_state_sha256(payload, field=f"state v{version}")
        snapshot = self._put_internal_json(
            self.layout.canonical,
            f"state-v{version:06d}",
            {
                "schema": "CanonicalSnapshot@2",
                "project_id": self._manifest.project_id,
                "version": version,
                "state_sha256": state_sha256,
                "parent": previous.to_dict(),
                "state": payload,
            },
        )
        replacement = ProjectVersionRef(
            self._manifest.project_id, version, state_sha256,
        )
        event = self._put_internal_json(
            self.layout.events,
            f"event-v{version:06d}",
            {
                **(dict(carried) if carried else {}),
                "schema": "ProjectEvent@2",
                "project_id": self._manifest.project_id,
                "event_type": event_type,
                "decision": decision,
                "run_id": run_id,
                "from": previous.to_dict(),
                "from_snapshot": _record_dict(previous_snapshot),
                "to": replacement.to_dict(),
                "to_snapshot": _record_dict(snapshot),
                "previous_event": _record_dict(previous_event),
                "decision_receipt": decision_receipt,
            },
        )
        return PreparedTransition(previous, event, snapshot)

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

    def lock_paths(self) -> tuple[Path, ...]:
        """The advisory lock files this repository's guarded reads acquire.

        ``export_transfer`` reads the closure under the HEAD and design locks,
        and acquiring one creates its file if it is absent. A caller that must
        not change the project at all can check these first and refuse.
        """

        return (self._head_lock.path, self._design_lock.path)

    def export_transfer(
        self, *, run_id: str | None = None,
        known_files: Mapping[str, str] | None = None,
        include_contents: bool = True,
        include_all_runs: bool = False,
    ) -> dict[str, Any]:
        """Read a retained design snapshot or one candidate and its dependencies.

        This is a transport value, not a new project format. All identities and
        file bytes are the existing P036 ones. Only receipt-named workspace
        artifacts travel; speculative scripts, logs and recovery files do not.
        Archives may include all retained runs, including project documents,
        boards and unaccepted candidates; ordinary synchronization stays scoped.
        """
        if include_all_runs and run_id is not None:
            raise ValueError("all retained runs require a project snapshot")
        with self._lock, self._head_lock, self._design_lock:
            report = self.verify()
            branches = self.read_design_branches() if run_id is None else {}
            files: dict[str, tuple[str, int]] = {}
            contents: dict[str, str] = {}
            known = known_files or {}
            pending: list[tuple[str, str | None]] = []
            runs: set[str] = set()

            def add(path: str, digest: str | None = None) -> None:
                pending.append((_transfer_path(path), digest))

            def add_run(value: str) -> None:
                require_identifier(value, "transfer run_id")
                if value in runs:
                    return
                run = self.load_run(value)
                self.load_version_state(run.base)
                runs.add(value)
                add(f"runs/{value}/run.json")
                for area in ("records", "reviews", "candidates", "branches"):
                    directory = self.layout.run(value).root / area
                    if not directory.exists():
                        continue
                    for path in sorted(directory.rglob("*.json")):
                        try:
                            _, digest = parse_record_file_name(path.name)
                        except ValueError:
                            continue
                        add(path.relative_to(self.layout.root).as_posix(), digest)

            def artifact(path: str, digest: str, current_run: str | None) -> None:
                # Native CAD receipts name a workspace-local export. P036
                # artifact refs instead carry a complete project-relative path.
                if path.startswith(
                    ("runs/", "objects/", "canonical/", "events/", "exports/")
                ):
                    add(path, digest)
                    return
                if current_run is None:
                    raise ProjectIntegrityError("TRANSFER_DEPENDENCY_MISSING: artifact has no run")
                name = PurePosixPath(path.replace("\\", "/")).name
                matches = [p for p in self.layout.run(current_run).workspaces.rglob(name)
                           if p.is_file() and _sha256(_read_bytes(p)) == digest]
                if not matches:
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {current_run}/{name}")
                add(sorted(matches)[0].relative_to(self.layout.root).as_posix(), digest)

            def references(value: Any, current_run: str | None) -> None:
                if isinstance(value, Mapping):
                    # Uploaded source documents name their object by digest.
                    # Generated drawings instead link a receipt via revisionRef;
                    # the recursive project URI traversal retains its artifacts.
                    if value.get("schema") == "StudioSourceDocument@1":
                        if value.get("project_id") != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign document")
                        if value.get("revisionRef") is None:
                            digest = value["asset_sha256"]
                            artifact(f"objects/sha256/{digest[:2]}/{digest}", digest, current_run)
                    if "relative_path" in value and "sha256" in value:
                        if value.get("project_id", self._manifest.project_id) != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign artifact")
                        artifact(value["relative_path"], value["sha256"], current_run)
                    # An authored record can name a run without being bound to
                    # one. A retained RunRef always has a non-null base.
                    if {"run_id", "project_id", "base"}.issubset(value):
                        if value["project_id"] != self._manifest.project_id:
                            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign run")
                        if value["base"] is not None:
                            add_run(value["run_id"])
                    native = value.get("artifact_relative_path")
                    inspection = value.get("inspection")
                    if native and isinstance(inspection, Mapping) and inspection.get("file_sha256"):
                        artifact(native, inspection["file_sha256"], current_run)
                    for key, item in value.items():
                        evidence_list = key == "archflow:evidence" or (
                            key == "value" and value.get("key") == "archflow:evidence"
                        )
                        if evidence_list and isinstance(item, str):
                            # The CAD metadata contract serializes evidence as
                            # a comma-separated list in this specific field.
                            # Every member still takes the strict URI path check.
                            for source in item.split(","):
                                references(source, current_run)
                        else:
                            references(item, current_run)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        references(item, current_run)
                elif isinstance(value, str) and value.startswith("project://"):
                    uri = urlsplit(value)
                    if uri.netloc != self._manifest.project_id or uri.query or uri.fragment:
                        raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: foreign project reference")
                    path = _transfer_path(unquote(uri.path.lstrip("/")))
                    parts = PurePosixPath(path).parts
                    if parts[0] == "runs" and len(parts) >= 2:
                        add_run(parts[1])
                        if len(parts) == 2:
                            return
                    add(path)

            add("project.json")
            add("HEAD")
            for path in report.reachable_paths:
                if path.startswith(("canonical/", "events/")):
                    add(path)
            if run_id is None:
                if self.layout.design_branches.exists():
                    add("design/branches.json")
                references(branches, None)
                for path in ("input/runner/state-record.json", "input/runner/seats.json",
                             "input/runner/program-sheet.json"):
                    if (self.layout.root / path).is_file():
                        add(path)
                if include_all_runs:
                    for manifest in sorted(self.layout.runs.glob("*/run.json")):
                        add_run(manifest.parent.name)
            else:
                add_run(run_id)
            while pending:
                path, expected_digest = pending.pop()
                if path in files:
                    if expected_digest is not None and files[path][0] != expected_digest:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
                    continue
                target = (self.layout.root / path).resolve()
                if not target.is_relative_to(self.layout.root) or not target.is_file():
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
                is_json = path.endswith(".json") or path == "HEAD"
                data = _read_bytes(target) if is_json else None
                if data is None:
                    with target.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    size = target.stat().st_size
                else:
                    digest, size = _sha256(data), len(data)
                if expected_digest is not None and digest != expected_digest:
                    raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
                files[path] = (digest, size)
                if include_contents and known.get(path) != digest:
                    if data is None:
                        data = _read_bytes(target)
                    if _sha256(data) != digest or len(data) != size:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path} changed during export")
                    contents[path] = base64.b64encode(data).decode("ascii")
                parts = PurePosixPath(path).parts
                current_run = parts[1] if parts[0] == "runs" else None
                if current_run is not None:
                    add_run(current_run)
                if is_json:
                    assert data is not None
                    references(_parse_json_document(data, path), current_run)
            return {
                "project_id": self._manifest.project_id,
                "format_version": self._manifest.format_version,
                "mode": "snapshot" if run_id is None else "candidate",
                "root_run_id": run_id, "head": report.head.to_dict(),
                "branches": branches, "run_ids": sorted(runs),
                "files": [{"path": path, "sha256": digest, "size": size}
                          for path, (digest, size) in sorted(files.items())],
                "contents": contents,
            }

    def read_transfer_file(self, path: str, sha256: str) -> bytes:
        """Read one retained file by identity without rescanning other binaries."""
        path = _transfer_path(path)
        target = (self.layout.root / path).resolve()
        if not target.is_relative_to(self.layout.root):
            raise ProjectIntegrityError("TRANSFER_PATH_INVALID: target escapes project root")
        parts = PurePosixPath(path).parts
        metadata = path in ("project.json", "HEAD", "design/branches.json",
                            "input/runner/state-record.json", "input/runner/seats.json",
                            "input/runner/program-sheet.json")
        run_id = parts[1] if parts[0] == "runs" and len(parts) >= 3 else None
        run_manifest = run_id is not None and len(parts) == 3 and parts[2] == "run.json"
        record_area = (parts[0] in ("canonical", "events") and len(parts) == 2) or (
            run_id is not None and (
                (len(parts) == 4 and parts[2] in ("records", "reviews", "candidates"))
                or (len(parts) == 6 and parts[2] == "branches" and parts[4] == "records")
            )
        )
        if run_manifest:
            self.load_run(run_id)
        elif record_area:
            try:
                _, named_digest = parse_record_file_name(parts[-1])
            except ValueError as exc:
                raise ProjectIntegrityError("TRANSFER_FILE_UNAVAILABLE: not a retained record") from exc
            if named_digest != sha256:
                raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: record filename")
        elif not metadata:
            workspace = run_id is not None and len(parts) >= 4 and parts[2] == "workspaces"
            object_file = parts == ("objects", "sha256", sha256[:2], sha256)
            # A project-owned export is served exactly like a run's workspace
            # file: only where a retained record names it, and only by digest.
            # Refusing it here while the closure lists it would break the very
            # archive the migration tells an operator to take first.
            exported = parts[0] == "exports" and len(parts) >= 2
            if not (workspace or object_file or exported) or not self._transfer_artifact_referenced(path, sha256, run_id):
                raise ProjectIntegrityError("TRANSFER_FILE_UNAVAILABLE: no retained artifact reference")
        data = _read_bytes(target)
        if _sha256(data) != sha256:
            raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: shared file changed")
        return data

    def _transfer_artifact_referenced(self, path: str, digest: str, run_id: str | None) -> bool:
        """Find an object's ref, or a retained receipt naming this artifact.

        A native CAD receipt names only a workspace-local export, so that loose
        filename match stays inside the run owning the bytes. A complete
        project-relative reference is already unambiguous and export follows it
        across runs, so one retained run may hold an artifact that only another
        retained run's receipt names, and a reference may point into the
        project's own ``exports`` root. Both forms still require the recorded
        digest, this project, and an already admissible workspace, export or
        object path; the owning run is searched first so the common read stops
        there.
        """
        def matches(value: Any, local: bool) -> bool:
            if isinstance(value, Mapping):
                if (value.get("schema") == "StudioSourceDocument@1"
                        and value.get("revisionRef") is None
                        and value.get("project_id") == self._manifest.project_id
                        and value.get("asset_sha256") == digest
                        and path == f"objects/sha256/{digest[:2]}/{digest}"):
                    return True
                relative = value.get("relative_path")
                if value.get("sha256") == digest and isinstance(relative, str):
                    if relative == path:
                        return value.get("project_id", self._manifest.project_id) == self._manifest.project_id
                    if local and not relative.startswith(("runs/", "objects/")):
                        if PurePosixPath(relative.replace("\\", "/")).name == PurePosixPath(path).name:
                            return True
                inspection = value.get("inspection")
                native = value.get("artifact_relative_path")
                if local and isinstance(inspection, Mapping) and isinstance(native, str):
                    if inspection.get("file_sha256") == digest and PurePosixPath(native.replace("\\", "/")).name == PurePosixPath(path).name:
                        return True
                return any(matches(item, local) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(matches(item, local) for item in value)
            return value == f"project://{self._manifest.project_id}/{path}"

        owner = self.layout.run(run_id).root if run_id else None
        roots = ([owner] if owner is not None else []) + [
            root for root in sorted(self.layout.runs.iterdir()) if root != owner
        ]
        for root in roots:
            for area in ("records", "reviews", "candidates", "branches"):
                if not (root / area).is_dir():
                    continue
                for record in (root / area).rglob("*.json"):
                    try:
                        _, record_digest = parse_record_file_name(record.name)
                    except ValueError:
                        continue
                    data = _read_bytes(record)
                    if _sha256(data) != record_digest:
                        raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {record.name}")
                    if matches(_parse_json_document(data, record.name), root == owner):
                        return True
        return False

    @classmethod
    def _validate_transfer(
        cls, transfer: Mapping[str, Any], *, expected_project_id: str,
        existing_root: Path | None = None,
    ) -> dict[str, bytes]:
        """Verify the complete received closure before any destination write."""
        keys = {"project_id", "format_version", "mode", "root_run_id", "head",
                "branches", "run_ids", "files", "contents"}
        if not isinstance(transfer, Mapping) or set(transfer) != keys:
            raise ProjectIntegrityError("TRANSFER_INVALID: invalid transport fields")
        if transfer["project_id"] != expected_project_id:
            raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: transfer names another project")
        if transfer["mode"] not in ("snapshot", "candidate") or (
            (transfer["root_run_id"] is None) != (transfer["mode"] == "snapshot")
        ):
            raise ProjectIntegrityError("TRANSFER_INVALID: invalid transfer root")
        if not isinstance(transfer["contents"], Mapping) or not isinstance(transfer["files"], list):
            raise ProjectIntegrityError("TRANSFER_INVALID: expected files and contents")
        files: dict[str, bytes] = {}
        for row in transfer["files"]:
            if not isinstance(row, Mapping) or set(row) != {"path", "sha256", "size"}:
                raise ProjectIntegrityError("TRANSFER_INVALID: invalid file manifest")
            path = _transfer_path(row["path"])
            if path in files:
                raise ProjectIntegrityError("TRANSFER_INVALID: duplicate path")
            if path in transfer["contents"]:
                try:
                    data = base64.b64decode(transfer["contents"][path], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ProjectIntegrityError("TRANSFER_INVALID: invalid file bytes") from exc
            elif existing_root is not None:
                target = (existing_root / path).resolve()
                if not target.is_relative_to(existing_root.resolve()) or not target.is_file():
                    raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
                data = _read_bytes(target)
            else:
                raise ProjectIntegrityError(f"TRANSFER_DEPENDENCY_MISSING: {path}")
            if type(row["size"]) is not int or len(data) != row["size"] or _sha256(data) != row["sha256"]:
                raise ProjectIntegrityError(f"TRANSFER_DIGEST_MISMATCH: {path}")
            parts = PurePosixPath(path).parts
            # A run's workspace and the project's exports hold native exports,
            # and some of those are themselves JSON. They are artifacts, not
            # records: the sender only admits one a retained receipt names, and
            # ``read_transfer_file`` already serves it by area and digest. The
            # content-addressed record filename is required of records,
            # canonical state and events alone, so applying it here too would
            # refuse an archive this code wrote.
            workspace = parts[0] == "exports" or (
                parts[0] == "runs" and len(parts) >= 4 and parts[2] == "workspaces"
            )
            if path.endswith(".json") and not workspace and path not in (
                "project.json", "design/branches.json", "input/runner/state-record.json",
                "input/runner/seats.json", "input/runner/program-sheet.json",
            ) and not (parts[0] == "runs" and len(parts) == 3 and parts[2] == "run.json"):
                try:
                    _, named_digest = parse_record_file_name(parts[-1])
                except ValueError as exc:
                    raise ProjectIntegrityError("TRANSFER_INVALID: non-record JSON") from exc
                if named_digest != row["sha256"]:
                    raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: record filename")
            if parts[0] == "objects" and parts != ("objects", "sha256", row["sha256"][:2], row["sha256"]):
                raise ProjectIntegrityError("TRANSFER_DIGEST_MISMATCH: object path")
            files[path] = data
        if set(transfer["contents"]) - set(files):
            raise ProjectIntegrityError("TRANSFER_INVALID: unlisted contents")
        # This disposable repository is owned by P036 too. Reopening it tests
        # the original format, event chain, Stage ancestry and all references;
        # no transport parser is allowed to replace those existing readers.
        with tempfile.TemporaryDirectory(prefix="archflow-transfer-") as temporary:
            root = Path(temporary)
            for path, data in files.items():
                _write_immutable(root / path, data)
            staged = cls.open(root)
            if staged.load_manifest().project_id != expected_project_id:
                raise ProjectIntegrityError("TRANSFER_PROJECT_MISMATCH: manifest")
            checked = staged.export_transfer(
                run_id=transfer["root_run_id"], include_contents=False,
                include_all_runs=transfer["mode"] == "snapshot",
            )
            for key in keys - {"contents", "files"}:
                if checked[key] != transfer[key]:
                    raise ProjectIntegrityError(f"TRANSFER_INVALID: {key} differs from retained content")
            if checked["files"] != sorted(transfer["files"], key=lambda row: row["path"]):
                raise ProjectIntegrityError("TRANSFER_DEPENDENCY_MISSING: transfer is not the complete retained closure")
        return files

    @classmethod
    def bootstrap_transfer(
        cls, root: Path, transfer: Mapping[str, Any], *, expected_project_id: str,
    ) -> FilesystemProjectRepository:
        """Install a trusted snapshot; retry only matching unfinished bytes."""
        if transfer.get("mode") != "snapshot":
            raise ProjectIntegrityError("TRANSFER_INVALID: bootstrap requires a snapshot")
        files = cls._validate_transfer(transfer, expected_project_id=expected_project_id)
        root = Path(root).resolve()
        directories = {parent.as_posix() for path in files
                       for parent in PurePosixPath(path).parents if parent != PurePosixPath(".")}
        project_directories = {"objects", "objects/sha256", "events", "canonical", "runs", "exports", "input"}
        directories.update(project_directories)

        def require_resume_target() -> None:
            if not root.exists():
                return
            if not root.is_dir():
                raise ProjectAlreadyExists("bootstrap destination is not a directory")
            if (root / "HEAD").exists():
                raise ProjectAlreadyExists("bootstrap destination already has a published position")
            for path in root.rglob("*"):
                relative = path.relative_to(root).as_posix()
                if relative == "HEAD.lock":
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ProjectAlreadyExists("bootstrap destination contains a redirected path")
                if path.is_dir() and relative in directories:
                    continue
                if not path.is_file() or relative not in files or _read_bytes(path) != files[relative]:
                    raise ProjectAlreadyExists("bootstrap destination contains unrelated or conflicting content")

        require_resume_target()
        with _project_lock(root), _HeadFileLock(root / "HEAD.lock"):
            require_resume_target()
            for directory in project_directories:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for path, data in files.items():
                if path != "HEAD":
                    _write_immutable(root / path, data)
            _write_immutable(root / "HEAD", files["HEAD"])
        return cls.open(root)

    @classmethod
    def migrate_project_format(
        cls,
        source_root: Path,
        target_root: Path,
    ) -> ProjectFormatMigration:
        """Write a format-2 copy of a format-1 project into an empty directory.

        The source is never written, not even its advisory lock files: it is
        copied into a disposable staging directory beside the target, and that
        copy is what is opened, planned and read. Every version's identity is
        computed before the first target byte is written; the envelope is then
        written by this build's own transition writer and each HEAD move goes
        through ``compare_and_swap``. Run manifests and the authored state
        record have their base restated by their owners; everything else is
        copied byte for byte and the project-version identities it still
        embeds are listed, not rewritten. If anything fails, the directory this
        created is removed, so a retry starts from the same empty target.
        """

        _load_version_ref_owners()
        source_root = _migration_source(source_root)
        target_root = Path(target_root).resolve(strict=False)
        inspection = inspect_project_format(source_root)
        if (
            inspection.status != PROJECT_FORMAT_SUPPORTED_LEGACY
            or inspection.format_version != LEGACY_FORMAT_VERSION
        ):
            raise ProjectIntegrityError(
                f"MIGRATION_NOT_APPLICABLE: {inspection.status}: {inspection.detail}"
            )
        project_id = inspection.project_id
        if project_id is None or target_root.name != project_id:
            raise ProjectIntegrityError(
                f"MIGRATION_TARGET_NAME: the target directory must be named {project_id!r}"
            )
        if target_root.exists() and (not target_root.is_dir() or any(target_root.iterdir())):
            raise ProjectIntegrityError(
                "MIGRATION_TARGET_USED: the target directory already contains files"
            )
        if _contains_path(source_root, target_root) or _contains_path(target_root, source_root):
            raise ProjectIntegrityError(
                "MIGRATION_TARGET_NESTED: source and target may not contain each other"
            )
        # A junction is not a symlink to ``Path.is_symlink``, and ``copytree``
        # would inline whatever it points at. This is the same pre-check the
        # archive restore makes about its own destination.
        for path in source_root.rglob("*"):
            if (
                path.is_symlink()
                or path.is_junction()
                or not path.resolve().is_relative_to(source_root)
            ):
                raise ProjectIntegrityError(
                    f"MIGRATION_SOURCE_REDIRECTED: "
                    f"{path.relative_to(source_root).as_posix()}"
                )

        # Finding 7: an empty directory the caller made is kept, but every
        # file this writes into it is removed again if the migration fails.
        target_existed = target_root.exists()
        created: Path | None = None
        # Staging lives beside the target, on the target's volume: the copy is
        # the migration's working set, not something to leave in the system
        # temporary directory, and a hard link in the target cannot cross a
        # volume boundary that staging silently introduced.
        target_root.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=".archflow-migrate-", dir=target_root.parent,
            ) as temporary:
                staging = Path(temporary) / project_id
                shutil.copytree(
                    source_root, staging, symlinks=False,
                    ignore=shutil.ignore_patterns("*.tmp"),
                )
                if _read_bytes(source_root / "HEAD") != _read_bytes(staging / "HEAD"):
                    raise ProjectIntegrityError(
                        "MIGRATION_SOURCE_MOVED: the source published a new version "
                        "while it was being copied; nothing was written"
                    )
                source_head_sha256 = _sha256(_read_bytes(staging / "HEAD"))
                legacy = cls.open(staging)
                for lock in legacy.lock_paths():
                    lock.parent.mkdir(parents=True, exist_ok=True)
                    lock.touch(exist_ok=True)
                plan = plan_project_migration(staging)
                # An undeclared identity is re-detected below, per record and
                # per pointer; every other refusal is final here.
                refusals = [
                    blocker for blocker in plan.blockers
                    if UNDECLARED_IDENTITY_BLOCKER not in blocker
                ]
                if not plan.planned or refusals:
                    raise ProjectIntegrityError(
                        "MIGRATION_BLOCKED: " + "; ".join(refusals or plan.blockers)
                    )
                if not plan.migration_required:
                    raise ProjectIntegrityError(
                        "MIGRATION_NOT_REQUIRED: the project is already at the target format"
                    )
                if MIGRATION_RUN_ID in plan.run_ids:
                    raise ProjectIntegrityError(
                        f"MIGRATION_RECEIPT_RUN_TAKEN: the project already retains a run "
                        f"named {MIGRATION_RUN_ID!r}, which is where the migration files "
                        f"its own receipt"
                    )

                lineage = legacy._legacy_lineage()
                project_files = {
                    item.relative_to(staging).as_posix(): item
                    for item in staging.rglob("*") if item.is_file()
                }
                # Everything the envelope needs is computed before the target
                # exists: a state that cannot be digested must not leave a
                # half-written directory that inspects as current at v0.
                semantic: list[str] = []
                for ref, _, state in lineage:
                    _require_semantic_state_identity(
                        state, project_id=project_id, version=ref.version,
                        field=f"migrated state v{ref.version}",
                    )
                    semantic.append(_semantic_state_sha256(
                        state, field=f"migrated state v{ref.version}",
                    ))

                mapping = {
                    ref.require_digest(): digest
                    for (ref, _, _), digest in zip(lineage, semantic)
                }
                legacy_digests = {
                    ref.require_digest(): ref.version for ref, _, _ in lineage
                }
                # Records are restated and moved to a fixed point before a
                # single target byte exists: an event names a decision receipt,
                # so the envelope cannot be written until the receipt's final
                # name is known, and an undeclared identity must refuse here.
                payloads: dict[str, dict[str, Any]] = {}
                for relative, item in sorted(project_files.items()):
                    if (
                        relative in ("project.json", "HEAD")
                        or relative.startswith(("canonical/", "events/"))
                        or not relative.endswith(".json")
                        or _retained_category(relative)[0] == "artifact"
                        or _TEMPORARY_NAME.match(PurePosixPath(relative).name)
                    ):
                        continue
                    # A retained document that does not decode has already
                    # failed the plan's own read of this closure above.
                    payloads[relative] = _parse_json_document(
                        _read_bytes(item), relative,
                    )
                cascade = _cascade_records(
                    payloads, project_id=project_id,
                    mapping=mapping, legacy_digests=legacy_digests,
                    moved=_canonical_snapshot_moves(lineage, semantic, project_id),
                )
                if cascade.blockers:
                    raise ProjectIntegrityError(
                        "MIGRATION_BLOCKED: " + "; ".join(cascade.blockers)
                    )
                authored = payloads.get(AUTHORED_RECORD_PATH)
                if authored is not None:
                    from archflow.project.version_refs import locations as _at
                    stranded = [
                        digest for _, _, digest in _at(authored)
                        if digest not in mapping and digest not in set(mapping.values())
                    ]
                    if stranded:
                        raise ProjectIntegrityError(
                            "MIGRATION_AUTHORED_BASE_UNKNOWN: the authored state record "
                            "is based on a version this project does not publish, so no "
                            "owner can restate it"
                        )

                # ``initialize`` writes the manifest, the first snapshot and
                # HEAD before it verifies its own work, so the target is this
                # migration's from before that call, not after it returns.
                created = target_root
                target = cls.initialize(
                    target_root, project_id=project_id, initial_state=lineage[0][2],
                )
                versions: list[tuple[int, str, str]] = [
                    (0, lineage[0][0].require_digest(), target.read_head().require_digest())
                ]
                with target._lock, target._head_lock:
                    for (legacy_ref, event, state), digest in zip(lineage[1:], semantic[1:]):
                        previous, previous_snapshot, previous_event = (
                            target._read_head_document()
                        )
                        carried = {
                            key: value for key, value in event.items()
                            if key not in _MIGRATED_EVENT_KEYS
                        }
                        prepared = target._write_transition(
                            previous=previous,
                            previous_snapshot=previous_snapshot,
                            previous_event=previous_event,
                            state=state,
                            event_type=event.get("event_type"),
                            decision=event.get("decision"),
                            run_id=event.get("run_id"),
                            decision_receipt=_rewrite_references(
                                event.get("decision_receipt"), project_id,
                                cascade.renames, {},
                            ),
                            carried=carried,
                        )
                        published = target.compare_and_swap(
                            expected=previous,
                            event=prepared.event,
                            replacement=prepared.replacement,
                        )
                        if published.require_digest() != digest:
                            raise ProjectIntegrityError(
                                f"MIGRATION_VERIFY: published v{published.version} does "
                                f"not carry the digest its state was validated with"
                            )
                        versions.append(
                            (legacy_ref.version, legacy_ref.require_digest(), digest)
                        )

                report = _copy_retained_closure(
                    staging, target_root, legacy, mapping, legacy_digests, cascade,
                )

                migrated = cls.open(target_root)
                migrated.verify()
                closure = migrated.export_transfer(
                    include_contents=False, include_all_runs=True,
                )
                if closure["format_version"] != CURRENT_FORMAT_VERSION:
                    raise ProjectIntegrityError(
                        "MIGRATION_VERIFY: the migrated closure does not report the target format"
                    )
                if plan_project_migration(target_root).migration_required:
                    raise ProjectIntegrityError(
                        "MIGRATION_VERIFY: the migrated project still requires migration"
                    )
                # The scan is not decoration: a legacy identity anywhere in the
                # migrated closure means the cascade missed a location.
                if report.embedded:
                    path, pointer, _, _, _ = report.embedded[0]
                    raise ProjectIntegrityError(
                        f"MIGRATION_VERIFY: {len(report.embedded)} retained location(s) "
                        f"still name a legacy version, the first at {path}{pointer}"
                    )
                _require_consistent_migration(
                    migrated, closure, mapping, cascade.moved_digests,
                )
                result = ProjectFormatMigration(
                    source_root=source_root,
                    target_root=target_root,
                    project_id=project_id,
                    source_format_version=LEGACY_FORMAT_VERSION,
                    target_format_version=CURRENT_FORMAT_VERSION,
                    source_head_sha256=source_head_sha256,
                    versions=tuple(versions),
                    rewritten=tuple(report.rewritten),
                    preserved_files=report.preserved_files,
                    orphans=tuple(report.orphans),
                    orphan_legacy_references=tuple(report.orphan_legacy),
                    unscanned_binaries=tuple(report.unscanned_binaries),
                    embedded_legacy_references=tuple(report.embedded),
                    receipt=None,
                )
                # Last, and only once the migrated project verifies: the
                # migration files its own account as a retained record, through
                # the repository's own writer, inside the closure it produced.
                return replace(result, receipt=migrated._file_migration_receipt(result))
        except BaseException as exc:
            if created is not None and created.exists():
                if target_existed:
                    # The directory was the caller's; only its contents are ours.
                    for item in created.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                else:
                    shutil.rmtree(created, ignore_errors=True)
            if isinstance(exc, ProjectRepositoryError):
                raise
            if isinstance(exc, (OSError, ValueError, KeyError, TypeError)):
                raise ProjectIntegrityError(f"MIGRATION_FAILED: {exc}") from exc
            raise

    def _legacy_lineage(self) -> list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]]:
        """Published history oldest-first: (version, its event, its state).

        One backward walk reads each snapshot exactly once, so a long history
        costs one pass rather than one full replay per version.
        """

        current, snapshot_ref, event_ref = self._read_head_document()
        walked: list[tuple[ProjectVersionRef, dict[str, Any], dict[str, Any]]] = []
        while True:
            event = self.load_json(event_ref)
            snapshot = self._verify_snapshot(snapshot_ref, current)
            state = snapshot.get("state")
            if not isinstance(state, dict):
                raise ProjectIntegrityError("canonical snapshot state is not an object")
            walked.append((current, event, state))
            parent = event.get("from")
            if parent is None:
                break
            current = _version_from_dict(parent, field="event from")
            snapshot_ref = self._canonical_ref_for_version(current)
            event_ref = _record_from_dict(
                event.get("previous_event"),
                project_id=self._manifest.project_id,
                field="previous_event",
            )
        walked.reverse()
        if [ref.version for ref, _, _ in walked] != list(range(len(walked))):
            raise ProjectIntegrityError(
                "MIGRATION_LINEAGE: published versions are not contiguous from 0"
            )
        return walked

    def _file_migration_receipt(self, result: ProjectFormatMigration) -> ProjectRecordRef:
        """Retain the migration's own account inside the project it produced."""

        run = self.create_run(MIGRATION_RUN_ID)
        return self.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD, run_id=MIGRATION_RUN_ID,
            ),
            record_kind=PROJECT_FORMAT_MIGRATION,
            payload=result.to_dict(),
        )

    def import_candidate_transfer(self, transfer: Mapping[str, Any]) -> None:
        """Import immutable candidate evidence; never accept, branch or issue."""
        if transfer.get("mode") != "candidate":
            raise ProjectIntegrityError("TRANSFER_INVALID: candidate upload requires a candidate transfer")
        files = self._validate_transfer(transfer, expected_project_id=self._manifest.project_id,
                                        existing_root=self.layout.root)
        with self._lock, self._head_lock, self._design_lock:
            if self.read_head().to_dict() != transfer["head"]:
                raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: synchronize the published base before upload")
            self._install_transfer_files(files)

    def pull_transfer(
        self, transfer: Mapping[str, Any], *, expected_head: ProjectVersionRef,
        expected_branches: Mapping[str, Any],
    ) -> None:
        """Adopt shared Stage positions, preserving local runs and authored WIP.

        Published HEAD migration is deliberately not this operation. A changed
        published base requires an explicit later synchronization capability.
        """
        if transfer.get("mode") != "snapshot":
            raise ProjectIntegrityError("TRANSFER_INVALID: pull requires a shared snapshot")
        files = self._validate_transfer(transfer, expected_project_id=self._manifest.project_id,
                                        existing_root=self.layout.root)
        with self._lock, self._head_lock, self._design_lock:
            if self.read_head() != expected_head or transfer["head"] != expected_head.to_dict():
                raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: published base synchronization is not supported by Stage pull")
            if self.read_design_branches() != expected_branches:
                raise StaleDesignBranch("local design branches changed while the shared snapshot was downloaded")
            for branch_id, previous in expected_branches.items():
                replacement = transfer["branches"].get(branch_id)
                if replacement is None or any(previous[key] != replacement[key] for key in ("fork_stage", "parent_branch")):
                    raise StaleDesignBranch("shared snapshot cannot remove or refork existing local history")
                stage = replacement["head_stage"]
                while stage != previous["head_stage"]:
                    payload = _parse_json_document(files[stage["relative_path"]], "pulled design Stage")
                    stage = payload.get("parent_stage")
                    if stage is None:
                        raise StaleDesignBranch("shared snapshot does not continue the current local design head")
            self._install_transfer_files(files)
            if transfer["branches"]:
                _replace_atomic(self.layout.design_branches, files["design/branches.json"])

    def _install_transfer_files(self, files: Mapping[str, bytes]) -> None:
        writes: list[tuple[Path, bytes]] = []
        for path, data in files.items():
            if path == "design/branches.json" or path.startswith("input/"):
                continue
            target = (self.layout.root / path).resolve()
            if not target.is_relative_to(self.layout.root):
                raise ProjectIntegrityError("TRANSFER_PATH_INVALID: destination escapes project root")
            if target.exists() and _read_bytes(target) != data:
                raise ProjectIntegrityError(f"TRANSFER_CONTENT_CONFLICT: {path}")
            if path in ("HEAD", "project.json") or path.startswith(("canonical/", "events/")):
                if not target.exists():
                    raise StaleProjectHead("TRANSFER_PUBLISHED_HEAD_CHANGED: retained published history differs")
                continue
            if not target.exists():
                writes.append((target, data))
        for target, data in writes:
            _write_immutable(target, data)

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

        design_branches = self.read_design_branches()
        if design_branches:
            reachable.add(self.layout.design_branches.relative_to(self.layout.root).as_posix())
        for branch in design_branches.values():
            for field in ("fork_stage", "head_stage"):
                stage_ref: ProjectRecordRef | None = ProjectRecordRef.from_dict(branch[field])
                seen: set[str] = set()
                while stage_ref is not None:
                    if stage_ref.relative_path in seen:
                        raise ProjectIntegrityError("design history contains a cycle")
                    seen.add(stage_ref.relative_path)
                    stage = self._require_design_stage(stage_ref)
                    reachable.add(stage_ref.relative_path)
                    parent = stage.get("parent_stage")
                    stage_ref = None if parent is None else ProjectRecordRef.from_dict(parent, "parent_stage")
        retained = {
            path.relative_to(self.layout.root).as_posix()
            for base in (self.layout.events, self.layout.canonical)
            if base.exists()
            for path in base.rglob("*.json")
            if path.is_file()
        }
        retained.update(
            path.relative_to(self.layout.root).as_posix()
            for path in self.layout.runs.glob(f"*/reviews/{DESIGN_STAGE}-*.json")
            if path.is_file()
        )
        return RecoveryReport(
            head=head,
            reachable_paths=tuple(sorted(reachable)),
            orphan_paths=tuple(sorted(retained - reachable)),
        )

    def _read_head_document(
        self,
    ) -> tuple[ProjectVersionRef, ProjectRecordRef, ProjectRecordRef]:
        # HEAD is the one mutable document: a concurrent compare_and_swap may
        # be replacing it right now, so the read must tolerate the transient
        # Windows sharing violation instead of reporting corruption.
        payload = _read_shared_json(self.layout.head)
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
        # ``ProjectHead@1``/``@2`` keep their names: every retained project
        # document on disk declares one of them and readers match the literal
        # (ADR-004). The design this points at is the *published* one, and
        # putting it there is an *issue* (ADR-007); the schema string is not
        # part of that vocabulary.
        return {
            "schema": (
                "ProjectHead@1"
                if self._manifest.format_version == LEGACY_FORMAT_VERSION
                else "ProjectHead@2"
            ),
            "project_id": self._manifest.project_id,
            "current": current.to_dict(),
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
        path = directory / record_file_name(record_kind, digest)
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
            "base": run.base.to_dict(),
        }
        if payload != expected:
            raise ProjectIntegrityError("run manifest changed or base drifted")


# ---- read-only project-format inspection and dry-run migration planning
#
# ``project.json.format_version`` is the one global project-format authority;
# each retained record keeps its own ``schema`` where that record is (ADR-004).
# Both entry points are read-only and neither interprets a record payload
# beyond the ``schema`` literal it declares: what a migration must do with a
# record's references is that record's typed owner's answer, not a parser's.


def inspect_project_format(root: Path) -> ProjectFormatInspection:
    """Classify a directory's declared project format without opening it.

    ``current`` and ``supported_legacy`` are the formats this build reads;
    ``too_new`` fails closed with what this build supports, and ``invalid``
    names a directory whose ``project.json`` is missing or drifted. Only
    ``project.json`` is read, and no file is created or changed.
    """

    resolved = Path(root).resolve(strict=False)

    def answer(
        status: str,
        version: int | None,
        project_id: str | None,
        detail: str,
    ) -> ProjectFormatInspection:
        return ProjectFormatInspection(
            root=resolved,
            status=status,
            format_version=version,
            project_id=project_id,
            detail=detail,
        )

    try:
        payload = _read_json(resolved / "project.json")
    except ProjectIntegrityError as exc:
        return answer(
            PROJECT_FORMAT_INVALID, None, None,
            f"project.json is missing or unreadable: {exc}",
        )
    try:
        manifest = ProjectManifest.from_dict(payload)
    except (ProjectManifestError, TypeError, ValueError) as exc:
        return answer(
            PROJECT_FORMAT_INVALID, None, None,
            f"project.json is not a valid project manifest: {exc}",
        )
    supported = ", ".join(str(version) for version in SUPPORTED_FORMAT_VERSIONS)
    version = manifest.format_version
    if version == CURRENT_FORMAT_VERSION:
        return answer(
            PROJECT_FORMAT_CURRENT, version, manifest.project_id,
            f"project format {version} is current",
        )
    if version in SUPPORTED_FORMAT_VERSIONS:
        return answer(
            PROJECT_FORMAT_SUPPORTED_LEGACY, version, manifest.project_id,
            f"project format {version} is supported legacy; this build reads it "
            f"unchanged and never rewrites it in place",
        )
    if version > CURRENT_FORMAT_VERSION:
        return answer(
            PROJECT_FORMAT_TOO_NEW, version, manifest.project_id,
            f"project format {version} was written by a newer build; this build "
            f"supports {supported}. Open it with the build that wrote it; a "
            f"project format is never downgraded",
        )
    return answer(
        PROJECT_FORMAT_INVALID, version, manifest.project_id,
        f"project format {version} is not one of {supported}",
    )


def _retained_category(path: str) -> tuple[str, str, bool | None]:
    """Which authority governs one retained path, and its kind and registration.

    Artifact bytes are identified by digest and carry no project schema, so the
    planner never opens them; every other retained file is JSON whose declared
    ``schema`` the inventory reports.
    """

    if path in ("project.json", "HEAD"):
        return "envelope", path, None
    if path == "design/branches.json":
        return "design", path, None
    if path.startswith("input/"):
        return "authored_input", path, None
    parts = PurePosixPath(path).parts
    if parts[0] == "canonical":
        return "envelope", "canonical/snapshot", None
    if parts[0] == "events":
        return "envelope", "events/event", None
    if parts[0] == "objects":
        return "artifact", "objects/sha256", None
    if parts[0] == "runs" and len(parts) >= 3:
        if len(parts) == 3 and parts[2] == "run.json":
            return "run_manifest", "runs/run.json", None
        if parts[2] == "workspaces":
            return "artifact", "runs/workspaces", None
        try:
            kind, _ = parse_record_file_name(parts[-1])
        except ValueError:
            return "artifact", f"runs/{parts[2]}", None
        return "record", kind, is_registered(kind)
    return "artifact", parts[0], None


def plan_project_migration(
    root: Path,
    *,
    target_format_version: int = CURRENT_FORMAT_VERSION,
) -> ProjectMigrationPlan:
    """Report what migrating one retained project would require. Writes nothing.

    The project is opened and verified with the reader its own format already
    has, and the inventory is the complete retained closure the existing
    transfer export walks, including every run, authored input and source
    artifact. The inventory reports each retained kind, its declared schema and
    whether the record-kind table still holds it; it does not parse payloads.
    Records the migration does not own are preserved and listed; blockers
    remain only for a closure this build cannot read completely.

    Reading that closure goes through the repository's own guarded export, which
    acquires the advisory HEAD and design locks; acquiring one creates its file.
    A project missing those lock files therefore cannot be inventoried without
    changing it, and this refuses instead, naming the exact paths.
    """

    _load_version_ref_owners()
    supported = ", ".join(str(version) for version in SUPPORTED_FORMAT_VERSIONS)
    if target_format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(
            f"target project format {target_format_version} is not one of {supported}"
        )
    inspection = inspect_project_format(root)

    def refused(*blockers: str) -> ProjectMigrationPlan:
        return ProjectMigrationPlan(
            inspection=inspection,
            target_format_version=target_format_version,
            planned=False,
            migration_required=False,
            project_id=inspection.project_id,
            head=None,
            run_ids=(),
            inventory=(),
            retained_files=0,
            retained_bytes=0,
            orphan_paths=(),
            required_transformations=(),
            preserved=(),
            blockers=blockers,
        )

    if inspection.status in (PROJECT_FORMAT_INVALID, PROJECT_FORMAT_TOO_NEW):
        return refused(inspection.detail)
    source = inspection.format_version
    if source is None:  # pragma: no cover - a supported status carries its version
        return refused(inspection.detail)
    if source > target_format_version:
        return refused(
            f"a project format migration is forward-only; this project is format "
            f"{source} and the requested target is {target_format_version}"
        )

    try:
        repository = FilesystemProjectRepository.open(inspection.root)
    except ProjectRepositoryError as exc:
        return refused(
            f"the project does not open and verify through its own format-{source} "
            f"reader, so its retained closure cannot be planned: {exc}"
        )
    absent = [path for path in repository.lock_paths() if not path.exists()]
    if absent:
        return refused(
            "the retained closure is read through the repository's guarded export, "
            "which acquires the advisory lock(s) "
            + ", ".join(
                path.relative_to(inspection.root).as_posix() for path in absent
            )
            + "; this project does not have them yet and acquiring a lock creates "
            "its file, so inventorying this project would change it. The format "
            "above was read without opening the project; inventory a project whose "
            "own owner has already opened it for writing"
        )

    grouped: dict[tuple[str, str, str | None, bool | None], list[int]] = {}
    unknown: dict[tuple[str, str | None], int] = {}
    # kind -> (record count, one example "<path><pointer>")
    undeclared: dict[str, tuple[int, str]] = {}
    unreadable: list[str] = []
    # The same legacy identities the migration would remap, so the dry run and
    # the migration cannot disagree about what blocks.
    try:
        legacy_digests = (
            {ref.require_digest(): ref.version for ref, _, _ in repository._legacy_lineage()}
            if source != target_format_version else {}
        )
    except ProjectRepositoryError as exc:
        return refused(
            f"the published history could not be read completely, so this project "
            f"cannot be planned: {exc}"
        )
    try:
        transfer = repository.export_transfer(
            include_contents=False, include_all_runs=True,
        )
        report = repository.verify()
        for row in transfer["files"]:
            path, digest, size = row["path"], row["sha256"], row["size"]
            category, kind, registered = _retained_category(path)
            schema: str | None = None
            if category != "artifact":
                payload = _parse_json_document(
                    repository.read_transfer_file(path, digest), path,
                )
                declared = payload.get("schema")
                schema = declared if isinstance(declared, str) else None
                if registered is False:
                    unknown[(kind, schema)] = unknown.get((kind, schema), 0) + 1
                if category in ("record", "run_manifest", "authored_input", "design"):
                    # The migration asks each owner for this record's content
                    # digest; if the owner cannot read its own record the
                    # migration stops, so the dry run has to say so too.
                    try:
                        _content_digest_of(payload)
                    except ValueError as exc:
                        unreadable.append(f"{path}: {exc}")
                    for found in undeclared_version_identities(payload, legacy_digests):
                        count, example = undeclared.get(kind, (0, ""))
                        undeclared[kind] = (
                            count + 1, example or f"{path}{found.json_pointer}",
                        )
            totals = grouped.setdefault((category, kind, schema, registered), [0, 0])
            totals[0] += 1
            totals[1] += size
    except ProjectRepositoryError as exc:
        # A record or manifest that changed mid-scan leaves a partial reading.
        # Report that the project cannot be planned rather than presenting an
        # incomplete inventory as the complete retained closure.
        return refused(
            f"the retained closure could not be read completely, so no inventory "
            f"of this project is reported: {exc}"
        )

    inventory = tuple(
        RetainedFormatEntry(
            category=category,
            kind=kind,
            schema=schema,
            registered=registered,
            count=count,
            bytes=size,
        )
        for (category, kind, schema, registered), (count, size) in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
        )
    )
    counts = {
        name: sum(entry.count for entry in inventory if match(entry))
        for name, match in (
            ("artifact", lambda entry: entry.category == "artifact"),
            ("record", lambda entry: entry.category == "record"),
            ("snapshot", lambda entry: entry.kind == "canonical/snapshot"),
            ("event", lambda entry: entry.kind == "events/event"),
        )
    }
    common = {
        "inspection": inspection,
        "target_format_version": target_format_version,
        "planned": True,
        "project_id": repository.load_manifest().project_id,
        "head": report.head,
        "run_ids": tuple(transfer["run_ids"]),
        "inventory": inventory,
        "retained_files": len(transfer["files"]),
        "retained_bytes": sum(entry.bytes for entry in inventory),
        "orphan_paths": report.orphan_paths,
    }
    if source == target_format_version:
        return ProjectMigrationPlan(
            migration_required=False, required_transformations=(),
            preserved=(), blockers=(),
            **common,
        )

    transformations = [
        f"project.json format_version {source} -> {target_format_version}",
        f"HEAD ProjectHead@{source} -> ProjectHead@{target_format_version}",
    ]
    if counts["snapshot"]:
        transformations.append(
            f"{counts['snapshot']} canonical snapshot(s) CanonicalSnapshot@{source} -> "
            f"@{target_format_version}, whose state_sha256 becomes the semantic state "
            f"digest instead of the snapshot-file digest"
        )
    if counts["event"]:
        transformations.append(
            f"{counts['event']} retained event(s) ProjectEvent@{source} -> "
            f"@{target_format_version}, which must name from_snapshot/to_snapshot"
        )
    if counts["record"]:
        transformations.append(
            f"each of the {counts['record']} retained record(s) listed above is "
            f"restated at the project-version pointers its schema's owner declares, "
            f"re-hashed when its payload changes, and every retained reference that "
            f"names it is moved with it; a record carrying a legacy identity no owner "
            f"declares blocks the migration instead of being guessed at"
        )
    if counts["artifact"]:
        transformations.append(
            f"preserve {counts['artifact']} retained binary/source artifact(s) "
            f"byte-for-byte"
        )

    # A location no owner declares is where a migration would have to guess.
    # Grouped by kind with a count and one example, so a project carrying many
    # of them reads as a list of contracts to declare, not a wall of paths.
    blockers = [
        f"a retained record its own owner cannot read, so no owner can restate "
        f"it: {detail}"
        for detail in sorted(unreadable)
    ] + [
        f"retained kind {kind!r}: {count} record(s) carry a project-version "
        f"identity at a location no owner in this build restates "
        f"(for example {example}); {UNDECLARED_IDENTITY_BLOCKER}"
        for kind, (count, example) in sorted(undeclared.items())
    ]
    preserved = [
        f"retained record kind {kind!r} ({count} record(s), schema "
        f"{schema or 'undeclared'}) is not in the current record-kind table; the "
        f"migration preserves it byte for byte and lists any project-version "
        f"identity it embeds in the migration receipt"
        for (kind, schema), count in sorted(
            unknown.items(), key=lambda item: (item[0][0], item[0][1] or ""),
        )
        if kind not in undeclared
    ]

    return ProjectMigrationPlan(
        migration_required=True,
        required_transformations=tuple(transformations),
        preserved=tuple(preserved),
        blockers=tuple(blockers),
        **common,
    )
