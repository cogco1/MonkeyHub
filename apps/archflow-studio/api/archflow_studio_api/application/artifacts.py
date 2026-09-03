"""The exported models a run's receipts certify, and nothing else.

A ``.3dm`` file on disk is not an artifact: it is a file. What makes it an
artifact is a retained ``seat-rhino-execution`` receipt that says which run and
which program produced it, against which base, and what its bytes hash to. So
this module never looks for files and then asks what they are — it reads the
receipts and then asks whether the file they certify is still there.

That order is what lets the listing be honest about the three ways an artifact
can be absent: the receipt claimed no digest (a failed export), no file of that
name is in the run's workspaces, or files are there and none of them hash to the
digest the receipt claims. The third is the interesting one: it is what an
edited-in-Rhino model looks like from here, and it is never reported as success.

Resolution is content-addressed on purpose. Older runs did not put exports under
``cad-<stage_id>``, and two receipts in one run can name the same file. Matching
by digest inside that run's workspaces answers both without a convention.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError
from .binding import ProjectBinding, record_kind

# The one record kind that certifies an exported model. Compared by equality:
# a prefix test would let a summary record answer as an execution receipt.
RHINO_EXECUTION_KIND = "seat-rhino-execution"

# A file digest, as it travels in a path parameter. Lowercase because that is
# what the kernel writes; anything else names no artifact here.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Why an artifact cannot be served. Three distinct facts, never collapsed.
NO_DIGEST = "no inspection digest"
FILE_MISSING = "file missing"
DIGEST_MISMATCH = "digest mismatch"


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One receipt, and what became of the file it certified."""

    artifact_id: str
    run_id: str
    stage_id: str | None
    file_name: str
    relative_path: str | None
    sha256: str | None
    size_bytes: int | None
    object_count: int | None
    status: str | None
    readback_verified: bool | None
    available: bool
    unavailable_reason: str | None
    base_version: int | None
    base_state_sha256: str | None
    branch_id: str | None
    branch_epoch: int | None
    program_ref: str | None
    program_digest: str | None
    design_state_digest: str | None
    length_unit: str | None
    up_axis: str | None
    receipt_ref: str


@dataclass(frozen=True, slots=True)
class ArtifactListing:
    """Every artifact this project can account for, and what it could not read."""

    project_id: str
    artifacts: tuple[ArtifactRecord, ...]
    # Run directories whose records could not be listed. One corrupt run must
    # not cost the client every other artifact, and the skip is never silent.
    skipped_runs: tuple[str, ...]


def list_artifacts(binding: ProjectBinding) -> ArtifactListing:
    """Every artifact certified by a receipt in this project, run by run."""

    records: list[ArtifactRecord] = []
    skipped: list[str] = []
    for run_id in binding.run_ids():
        try:
            receipts = _receipts_of(binding, run_id)
        except (StudioError, ProjectRepositoryError, ValueError, OSError):
            skipped.append(run_id)
            continue
        if not receipts:
            continue
        # One walk of the run's workspaces answers every receipt in it.
        index = _workspace_index(binding, run_id)
        for ref, payload in receipts:
            records.append(_artifact(binding, run_id, ref, payload, index))
    records.sort(key=lambda item: (item.run_id, item.stage_id or "", item.file_name))
    return ArtifactListing(
        project_id=binding.project_id,
        artifacts=tuple(records),
        skipped_runs=tuple(skipped),
    )


