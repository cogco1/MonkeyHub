"""Stable project, run, branch, record, and artifact references."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_HEX = frozenset("0123456789abcdef")

# The one shape of a retained record's file name: ``<kind>-<64 hex>.json``.
# ``kind`` is greedy, so the longest kind before the digest wins; a name is
# not a record unless it ends in a digest, and no reader invents a second
# rule for reading one back.
RECORD_FILE_NAME = re.compile(r"^(?P<kind>.+)-(?P<sha256>[0-9a-f]{64})\.json$")


def record_file_name(record_kind: str, sha256: str) -> str:
    """The file name a record of this kind and digest is stored under."""

    return f"{record_kind}-{sha256}.json"


def parse_record_file_name(name: str) -> tuple[str, str]:
    """Read a record file name back as ``(record_kind, sha256)``.

    The kind is everything before the digest, so a name whose kind itself
    ends in a hyphenated word comes back whole; whether that word is a kind
    the spine writes is the record-kind table's question, not this one's.
    """

    if not isinstance(name, str):
        raise TypeError("record file name must be text")
    match = RECORD_FILE_NAME.fullmatch(name)
    if match is None:
        raise ValueError(
            "record file name must be <kind>-<64 hex sha256>.json; "
            f"{name!r} is not one"
        )
    return match.group("kind"), match.group("sha256")


def require_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a portable 1-100 character identifier"
        )
    return value


def _require_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return digest


def require_project_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("relative_path must be non-empty portable POSIX text")
    path = PurePosixPath(value)
    if path.is_absolute() or path == PurePosixPath("."):
        raise ValueError("relative_path must be project-relative")
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("relative_path contains an unsafe segment")
    if any(_PATH_SEGMENT.fullmatch(part) is None for part in path.parts):
        raise ValueError("relative_path contains a non-portable segment")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("relative_path must already be normalized")
    return normalized


def _exact_dict(
    value: object,
    keys: set[str],
    field: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    if set(value) != keys:
        raise ValueError(f"{field} schema drifted")
    return value


@dataclass(frozen=True, slots=True, init=False)
class ProjectVersionRef:
    project_id: str
    version: int
    state_sha256: str | None

    def __init__(
        self,
        project_id: str | None = None,
        version: int = 0,
        state_sha256: str | None = None,
        *,
        run_id: str | None = None,
    ) -> None:
        """Create a project-version reference.

        ``state_sha256`` names canonical state content in current project
        documents. The immutable snapshot record has its own separate digest.
        Format-version-1 repositories retain their historical snapshot-digest
        interpretation only inside the repository compatibility boundary.

        ``run_id`` is accepted only as a temporary source-compatibility keyword
        for pre-M007 callers.  It is interpreted as a project id and is never
        stored as run identity.  Durable references supplied by the project
        repository always include ``state_sha256``.
        """

        if project_id is not None and run_id is not None:
            raise TypeError("provide project_id, not both project_id and legacy run_id")
        resolved_project_id = project_id if project_id is not None else run_id
        if resolved_project_id is None:
            raise TypeError("project_id is required")
        require_identifier(resolved_project_id, "project_id")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 0
        ):
            raise ValueError("version must be a non-negative integer")
        digest = (
            _require_sha256(state_sha256, "state_sha256")
            if state_sha256 is not None
            else None
        )
        object.__setattr__(self, "project_id", resolved_project_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "state_sha256", digest)

    @property
    def run_id(self) -> str:
        """Pre-M007 read compatibility; this value is the project id."""

        return self.project_id

    def require_digest(self) -> str:
        if self.state_sha256 is None:
            raise ValueError("durable project-version reference requires state_sha256")
        return self.state_sha256

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "version": self.version,
            "state_sha256": self.require_digest(),
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        field: str = "base",
    ) -> "ProjectVersionRef":
        payload = _exact_dict(
            value,
            {"project_id", "version", "state_sha256"},
            field,
        )
        return cls(
            project_id=payload["project_id"],
            version=payload["version"],
            state_sha256=payload["state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class RunRef:
    project_id: str
    run_id: str
    base: ProjectVersionRef

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be a ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("run and base belong to different projects")

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": self.base.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object, field: str = "run") -> "RunRef":
        payload = _exact_dict(
            value,
            {"project_id", "run_id", "base"},
            field,
        )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef.from_dict(payload["base"], f"{field} base"),
        )


@dataclass(frozen=True, slots=True)
class BranchRef:
    run: RunRef
    branch_id: str
    epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunRef):
            raise TypeError("run must be a RunRef")
        require_identifier(self.branch_id, "branch_id")
        if (
            not isinstance(self.epoch, int)
            or isinstance(self.epoch, bool)
            or self.epoch < 0
        ):
            raise ValueError("epoch must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        """Exact, persistence-neutral serialization for branch identity."""

        branch = require_exact_branch(self)
        return {
            "project_id": branch.run.project_id,
            "run_id": branch.run.run_id,
            "base": branch.run.base.to_dict(),
            "branch_id": branch.branch_id,
            "epoch": branch.epoch,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BranchRef":
        if not isinstance(value, Mapping) or set(value) != {
            "project_id",
            "run_id",
            "base",
            "branch_id",
            "epoch",
        }:
            raise ValueError("branch schema drifted")
        base = value["base"]
        if not isinstance(base, Mapping) or set(base) != {
            "project_id",
            "version",
            "state_sha256",
        }:
            raise ValueError("branch base schema drifted")
        project_id = value["project_id"]
        if base["project_id"] != project_id:
            raise ValueError("branch and base belong to different projects")
        result = cls(
            run=RunRef(
                project_id=project_id,
                run_id=value["run_id"],
                base=ProjectVersionRef(
                    project_id=base["project_id"],
                    version=base["version"],
                    state_sha256=base["state_sha256"],
                ),
            ),
            branch_id=value["branch_id"],
            epoch=value["epoch"],
        )
        return require_exact_branch(result)


@dataclass(frozen=True, slots=True)
class ProjectRecordRef:
    project_id: str
    relative_path: str
    sha256: str
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        object.__setattr__(
            self,
            "relative_path",
            require_project_relative_path(self.relative_path),
        )
        object.__setattr__(
            self,
            "sha256",
            _require_sha256(self.sha256, "sha256"),
        )
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be non-empty text")

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        field: str = "project record ref",
    ) -> "ProjectRecordRef":
        payload = _exact_dict(
            value,
            {"project_id", "relative_path", "sha256", "media_type"},
            field,
        )
        return cls(
            project_id=payload["project_id"],
            relative_path=payload["relative_path"],
            sha256=payload["sha256"],
            media_type=payload["media_type"],
        )

    @property
    def record_kind(self) -> str:
        """The kind in this record's file name; refuses any other shape."""

        return parse_record_file_name(self.relative_path.rsplit("/", 1)[-1])[0]

    @property
    def uri(self) -> str:
        return (
            f"project://{quote(self.project_id, safe='')}/"
            f"{quote(self.relative_path, safe='/._-')}"
        )


