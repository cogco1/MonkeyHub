"""Receipt-certified model artifacts and non-canonical viewport captures.

A ``.3dm`` or ``.step`` file on disk is not an artifact: it is a file. What
makes it an artifact is a retained export receipt — a ``seat-occt-execution``
(the ordinary in-process export: one exact STEP file and one mesh ``.3dm``
preview of the same model) or a ``seat-rhino-execution`` (the Rhino host
export) — that says which run and which program produced it, against which
base, and what its bytes hash to. So this module never looks for files and then
asks what they are — it reads the receipts and then asks whether the file they
certify is still there. One OCCT receipt is two rows, because it certifies two
files; each row says which it is (``representation``: exact or preview) and
what it is (``format``: step or 3dm), and a preview is never labelled as a
B-rep.

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

A viewport capture is deliberately different: it is an inspection image, not
an exported model. The Studio validates its PNG bytes and asks P036 to retain
them below the existing run named by the request. No receipt is minted, the
capture is not returned by ``list_artifacts``, and canonical HEAD is untouched.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
from typing import Any, Mapping, NamedTuple

from PIL import Image

from archflow.project.record_kinds import SEAT_OCCT_EXECUTION, SEAT_RHINO_EXECUTION
from archflow.project.ports import PersistenceArea, PersistenceDestination
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
PNG_MEDIA_TYPE = "image/png"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"

# The receipts that certify an exported file, and the schema that tells the
# two apart. Both are read; nothing is ever regenerated or run by reading them.
EXPORT_RECEIPT_KINDS = (SEAT_OCCT_EXECUTION, SEAT_RHINO_EXECUTION)
OCCT_RECEIPT_SCHEMA = "OcctExecutionReceipt@1"

# What a listed file is. ``format`` is the file format a reader has to know
# to open it: ``step`` (ISO 10303-21) or ``3dm`` (what the viewer loads).
# ``representation`` is the claim the receipt makes about its geometry:
# ``exact`` for the delivered model (a STEP B-rep, or a Rhino export that was
# read back), ``preview`` for a render mesh tessellated from the exact model
# so a viewer can show it — never a NURBS or B-rep delivery.
FORMAT_STEP = "step"
FORMAT_3DM = "3dm"
EXACT = "exact"
PREVIEW = "preview"


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
    # ``step`` or ``3dm``: what a reader must know to open the file.
    format: str
    # ``exact`` or ``preview``: what the receipt claims the geometry is. A
    # preview is a mesh for looking at; the exact file is the delivery.
    representation: str


@dataclass(frozen=True, slots=True)
class ArtifactListing:
    """Every artifact this project can account for, and what it could not read."""

    project_id: str
    artifacts: tuple[ArtifactRecord, ...]
    # Run directories whose records could not be listed. One corrupt run must
    # not cost the client every other artifact, and the skip is never silent.
    skipped_runs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ViewportCapture:
    """One non-canonical inspection image retained in its named run."""

    project_id: str
    run_id: str
    relative_path: str
    sha256: str
    media_type: str
    size_bytes: int


def save_viewport_capture(
    binding: ProjectBinding,
    run_id: str,
    png_base64: str,
) -> ViewportCapture:
    """Retain one PNG below the explicitly named existing run through P036."""

    if not isinstance(png_base64, str) or not png_base64:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a PNG image",
        )
    try:
        png_bytes = base64.b64decode(png_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not valid base64 PNG data",
        ) from exc
    # Pillow tolerates a truncated IEND checksum; captures must be complete.
    if not png_bytes.endswith(PNG_END):
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a complete PNG image",
        )
    try:
        with Image.open(BytesIO(png_bytes), formats=["PNG"]) as capture:
            capture.verify()
        # verify checks the chunks, not the compressed pixel data.
        with Image.open(BytesIO(png_bytes), formats=["PNG"]) as capture:
            capture.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise StudioError(
            422,
            "CAPTURE_INVALID",
            "the viewport capture is not a readable PNG image",
        ) from exc
    run = binding.load_run(run_id)

    digest = hashlib.sha256(png_bytes).hexdigest()
    workspace_path = f"studio-captures/viewport-{digest}.png"
    try:
        ref = binding.repository.put_workspace_file(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_WORKSPACE,
                run_id=run.run_id,
            ),
            artifact_id=f"viewport-capture-{digest}",
            workspace_relative_path=workspace_path,
            media_type=PNG_MEDIA_TYPE,
            source=BytesIO(png_bytes),
        )
    except (ProjectRepositoryError, OSError) as exc:
        raise StudioError(
            409,
            "CAPTURE_WRITE_FAILED",
            f"{binding.project_id}: the viewport capture could not be retained "
            f"in run {run.run_id}: {error_sentence(exc)}",
        ) from exc
    return ViewportCapture(
        project_id=ref.project_id,
        run_id=run.run_id,
        relative_path=ref.relative_path,
        sha256=ref.sha256,
        media_type=ref.media_type,
        size_bytes=len(png_bytes),
    )


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
            records.extend(_artifacts(binding, run_id, ref, payload, index))
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
            f"{binding.project_id}: no retained export receipt "
            f"({' or '.join(EXPORT_RECEIPT_KINDS)}) claims an artifact with "
            f"sha256 {sha256}"
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
        if record_kind(ref) in EXPORT_RECEIPT_KINDS
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


def _artifacts(
    binding: ProjectBinding,
    run_id: str,
    ref: ProjectRecordRef,
    receipt: Mapping[str, Any],
    index: Mapping[str, tuple[Path, ...]],
) -> tuple[ArtifactRecord, ...]:
    """One receipt read onto the wire's terms: one row per file it certifies.

    A Rhino receipt certifies one ``.3dm``. An OCCT receipt certifies the
    exact STEP file and the mesh preview tessellated from the same model, and
    is two rows sharing the receipt, the stage and the program binding — a
    client groups them by ``receipt_ref``; they are never two candidates.
    """

    identity = _mapping(receipt.get("identity"))
    program_binding = _mapping(identity.get("binding"))
    base = _mapping(program_binding.get("base"))
    bound = dict(
        run_id=run_id,
        stage_id=_text(program_binding.get("stage_id")),
        status=_text(receipt.get("status")),
        readback_verified=_flag(receipt.get("readback_verified")),
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

    def row(
        file_name: str,
        claimed: str | None,
        *,
        format: str,
        representation: str,
        size_bytes: int | None,
        object_count: int | None,
    ) -> ArtifactRecord:
        resolution = _resolve(binding, index, file_name, claimed)
        path = resolution.path
        return ArtifactRecord(
            # Content addresses the artifact; a receipt whose export claimed
            # no digest can only be addressed by the receipt itself, and says so.
            artifact_id=claimed if claimed is not None else f"receipt:{ref.sha256}",
            file_name=file_name,
            relative_path=(
                None
                if path is None
                else path.relative_to(binding.repository.layout.root).as_posix()
            ),
            path=path,
            sha256=claimed,
            size_bytes=size_bytes,
            object_count=object_count,
            available=path is not None,
            unavailable_reason=resolution.reason,
            unavailable_error=resolution.error,
            format=format,
            representation=representation,
            **bound,
        )

    if receipt.get("schema") != OCCT_RECEIPT_SCHEMA:
        inspection = _mapping(receipt.get("inspection"))
        return (
            row(
                Path(_text(receipt.get("artifact_relative_path")) or "").name,
                _text(inspection.get("file_sha256")),
                format=FORMAT_3DM,
                representation=EXACT,
                size_bytes=_whole(inspection.get("file_bytes")),
                object_count=_whole(inspection.get("object_count")),
            ),
        )

    physical = receipt.get("physical_object_ids")
    object_count = len(physical) if isinstance(physical, list) else None
    exact = receipt.get("exact_artifact")
    preview = receipt.get("preview_artifact")
    rows: list[ArtifactRecord] = []
    if not isinstance(exact, Mapping) and not isinstance(preview, Mapping):
        # Nothing was written (the build itself failed): one row, addressed by
        # the receipt, named by the stem the runner would have used, so the
        # failure is listed with its reason rather than vanishing.
        stage_id = _text(program_binding.get("stage_id")) or "export"
        digest = _text(program_binding.get("program_digest")) or ""
        rows.append(
            row(
                f"{stage_id}@{digest[:12]}.step" if digest else f"{stage_id}.step",
                None,
                format=FORMAT_STEP,
                representation=EXACT,
                size_bytes=None,
                object_count=object_count,
            )
        )
    if isinstance(exact, Mapping):
        rows.append(
            row(
                Path(_text(exact.get("relative_path")) or "").name,
                _text(exact.get("sha256")),
                format=FORMAT_STEP,
                representation=EXACT,
                size_bytes=None,
                object_count=object_count,
            )
        )
    if isinstance(preview, Mapping):
        inspected = _whole(_mapping(receipt.get("preview_inspection")).get("object_count"))
        rows.append(
            row(
                Path(_text(preview.get("relative_path")) or "").name,
                _text(preview.get("sha256")),
                format=FORMAT_3DM,
                representation=PREVIEW,
                size_bytes=None,
                object_count=inspected if inspected is not None else object_count,
            )
        )
    return tuple(rows)


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