def artifact_bytes(
    binding: ProjectBinding, sha256: str
) -> tuple[ArtifactRecord, bytes]:
    """The bytes of the artifact with that digest, re-verified as they are read.

    The listing already matched this file by content, but the check is made
    again here against the digest the caller asked for: between listing and
    reading, the file can change, and a download that no longer hashes to what
    was requested is a conflict, not a success.
    """

    if SHA256_HEX.match(sha256) is None:
        raise StudioError(
            404,
            "ARTIFACT_NOT_FOUND",
            f"{sha256!r} is not an artifact digest: artifacts are addressed by "
            "the 64 lowercase hex characters of their sha256.",
        )
    listing = list_artifacts(binding)
    claiming = [item for item in listing.artifacts if item.sha256 == sha256]
    if not claiming:
        raise StudioError(
            404,
            "ARTIFACT_NOT_FOUND",
            f"{binding.project_id}: no retained {RHINO_EXECUTION_KIND} receipt "
            f"claims an artifact with sha256 {sha256}.",
        )
    # A digest exported twice is still one file; serve the copy that is there.
    record = next(
        (item for item in claiming if item.available), claiming[0]
    )
    if record.relative_path is None:
        raise _unavailable(binding, record)
    path = binding.repository.layout.resolve_relative(record.relative_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StudioError(
            404,
            "ARTIFACT_NOT_FOUND",
            f"{record.relative_path}: the file this receipt certifies could "
            f"not be read: {exc}",
        ) from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise StudioError(
            409,
            "ARTIFACT_DIGEST_MISMATCH",
            f"{record.relative_path} changed while it was being served: its "
            f"bytes hash to {actual}, not to the {sha256} its receipt "
            f"{record.receipt_ref} certifies.",
        )
    return record, data


def _unavailable(binding: ProjectBinding, record: ArtifactRecord) -> StudioError:
    """Why a receipt's digest cannot be served, in the receipt's own terms."""

    if record.unavailable_reason == DIGEST_MISMATCH:
        return StudioError(
            409,
            "ARTIFACT_DIGEST_MISMATCH",
            f"{binding.project_id}: run {record.run_id} holds a file named "
            f"{record.file_name}, but none of the copies in its workspaces "
            f"hashes to {record.sha256}, which receipt {record.receipt_ref} "
            "certifies. The file on disk is not the exported model.",
        )
    return StudioError(
        404,
        "ARTIFACT_NOT_FOUND",
        f"{binding.project_id}: receipt {record.receipt_ref} certifies "
        f"{record.file_name} with sha256 {record.sha256}, but no such file is "
        f"in the workspaces of run {record.run_id}.",
    )


def _receipts_of(
    binding: ProjectBinding, run_id: str
) -> tuple[tuple[ProjectRecordRef, Mapping[str, Any]], ...]:
    """The run's execution receipts, digest-verified by the repository."""

    return tuple(
        (ref, binding.repository.load_json(ref))
        for ref in binding.record_refs(run_id)
        if record_kind(ref) == RHINO_EXECUTION_KIND
    )


def _workspace_index(
    binding: ProjectBinding, run_id: str
) -> dict[str, tuple[Path, ...]]:
    """Every file under one run's ``workspaces``, indexed by name.

    The walk is bounded by that directory: the search for an exported model
    never leaves the run that claims to have produced it, and never asks the
    project at large what ``.3dm`` files it happens to contain.
    """

    root = binding.repository.layout.run(run_id).workspaces
    if not root.is_dir():
        return {}
    found: dict[str, list[Path]] = {}
    for parent, _directories, files in os.walk(root):
        directory = Path(parent)
        for name in files:
            found.setdefault(name, []).append(directory / name)
    return {name: tuple(sorted(paths)) for name, paths in found.items()}


def _artifact(
    binding: ProjectBinding,
    run_id: str,
    ref: ProjectRecordRef,
    receipt: Mapping[str, Any],
    index: Mapping[str, tuple[Path, ...]],
) -> ArtifactRecord:
    """One receipt read onto the wire's terms, with its file located or not."""

    identity = _mapping(receipt.get("identity"))
    program_binding = _mapping(identity.get("binding"))
    base = _mapping(program_binding.get("base"))
    inspection = _mapping(receipt.get("inspection"))
    claimed = _text(inspection.get("file_sha256"))
    file_name = Path(_text(receipt.get("artifact_relative_path")) or "").name
    path, reason = _resolve(binding, index, file_name, claimed)
    return ArtifactRecord(
        # Content addresses the artifact; a receipt whose export claimed no
        # digest can only be addressed by the receipt itself, and says so.
        artifact_id=claimed if claimed is not None else f"receipt:{ref.sha256}",
        run_id=run_id,
        stage_id=_text(program_binding.get("stage_id")),
        file_name=file_name,
        relative_path=(
            None
            if path is None
            else path.relative_to(binding.repository.layout.root).as_posix()
        ),
        sha256=claimed,
        size_bytes=_whole(inspection.get("file_bytes")),
        object_count=_whole(inspection.get("object_count")),
        status=_text(receipt.get("status")),
        readback_verified=_flag(receipt.get("readback_verified")),
        available=path is not None,
        unavailable_reason=reason,
        base_version=_whole(base.get("version")),
        base_state_sha256=_text(base.get("state_sha256")),
        branch_id=_text(program_binding.get("branch_id")),
        branch_epoch=_whole(program_binding.get("branch_epoch")),
        program_ref=_text(_mapping(program_binding.get("program_ref")).get("uri")),
        program_digest=_text(program_binding.get("program_digest")),
        design_state_digest=_text(program_binding.get("design_state_digest")),
        length_unit=_text(identity.get("length_unit")),
        up_axis=_text(identity.get("up_axis")),
        receipt_ref=ref.uri,
    )


def _resolve(
    binding: ProjectBinding,
    index: Mapping[str, tuple[Path, ...]],
    file_name: str,
    claimed: str | None,
) -> tuple[Path | None, str | None]:
    """The copy whose bytes are what the receipt certified, or why there is none."""

    if claimed is None:
        return None, NO_DIGEST
    candidates = index.get(file_name, ())
    if not candidates:
        return None, FILE_MISSING
    for path in candidates:
        if _file_sha256(binding, path) == claimed:
            return path, None
    return None, DIGEST_MISMATCH


def _file_sha256(binding: ProjectBinding, path: Path) -> str | None:
    """The file's own identity, hashed once per (path, size, mtime)."""

    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cache = binding.file_sha256_cache
    remembered = cache.get(key)
    if remembered is not None:
        return remembered
    try:
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None
    cache[key] = digest
    return digest


def _mapping(value: object) -> Mapping[str, Any]:
    """A nested receipt section, or an empty one when the receipt omits it."""

    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