@dataclass(frozen=True, slots=True)
class ProjectArtifactRef:
    project_id: str
    artifact_id: str
    relative_path: str
    sha256: str
    media_type: str

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.artifact_id, "artifact_id")
        object.__setattr__(
            self,
            "relative_path",
            require_project_relative_path(self.relative_path),
        )
        object.__setattr__(
            self,
            "sha256",
            _require_sha256(self.sha256, "sha256"),
        )
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be non-empty text")

    @property
    def uri(self) -> str:
        return (
            f"project://{quote(self.project_id, safe='')}/"
            f"{quote(self.relative_path, safe='/._-')}"
        )


def require_exact_branch(branch: object, field: str = "branch") -> BranchRef:
    """Require a branch whose run is bound to digested canonical base state."""

    if not isinstance(branch, BranchRef):
        raise TypeError(f"{field} must be a BranchRef")
    branch.run.base.require_digest()
    return branch


def require_same_branch(
    expected: BranchRef,
    actual: BranchRef,
    *,
    field: str,
) -> None:
    expected = require_exact_branch(expected, "expected branch")
    actual = require_exact_branch(actual, field)
    if actual != expected:
        raise ValueError(f"{field} crossed its exact branch")


def record_ref_from_uri(uri: str, project_id: str) -> ProjectRecordRef:
    """Read back the record reference a ``project://`` record URI names.

    The one parser: the scheme and the project have to be this project's, and
    the last path segment has to be a record file name. Anything else is a
    ``ValueError`` rather than a reference to something that is not a record.
    """

    if not isinstance(uri, str):
        raise TypeError("record URI must be text")
    parsed = urlsplit(uri)
    if parsed.scheme != "project" or unquote(parsed.netloc) != project_id:
        raise ValueError("record URI belongs to another project")
    relative = unquote(parsed.path).lstrip("/")
    _, sha256 = parse_record_file_name(relative.rsplit("/", 1)[-1])
    return ProjectRecordRef(
        project_id=project_id,
        relative_path=relative,
        sha256=sha256,
        media_type="application/json",
    )
