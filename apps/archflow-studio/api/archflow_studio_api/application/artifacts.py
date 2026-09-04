"""The exported models a run's receipts certify, and nothing else.

A ``.3dm`` file on disk is not an artifact: it is a file. What makes it an
artifact is a retained ``seat-rhino-execution`` receipt that says which run and
which program produced it, against which base, and what its bytes hash to. So
this module never looks for files and then asks what they are — it reads the
receipts and then asks whether the file they certify is still there.

That order is what lets the listing be honest about the four ways an artifact
can be absent: the receipt claimed no digest (a failed export), no file of that
name is in the run's workspaces, the copies that are there could not be read at
all, or they were read and none of them hash to the digest the receipt claims.
The last is the interesting one: it is what an edited-in-Rhino model looks like
from here, and it is never reported as success. The one before it is kept apart
from it on purpose — a file we could not open says nothing about its contents,
and calling that corruption would put a claim on the wire that nobody checked.

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
from typing import Any, Mapping, NamedTuple

from archflow.project.record_kinds import SEAT_RHINO_EXECUTION
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import ProjectRepositoryError

from ..transport.errors import StudioError, error_sentence
from .binding import ProjectBinding, record_kind

# A file digest, as it travels in a path parameter. Lowercase because that is
# what the kernel writes; anything else names no artifact here.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Why an artifact cannot be served. Four distinct facts, never collapsed —
# ``FILE_UNREADABLE`` especially: a copy that would not open has not been shown
# to be wrong, only to be unavailable, and saying otherwise would be a claim
# about contents nobody read.
NO_DIGEST = "no inspection digest"
FILE_MISSING = "file missing"
FILE_UNREADABLE = "file unreadable"
DIGEST_MISMATCH = "digest mismatch"


class _Resolution(NamedTuple):
    """Where the certified bytes are, or why they are not to be had."""

    path: Path | None
    reason: str | None
    # The OSError class that stopped the read, when that is the reason.
    error: str | None


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One receipt, and what became of the file it certified."""

    artifact_id: str
    run_id: str
    stage_id: str | None
    file_name: str
    relative_path: str | None
    # The located file itself. Kept beside the rendered path because the
    # rendering is one-way: a run's workspace may hold an export whose name is
    # not a portable P036 path segment (an accent, a Chinese character), and
    # asking the kernel to parse our own display string back would refuse it.
    path: Path | None
    sha256: str | None
    size_bytes: int | None
    object_count: int | None
    status: str | None
    readback_verified: bool | None
    available: bool
    unavailable_reason: str | None
    # Which OSError stopped the read, when the reason is ``FILE_UNREADABLE``.
    # It belongs in the refusal's detail, not on the listing's wire form.
    unavailable_error: str | None
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
            refs = _receipt_refs(binding, run_id)
        except (StudioError, ProjectRepositoryError, ValueError, OSError):
            skipped.append(run_id)
            continue
        if not refs:
            continue
        # One walk of the run's workspaces answers every receipt in it.
        index = _workspace_index(binding, run_id)
        for ref in refs:
            try:
                payload = binding.repository.load_json(ref)
            except (ProjectRepositoryError, ValueError, OSError):
                # One receipt that will not load costs its own row and no
                # more: the other receipts in this run still certify exactly
                # what they certified. The run is named as skipped, because a
                # listing that dropped a row silently would be a shorter
                # answer indistinguishable from a complete one.
                if run_id not in skipped:
                    skipped.append(run_id)
                continue
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
            f"{binding.project_id}: no retained {SEAT_RHINO_EXECUTION} receipt "
            f"claims an artifact with sha256 {sha256}"
            # "Not found" is only true of what was searched. Runs the listing
            # could not read were not searched, so they are named here rather
            # than letting the client read this as "no such artifact exists".
            + _unsearched(listing.skipped_runs),
        )
    # A digest exported twice is still one file; serve the copy that is there.
    record = next(
        (item for item in claiming if item.available), claiming[0]
    )
    if record.path is None:
        raise _unavailable(binding, record)
    try:
        data = record.path.read_bytes()
    except OSError as exc:
        raise StudioError(
            409,
            "ARTIFACT_UNREADABLE",
            f"{record.relative_path}: the file receipt {record.receipt_ref} "
            f"certifies could not be read ({error_sentence(exc)}). "
            "Nothing is claimed about its contents.",
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
    if record.unavailable_reason == FILE_UNREADABLE:
        # Deliberately not a mismatch: an unopened file has not disagreed with
        # anything. What failed is the read, and the read is what is reported.
        return StudioError(
            409,
            "ARTIFACT_UNREADABLE",
            f"{binding.project_id}: run {record.run_id} holds a file named "
            f"{record.file_name}, but the copies of it in that run's "
            f"workspaces could not be read ({record.unavailable_error}), so "
            f"whether any of them is the {record.sha256} that receipt "
            f"{record.receipt_ref} certifies is unknown.",
        )
    return StudioError(
        404,
        "ARTIFACT_NOT_FOUND",
        f"{binding.project_id}: receipt {record.receipt_ref} certifies "
        f"{record.file_name} with sha256 {record.sha256}, but no such file is "
        f"in the workspaces of run {record.run_id}.",
    )


def _unsearched(skipped_runs: tuple[str, ...]) -> str:
    """The runs a "not found" did not actually look in, named in its detail."""

    if not skipped_runs:
        return "."
    count = len(skipped_runs)
    noun = "directory" if count == 1 else "directories"
    return (
        f"; {count} run {noun} could not be read and may hold the claiming "
        f"receipt: {', '.join(skipped_runs)}."
    )


def _receipt_refs(
    binding: ProjectBinding, run_id: str
) -> tuple[ProjectRecordRef, ...]:
    """The run's execution receipt records, digest-verified by the repository.

    Listing only. What each receipt *says* is read one at a time by the
    caller, so a record that will not load costs one row rather than a run.
    """

    return tuple(
        ref
        for ref in binding.record_refs(run_id)
        if record_kind(ref) == SEAT_RHINO_EXECUTION
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
    for parent, directories, files in os.walk(root):
        parent_path = Path(parent)
        # Directories are indexed alongside files: something wearing the
        # artifact's name that cannot be opened is a different answer from
        # nothing being there, and the caller is entitled to the difference.
        for name in (*files, *directories):
            found.setdefault(name, []).append(parent_path / name)
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
    resolution = _resolve(binding, index, file_name, claimed)
    path = resolution.path
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
        path=path,
        sha256=claimed,
        size_bytes=_whole(inspection.get("file_bytes")),
        object_count=_whole(inspection.get("object_count")),
        status=_text(receipt.get("status")),
        readback_verified=_flag(receipt.get("readback_verified")),
        available=path is not None,
        unavailable_reason=resolution.reason,
        unavailable_error=resolution.error,
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
) -> _Resolution:
    """The copy whose bytes are what the receipt certified, or why there is none.

    "None of them matched" and "none of them could be read" are separate
    answers. Only the first is evidence about content, so a candidate that
    refused to open is remembered as a failed read and never counted as a
    disagreeing one.
    """

    if claimed is None:
        return _Resolution(None, NO_DIGEST, None)
    candidates = index.get(file_name, ())
    if not candidates:
        return _Resolution(None, FILE_MISSING, None)
    read_one = False
    error: str | None = None
    for path in candidates:
        try:
            digest = _file_sha256(binding, path)
        except OSError as exc:
            error = error_sentence(exc)
            continue
        read_one = True
        if digest == claimed:
            return _Resolution(path, None, None)
    if read_one:
        return _Resolution(None, DIGEST_MISMATCH, None)
    return _Resolution(None, FILE_UNREADABLE, error)


def _file_sha256(binding: ProjectBinding, path: Path) -> str:
    """The file's own identity, hashed once per (path, size, mtime).

    Raises ``OSError`` when the file cannot be read: the caller decides what an
    unread candidate means, because here it means nothing at all.
    """

    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cache = binding.file_sha256_cache
    remembered = cache.get(key)
    if remembered is not None:
        return remembered
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    cache[key] = digest
    return digest


def _mapping(value: object) -> Mapping[str, Any]:
    """A nested receipt section, or an empty one when the receipt omits it."""

    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    """A receipt string that is actually a string, and not an empty one."""

    return value if isinstance(value, str) and value else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
